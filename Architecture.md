# Architecture

This page explains how ScrapePy is put together, for people changing the code. What a run writes is specified in [docs/MARKDOWN-OUTPUT.md](docs/MARKDOWN-OUTPUT.md), and the options are in [README.md](README.md).

## Overview

```
main.py / python -m scrapepy / scrapepy
   |
cli.py            argparse -> ScrapeConfig, validation, exit code
   |
interrupts.py     run_async(): asyncio.run() with SIGTERM handled like Ctrl+C
   |
scraper.py        picks the mode
   |
   +-- modes/crawl.py        default crawl and -j (the main path)
   +-- modes/feed.py         an RSS or Atom feed URL, or --feed
   +-- modes/single_page.py  -1
   +-- modes/swagger.py      --swagger
   +-- modes/docs.py         -d
   +-- modes/instagram.py    --instagram
   +-- modes/audit.py        --audit

engine/       fetcher.py (HTTP, browser and asset fetching), threads.py,
              browser_actions.py and instagram_actions.py (page JavaScript)
processing/   robots, discovery, feeds, navigation, outline, classifier,
              dates, urls, errors, assets, rewriter
output/       writer (paths and file I/O), naming (which file each URL gets),
              cache, run_summary, broken_links
convert/      to_markdown, html_prep, recipe, openapi_to_markdown,
              instagram_to_markdown
```

`scraper.py` checks the flags in this order: `--audit`, `--instagram`, then for everything that is not `-1`, `--swagger` or `-d` it runs feed mode when `--feed` is given or the URL looks like a feed (and `--no-markdown` and `-j` are not set), falling back to the crawl when the response is not a feed. The browser modes check the start URL against robots.txt first.

## Default crawl and `-j` (`modes/crawl.py`)

1. `run_summary.json` is replaced with a `running` placeholder.
2. robots.txt is fetched in a worker thread and becomes a `RobotsPolicy` (`processing/robots.py`): RFC 9309 longest match, `*` and `$` through the linear-time `glob_match()`, product-token groups, `Crawl-delay`, up to five redirects. One `HostRateLimiter` paces every later request to the site: pages at least `max(--wait, Crawl-delay)` apart, reconnaissance at `min(--wait, 1s)` or `Crawl-delay`.
3. Reconnaissance (`processing/discovery.py`): `/sitemap.xml` and the homepage together, sub-sitemaps (up to 20 per level, two levels) and robots.txt `Sitemap:` lines, then feeds named by the homepage's `<link rel="alternate">` or, when there are none, seven common feed paths (at most ten feeds). Sitemap `<lastmod>` and feed dates fill a `DiscoveryMap` (URL path to date).
4. Navigation (`processing/navigation.py`) is read from the homepage in priority order: `<header>`, `<nav>`, elements with navigation classes, `<footer>`, following `<ul>`/`<li>` nesting and dropdown or mega-menu containers. `processing/outline.py` builds a `SiteOutline` of `NavSection` trees, assigns sitemap URLs to sections by longest prefix, saves `site_outline.json` and seeds the queue.
5. The BFS loop takes URLs from the current section batch, then queued section batches, then the regular queue. For each URL it checks scope (`-s`, `-w`), robots.txt and the date filters, fetches it (prefetched tasks overlap waiting, never requests), and handles:
   - 304 Not Modified or HTML identical to the cache: unchanged, cached links replayed;
   - HTTP errors and error pages: failed, listed in `broken_links.txt`;
   - a Content-Type that is not HTML: skipped and listed under `skipped_non_html`;
   - a Cloudflare challenge page: a 5 second pause, then the page is processed as fetched.
6. Links are extracted with regular expressions after `<link rel="alternate">` tags with a non-HTML type are removed; URLs ending in data-format extensions (`.json`, `.xml`, `.rss`, `.atom`) and file types such as images and PDFs are never queued, and query strings and trailing slashes are dropped. With `--recent` or `--since`, section pages split their links into a DOM-ordered article batch and other links, and an article older than the cutoff ends its batch.
7. Assets are downloaded with `httpx` (not in `--markdown-only`), URLs are rewritten for offline viewing (`processing/rewriter.py`), and the page is converted with `HTMLToMarkdown.prepare()`.
8. Each page is placed by `output/naming.py`: it gets its natural file name unless another URL already holds that name, in this run, in the stored assignments in the cache, or as a Markdown file on disk whose frontmatter `url` names another URL; then it gets a `-<8 hex>` suffix. A page whose Markdown body equals a file already final in this run is recorded as a duplicate instead of written.
9. After the loop: generated section `_index.md` listings (with date filters, unless `--no-index-pages`), section images copied, the cache saved with the file assignments, `broken_links.txt` written, and `run_summary.json` written atomically. Its `exit_code` becomes the process exit code.

Ctrl+C or SIGTERM cancels the main task; the loop catches the cancellation, saves the cache and writes the summary with status `interrupted` (exit 130 or 143).

