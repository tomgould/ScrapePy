# Examples

## Basic Crawling

### Crawl an entire site

```bash
python main.py https://www.example.com
```

Crawls all pages on `example.com`, downloads images and all assets (CSS, JS, fonts), rewrites URLs for offline viewing, and converts to Markdown. Reconnaissance runs first: fetches robots.txt, sitemaps, RSS feeds, parses homepage navigation (including mega-menus and dropdown panels), builds `site_outline.json`, and seeds the queue from navigation structure. Incremental caching is on by default. Output:

```
ScrapedSites/example_com/
  index.html
  about.html
  contact.html
  site_outline.json       (navigation structure + sitemap stats)
  run_summary.json        (what this run did)
  .scrapepy_cache.json    (incremental cache)
  images/
    logo.png
    hero.jpg
  css/
    style.css
  js/
    app.js
  fonts/
    roboto.woff2
```

Markdown files are generated alongside (default on):

```
MarkdownSites/example_com/
  _index.md        (the homepage, usually classified as a section)
  about.md
  contact.md
  images/          (copied from ScrapedSites)
  media/           (copied from ScrapedSites)
```

Crawl mode converts pages as they are saved (not in a separate pass). The converter tries its extractors in order: API docs, Swagger UI, RoboHelp, standard CMS containers, trafilatura, then a generic fallback.

### Markdown only, for a search index

```bash
python main.py https://www.example.com --markdown-only --no-index-pages -o /data/crawls
```

Writes only the Markdown and the run state: no HTML copy, no images or assets, and links stay absolute URLs. The output contract is in [docs/MARKDOWN-OUTPUT.md](docs/MARKDOWN-OUTPUT.md).

### A feed

```bash
python main.py https://blog.example.com/feed/ --max-articles 20
```

Reads an RSS or Atom feed with one request and writes one Markdown file per entry (see Feeds in [README.md](README.md)). Recipe pages need no flag: see Recipes in [README.md](README.md).

### Crawl without Markdown

```bash
python main.py https://www.example.com --no-markdown
```

Skips Markdown conversion. Only raw HTML and assets are saved.

### Crawl without assets (images only)

```bash
python main.py https://www.example.com --no-include-assets
```

Downloads images but skips CSS, JS, and font files.

## Scoped Crawling

### Stay within a path

```bash
python main.py https://docs.example.com/api/v2/ -s
```

Only crawls URLs under `/api/v2/`. Pages outside that path are linked back to the live site (full URLs preserved). Output directory reflects the scoped path:

```
ScrapedSites/docs_example_com_api_v2/
  index.html
  endpoints.html
  authentication.html
```

### Wildcard path matching

```bash
python main.py https://docs.example.com/api/v2/ -s -w
```

Crawls any URL that _contains_ `/api/v2/` anywhere in the path (not just URLs that start with it). Useful when content is spread across multiple path hierarchies.

## JavaScript-Rendered Sites

### JS render mode

```bash
python main.py https://spa-site.com -j
```

Fetches each page with a stealth browser (Patchright), which returns the HTML once the page has loaded and Scrapling's Cloudflare check has finished (a few seconds), without scrolling. Links are then extracted from that HTML the same way as in the default crawl.

### Slower request pacing

```bash
python main.py https://slow-spa.com -j --wait 10
```

Starts page requests at least 10 seconds apart instead of the default 5 in browser modes. It does not give each page longer to render.

## Single Page Applications

### Capture an SPA

```bash
python main.py https://api-reference.example.com -1
```

Launches a stealth browser, loads the page, scrolls the entire document to trigger lazy loading (via `scroll_full_page()`), waits for all content to render, and saves the fully rendered HTML as `index.html`.

### SPA without Markdown output

```bash
python main.py https://api-reference.example.com -1 --no-markdown
```

Saves only the rendered HTML without Markdown conversion.

## Swagger/OpenAPI Documentation

### Scrape Swagger UI

```bash
python main.py https://petstore.swagger.io --swagger
```

Loads Swagger UI in a stealth browser, then:
1. Waits for the UI to render
2. Searches for the OpenAPI spec URL (meta tags, scripts, input fields) via `find_spec_url()`
3. Downloads the raw spec file (JSON or YAML) via `page.evaluate()` fetch
4. Expands all endpoint sections via `expand_swagger_sections()`
5. Expands all model/schema sections
6. Expands all show/hide toggles
7. Scrolls the entire page
8. Saves the fully expanded HTML

