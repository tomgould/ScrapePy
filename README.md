# ScrapePy

Python web scraper using [Scrapling](https://github.com/D4Vinci/Scrapling) + [Patchright](https://github.com/AuroraOSS/patchright) for stealth browser automation and anti-detection.

ScrapePy crawls websites, downloads assets, rewrites URLs for offline viewing, and optionally converts everything to Markdown. It handles Cloudflare challenges, JavaScript-rendered SPAs, Swagger/OpenAPI docs, documentation platforms, Instagram profiles, and news sites with date filtering out of the box.

## Responsible use

You are responsible for how you use ScrapePy. Follow the terms of service of the sites you access, respect their robots.txt and rate limits, and comply with the law that applies to you, including copyright and data protection rules. Only scrape content you have the right to access and keep.

The stealth browser, proxy and Instagram features are here as a technical demonstration of what the underlying tools can do. The authors accept no liability for how the software is used.

## Installation

```bash
# Install Python dependencies
pip install -r requirements.txt

# or install the package (provides the `scrapepy` command)
pip install .

# Install the Patchright browser (Chromium)
scrapling install
```

Requires Python 3.11+.

## Testing

```bash
# Run all tests (no network calls, a few seconds)
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_navigation.py -v
```

Tests cover date extraction, error detection, URL normalization, page classification, URL rewriting, config parsing, cache operations, sitemap/RSS/Atom parsing, navigation parsing (including mega-menu/dropdown), outline building (including hierarchical sub-sections), article detection, section splitting, numeric segment flattening, slug extraction, section-based filepath mapping, YAML frontmatter generation, section index generation, and fixture-driven validation -- all pure logic with no network I/O.

## Usage

```bash
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
| `--wait SECONDS` | Custom wait time per page (default: 5s browser, 3s HTTP) |
| `-c`, `--concurrent N` | Max concurrent requests for assets and page prefetching (default: 10) |
| `--auth USER:PASS` | HTTP Basic Auth credentials |
| `--proxy URL` | Proxy URL for requests |
| `--headless` | Run browser headless (default: visible) |
| `-v`, `--verbose` | Verbose logging |
| `--recent DURATION` | Only keep articles from last N period: `2w`, `1d`, `6h` |
| `--since DATE` | Only keep articles published after date: `YYYY-MM-DD` |
| `--min-articles N` | Keep at least N articles per section even if old (use with `--recent`/`--since`) |
| `--max-articles N` | Max articles per section (newest N, default: 0 = no limit) |
| `--audit` | Reconnaissance only: analyze site structure and save test fixtures (no crawl) |
| `--instagram` | Instagram profile scraping mode |
| `--accounts FILE` | File with Instagram usernames/URLs (one per line) |
| `--ig-login` | Enable Instagram login (visible browser, saves cookies) |
| `--ig-max-posts N` | Max posts to scrape per account (0 = all) |
| `--update-meta` | Re-scrape posts to refresh metadata but skip re-downloading existing media |

### Examples

```bash
# Basic site crawl (markdown, incremental, and assets are ON by default)
python main.py https://www.example.com

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

Scraped sites are saved to `ScrapedSites/` with this structure:

```
ScrapedSites/example_com/
  index.html
  about.html
  products/widgets.html
  site_outline.json        (navigation structure + sitemap stats)
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

When date filtering produces section batches, articles are organized under section directories with generated index files:

```
MarkdownSites/example_com/
  index.md
  sport/
    index.md          (section index with article listings + thumbnails)
    images/           (images referenced by section articles)
    my-article.md
    football/
      index.md        (sub-section index)
      images/
      article-two.md
  news/
    index.md
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
date: 2026-02-20
date_source: META_TAG
page_type: article
word_count: 1247
description: A short description of the article...
domain: example.com
---
# Example article title
...content...
```

Fields are populated automatically: in crawl mode from live metadata (URL, date, page type), in batch mode by extracting from the HTML. Only non-null fields are included. Date sources are ranked by confidence: `META_TAG` > `JSON_LD` > `URL_PATTERN` > `TIME_ELEMENT` > `HTTP_HEADER`.

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
1. Fetches `robots.txt` for sitemap URLs
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
- **Stealth headers**: Realistic User-Agent and header ordering

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
