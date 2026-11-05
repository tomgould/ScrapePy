# ScrapePy

Python web scraper using [Scrapling](https://github.com/D4Vinci/Scrapling) + [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) for stealth browser automation and anti-detection.

ScrapePy crawls websites, downloads assets, rewrites URLs for offline viewing, and converts everything to Markdown with YAML frontmatter. It handles Cloudflare challenges, JavaScript-rendered SPAs, Swagger/OpenAPI docs, documentation platforms, Instagram profiles, and news sites with date filtering out of the box.

It also works as a building block: each crawl writes a machine-readable `run_summary.json` listing the new and changed Markdown files, every Markdown file carries a `content_hash`, and the exit code tells a script whether the run worked.

## Responsible use

You are responsible for how you use ScrapePy. Follow the terms of service of the sites you access and comply with the law that applies to you, including copyright and data protection rules. Only scrape content you have the right to access and keep. The authors accept no liability for how the software is used.

What ScrapePy does to keep a crawl polite:

- robots.txt is read at the start of every run and its rules are applied to the URLs a mode picks to fetch. Blocked URLs are skipped, counted in the run summary and listed in `run_summary.json`.
- Rules follow [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309): the longest matching `Allow` or `Disallow` pattern wins whatever order the lines are in, and `Allow` wins a tie. `*` matches any run of characters and a trailing `$` anchors the end of the URL, so `Disallow: /*.pdf$` blocks PDFs and `Disallow: /*?sort=` blocks sorted listings. Links found while crawling never carry query strings (they are stripped before queueing), so rules about query strings only affect sitemap and feed URLs. Paths are case-sensitive and percent-encoding is normalised. An empty `Disallow:` allows everything, and a UTF-8 byte order mark at the start of the file is ignored. Path parameters (`;`) are matched as ordinary characters. Matching uses no regular expressions and its cost is bounded: `Allow` and `Disallow` patterns longer than 2,000 characters are ignored, and when the site has rules for you, a URL whose path and query come to more than 8,192 characters is not fetched and is counted as blocked by robots.txt.
- The group is chosen by your `--user-agent` product token (`MyBot` in `MyBot/1.0`): a group whose `User-agent` line names the same token, compared case-insensitively and ignoring any version suffix, replaces the `User-agent: *` group. `User-agent: M` does not match `MyBot`. Groups naming the same token are merged. Without `--user-agent`, the `*` group applies. `Crawl-delay` is read from the chosen group only.
- A robots.txt that returns 404 or another 4xx means there are no restrictions. One that returns a 5xx error, or a server that cannot be reached or does not answer within 10 seconds (one attempt, no retries), means the whole site is treated as disallowed: ScrapePy prints why and fetches nothing.
- Up to five redirects are followed when fetching robots.txt, to any host, and the file they lead to applies to the site being crawled. Only `http` and `https` redirect targets are followed. More than five redirects, a redirect loop, a redirect without a `Location` header or a redirect to any other scheme (such as `file:` or `ftp:`) count as unreachable, so the site is treated as disallowed.
- Page requests to a site start at least `--wait` seconds apart (3 seconds by default, 5 in browser modes). A robots.txt `Crawl-delay` raises that gap and is never lowered by `--wait`. Prefetching does not get around the gap: it overlaps waiting, not requests. Reconnaissance and page requests share one limiter, so the gap also holds between the last reconnaissance request and the first page.
- At most 4 requests run at once by default (`-c` changes this).
- Reconnaissance requests (sitemaps, feeds, homepage) are spaced by one second or `--wait`, whichever is shorter, and never by less than `Crawl-delay`.

Which modes check robots.txt:

| Mode | robots.txt |
|------|------------|
| Default crawl, `-j` | Every page URL, including prefetched ones, and the sitemap, feed and homepage requests made during reconnaissance |
| `-d` docs | The start URL and every sidebar link |
| `-1` single page, `--swagger` | The start URL |
| `--audit` | Reconnaissance requests and the sample pages. Audit writes into `tests/fixtures/` and ignores `-o` |
| `--instagram` | Not checked. Instagram's robots.txt does not allow general crawling, so read its terms before using this mode |

Asset downloads (images, CSS, JS) linked from an allowed page are not checked against robots.txt and are not paced, though they share the `-c` concurrency limit.

`--ignore-robots` turns off the rules and `Crawl-delay`, and also overrides an unreadable (5xx) robots.txt. ScrapePy prints a warning when it is used. Only use it on sites you own or have permission to crawl this way.

The stealth browser and proxy features exist because some sites block ordinary clients even for content they publish openly. They do not give you permission to access anything.

## Installation

```bash
# Install the package (provides the `scrapepy` command)
pip install .

# or, for development, with the test dependencies
pip install -e ".[dev]"

# Install the Patchright browser (Chromium), needed for browser modes only
scrapling install
```

Requires Python 3.11+. `pip install -r requirements.txt` still works if you only want to run `python main.py`.

## Testing

```bash
# Run all tests (no network calls, a few seconds)
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_navigation.py -v
```

Tests cover date extraction, error detection, URL normalization, page classification, URL rewriting, config and CLI parsing, exit codes, robots.txt rules and request pacing, cache operations, sitemap/RSS/Atom parsing, navigation parsing (including mega-menu/dropdown), outline building, article detection, section splitting, path mapping, YAML frontmatter, section index generation, the run summary, and fixture-driven validation. A few tests run the whole crawl loop against in-memory pages, and one sends a real Ctrl+C to a crawl of a test server on 127.0.0.1. None of them reach the internet.

## Usage

```bash
scrapepy <url> [options]
# or
python -m scrapepy <url> [options]
python main.py <url> [options]
```

### Options

| Flag | Description |
|------|-------------|
| `-m`, `--markdown` / `--no-markdown` | Convert to Markdown after scraping (default: on) |
| `-s`, `--stay-in-path` | Only crawl URLs under the starting path |
| `-w`, `--path-wildcard` | Match URLs containing the path (use with `-s`) |
| `-j`, `--js-render` | Wait for JavaScript to render content |
| `-1`, `--single-page` | Single page SPA mode (no crawling) |
| `--swagger`, `--openapi` | Swagger/OpenAPI documentation mode |
| `-d`, `--docs` | Documentation site mode |
| `-a`, `--include-assets` / `--no-include-assets` | Include CSS/JS/font files (default: on, images always included) |
| `-i`, `--incremental` / `--no-incremental` | Only re-download pages/assets modified since last crawl (default: on) |
| `--wait SECONDS` | Minimum gap between page requests to the site (default: 3s HTTP, 5s browser). robots.txt `Crawl-delay` raises it |
| `-c`, `--concurrent N` | Max concurrent requests for assets and page prefetching (default: 4) |
| `--user-agent STRING` | User-Agent header for all requests, also used to match robots.txt groups (default: a browser-like user agent) |
| `--ignore-robots` | Do not apply robots.txt rules or `Crawl-delay`. Prints a warning; only use with permission |
| `-o`, `--output-dir DIR` | Folder under which `ScrapedSites/` and `MarkdownSites/` are created (default: current directory) |
| `--index-pages` / `--no-index-pages` | Write the generated `_index.md` section listing pages (default: on) |
| `--auth USER:PASS` | HTTP Basic Auth credentials |
| `--proxy URL` | Proxy URL for requests |
| `--headless` | Run browser headless (default: visible) |
| `-v`, `--verbose` | Verbose logging |
| `--recent DURATION` | Only keep articles from last N period: `2w`, `1d`, `6h` |
| `--since DATE` | Only keep articles published after date: `YYYY-MM-DD` |
| `--min-articles N` | Keep at least N articles per section even if old (use with `--recent`/`--since`) |
| `--max-articles N` | Max articles per section (newest N, default: 0 = no limit) |
| `--audit` | Reconnaissance only: analyze site structure and save test fixtures in `tests/fixtures/` (no crawl, ignores `-o`) |
| `--instagram` | Instagram profile scraping mode |
| `--accounts FILE` | File with Instagram usernames/URLs (one per line) |
| `--ig-login` | Enable Instagram login (visible browser, saves cookies) |
| `--ig-max-posts N` | Max posts to scrape per account (0 = all) |
| `--update-meta` | Re-scrape posts to refresh metadata but skip re-downloading existing media |
| `--version` | Print the version and exit |

### Examples

```bash
# Basic site crawl (markdown, incremental, and assets are ON by default)
python main.py https://www.example.com

# Write output somewhere else and identify your crawler
python main.py https://www.example.com -o /data/crawls --user-agent "MyCrawler/1.0 (+https://example.org/bot)"

# Markdown for a search index: skip the generated section listing pages
python main.py https://www.example.com --no-index-pages

# Crawl without Markdown conversion
python main.py https://www.example.com --no-markdown

# Stay within a specific path
python main.py https://docs.example.com/api/v2/ -s

# JavaScript-rendered site with extra wait time
python main.py https://spa-site.com -j --wait 10

# Single page application
python main.py https://api-reference.example.com -1

# Swagger/OpenAPI documentation
python main.py https://petstore.swagger.io --swagger

# Documentation site (Readme.io, Stoplight, etc.)
python main.py https://developers.example.com -d

# Proxy support
python main.py https://example.com --proxy http://proxy:8080

# Skip incremental (always re-download everything)
python main.py https://example.com --no-incremental

# Only recent articles (last 2 weeks)
python main.py https://news-site.com --recent 2w

# Articles since a specific date
python main.py https://news-site.com --since 2026-02-01

# Recent articles with minimum per section
python main.py https://news-site.com --recent 1w --min-articles 3

# Cap to newest 10 articles per section
python main.py https://news-site.com --recent 1w --max-articles 10

# Daily re-scan for new articles (incremental is on by default)
python main.py https://news-site.com --recent 1d

# Audit site structure (no crawl, saves test fixtures)
python main.py --audit https://books.toscrape.com -v

# Headless browser mode
python main.py https://example.com -1 --headless

# Instagram: scrape a profile (latest 5 posts)
python main.py https://www.instagram.com/natgeo/ --instagram --ig-max-posts 5

# Instagram: multiple accounts from file with Markdown
python main.py --instagram --accounts accounts.txt -m

# Instagram: with login for private profiles
python main.py https://www.instagram.com/someuser/ --instagram --ig-login

# Instagram: incremental (only scrape new posts)
python main.py https://www.instagram.com/natgeo/ --instagram -i

# Instagram: daily monitoring (refresh metadata, skip existing media)
python main.py --instagram --accounts accounts.txt --ig-max-posts 5 --update-meta
```

## Output

Output goes under the current directory, or under `--output-dir` when given. Scraped sites are saved to `ScrapedSites/` with this structure:

```
ScrapedSites/example_com/
  index.html
  about.html
  products/widgets.html
  site_outline.json        (navigation structure + sitemap stats)
  run_summary.json         (what this crawl did, see below)
  .scrapepy_cache.json     (incremental cache)
  images/
  css/
  js/
  fonts/
  media/
  broken_links.txt         (if any broken links found)
```

Markdown files are saved to `MarkdownSites/` (on by default; use `--no-markdown` to disable):

```
MarkdownSites/example_com/
  index.md
  about.md
  products/widgets.md
  images/             (copied from ScrapedSites so image links work)
  media/              (copied from ScrapedSites)
```

Pages classified as sections (the homepage usually is) are written as `_index.md` inside a folder named after the section. When date filtering produces section batches, articles are organized under section directories, and each section gets a generated `_index.md` listing its articles. `--no-index-pages` skips the generated listings, which helps when the Markdown feeds a search index:

```
MarkdownSites/example_com/
  _index.md
  sport/
    _index.md         (section index with article listings + thumbnails)
    images/           (images referenced by section articles)
    my-article.md
    football/
      _index.md       (sub-section index)
      images/
      article-two.md
  news/
    _index.md
    images/
    some-article.md
```

Image and media directories are automatically copied from `ScrapedSites/` so that references in the Markdown files resolve correctly. CSS, JS, and font files are not copied since they aren't referenced in Markdown output.

### YAML Frontmatter

Every Markdown file includes YAML frontmatter with page metadata, useful for AI/RAG pipelines:

```yaml
---
title: Example article title
url: "https://example.com/news/some-article"
date: 2026-02-20
date_source: META_TAG
page_type: article
word_count: 1247
description: A short description of the article...
domain: example.com
scraped_at: "2026-11-05T12:00:00+00:00"
content_hash: "sha256:3f5a..."
---

# Example article title
...content...
```

Fields are populated automatically: in crawl mode from live metadata (URL, date, page type), in batch mode by extracting from the HTML. Only non-null fields are written, so a missing `date` means no date was found. Keys are always lowercase, and generated `_index.md` pages use the same keys (with `page_type: section`, plus `articles` and `sub_sections` counts). Date sources are ranked by confidence: `META_TAG` > `JSON_LD` > `URL_PATTERN` > `TIME_ELEMENT` > `HTTP_HEADER`.

`scraped_at` is when the file was written, in UTC. `content_hash` is the sha256 of the Markdown body, meaning everything after the blank line that follows the closing `---`. It stays the same when the page content does, so an indexer can skip files whose hash it has already seen.

### Run summary (`run_summary.json`)

Every crawl writes `run_summary.json` into the site's `ScrapedSites/<site>/` folder and prints its path at the end. Paths in it are relative to the output directory (`--output-dir`, or the current directory).

The file is replaced as soon as a crawl starts with a placeholder whose `status` is `running`, so a summary left by an earlier run is never mistaken for the current one. If the process dies without finishing, the file still says `running`. Each write goes to a temporary file that is then renamed, so a reader never sees half a file. On Ctrl+C or SIGTERM (from cron, systemd or `kill`) the crawl stops, saves the incremental cache so the next run carries on from there, and writes the summary with `status` set to `interrupted`, `interrupted_by` set to `SIGINT` or `SIGTERM`, and exit code 130 or 143. Requests still in flight are abandoned rather than waited for, so the process exits within a moment even when the site has stopped answering. If a second Ctrl+C arrives while it is still saving, it stops at once and the summary may be left at `running`.

```json
{
  "schema_version": 1,
  "status": "complete",
  "start_url": "https://quotes.toscrape.com/",
  "started_at": "2026-11-05T12:00:00+00:00",
  "ended_at": "2026-11-05T12:01:10+00:00",
  "output_dir": "/data/crawls",
  "html_dir": "ScrapedSites/quotes_toscrape_com",
  "markdown_dir": "MarkdownSites/quotes_toscrape_com",
  "options": { "concurrent": 4, "wait_seconds": 3.0, "ignore_robots": false, "...": "..." },
  "counts": {
    "fetched": 12, "new": 10, "changed": 0, "unchanged": 0,
    "failed": 2, "blocked_by_robots": 0, "skipped_old": 0
  },
  "new_markdown": ["MarkdownSites/quotes_toscrape_com/_index.md"],
  "changed_markdown": [],
  "unchanged_urls": [],
  "failed": [{ "url": "https://quotes.toscrape.com/missing", "reason": "HTTP 404" }],
  "blocked_by_robots": [],
  "interrupted": false,
  "interrupted_by": null,
  "exit_code": 0
}
```

| Field | Meaning |
|-------|---------|
| `schema_version` | Goes up when a field is renamed or removed. New fields can appear without a bump |
| `status` | `running` while the crawl is in progress, then `complete` or `interrupted`. `ended_at` and `exit_code` are `null` while it is `running` |
| `interrupted`, `interrupted_by` | Whether the run was stopped early, and by which signal (`SIGINT` or `SIGTERM`, otherwise `null`) |
| `start_url`, `started_at`, `ended_at` | The URL given and the run's start and end times (UTC, ISO 8601) |
| `output_dir` | Absolute path of the output directory. `html_dir` and `markdown_dir` are relative to it; `markdown_dir` is `null` with `--no-markdown` |
| `options` | The settings that shaped the run. Credentials are never written: `auth_used` and `proxy_used` only say whether they were set |
| `counts.fetched` | Page requests that completed, with a response or a network error, including ones answered 304 Not Modified. A request still waiting for its turn when the run is interrupted is not counted |
| `counts.new`, `counts.changed` | Pages saved this run. A page is changed when an earlier run saved it (it is in the incremental cache or its HTML file already exists). A second URL that saves to a file already written in the same run, such as `/index.html` after `/`, is fetched but not counted again |
| `counts.unchanged` | Pages skipped because the server said 304 or the HTML matched the cache. Their URLs are in `unchanged_urls` |
| `counts.failed` | Fetch errors, HTTP 4xx/5xx and error pages. `failed` lists each URL with a reason |
| `counts.blocked_by_robots` | URLs skipped because robots.txt disallows them, listed in `blocked_by_robots` |
| `counts.skipped_old` | Articles dropped by `--recent`/`--since` after fetching |
| `new_markdown`, `changed_markdown` | Markdown files written for new and changed pages. A page too short to convert is counted but has no file here. Generated `_index.md` listing pages are not included |
| `exit_code` | The exit code the run finished with |

New, changed and unchanged are decided on the page HTML, so a page whose HTML changed but whose Markdown did not still counts as changed. Compare `content_hash` if you need the stricter test.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success: at least one page was fetched without failing (unchanged pages count) |
| 1 | Nothing was fetched (for example robots.txt blocked the start URL) or every fetch failed |
| 2 | Bad arguments, such as an invalid `--since` date |
| 130 | Interrupted with Ctrl+C (SIGINT) |
| 143 | Stopped with SIGTERM |

Browser modes (`-1`, `--swagger`, `-d`) exit 1 when they save no page. Audit and Instagram modes exit 0 unless the arguments are bad.

### Numeric Path Flattening

URLs with numeric path segments are flattened to avoid deeply nested single-file directories:

```
/news/12345/1/slug  →  news/slug-12345-1.html
/news/1005473      →  news/1005473.html (number is the filename, no slug)
/a/1/2/3/4/slug     →  a/slug-1-2-3-4.html
```

## Modes

### Default Crawl

Recursively crawls all pages on the same domain using HTTP requests with TLS fingerprint spoofing. Downloads images, rewrites internal URLs to relative paths for offline viewing, and detects error pages (404, 403, etc.).

Before crawling begins, a lightweight **Phase 0 reconnaissance** runs automatically:
1. Fetches `robots.txt` for its access rules, `Crawl-delay` and sitemap URLs
2. Fetches sitemaps and RSS/Atom feeds to build a dated URL map
3. Parses the homepage `<header>`/`<nav>`/`<footer>` to extract navigation structure (including mega-menus and dropdown panels)
4. Saves a `site_outline.json` with the site's section hierarchy (including sub-sections from dropdowns) and sitemap stats
5. Seeds the crawl queue from the navigation structure (instead of just `/`)

This ensures the scraper understands the site's organizational structure before scraping content.

### Single Page (`-1`)

For single-page applications. Launches a stealth browser, waits for JavaScript to render, scrolls the entire page to trigger lazy loading, then saves the fully rendered HTML.

### JS Render (`-j`)

Like the default crawl, but uses a stealth browser for each page to handle JavaScript-rendered content. Scrolls pages and waits for network idle before capturing.

### Swagger (`--swagger`)

Loads Swagger UI in a stealth browser, expands all endpoint sections and model schemas, attempts to locate and download the raw OpenAPI spec file, then saves everything. With `-m`, converts the spec directly to Markdown for clean documentation.

### Docs (`-d`)

For documentation platforms (Readme.io, Stoplight, GitBook, etc.). Finds sidebar navigation links, visits each page, expands collapsed sections, and saves individual pages plus a combined document.

### Audit (`--audit`)

Runs site reconnaissance without crawling. Analyzes the site's structure (robots.txt, sitemaps, RSS feeds, navigation) and fetches a few sample pages, then saves everything as test fixtures:

```
tests/fixtures/books_toscrape_com/
  audit_report.json       # Full recon results
  test_config.json        # Expected parse results (committable)
  pages/homepage.html     # HTML snapshots
  pages/section_travel.html
  pages/section_mystery.html
  meta/robots.txt
```

The `test_config.json` is safe to commit (just expected values, no site content). Running `pytest` validates parsing logic against the real HTML fixtures.

### Instagram (`--instagram`)

Scrapes Instagram profiles using a stealth browser. Downloads post images/videos, extracts captions, timestamps, engagement metrics, hashtags, mentions, and location data. Supports carousel posts, multiple accounts from a file, and optional login with persistent cookies for private profiles.

Output structure:

```
ScrapedSites/instagram/
  natgeo/
    profile.json
    profile.md                  (with -m)
    2024-06-15_Cxy123abc/
      post.html
      post.json
      post.md                   (with -m)
      media/
        image_1.jpg
        image_2.jpg
```

Accounts file format (for `--accounts`):

```text
# One account per line; supports URLs, @username, or bare usernames
https://www.instagram.com/natgeo/
@nasa
spacex
```

### Recent Content Filtering (`--recent` / `--since`)

Filters large sites (especially news/media) to only scrape recent articles while preserving section/navigation pages. Uses multi-signal date extraction (meta tags, JSON-LD, URL patterns, `<time>` elements, HTTP headers) and page classification (section vs article vs unknown). Articles older than the cutoff are skipped; section pages are always kept.

Pre-crawl discovery (sitemaps, RSS feeds) builds a date map to skip old URLs without fetching them.

Section-ordered traversal processes article links in DOM order (newest first). When an old article is found in a section batch, remaining articles are discarded.

## Anti-Detection

ScrapePy uses Scrapling's built-in stealth features:

- **StealthyFetcher**: Patchright (undetected Playwright fork) with browser fingerprint spoofing
- **Cloudflare bypass**: Automatic challenge solving via `solve_cloudflare=True`
- **TLS fingerprinting**: HTTP requests mimic real browser TLS signatures via curl_cffi
- **Stealth headers**: Realistic User-Agent and header ordering, unless you set `--user-agent`

## Dependencies

| Package | Purpose |
|---------|---------|
| [scrapling](https://github.com/D4Vinci/Scrapling) | Adaptive scraping framework (includes patchright, curl_cffi) |
| [beautifulsoup4](https://pypi.org/project/beautifulsoup4/) | HTML parsing |
| [lxml](https://pypi.org/project/lxml/) | Fast HTML/XML parser |
| [markdownify](https://pypi.org/project/markdownify/) | HTML-to-Markdown conversion |
| [trafilatura](https://pypi.org/project/trafilatura/) | Content extraction fallback |
| [pyyaml](https://pypi.org/project/PyYAML/) | OpenAPI YAML spec parsing |
| [httpx](https://pypi.org/project/httpx/) | Async HTTP for asset downloads |

## Licence

MIT. See [LICENSE](LICENSE).