Output:

```
ScrapedSites/petstore_swagger_io/
  index.html           # Fully expanded Swagger UI
  openapi-spec.json    # Raw OpenAPI spec (if found)
```

### Swagger with Markdown

Markdown conversion is on by default. If the raw spec was downloaded, converts it directly to structured Markdown via `convert_openapi_file()` (better output than HTML conversion). Otherwise falls back to HTML-to-Markdown via `extract_swagger_content()`.

```
MarkdownSites/petstore_swagger_io/
  api-documentation.md
```

The Markdown output includes:
- API title, version, description
- Base URLs and servers
- Authentication schemes
- Endpoints grouped by tag with parameters and response tables
- Schema definitions with property tables
- `$ref` resolution and cross-linking

## Documentation Sites

### Scrape a docs site

```bash
python main.py https://developers.example.com -d
```

Finds sidebar navigation links via `find_nav_links()`, visits every page, expands collapsed sections via `expand_docs_sections()`, and saves:

```
ScrapedSites/developers_example_com/
  index.html              # Main page
  getting-started.html    # Individual pages
  authentication.html
  endpoints.html
  ...
  combined.html           # All pages in one document
```

### Docs with Markdown

Markdown conversion is on by default. The extraction chain handles Readme.io, Stoplight, GitBook, and other documentation platforms with specialized extractors.

## Incremental Crawling

### First crawl (creates cache)

```bash
python main.py https://www.example.com
```

Incremental caching is on by default. The first crawl creates `.scrapepy_cache.json` in the output directory. The `CrawlCache` stores per-page: ETag, Last-Modified, SHA-256 content hash, extracted links, timestamp, published date, and page type. Per-asset: ETag, Last-Modified, timestamp.

### Re-crawl (only changed pages)

```bash
python main.py https://www.example.com
```

On subsequent runs, sends conditional HTTP headers (`If-None-Match`, `If-Modified-Since`). Pages that return 304 Not Modified are skipped (cached links are replayed to maintain queue). Pages that return 200 but have the same SHA-256 content hash are also skipped. Only truly changed pages are re-downloaded and re-saved.

Output shows which pages were skipped:

```
Fetching page: https://www.example.com/
  Unchanged (304): https://www.example.com/
Fetching page: https://www.example.com/about
  Saved: about.html
Fetching page: https://www.example.com/contact
  Unchanged (same content): https://www.example.com/contact

--- Scraping Complete ---
Pages saved: 1
Pages unchanged (skipped): 2
```

### Skip incremental (force re-download)

```bash
python main.py https://docs.example.com --no-incremental
```

Disables incremental caching and re-downloads everything from scratch.

### Incremental with scoped crawl

```bash
python main.py https://docs.example.com/api/v2/ -s
```

Scoped incremental crawl that only re-downloads changed pages within `/api/v2/`. Both incremental and Markdown are on by default.

## Site Reconnaissance and Outline

Every crawl starts with reconnaissance that parses the homepage navigation, fetches sitemaps, and builds a `site_outline.json`.

### View the site outline

```bash
python main.py https://news-site.com/ -v
cat ScrapedSites/news_site_com/site_outline.json | python -m json.tool
```

Sample `site_outline.json`:

```json
{
  "url": "https://news-site.com/",
  "domain": "news-site.com",
  "timestamp": "2026-02-25T12:00:00+00:00",
  "nav_sections": [
    {
      "label": "F1",
      "path": "/f1",
      "children": ["/f1/news", "/f1/results"],
      "sub_sections": [
        {
          "label": "F1 News",
          "path": "/f1/news",
          "children": [],
          "sub_sections": [],
          "source": "header",
          "article_count": 0,
          "recent_count": 0
        }
      ],
      "source": "header",
      "article_count": 367,
      "recent_count": 24
    },
    {
      "label": "MotoGP",
      "path": "/motogp",
      "children": ["/motogp/news", "/motogp/results"],
      "sub_sections": [],
      "source": "header",
      "article_count": 350,
      "recent_count": 20
    }
  ],
  "footer_links": [
    {"label": "About Us", "path": "/about-us"},
    {"label": "Contact", "path": "/contact"}
  ],
  "sitemap_url_count": 14316,
  "rss_feeds": ["https://news-site.com/rss"],
  "robots_sitemaps": ["https://news-site.com/sitemap.xml"],
  "uncategorized_articles": 523
}
```