`-j` uses the same loop with `StealthyFetcher` for each page (network idle, a wait of one second, no scrolling) and the same regular-expression link extraction.

## Feed mode (`modes/feed.py`)

robots.txt is checked for the feed URL, the feed is fetched once after the pacing gap, and `processing/feeds.py` parses it: the DOCTYPE and entity declarations are removed first, HTML entities become numeric references, then BeautifulSoup's XML parser reads RSS 2.0 items or Atom entries. Each entry up to `--max-articles` (default 50) becomes one Markdown file through the converter's own pieces (`markdownify`, `clean_markdown`, `normalize_headings`, `build_frontmatter`). File names come from the entry links, with `output/naming.py` keeping each URL's existing file. Entry links are never fetched.

## Browser modes (`-1`, `--swagger`, `-d`)

Each mode passes a `page_action` callback to `StealthyFetcher.async_fetch()` (`network_idle=True`, `solve_cloudflare=True`, `timeout=120000`, `wait=int(...)`). `-1` scrolls the page and saves `index.html`. `--swagger` expands the operation blocks, looks for the spec URL and saves it as `openapi-spec.json` or `.yaml`. `-d` follows the sidebar links, checks each against robots.txt, and saves each page plus `combined.html`. Markdown conversion then runs over the saved HTML (`HTMLToMarkdown.convert_all()`), or `convert_openapi_file()` when a spec was saved.

## Instagram mode (`modes/instagram.py`)

A browser with a persistent profile (`~/.scrapepy/instagram_profile/`) loads each profile, optionally waits up to three minutes for a manual login (`--ig-login`), scrolls the post grid, then visits each post with a pause of 2 to 5 seconds derived from a hash of the post URL, downloading its media and writing `post.json`, `post.html` and, with Markdown on, `post.md`. Accounts are 5 seconds apart. robots.txt is not checked.

## Audit mode (`modes/audit.py`)

Runs the reconnaissance steps, fetches the homepage, up to three section pages and up to three recent articles, and writes `audit_report.json`, `test_config.json`, `pages/*.html` and `meta/robots.txt` under `tests/fixtures/<site>/`.

## Markdown conversion (`convert/`)

`HTMLToMarkdown.prepare()` turns one page into its Markdown file contents:

1. Feeds, manifests, error pages and WordPress boilerplate paths are skipped.
2. Links are made absolute in `--markdown-only`, block links are unwrapped and the canonical URL is read (`html_prep.py`).
3. schema.org Recipe data, when present, is read by `recipe.py`, and the lines it repeats are removed from the HTML.
4. Content is extracted by the first extractor that returns more than 100 characters: API documentation layouts, Swagger UI, RoboHelp and MadCap, the standard CMS containers, trafilatura, then a generic fallback.
5. The Markdown is cleaned, the title H1 is chosen and headings are normalised (one H1, no skipped levels), the recipe block is added, links are converted, and pages under 50 words (recipes excepted) are dropped.
6. The frontmatter is built by `build_frontmatter()` with `yaml.safe_dump`, in `FRONTMATTER_FIELDS` order.

`commit()` writes a prepared page; the crawl decides the file name first (see step 8 above).

## Fetching (`engine/fetcher.py`)

| Function | Backend | Used by |
|----------|---------|---------|
| `fetch_page_http()` | Scrapling `Fetcher.get()` (curl_cffi, browser-like TLS and headers) | the crawl, reconnaissance, robots.txt, feed mode, audit |
| `fetch_page_browser()` | Scrapling `StealthyFetcher.async_fetch()` (Patchright) | `-j`, `-1`, `--swagger`, `-d`, `--instagram` |
| `create_asset_client()`, `download_asset()` | `httpx.AsyncClient` | asset downloads |

Scrapling's fetchers are classmethods: call them on the class. `--proxy` is passed to the browser and to the `httpx` asset client; `fetch_page_http()` does not pass it, so HTTP page requests do not use the proxy. Blocking fetches run in daemon threads through `engine/threads.run_blocking()`, so an interrupted run never waits for a hung request.

## Incremental cache (`output/cache.py`)

`.scrapepy_cache.json` holds, per page, the ETag, Last-Modified, a SHA-256 of the HTML, its links, the published date and the page type; per asset, the ETag and Last-Modified; and the file assignments of every URL. Later runs send `If-None-Match` and `If-Modified-Since`, treat a 304 or an identical HTML hash as unchanged, and reuse the stored assignments. Instagram mode does not use this cache: it skips posts whose folder already holds a `post.json`.

## Tests

`python -m pytest tests/` runs the suite with no internet access and no browser. Most modules have a test file of their own (`test_<module>.py`). `test_crawl_run.py` drives the crawl loop against in-memory pages, the `*_localhost.py`, recipe and feed tests crawl a server bound to 127.0.0.1, and `test_audit_fixtures.py` checks navigation, classification, dates and robots.txt against the committed fixtures in `tests/fixtures/`.
