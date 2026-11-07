# ScrapePy

Python web scraper using [Scrapling](https://github.com/D4Vinci/Scrapling) + [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) for stealth browser automation and anti-detection.

ScrapePy crawls websites, downloads assets, rewrites URLs for offline viewing, and converts everything to Markdown with YAML frontmatter. It handles Cloudflare challenges, JavaScript-rendered SPAs, Swagger/OpenAPI docs, documentation platforms, Instagram profiles, and news sites with date filtering out of the box.

It also works as a building block for search indexing: `--markdown-only` writes one structured Markdown file per page with YAML frontmatter that always loads with `yaml.safe_load`, each crawl writes a machine-readable `run_summary.json` listing the new and changed files, and the exit code tells a script whether the run worked. [docs/MARKDOWN-OUTPUT.md](https://github.com/tomgould/ScrapePy/blob/master/docs/MARKDOWN-OUTPUT.md) is the full output contract for tools that read it.

## Responsible use

You are responsible for how you use ScrapePy. Follow the terms of service of the sites you access and comply with the law that applies to you, including copyright and data protection rules. Only scrape content you have the right to access and keep. The authors accept no liability for how the software is used.

What ScrapePy does to keep a crawl polite:

- robots.txt is optional, and ScrapePy does not apply it unless you pass `--obey-robots`. By default no URL is skipped because robots.txt disallows it, no `Crawl-delay` is waited out, and a robots.txt that cannot be read stops nothing. It is still requested once, only to read its `Sitemap:` lines, by the default crawl, `-j` and `--audit` when reconnaissance runs (`--audit` also saves it as a fixture). No other mode requests it, and neither does a targeted crawl that skips reconnaissance (see Targeted crawls). `run_summary.json` records the choice as `options.obey_robots`.
- `--obey-robots` applies robots.txt as described below. `--ignore-robots`, which used to switch it off, is accepted and does nothing, so existing scripts keep working. Passing both flags is an error and exits 2.
- Pacing is not part of robots.txt and always applies. Page requests to a site start at least `--wait` seconds apart (3 seconds by default, 5 in browser modes). Prefetching does not get around the gap: it overlaps waiting, not requests. Reconnaissance and page requests share one limiter, so the gap also holds between the last reconnaissance request and the first page.
- At most 4 requests run at once by default (`-c` changes this).
- Reconnaissance requests (sitemaps, feeds, homepage) are spaced by one second or `--wait`, whichever is shorter.

With `--obey-robots`:

- robots.txt is read at the start of every run (except in Instagram mode, see below) and its rules are applied to the URLs a mode picks to fetch. Blocked URLs are skipped, counted in the run summary and listed in `run_summary.json`.
- Rules follow [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309): the longest matching `Allow` or `Disallow` pattern wins whatever order the lines are in, and `Allow` wins a tie. `*` matches any run of characters and a trailing `$` anchors the end of the URL, so `Disallow: /*.pdf$` blocks PDFs and `Disallow: /*?sort=` blocks sorted listings. Links found while crawling never carry query strings (they are stripped before queueing), so rules about query strings only affect sitemap and feed URLs. Paths are case-sensitive and percent-encoding is normalised. An empty `Disallow:` allows everything, and a UTF-8 byte order mark at the start of the file is ignored. Path parameters (`;`) are matched as ordinary characters. Matching uses no regular expressions and its cost is bounded: `Allow` and `Disallow` patterns longer than 2,000 characters are ignored, and when the site has rules for you, a URL whose path and query come to more than 8,192 characters, measured after percent-encoding (so each non-ASCII byte counts as three), is not fetched and is counted as blocked by robots.txt.
- The group is chosen by your `--user-agent` product token (`MyBot` in `MyBot/1.0`): a group whose `User-agent` line names the same token, compared case-insensitively and ignoring any version suffix, replaces the `User-agent: *` group. `User-agent: M` does not match `MyBot`. Groups naming the same token are merged. Without `--user-agent`, the `*` group applies. `Crawl-delay` is read from the chosen group only.
- A robots.txt that returns 404 or another 4xx means there are no restrictions. One that returns a 5xx error, or a server that cannot be reached or does not answer within 10 seconds (one attempt, no retries), means the whole site is treated as disallowed: ScrapePy prints why and fetches nothing.
- Up to five redirects are followed when fetching robots.txt, to any host, and the file they lead to applies to the site being crawled. Only `http` and `https` redirect targets are followed. More than five redirects, a redirect loop, a redirect without a `Location` header or a redirect to any other scheme (such as `file:` or `ftp:`) count as unreachable, so the site is treated as disallowed.
- A robots.txt `Crawl-delay` raises the gap between page requests and is never lowered by `--wait`. Reconnaissance requests are never spaced by less than `Crawl-delay` either.

Which modes use robots.txt:

| Mode | Default | With `--obey-robots` |
|------|---------|----------------------|
| Default crawl, `-j` | Requested once for its `Sitemap:` lines, only when reconnaissance runs. No rule or `Crawl-delay` applied | Every page URL, including prefetched ones, and the sitemap, feed and homepage requests made during reconnaissance |
| `-d` docs | Not requested | The start URL and every sidebar link |
| `-1` single page, `--swagger` | Not requested | The start URL |
| `--feed` (or a feed URL) | Not requested | The feed URL, the only page request |
| `--audit` | Requested for its `Sitemap:` lines and saved to `meta/robots.txt`. No rule or `Crawl-delay` applied. Audit writes into `tests/fixtures/` and ignores `-o` | Reconnaissance requests and the sample pages |
| `--instagram` | Not requested | Not checked. Instagram's robots.txt does not allow general crawling, so read its terms before using this mode |

Asset downloads (images, CSS, JS) linked from an allowed page are not checked against robots.txt and are not paced, though they share the `-c` concurrency limit.

Only crawl sites you own or have permission to crawl when you leave robots.txt off.

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

Requires Python 3.11 or later; the tests run on 3.11, 3.12 and 3.13. `pip install -r requirements.txt` installs the dependencies without the package, which is enough to run `python main.py`.

## Testing

```bash
# Run all tests (no internet access needed, a minute or two)
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_navigation.py -v
```

Tests cover date extraction, error detection, URL normalization, page classification, URL rewriting, config and CLI parsing, exit codes, robots.txt rules and request pacing, cache operations, sitemap/RSS/Atom parsing, navigation parsing (including mega-menu/dropdown), outline building, article detection, section splitting, path mapping, YAML frontmatter, section index generation, the run summary, and fixture-driven validation. Some tests run the whole crawl loop against in-memory pages, and the `*_localhost.py`, recipe and feed tests crawl a test server bound to 127.0.0.1 (two of them send a real Ctrl+C). None of them reach the internet, and none starts a browser unless `SCRAPEPY_BROWSER_TESTS=1` is set.

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
| `-j`, `--js-render` | Fetch each page with the stealth browser, then find links by regular expression as in the default crawl. The page is not scrolled and `--wait` is only the gap between requests |
| `-1`, `--single-page` | Fetch one page with the stealth browser, let it render and scroll it, then save it like a crawled page (no crawling). Combines with `--site-name`, not with the other targeted-crawl options or `--markdown-only` |
| `--swagger`, `--openapi` | Swagger/OpenAPI documentation mode |
| `-d`, `--docs` | Documentation site mode |
| `-a`, `--include-assets` / `--no-include-assets` | Include CSS/JS/font files (default: on, images always included) |
| `-i`, `--incremental` / `--no-incremental` | Only re-download pages/assets modified since last crawl (default: on) |
| `--wait SECONDS` | Minimum gap between page requests to the site (default: 3s HTTP, 5s browser). With `--obey-robots`, a robots.txt `Crawl-delay` raises it |
| `-c`, `--concurrent N` | Max concurrent requests for assets and page prefetching (default: 4) |
| `--user-agent STRING` | User-Agent header for all requests, also used to match robots.txt groups with `--obey-robots` (default: a browser-like user agent) |
| `--obey-robots` | Apply robots.txt: skip URLs it disallows and wait out its `Crawl-delay`. Without it neither is applied (robots.txt is optional) |
| `--ignore-robots` | Accepted and ignored, because not applying robots.txt is the default. Combined with `--obey-robots` it exits 2 |
| `-o`, `--output-dir DIR` | Folder under which `ScrapedSites/` and `MarkdownSites/` are created (default: current directory) |
| `--markdown-only` | Write only Markdown: no HTML mirror, no image or asset downloads, links and images stay absolute URLs. Default crawl and `-j` only |
| `--include REGEX` | Follow a discovered link only when its path matches (repeatable). With `-s`, links inside the starting path are followed too. Start URLs are always fetched. See Targeted crawls for how patterns are matched |
| `--exclude REGEX` | Never follow a link whose path matches (repeatable, matched like `--include`) |
| `--max-depth N` | Follow links at most N steps from the start URLs (start = 0; default: no limit) |
| `--max-pages N` | Stop after N pages fetched (default: 0 = no limit); `run_summary.json` records `stopped_by_limit`. Each page counts once however many times a failing request is retried, and robots.txt, reconnaissance and asset requests are not counted |
| `--urls-file FILE` | Start URLs, one per line (`#` comments, http or https, one site and one scheme), all at depth 0 in one run; the positional URL is optional |
| `--only-recipes` | Write only pages with schema.org Recipe data; other pages are fetched to find links but not written |
| `--site-name NAME` | Output folder name for the site (instead of the host and `-s` path), so several runs share one folder. Works with the default crawl, `-j` and `-1` |
| `--follow-next` | Follow pagination (`rel="next"` and `/page/N` links) of in-scope listings without counting it as depth. Only `--max-pages` bounds it (pagination does not count as depth): without it, it continues as long as the site offers another page |
| `--index-pages` / `--no-index-pages` | Write the generated `_index.md` section listing pages (default: on) |
| `--feed` | Read the URL as an RSS or Atom feed: one Markdown file per entry from one request. Automatic for URLs such as `/feed` or `/rss.xml`. `--max-articles` caps the entries (default 50) |
| `--auth USER:PASS` | HTTP Basic Auth credentials |
| `--proxy URL` | Proxy for every request: pages, reconnaissance, robots.txt, feeds, browser modes and asset downloads. Schemes `http`, `https`, `socks4`, `socks4a`, `socks5`, `socks5h`; a password in it is never printed or written |
| `--headless` | Run browser headless (default: visible) |
| `-v`, `--verbose` | Verbose logging |
| `--recent DURATION` | Only keep articles from last N period: `2w`, `1d`, `6h` |
| `--since DATE` | Only keep articles published after date: `YYYY-MM-DD` |
| `--min-articles N` | Keep at least N articles per section even if old (use with `--recent`/`--since`) |
| `--max-articles N` | Max articles per section (newest N, default: 0 = no limit). In feed mode, max entries (default 50) |
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

# Markdown for a search index: no HTML mirror or images, no generated listing pages
python main.py https://www.example.com --markdown-only --no-index-pages -o /data/crawls

# Crawl without Markdown conversion
python main.py https://www.example.com --no-markdown

# Stay within a specific path
python main.py https://docs.example.com/api/v2/ -s

# JavaScript-rendered site, page requests at least 10 seconds apart
python main.py https://spa-site.com -j --wait 10

# Single page application
python main.py https://api-reference.example.com -1

# Swagger/OpenAPI documentation
python main.py https://petstore.swagger.io --swagger

# Documentation site (Readme.io, Stoplight, etc.)
python main.py https://developers.example.com -d

# Send every request through a proxy (a password in the URL is never printed)
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

Output goes under the current directory, or under `--output-dir` when given. [docs/MARKDOWN-OUTPUT.md](https://github.com/tomgould/ScrapePy/blob/master/docs/MARKDOWN-OUTPUT.md) describes the Markdown, its frontmatter and the summary file exactly. With `--markdown-only` the `ScrapedSites/<site>/` folder holds only the run state (`run_summary.json`, `.scrapepy_cache.json` unless `--no-incremental`, and when there is something to record `site_outline.json` and `broken_links.txt`) and nothing is downloaded but pages. Otherwise scraped sites are saved to `ScrapedSites/` with this structure:

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
  _index.md
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
url: https://example.com/news/some-article
date: '2026-02-20'
date_source: META_TAG
page_type: article
word_count: 1247
description: A short description of the article...
domain: example.com
scraped_at: '2026-11-05T12:00:00+00:00'
content_hash: sha256:3f5a...
---

# Example article title
...content...
```

The frontmatter is written with `yaml.safe_dump`, so `yaml.safe_load` gives back exactly the strings and integers that were written, whatever the page text contains. `url` is always the address the page was fetched from, so it is unique per file; a same-site `<link rel="canonical">` that differs from it is recorded in `canonical_url`. Fields are populated automatically: in crawl mode from live metadata (URL, date, page type), in batch mode by extracting from the HTML. Only non-null fields are written, so a missing `date` means no date was found. Keys are always lowercase, and generated `_index.md` pages use the same keys (with `page_type: section`, plus `articles` and `sub_sections` counts). Date sources are ranked by confidence: `META_TAG` > `JSON_LD` > `URL_PATTERN` > `TIME_ELEMENT` > `HTTP_HEADER`.

`scraped_at` is when the file was written, in UTC. `content_hash` is the sha256 of the Markdown body, meaning everything after the blank line that follows the closing `---`. It stays the same when the page content does, so an indexer can skip files whose hash it has already seen.

### Run summary (`run_summary.json`)

The default crawl, `-j`, `-1` and feed mode write `run_summary.json` into the site's `ScrapedSites/<site>/` folder and print its path at the end (`-1` writes none when robots.txt blocks its URL, which is checked before the run starts). `--swagger`, `-d`, `--audit` and `--instagram` do not write one. Paths in it are relative to the output directory (`--output-dir`, or the current directory).

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
  "options": { "concurrent": 4, "wait_seconds": 3.0, "obey_robots": false, "ignore_robots": true, "...": "..." },
  "counts": {
    "fetched": 12, "new": 10, "changed": 0, "unchanged": 0,
    "failed": 2, "blocked_by_robots": 0, "duplicates": 0, "renamed": 0, "skipped_non_html": 0, "skipped_old": 0
  },
  "new_markdown": ["MarkdownSites/quotes_toscrape_com/_index.md"],
  "changed_markdown": [],
  "unchanged_urls": [],
  "failed": [{ "url": "https://quotes.toscrape.com/missing", "reason": "HTTP 404" }],
  "blocked_by_robots": [],
  "duplicates": [],
  "renamed": [],
  "skipped_non_html": [],
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
| `options` | The settings that shaped the run. Credentials are never written: `auth_used` and `proxy_used` only say whether they were set. `obey_robots` is `true` only with `--obey-robots`; `ignore_robots` is its opposite, kept for readers written before the default changed, and means robots.txt rules and `Crawl-delay` were not applied |
| `counts.fetched` | Page requests that completed, with a response or a network error, including ones answered 304 Not Modified. A request still waiting for its turn when the run is interrupted is not counted |
| `counts.new`, `counts.changed` | Pages saved this run. A page is changed when an earlier run saved it (it is in the incremental cache or its HTML file already exists). A page whose Markdown body matches a file already written or unchanged in the run is a duplicate and gets no file |
| `counts.unchanged` | Pages skipped because the server said 304 or the HTML matched the cache. Their URLs are in `unchanged_urls` |
| `counts.failed` | Fetch errors, HTTP 4xx/5xx and error pages. `failed` lists each URL with a reason |
| `counts.blocked_by_robots` | URLs skipped because robots.txt disallows them, listed in `blocked_by_robots`. Always 0 without `--obey-robots` |
| `counts.duplicates` | Pages whose Markdown was identical to a file already written in this run (such as `/` and `/index.html`). They are not written again; `duplicates` lists each `url` with the file it matched as `same_as` |
| `counts.renamed` | Pages written to a hash-suffixed file name (`about-1f3a9c0e.md`) because another URL already has their natural name. A page never replaces another URL's file; the assignment is stored in the cache, so each URL keeps its file in later runs. `renamed` lists each `url` with its `file` and `natural_file`. Use the frontmatter `url` as the key, not the file name |
| `counts.skipped_non_html` | Responses fetched but not converted because their Content-Type is not HTML; `skipped_non_html` lists each `url` with its `content_type` |
| `counts.skipped_old` | Articles dropped by `--recent`/`--since` after fetching |
| `new_markdown`, `changed_markdown` | Markdown files written for new and changed pages. A page too short to convert is counted but has no file here. Generated `_index.md` listing pages are not included |
| `exit_code` | The exit code the run finished with |
| `feed` | Feed mode only: the feed's format and title and its written, unchanged, over-cap, skipped and teaser entries. See [docs/MARKDOWN-OUTPUT.md](https://github.com/tomgould/ScrapePy/blob/master/docs/MARKDOWN-OUTPUT.md) |

New, changed and unchanged are decided on the page HTML, so a page whose HTML changed but whose Markdown did not still counts as changed. Compare `content_hash` if you need the stricter test.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success: at least one page was fetched without failing (unchanged pages count) |
| 1 | Nothing was fetched (for example, with `--obey-robots`, robots.txt blocked the start URL) or every fetch failed |
| 2 | Bad arguments, such as an invalid `--since` date or `--obey-robots` together with `--ignore-robots` |
| 130 | Interrupted with Ctrl+C (SIGINT) |
| 143 | Stopped with SIGTERM |

Feed mode exits 1 when the feed cannot be fetched (with `--obey-robots`, also when robots.txt blocks it), when `--feed` is given and the response is not a feed, or when the feed has entries and none can be read or written (`feed.error` says why). `-1` exits 0 when its page is fetched and not recorded as failed: saved, unchanged since the last run, a duplicate of a file already in the folder, or too short to convert. It exits 1 when the browser fails, the server answers 400 or above, the page is an error page or its conversion fails, and with `--obey-robots` also when robots.txt blocks the URL (no `run_summary.json` is written then). `--swagger` and `-d` exit 1 when they save no page (with `--obey-robots`, also when robots.txt blocks the start URL). Audit and Instagram modes exit 0 unless the arguments are bad.

### Numeric Path Flattening

In the HTML copy, URLs with numeric path segments are flattened to avoid deeply nested single-file directories; in `MarkdownSites/` this applies to article pages:

```
/news/12345/1/slug  →  news/slug-12345-1.html
/news/1005473      →  news/1005473.html (number is the filename, no slug)
/a/1/2/3/4/slug     →  a/slug-1-2-3-4.html
```

Section pages are not flattened in `MarkdownSites/`: every path segment becomes a folder (`/news/123/slug`, as a section, is `news/123/slug/_index.md`).

## Modes

### Default Crawl

Recursively crawls all pages on the same domain using HTTP requests with TLS fingerprint spoofing. Downloads images, rewrites internal URLs to relative paths for offline viewing, and detects error pages (404, 403, etc.).

Before crawling begins, a lightweight reconnaissance step runs automatically:
1. Fetches `robots.txt` for its sitemap URLs, and with `--obey-robots` also for its access rules and `Crawl-delay`
2. Fetches sitemaps and RSS/Atom feeds to build a dated URL map
3. Parses the homepage `<header>`/`<nav>`/`<footer>` to extract navigation structure (including mega-menus and dropdown panels)
4. Saves a `site_outline.json` with the site's section hierarchy (including sub-sections from dropdowns) and sitemap stats
5. Seeds the crawl queue from the navigation structure (instead of just `/`)

This ensures the scraper understands the site's organizational structure before scraping content.

### Single Page (`-1`)

For single-page applications and for one page at a time. Launches a stealth browser, waits `--wait` seconds for JavaScript to render, scrolls the entire page to trigger lazy loading, then saves the rendered page exactly as a crawl saves a page: the HTML copy and assets, the Markdown file, the incremental cache and `run_summary.json`. No links are followed.

The browser requests the URL exactly as given, trailing slash, query and fragment included. The page is recorded under the address the browser ended on: after a redirect to another page on the same host, that address is the frontmatter `url` and names the file (a redirect to another host keeps the requested address). The query string stays in `url`, and the file is named from the path, so `/search?q=apples` is `search.md` and a second query on the same path gets the hash-suffixed name described in [docs/MARKDOWN-OUTPUT.md](https://github.com/tomgould/ScrapePy/blob/master/docs/MARKDOWN-OUTPUT.md). Each page keeps its own file, so several `-1` runs on one host fill one site folder, and running the same page again updates its file or reports it unchanged. `canonical_url` is whatever the page declares.

`-1` writes only the site's root page to the root's own file. A site folder holding a `MarkdownSites/<site>/index/_index.md` whose `url` is the root but whose text is another page, with a `ScrapedSites/<site>/index.html` beside it, keeps those files: ScrapePy never writes another page into them, but the Markdown file claims the root address, so delete both by hand.

### JS Render (`-j`)

Like the default crawl, but each page is fetched with the stealth browser, which returns the HTML once the page has loaded and Scrapling's Cloudflare check has finished. The page is not scrolled, links are extracted from the HTML with regular expressions as in the default crawl, and `--wait` is only the gap between page requests.

### Swagger (`--swagger`)

Loads Swagger UI in a stealth browser, expands all endpoint sections and model schemas, attempts to locate and download the raw OpenAPI spec file, then saves everything. With `-m`, converts the spec directly to Markdown for clean documentation.

### Docs (`-d`)

For documentation platforms (Readme.io, Stoplight, GitBook, etc.). Finds sidebar navigation links, visits each page, expands collapsed sections, and saves individual pages plus a combined document (`combined.html`).

### Recipes

Pages with schema.org Recipe data (JSON-LD or microdata) need no flag. Their Markdown gets fixed `## Recipe details`, `## Ingredients`, `## Method` and `## Nutrition` sections, with ingredients and nutrition values exactly as published and missing nutrition marked "not stated", followed by the rest of the page under `## Page text`. The frontmatter says `page_type: recipe` and adds `servings`, `total_time`, `recipe_category`, `recipe_cuisine` and `keywords` when the page gives them. The exact shape is in [docs/MARKDOWN-OUTPUT.md](https://github.com/tomgould/ScrapePy/blob/master/docs/MARKDOWN-OUTPUT.md).

```bash
# One recipe page
python main.py https://recipes.example.com/recipes/oat-bites --markdown-only -s

# A recipe section of a site
python main.py https://recipes.example.com/recipes/ --markdown-only --no-index-pages -s
```

### Targeted crawls

Without targeting options a crawl follows every in-scope link from the start page and the site's navigation. With `--include`, `--max-depth` or `--urls-file`, only the start URLs seed the queue, and without `--recent` or `--since` no sitemap, feed or homepage requests are made: the run requests the pages and, outside `--markdown-only`, their assets, plus robots.txt with `--obey-robots`. Otherwise reconnaissance makes its usual requests (robots.txt, sitemaps, up to seven feed paths and the homepage) before the pages, and `--max-pages` does not count them. A discovered link is followed when it is on the same site, inside the `-s` scope (when `-s` is given) or matching an `--include`, matching no `--exclude`, and within `--max-depth`.

`--include` and `--exclude` are Python regular expressions searched in the link's path: no query string, no fragment and no trailing slash (`/recipes/r3`, so `^/recipes/r3/$` never matches), case-sensitive, with percent-encoding as the site wrote it. They are your own patterns and Python's regular expressions have no time limit, so a pathological pattern (such as `(a+)+$`) can stall a run; only the first 2,048 characters of a path are matched. This is unlike the robots.txt matcher, whose cost is bounded.

With `--follow-next`, a pagination link is followed as pagination, at the page's depth, when it matches no `--exclude` and, with `-s`, is inside the scope or matches an `--include`; without `-s`, when it matches an `--include`, is the page's `rel="next"` or is a `/page/N` page of the same listing (`/category/baby/page/2` from `/category/baby`). Any other pagination link is treated as an ordinary link. A run answered 304 reads the `rel="next"` links from the cache, so it follows the same pages as the first run; a cached page whose entry records no `rel="next"` links (a cache written without `--follow-next` support) is fetched in full once so they are recorded.

```bash
# Recipes from one category listing and its later pages, into one folder per site
python main.py https://recipes.example.com/category/baby-led-weaning/ \
  --include '^/recipes/' --only-recipes --max-depth 1 --follow-next \
  --max-pages 200 --site-name recipes_example_com --markdown-only

# The results of a site search (WordPress style), into the same folder
python main.py "https://recipes.example.com/?s=baby+led+weaning" \
  --include '^/recipes/' --only-recipes --max-depth 1 \
  --site-name recipes_example_com --markdown-only

# A list of pages to fetch, nothing else
python main.py --urls-file recipe-urls.txt --max-depth 0 --site-name recipes_example_com
```

A start URL with a query string, such as a site search, is fetched exactly as given when any targeting option is set, and the links on it are followed; the page itself is not written or cached, because its query cannot be part of a file name. Without a targeting option the query is dropped and the crawl starts from the path, as in every crawl. Query strings on discovered links are dropped, as in every crawl, so pagination such as `?page=2` is not followed; `/page/2/` style pagination is. `run_summary.json` gets a `targeting` object with the skipped counts (see [docs/MARKDOWN-OUTPUT.md](https://github.com/tomgould/ScrapePy/blob/master/docs/MARKDOWN-OUTPUT.md)).

### Feeds (`--feed`)

An RSS 2.0 or Atom feed is fetched once (with `--obey-robots`, after checking robots.txt), and each entry becomes its own Markdown file (`page_type: feed_entry`) with the body from the feed. Entry links are not followed. `run_summary.json` gets a `feed` object; `teaser_urls` lists entries whose feed body is only a short excerpt, so you can crawl those pages yourself.

```bash
# Detected from the URL
python main.py https://blog.example.com/feed/ -o /data/crawls

# Any feed URL, newest 20 entries
python main.py "https://blog.example.com/?type=latest" --feed --max-articles 20
```

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

## Known limits

- A page too short to convert (under 50 words, recipe pages excepted) is counted as new or changed in `run_summary.json` but has no Markdown file.
- After an interrupted run, pages that were not reached yet appear in no list in `run_summary.json`.
- Crawled paths are requested without a trailing slash (`/docs/` is fetched as `/docs`), so a server that answers only the slash form must redirect.
- With `--stay-in-path`, no `--site-name` and no `--recent` or `--since`, article files have the starting path removed from their path while section `_index.md` files keep it: crawling `/docs/` with `-s`, the article `/docs/how-to-bake-sourdough-bread` is `how-to-bake-sourdough-bread.md`, while `/docs/how-to-bake-bread` and `/docs/kitchen`, which are classified as sections, are `docs/how-to-bake-bread/_index.md` and `docs/kitchen/_index.md`. With `--recent` or `--since`, articles found on a section are written under that section's folder and keep the starting path (`docs/how-to-bake-sourdough-bread.md`).
- A brand name in an H1 with no brand signal (no `og:site_name`, `application-name` or schema.org name, no link to the home page, a host name that differs) can become the page title.
- In recipe pages, the same amount written two ways (`200g` and `200 g`, `½` and `1/2`) leaves a duplicate line in `## Page text`.
- `-j` returns the page once the browser has loaded it and Scrapling's Cloudflare check has finished (a few seconds), so JavaScript-rendered content that appears later can be missed. The page is not scrolled, links are found in the HTML by regular expression, and `--wait` does not lengthen the wait: it is only the gap between page requests.
- `-j`'s argument parsing and settings are covered by the automated tests. Its crawl is covered only by an opt-in test (`SCRAPEPY_BROWSER_TESTS=1`, which starts a browser) that checks it goes through `--proxy`; `-j` with `--stay-in-path` is not tested.
- `-j`, `-1`, `--swagger`, `-d` and `--instagram` need the browser installed (`scrapling install`).
- Pages whose path, relative to the crawl's start, contains `/category/`, `/tag/` or `/author/` are not written, so a category listing used as a start URL with `--site-name` is read for its links only.
- With `--urls-file` and `-s`, the scope is the first start URL's path.
- `--site-name` is meant for one site. Using one name for two hosts overwrites nothing (each URL keeps its own file), but each run treats the other host's pages as changed and rewrites the cache; ScrapePy prints a warning when the folder's last run was on another host.
- A start URL that looks like a feed runs feed mode, where the targeted-crawl options and any other `--urls-file` URLs do not apply; ScrapePy prints a notice.
- `--follow-next` follows only path-based pagination (`/page/2/`) and `rel="next"` links whose path differs; pagination that changes only the query string (`?page=2`) leads back to the same page and stops.

[docs/MARKDOWN-OUTPUT.md](https://github.com/tomgould/ScrapePy/blob/master/docs/MARKDOWN-OUTPUT.md) lists more limits of the Markdown output.

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
| [pyyaml](https://pypi.org/project/PyYAML/) | YAML frontmatter and OpenAPI YAML specs |
| [httpx](https://pypi.org/project/httpx/) | Async HTTP for asset downloads |

## Licence

MIT. See [LICENSE](LICENSE).