The outline is built by:
1. Parsing homepage `<header>/<nav>/<footer>` for navigation links (4-tier priority), including mega-menus and dropdown panels
2. Following `<ul>/<li>` nesting for section->children hierarchy; detecting dropdown containers (`.dropdown-menu`, `.submenu`, `.mega-menu`, etc.) for sub-sections
3. Building a hierarchical `NavSection` tree with `sub_sections` for nested dropdowns
4. Mapping sitemap URLs to sections by longest-prefix match (recursive search through sub-sections)
5. Counting articles and recent articles per section
6. `SiteOutline.all_section_paths()` flattens the tree for crawl seeding

### Cap articles per section

```bash
python main.py https://news-site.com/ --recent 1w --max-articles 10 -v
```

Crawls each nav section but limits to the newest 10 articles per section. When combined with `--recent` and a detected site outline, this activates **focused crawl mode**: only known nav section pages create article batches, no links are queued from pages (preventing topic/author/tag page explosion), and the crawl stops early once all sections are done. The `--max-articles` cap is applied at batch creation time (truncates article list) and during processing (stops batch when cap reached):

```
Fetching page: https://news-site.com/f1/news
  [section] capped 45 articles to 10
  [section] queued 10 article links for ordered processing
```

### Min and max articles together

```bash
python main.py https://news-site.com --recent 1w --min-articles 3 --max-articles 10
```

Guarantees at least 3 articles per section (even if older than 1 week) but never more than 10. This gives balanced coverage across all sections. The `--min-articles` quota is tracked per section independently.

## Recent Content Filtering

### Only recent articles

```bash
python main.py https://news-site.com --recent 2w
```

Crawls the site but only saves articles published in the last 2 weeks. Section/navigation pages (categories, tags, archives) are always kept. The scraper uses multi-signal date extraction:

1. Pre-crawl discovery: Sitemaps and RSS feeds build a `DiscoveryMap` of URL→datetime
2. Pre-filter: URLs with old dates in the URL path or discovery map are skipped without fetching
3. Post-fetch classification: Pages classified as SECTION (always keep), ARTICLE (check date), or UNKNOWN (keep conservatively)
4. Section batches: Article links from section pages are processed in DOM order (newest first). When an old article is found, remaining articles in the batch are discarded.

Date signal priority: meta tags (`article:published_time`) > JSON-LD (`datePublished`) > URL patterns (`/2026/02/25/`) > `<time>` elements > HTTP `Last-Modified` header.

Supported duration formats: `6h` (hours), `3d` (days), `2w` (weeks).

### Articles since a specific date

```bash
python main.py https://news-site.com --since 2026-02-01
```

Same as `--recent` but with an absolute date cutoff. Everything published before February 1, 2026 is skipped. Supported format: `YYYY-MM-DD`.

### Daily re-scan

```bash
python main.py https://news-site.com --recent 1d
```

Incremental caching is on by default, so this efficiently combines incremental crawling with date filtering. Only fetches pages that changed (via ETags/content hashes) and only keeps articles from the last day.

### Recent with Markdown

```bash
python main.py https://news-site.com --recent 1w
```

Only recent articles get saved and converted to Markdown (inline, as each page is saved). Markdown is on by default.

### Minimum articles per section

```bash
python main.py https://news-site.com --recent 1w --min-articles 3
```

Keeps at least 3 articles per section even if they're older than the cutoff. Useful for ensuring every topic has representative content. Each section page's batch tracks its own article count independently. Markdown is on by default.

### Recent with asset optimization

```bash
python main.py https://news-site.com --recent 2w
```

When date filtering is active, section/navigation pages use `images_only=True` to skip CSS/JS/font downloads (their images are just article thumbnails). Only article pages get their full assets downloaded. The Markdown output directory only gets `images/` and `media/`, no CSS, JS, or fonts since they aren't referenced in Markdown. All assets and Markdown are on by default.

### Recent with verbose output

```bash
python main.py https://news-site.com --recent 2w -v
```

Verbose mode shows reconnaissance and classification decisions:

```
Running site reconnaissance...
  Found 1 sitemaps in robots.txt
  Discovery: sitemap.xml: 1,234 dated URLs
  Discovery: feed: 20 dated URLs
  Discovery complete: 1,254 total dated URLs
  Found 18 navigation links
  Site outline: 6 sections, 1254 sitemap URLs
  Saved: site_outline.json

Fetching page: https://news-site.com/
  [section] keeping navigation page
  [section] root page: queuing section links only (filtered articles)
  Saved: index.html
Fetching page: https://news-site.com/topics/technology
  [section] keeping navigation page
  [section] queued 15 article links for ordered processing
  Saved: topics/technology.html
Fetching page: https://news-site.com/2026/02/20/new-article
  [article] keeping: published 2026-02-20T10:00:00Z
  Saved: 2026/02/20/new-article.html
Fetching page: https://news-site.com/2024/01/15/old-article
  [article] skipped: published /2024/01/15/ (before cutoff)
  Skipped (pre-crawl: URL date): https://news-site.com/2023/06/01/ancient-article

--- Scraping Complete ---
Pages saved: 45
Site outline: 6 nav sections, 1254 sitemap URLs
Sections kept: 12
Articles kept (recent): 33
Articles skipped (too old): 87
URLs skipped (pre-crawl/section cutoff): 1,156
Min-articles kept (old): 9 across 3 sections
```

### Section-ordered traversal with prefetching

When date filtering is active and section pages are encountered, the crawl uses section-ordered traversal:

1. Section page's links are split: article links (slug-based detection) → ordered batch, navigation links → regular queue
2. Article slugs identified by: >= 15 chars with hyphens, or >= 2 hyphens
3. Article batch processed in DOM order (newest first on most sites)
4. Upcoming articles are prefetched concurrently (up to `-c` connections) using asyncio tasks
5. When an old article is found, the rest of the batch is discarded (and prefetch tasks cancelled)

```bash
python main.py https://news-site.com/ --recent 1w -v
```

The default concurrency is 4 connections.

## Audit Mode

### Audit a site's structure

```bash
python main.py --audit https://news-site.com -v
```

Runs reconnaissance without crawling: fetches robots.txt, sitemaps, RSS feeds, parses homepage navigation, and fetches a few sample pages. Saves everything as test fixtures:

```
tests/fixtures/news_site_com/
  audit_report.json       # Full recon results (not committed)
  test_config.json        # Expected parse results (committable)
  pages/
    homepage.html
    section_f1.html
    section_motogp.html
    article_verstappen-wins-race.html
  meta/
    robots.txt
```

Sample pages are selected:
- Homepage: reused from discovery (no extra fetch)
- Sections: first 3 from outline.nav_sections
- Articles: newest 3 from discovery_map with article-like slugs

Each sample is classified (`classify_page()`) and dated (`get_best_date()`).

### Run fixture tests

```bash
python -m pytest tests/test_audit_fixtures.py -v
```

Tests validate parsing logic against the real HTML:
- Navigation: homepage produces expected section labels and link counts
- Classification: section pages classify as `section`, articles as `article`
- Dates: article pages yield expected publish dates
- Robots: robots.txt contains expected sitemap count

Tests skip automatically if no fixtures exist.

### Workflow: audit then test

```bash
# 1. Generate fixtures for a site
python main.py --audit https://news-site.com -v

# 2. Review and commit test_config.json (expected values only)
cat tests/fixtures/news_site_com/test_config.json | python -m json.tool
git add tests/fixtures/news_site_com/test_config.json

# 3. Run all tests including fixture tests
python -m pytest tests/ -v
```

The HTML fixtures (`pages/`, `meta/`) are gitignored since they contain real site content. The `test_config.json` is safe to commit: it only contains expected values like section labels, page types, and dates.

## Instagram Mode

### Scrape a public profile

```bash
python main.py https://www.instagram.com/natgeo/ --instagram --ig-max-posts 3
```

Launches a stealth browser, scrolls the profile grid (1.5s intervals, 3 stability checks), extracts post links, then visits each post page to collect structured data and download media. Rate limited: a pause of 2 to 5 seconds per post, derived from a hash of the post URL, and 0.5s between media files.

```
ScrapedSites/instagram/natgeo/
  profile.json
  2024-06-15_Cxy123abc/
    post.html
    post.json
    media/
      image_1.jpg
      image_2.jpg
  2024-06-14_Cxy456def/
    post.html
    post.json
    media/
      video_1.mp4
  2024-06-13_Cxy789ghi/
    post.html
    post.json
    media/
      image_1.jpg
```

Post data extraction includes 40+ fields: identifiers, content (caption, alt_text, hashtags, mentions), engagement (likes, comments, views, plays), media URLs, video metadata, audio/music info, location, author, collaboration/coauthors, usertags, and preview comments.

Profile data uses 3-tier extraction: embedded JSON (ScheduledServerJS payloads) > meta tags > DOM.

### Instagram with Markdown

```bash
python main.py https://www.instagram.com/natgeo/ --instagram --ig-max-posts 3
```

Markdown is on by default. Generates `profile.md` and `post.md` in each directory. Post markdown includes engagement stats, media embeds, comments with timestamps. Profile markdown includes stats table (posts/followers/following), bio, and website. Use `--no-markdown` to skip Markdown generation.

### Multiple accounts from file

Create `accounts.txt`:

```text
# Instagram accounts to monitor
https://www.instagram.com/natgeo/
@nasa
spacex
```

```bash
python main.py --instagram --accounts accounts.txt --ig-max-posts 2
```

Scrapes all listed accounts with a 5-second delay between each. Each gets its own subdirectory under `ScrapedSites/instagram/`. Supports URLs, @username, and bare username formats.

### Login for private profiles

```bash
python main.py https://www.instagram.com/someuser/ --instagram --ig-login --ig-max-posts 5
```

First run: the browser opens visibly (not headless), navigates to Instagram's login page, and waits up to 3 minutes for you to log in manually. After login, cookies are saved to `~/.scrapepy/instagram_profile/` via Patchright's `launch_persistent_context`.

Subsequent runs with `--ig-login`: cookies are reused automatically, no login prompt.

### Daily monitoring with --update-meta

```bash
python main.py --instagram --accounts accounts.txt --ig-max-posts 5 --update-meta
```

Re-scrapes every post to get fresh engagement data (likes, views, comments) but skips re-downloading media files (videos, images) that already exist on disk. Ideal for daily monitoring of multiple accounts where you want updated metrics without wasting bandwidth on the same media.

The updated `post.json`, `post.html`, and `post.md` files reflect the latest engagement numbers each run.

### Incremental Instagram scraping

```bash
python main.py https://www.instagram.com/natgeo/ --instagram
```

Incremental is on by default. On subsequent runs, it skips posts that have already been scraped. It checks for existing post directories with a `post.json` file (does not use CrawlCache; Instagram's browser-fetched pages lack HTTP conditional headers). Only new posts are fetched. Combine with `--accounts` for efficient periodic monitoring:

```bash
python main.py --instagram --accounts accounts.txt
```

Note: `--update-meta` takes precedence over incremental: if both are specified, all posts are visited (to update metadata) but existing media is still skipped. Use `--no-incremental` to force re-scraping all posts.

### Scrape all posts (no limit)

```bash
python main.py https://www.instagram.com/natgeo/ --instagram
```

Scrolls the full profile grid (may take a while for accounts with thousands of posts) and scrapes every post. The scroll has a 120-second timeout and 3 stability checks (no new posts after 3 consecutive scroll attempts).

## Proxy and Stealth

### Using a proxy

```bash
python main.py https://example.com --proxy http://user:pass@proxy:8080
```

Routes every request through the proxy: page fetches (`Fetcher.get()`), reconnaissance, robots.txt, feeds, browser modes (`StealthyFetcher.async_fetch()`) and asset downloads (`httpx.AsyncClient`). The password is never printed or written.

### Headless browser

```bash
python main.py https://example.com -1 --headless
```

Runs the browser without a visible window. Default is visible (useful for debugging Cloudflare challenges).

### Concurrent asset downloads

```bash
python main.py https://example.com -c 20
```

Downloads up to 20 assets simultaneously (default is 4). The `-c` flag controls both asset download concurrency and page prefetching concurrency. Assets are on by default.

## Combined Flags

### Full documentation scrape

```bash
python main.py https://docs.example.com/api/ -s -d
```

Docs mode from `/api/`. Docs mode follows every sidebar link whatever its path (`-s` only changes the output folder name to `docs_example_com_api`), downloads no assets and keeps no incremental cache; Markdown conversion is on by default.

### JS site with everything

```bash
python main.py https://app.example.com -j --wait 8
```

JS rendering with page requests at least 8 seconds apart. Assets, Markdown, and incremental are all on by default.

### News site monitoring

```bash
python main.py https://news-site.com --recent 1d --max-articles 10 -v
```

Daily crawl, last 24 hours only, max 10 articles per section, verbose logging. Incremental and Markdown are on by default. Optimal for cron-based monitoring.

## Output Structure

The web crawl examples produce output in `ScrapedSites/` (with `--markdown-only` it holds only the run state):

```
ScrapedSites/<domain>/
  ├── index.html              # Home page
  ├── page-name.html          # Subpages (path separators become directories)
  ├── sub/path/page.html      # Nested paths preserved
  ├── slug-12345-1.html       # Numeric segments flattened into filename
  ├── site_outline.json       # Navigation structure + sitemap stats
  ├── run_summary.json        # What the run did
  ├── .scrapepy_cache.json    # Incremental cache (on by default)
  ├── images/                 # Always downloaded
  │   ├── logo.png
  │   └── hero.jpg
  ├── css/                    # Included by default (use --no-include-assets to skip)
  ├── js/                     # Included by default (use --no-include-assets to skip)
  ├── fonts/                  # Included by default (use --no-include-assets to skip)
  ├── media/                  # PDFs, videos, etc.
  └── broken_links.txt        # If any broken links detected
```

Markdown output goes to `MarkdownSites/` (on by default, use `--no-markdown` to skip):

```
MarkdownSites/<domain>/
  ├── _index.md
  ├── page-name.md
  ├── sub/path/page.md
  ├── images/              (copied from ScrapedSites)
  └── media/               (copied from ScrapedSites)
```

When date filtering produces section batches, articles are organized under their discovering section with generated index files:

```
MarkdownSites/<domain>/
  ├── _index.md
  ├── sport/
  │   ├── _index.md         (section index with article listings + thumbnails)
  │   ├── images/           (images referenced by section articles)
  │   ├── my-article.md
  │   └── football/
  │       ├── _index.md     (sub-section index)
  │       ├── images/
  │       └── article-two.md
  └── news/
      ├── _index.md
      ├── images/
      └── some-article.md
```

Image and media directories are automatically copied from `ScrapedSites/` so that references in the Markdown files resolve correctly. CSS, JS, and font files are not copied since they aren't referenced in Markdown output.

## Programmatic Usage

ScrapePy can be used as a library:

```python
import asyncio
from scrapepy.config import ScrapeConfig
from scrapepy.scraper import run_scraper

config = ScrapeConfig(
    url="https://example.com",
    markdown=True,
    stay_in_path=True,
    headless=True,
)

exit_code = asyncio.run(run_scraper(config))
```

### Using individual components

```python
from scrapepy.config import ScrapeConfig
from scrapepy.engine.fetcher import fetch_page_http
from scrapepy.processing.urls import is_in_scope, classify_asset_url

config = ScrapeConfig("https://example.com/docs/", stay_in_path=True)

# Fetch a page (Fetcher.get() is a classmethod: never instantiate)
response = fetch_page_http("https://example.com/docs/getting-started", config)
html = response.body.decode(response.encoding or "utf-8")
print(f"Status: {response.status}, Length: {len(html)}")

# Check scope
print(is_in_scope("/docs/api", config))   # True
print(is_in_scope("/blog/post", config))   # False

# Classify an asset
info = classify_asset_url("/images/logo.png", "https://example.com", False)
# {'folder': 'images', 'filename': 'logo.png', 'full_url': 'https://example.com/images/logo.png'}
```

### Converting HTML to Markdown

```python
from scrapepy.convert.to_markdown import HTMLToMarkdown

converter = HTMLToMarkdown("./ScrapedSites/example_com")
converter.convert_all()
# Converts all .html/.htm files, copies images/ and media/ directories
```

### Converting OpenAPI specs

```python
from scrapepy.convert.openapi_to_markdown import convert_openapi_file

convert_openapi_file("openapi-spec.yaml", "api-docs.md")
# Outputs: API info, endpoints by tag, parameters, responses, schemas
```

### Date extraction

```python
from scrapepy.processing.dates import get_best_date, extract_date_from_url

# Quick check URL for date
result = extract_date_from_url("/2026/02/25/article-slug")
print(result.date, result.source.name)  # 2026-02-25 00:00:00+00:00 URL_PATTERN

# Full multi-signal date extraction
result = get_best_date("/path", html_content, response_headers)
if result:
    print(f"Published: {result.date}, Source: {result.source.name}, Raw: {result.raw}")
```

### Page classification

```python
from scrapepy.processing.classifier import classify_page, PageType

page_type = classify_page("/news/", html_content)
if page_type == PageType.SECTION:
    print("Navigation/index page")
elif page_type == PageType.ARTICLE:
    print("Article with content")
else:
    print("Unknown: treat conservatively")
```
