# Architecture

## High-Level Overview

```
              main.py (3 lines)
                |
              cli.py (199 lines)     argparse -> ScrapeConfig
                |
            scraper.py (82 lines)    mode dispatch (lazy imports)
           /    |    \     \      \     \
      crawl  single  swagger  docs  instagram  audit     (modes/)
      1207   43      94       194   474        357       (lines)
         |      |       |       |       |         |
       fetcher.py (119) + browser_actions.py (272)       (engine/)
                          instagram_actions.py (721)
         |
    +---------+---------+---------+---------+---------+
    |         |         |         |         |         |
  urls.py  errors.py  assets.py  rewriter.py  dates.py     (processing/)
  280      42         128        174          211
                      classifier.py  discovery.py  navigation.py  outline.py
                      110            350           341            195
    |
  writer.py (193) + broken_links.py (37) + cache.py (155)  (output/)
    |
  to_markdown.py (880) + openapi_to_markdown.py (266)      (convert/)
                        + instagram_to_markdown.py (294)
```

**Total: 7,547 source lines + 3,501 test lines = 11,048 lines**

## Request Flow

### Default Crawl Mode

```
1. CLI parses args -> ScrapeConfig (incl. cutoff_date from --recent/--since)
2. scraper.py creates output dirs (css/, js/, images/, fonts/, media/), dispatches to crawl.py
3. Phase 0: Site Reconnaissance (always runs)
   a. Fetch /robots.txt → RobotsPolicy (Disallow rules, Crawl-delay) + Sitemap: directives.
      Missing robots.txt allows everything; --ignore-robots keeps the sitemaps only.
      Every later fetch (discovery, pages, prefetches) checks the policy and waits on a
      per-host HostRateLimiter: max(--wait, Crawl-delay) for pages, min(--wait, 1s) or
      Crawl-delay for discovery.
   b. Fetch /sitemap.xml + sub-sitemaps (cap 20) + robots.txt sitemaps
      Parse <loc> + <lastmod> dates into DiscoveryMap
   c. Fetch homepage HTML (reused for nav parsing, so no extra fetch)
   d. Auto-discover RSS/Atom feeds from homepage <link rel="alternate"> tags
   e. If no feeds discovered, try common paths (/feed, /rss.xml, etc.), capped at 10
   f. Parse RSS <item>/<pubDate> and Atom <entry>/<published> into DiscoveryMap
   g. Parse homepage <header>/<nav>/<footer> for navigation structure → list[NavLink]
      - 4-tier priority: header > standalone nav > class-matched > footer
      - Follows <ul>/<li> nesting for hierarchy detection
      - Handles mega-menu/dropdown <div> containers (_find_dropdown_container, _extract_dropdown_links)
      - Detects dropdown/submenu/mega-menu CSS classes and role="menu" attributes
      - Filters: external links, anchors, non-content paths, file extensions
   h. Discover section pages from all homepage links (urls.py discover_section_pages)
      - Extract all internal links, filter out article paths (3+ hyphens) and non-section paths
      - Results merged into nav_section_paths alongside outline nav sections
   i. Build SiteOutline: nav sections with children and sub_sections, footer links, sitemap stats
      - Level-0 nav links become top-level sections; level-1+ become sub_sections (NavSection tree)
      - SiteOutline.all_section_paths() flattens tree for queue seeding
      - Assigns sitemap URLs to sections by longest-prefix match (recursively searches sub_sections)
      - Counts article_count and recent_count per section
   j. Save site_outline.json to output directory
   k. Seed crawl queue from outline + discovered sections, footer links (footer skipped in focused mode)
   l. Focused crawl setup: when max_articles + date filter + nav_section_paths, set focused_crawl=True
4. crawl.py BFS loop (section batches have priority over regular deque):
   a0. Early exit check: in focused mode, if all nav sections visited and no batches remain, clear queue and stop
   a. Pick next URL:
      - Current section batch (pop front, DOM order) → from_section_batch=True
      - Next section batch from queue → activate as current
      - Regular to_visit deque (popleft, FIFO)
   b. Skip UI element IDs (mintoc-, rh-, dropspot patterns)
   c. Parse queue item → (url_path, hash_fragment, full_url)
   d. Check visited set, scope (is_in_scope), hash dedup
   e. Pre-crawl date filter (only for article-slug URLs, not sections):
      - Check discovery_map date → skip if before cutoff
      - Check URL date pattern → skip if before cutoff
      - Exception: --min-articles quota not yet met → don't skip
   f. Check prefetch buffer for pre-fetched result
   g. Fetch via prefetch task, HTTP (with semaphore), or browser
   h. Handle 304 Not Modified → replay cached links, skip
   i. Check HTTP status >= 400 → broken link
   j. Quick error page check (raw HTML patterns)
   k. Cloudflare challenge → sleep 5s
   l. JS content wait / politeness delay:
      - Browser mode: wait_for_js_content()
      - Section batch/prefetch: 0.2s yield
      - Regular: config.wait_time sleep
   m. Rendered error page check (BeautifulSoup title/h1/body)
   n. Content-hash check (SHA-256) → skip if unchanged
   o. Extract links (browser: extract_links_browser, HTTP: regex)
   p. Date filtering: classify page (SECTION/ARTICLE/UNKNOWN) + get_best_date()
      - SECTION pages:
        * Always kept, sections_kept++
        * Non-root + batchable (always true unless focused_crawl, then only nav section pages): split links into article_paths (DOM order) + other_links
        * Article paths → section_batches queue (with --max-articles cap)
        * Start prefetching first batch of articles immediately (up to -c concurrent)
        * Root page: filter out article-slug links, only queue section/nav links
      - ARTICLE/UNKNOWN pages with date before cutoff:
        * Check --min-articles quota for section → keep if needed
        * Otherwise skip, articles_skipped_old++
        * If from section batch: discard rest of batch (newest-first ordering)
        * Cancel prefetch tasks for discarded URLs
      - ARTICLE/UNKNOWN pages with date >= cutoff or no date:
        * Keep, articles_kept++
        * Update section article count
        * Check --max-articles cap → discard rest of batch if reached
   q. Download assets concurrently (httpx, persistent client), section pages: images_only=True; min-articles filler: skip entirely
   r. Rewrite URLs for offline viewing
   s. Save HTML to disk (url_to_filepath with numeric flattening)
   t. Inline markdown conversion (if -m / default on):
      - Section batch articles placed in section directories via article_filepath_in_section()
      - Article metadata tracked in section_articles for later index.md generation
      - Thumbnails extracted from section page HTML via _extract_article_thumbnails()
   u. Update incremental cache (ETag, Last-Modified, SHA-256, links, date, type)
   v. Queue extracted links (skip for min-articles filler articles AND focused_crawl mode)
      - Pre-filter: skip non-content paths, old article URLs, RSS/feed/atom URLs
   w. Kick off prefetches for upcoming section batch + regular queue URLs
5. Generate section _index.md files (for each section with articles, skipped with --no-index-pages):
   a. Lookup section label from outline NavSection (fallback: URL path-derived)
   b. Collect sub-section info for sub-section links
   c. generate_section_index() creates markdown with frontmatter + article listings
   d. Copy referenced images from global images/ to each section's images/ dir (always)
6. Save cache, write broken_links.txt if any
7. Print summary: pages saved, unchanged, assets, outline stats, date filter stats, robots.txt blocks
8. Write run_summary.json (RunStats → build_summary) and return it; its exit_code becomes the process exit code
```

### Browser Modes (-1, --swagger, -d)

```
1. CLI parses args -> ScrapeConfig (needs_browser = True)
2. scraper.py creates output dirs, dispatches to specific mode
3. Mode creates a page_action callback with custom JS
4. StealthyFetcher.async_fetch() called with:
   - page_action: async fn(page) -> page  (receives raw Patchright Page)
   - solve_cloudflare: True (automatic Cloudflare bypass)
   - network_idle: True (wait for network quiet)
   - headless: from config
   - wait: int(config.wait_time)  ← must be int, not float
   - timeout: 120000 (2 minutes)
   - proxy: from config (optional)
5. page_action executes browser automation:
   - single_page: scroll_full_page() to trigger lazy loading
   - swagger: expand_swagger_sections(), find_spec_url(), _download_spec()
   - docs: find_nav_links(), iterate pages, expand_docs_sections(), get_main_content_html()
6. Response HTML saved to disk
7. If -m: run _run_markdown_conversion()
   - swagger: try OpenAPI spec → Markdown first, fall back to HTML
   - others: HTMLToMarkdown.convert_all()
```

### JS Render Crawl Mode (-j)

```
1. Same as default crawl, but config.needs_browser = True
2. Each page fetched via StealthyFetcher instead of Fetcher
3. After fetch: wait_for_js_content(page, wait_time)
4. Re-extract HTML from page: html = await page.content()
5. Links extracted via extract_links_browser(page, base_url)
6. Rest of processing identical to default crawl
```

### Instagram Mode (--instagram)

```
1. CLI parses args -> ScrapeConfig (instagram=True)
2. scraper.py dispatches to instagram.py (before create_output_dirs)
3. _get_accounts() builds username list from URL + accounts file
   - accounts file: one per line, URLs / @username / bare usernames, # comments
4. Per account:
   a. Create account directory: ScrapedSites/instagram/{username}/
   b. Fetch profile via StealthyFetcher with user_data_dir for cookies
   c. page_action callback:
      - Check/handle login if --ig-login (3-minute manual login timeout)
      - Dismiss popups (cookie banners, "Not Now" dialogs)
      - Extract profile metadata:
        * Tier 1: embedded JSON from <script type="application/json"> ScheduledServerJS payloads
        * Tier 2: meta tags (og:description, og:image)
        * Tier 3: DOM scraping
        * 14 fields: username, full_name, bio, follower/following/post counts, profile_pic, etc.
      - Scroll to load post grid (1.5s intervals, 3 stable rounds, 120s timeout)
      - Extract post URLs: all /p/ and /reel/ links from grid
   d. Save profile.json (+ profile.md if -m)
   e. If --incremental: scan existing post dirs for shortcodes, skip already-scraped
   f. Iterate post URLs with rate limiting:
      - Deterministic 2-5s jitter per post (MD5 hash of shortcode)
      - Fetch post page via StealthyFetcher
      - Extract structured data (40+ fields):
        * JSON-LD primary, DOM fallback
        * Identifiers, content, engagement, media URLs, video metadata
        * Audio/music info, location, author, collaboration, usertags, comments
      - Handle carousel: navigate_carousel() clicks through slides
      - Setup video interceptor for CDN URLs, trigger_video_playback()
      - Download media: images (JPG/PNG/WebP), videos (MP4), carousel slides
        * 0.5s delay between files
        * --update-meta: skip existing media files
        * Filter out blob: URLs
      - Save post.html, post.json (+ post.md if -m)
   g. 5s delay before next account
5. Print summary (accounts scraped, posts scraped, media downloaded)
```

### Audit Mode (--audit)

```
1. CLI parses args -> ScrapeConfig (audit=True)
2. scraper.py dispatches to audit.py (first check, before Instagram)
3. Phase 1: Recon (identical to crawl.py Phase 0)
   a. fetch_robots_txt() -> save raw to meta/robots.txt
   b. parse_robots_sitemaps() -> extract sitemap URLs
   c. run_discovery() -> (discovery_map, homepage_html, feed_urls)
   d. extract_navigation() on homepage_html
   e. build_site_outline()
   f. discover_section_pages() from all homepage links → saved in audit_report.json + test_config.json
4. Phase 2: Sample pages (fetch via fetch_page_http)
   a. Homepage: reuse homepage_html from discovery (no extra fetch)
   b. Sections: first 3 from outline.nav_sections
   c. Articles: newest 3 from discovery_map with article slugs
   d. Each sample: classify_page() + get_best_date() + save HTML
5. Phase 3: Save
   a. audit_report.json (full results: recon data, samples, classifications)
   b. test_config.json (committable expected values: section labels, page types, dates)
   c. pages/*.html (HTML snapshots: homepage, sections, articles)
   d. meta/robots.txt (raw robots.txt content)
6. Output directory: tests/fixtures/<output_dir_name>/
```

## Package Responsibilities

### `scrapepy/config.py`

Central configuration. `ScrapeConfig` is a dataclass that:
- Takes all CLI options as fields (24 input fields)
- Derives `base_url`, `domain`, `starting_path`, `starting_hash`, `output_dir`, `output_dir_name` in `__post_init__`
- Computes `cutoff_date` from `--recent` (relative duration) or `--since` (absolute date)
- Instagram mode sets special output dir: `ScrapedSites/instagram`
- `--stay-in-path` adds path suffix to output dir name (e.g., `docs_example_com_api_v2`)
- Provides computed properties: `wait_time` (5.0 browser/3.0 HTTP), `needs_browser`, `has_date_filter`

All other modules receive a `ScrapeConfig` instance; they never read CLI args directly.

### `scrapepy/engine/fetcher.py`

Three fetching strategies:

| Function | Backend | Use Case | Key Parameters |
|----------|---------|----------|----------------|
| `fetch_page_http()` | `Fetcher.get()` (curl_cffi) | Default crawl, fast HTTP with TLS spoofing | `stealthy_headers=True`, `follow_redirects=True`, `timeout=30` |
| `fetch_page_browser()` | `StealthyFetcher.async_fetch()` (Patchright) | JS rendering, SPA, Swagger, docs, Instagram | `network_idle=True`, `solve_cloudflare=True`, `timeout=120000`, `wait=int(...)` |
| `download_asset()` | `httpx.AsyncClient` | Concurrent asset downloading | `follow_redirects=True`, `timeout=30.0`, Chrome UA string |

Note: Scrapling's `Fetcher` and `StealthyFetcher` methods (`.get()`, `.fetch()`, `.async_fetch()`) are all classmethods. Call them directly on the class and never instantiate `Fetcher()` or `StealthyFetcher()`, which triggers a deprecation warning. The `wait` parameter must be `int`, not `float`.

`fetch_page_browser()` wraps the caller's `page_action` callback and passes it to `StealthyFetcher.async_fetch()`. The callback receives the raw Patchright `page` object for direct browser control. The optional `user_data_dir` parameter enables persistent cookie storage (used by Instagram mode for login persistence via Patchright's `launch_persistent_context`).

### `scrapepy/engine/browser_actions.py`

All browser-side JavaScript lives here as `page.evaluate()` calls:

- `scroll_full_page(page)`: Scroll to bottom + lazy load trigger
- `expand_swagger_sections(page)`: Expand all Swagger UI operation blocks, models, show/hide toggles
- `find_spec_url(page) -> str | None`: Locate OpenAPI spec URL from meta tags, scripts, or input fields
- `expand_docs_sections(page)`: Expand collapsed content on doc pages
- `find_nav_links(page) -> list[dict]`: Extract sidebar navigation links
- `get_main_content_html(page) -> str`: Extract main content area
- `wait_for_js_content(page, wait_time)`: Wait for JS frameworks to render
- `extract_links_browser(page, base_url) -> list[str]`: Extract all links including data attributes, onclick handlers, hash patterns

### `scrapepy/engine/instagram_actions.py`

Instagram-specific browser automation (721 lines, 2nd largest file). All functions take a Patchright `page`:

- `check_login_status(page) -> bool`: Detect logged-in state via nav avatar or profile link
- `wait_for_login(page)`: Poll for manual login completion (3-minute timeout)
- `dismiss_login_popup(page)`: Close "Log In" / "Not Now" / cookie popups
- `scroll_profile_posts(page, max_posts) -> int`: Infinite-scroll grid: 1.5s intervals between scroll events, 3 stability checks (no new posts), 120s total timeout
- `extract_post_links(page) -> list[str]`: Get all `/p/` and `/reel/` URLs from post grid
- `extract_profile_data(page) -> dict`: 3-tier extraction: embedded JSON (ScheduledServerJS payloads in `<script type="application/json">`) > meta tags (og:description, og:image) > DOM; 14 fields including username, full_name, bio, followers, following, post_count, profile_pic_url, is_private, is_verified, category, external_url, user_id
- `setup_video_interceptor(page) -> list`: Network response listener for CDN video URLs
- `extract_post_data(page) -> dict`: 40+ fields via JSON-LD first, DOM fallback. Categories: identifiers (shortcode, post_id), content (caption, alt_text, hashtags, mentions), engagement (likes, comments, views, plays), media (image_versions2, video_versions, carousel), video metadata (duration, has_audio), audio/music info, location data, author info, collaboration/coauthors, usertags, preview comments with username/text/likes/timestamp
- `trigger_video_playback(page)`: Force play to emit CDN request for interceptor
- `navigate_carousel(page) -> list[dict]`: Click through carousel slides collecting all media URLs per slide

### `scrapepy/processing/urls.py`

URL logic:
- `SKIP_EXTENSIONS`: Compiled regex of 30+ file extensions to skip during crawling
- `NON_CONTENT_PATHS`: Compiled regex of non-content utility paths (login, cart, admin, share, etc.) shared by crawl.py and navigation.py
- `is_in_scope(url_path, config) -> bool`: Checks if URL path falls within configured scope (`-s` prefix match, `-w` contains match)
- `normalize_link(link, current_path, base_url) -> str | None`: Resolves relative links, strips query/fragment, filters non-HTML extensions, external links
- `extract_links_from_html(html, base_url) -> list[str]`: Regex extraction of `href`, `data-href`, `data-url`, `data-link` attributes
- `has_article_slug(path) -> bool`: Article slug heuristic: >= 15 chars with hyphens, or >= 2 hyphens (shared by crawl.py and audit.py)
- `is_likely_article_path(path) -> bool`: Stricter article detection: 3+ hyphens, 30+ chars with hyphens, or numeric CMS ID suffix. Used by `discover_section_pages()` to avoid false positives on section names.
- `discover_section_pages(html, base_url) -> list[str]`: Extracts section-like pages from homepage HTML, filtering out article paths and non-section paths
- `classify_asset_url(url, base_url, include_assets) -> dict | None`: Categorizes URLs into folders: images (always downloaded), css/js/fonts (only with `-a`), media (always). Handles protocol-relative URLs, truncates long filenames (>100 chars) with MD5 hash.

### `scrapepy/processing/dates.py`

Multi-signal date extraction for article pages:

- `extract_date_from_url(url_path)`: 3 regex patterns: `/YYYY/MM/DD/`, `/YYYY-MM-DD-`, `/YYYYMMDD-`
- `extract_dates_from_html(html)`: 8 meta property names (`article:published_time`, `og:article:published_time`, `datePublished`, `date`, `publish_date`, `article:published`, `sailthru.date`, `parsely-pub-date`), JSON-LD `@graph` traversal for `datePublished`/`dateCreated`, `<time datetime=...>` elements
- `extract_date_from_headers(headers)`: HTTP `Last-Modified` header (RFC 2822 format)
- `get_best_date(url_path, html, headers)`: Combines all signals, returns highest-confidence: META_TAG(5) > JSON_LD(4) > URL_PATTERN(3) > TIME_ELEMENT(2) > HTTP_HEADER(1)
- `url_has_old_date(url_path, cutoff) -> bool`: Quick pre-filter: checks only URL patterns (no HTML needed)

### `scrapepy/processing/classifier.py`

Heuristic page classification:

1. URL pattern matching: `_SECTION_URL_PATTERNS` (homepage, category, tag, topic, archive, author, pagination, section roots, feed) → SECTION; `_ARTICLE_URL_PATTERNS` (date in path) → ARTICLE
2. Date signals: Any date found in URL or HTML → article indicator
3. Link density: `_compute_link_density()` strips nav/header/footer/aside, computes link text / total text ratio
   - link_density > 0.4 + no date → SECTION
   - has date + link_density < 0.5 → ARTICLE
   - has date (any density) → ARTICLE
   - Otherwise → UNKNOWN (treated conservatively as fresh article)

The crawl mode also uses article slug detection (`has_article_slug()` in `processing/urls.py`) to split section links:
- Article slugs: >= 15 chars with at least 1 hyphen, OR >= 2 hyphens (any length)
- Non-article: short segments like "reviews", "gaming", "authors", numeric IDs

### `scrapepy/processing/navigation.py`

Homepage navigation parsing via BeautifulSoup:

- `extract_navigation(html, base_url)`: 4-tier detection:
  1. `<header>` containing `<nav>` or `<a>` elements
  2. `<nav>` elements (standalone, not inside header/footer)
  3. Elements with nav-related classes (if < 3 links found above)
  4. `<footer>` containing `<nav>` or `<a>` elements
- Follows `<ul>/<li>` nesting for hierarchy detection (parent->children relationships)
- Handles mega-menu/dropdown navigation via `_find_dropdown_container()` and `_extract_dropdown_links()`:
  - Detects `<div>` elements with dropdown/submenu/mega-menu CSS classes or `role="menu"` attribute
  - Falls back to any child `<div>` containing `<a>` links
  - Recursively extracts from nested sub-dropdown `<div>` elements for multi-level hierarchy
  - When no nested `<ul>` exists in a `<li>`, checks for dropdown `<div>` containers
- Filters: external links, anchor-only, JavaScript links, non-content paths (login, cart, admin), file extensions
- Returns `list[NavLink]` with label, path, parent_label, level, source

### `scrapepy/processing/outline.py`

Site outline builder:

- `build_site_outline()`: Level-0 nav links become top-level sections, level-1+ become `sub_sections` (hierarchical `NavSection` tree). Orphan children promoted to top-level sections. Sub-section paths also added to parent's `children` list for backward compatibility.
- `_assign_url_to_section()`: Maps sitemap URLs to sections by longest-prefix match (recursively searches section paths + children + sub_sections)
- `NavSection`: Dataclass with `sub_sections: list[NavSection]` for tree hierarchy from mega-menu/dropdown navigation
- `SiteOutline`: Counts `article_count` and `recent_count` per section from discovery map
- `SiteOutline.all_section_paths() -> set[str]`: Recursively collects all section paths (including sub-sections) into a flat set. Used for queue seeding and focused crawl mode.
- `SiteOutline.save()`: Writes `site_outline.json` (JSON with indent=2). Sub-sections serialized recursively.

### `scrapepy/processing/discovery.py`

Pre-crawl discovery builds a `DiscoveryMap` (`dict[str, datetime]`):

1. Fetch `/sitemap.xml` + process sub-sitemaps (1 level deep, cap 20)
2. Process extra sitemaps from `robots.txt` Sitemap: directives
3. Auto-discover RSS/Atom feeds from homepage `<link rel="alternate">`
4. If no feeds discovered, try 7 common paths (`/feed`, `/rss.xml`, etc.)
5. Parse feeds: RSS `<pubDate>` (RFC 2822), Atom `<published>` (ISO 8601)

All errors are silently caught, since missing sitemaps/feeds are normal. Returns `(date_map, homepage_html, feed_urls)` where homepage HTML is reused for navigation parsing (saves one fetch).

### `scrapepy/processing/rewriter.py`

`rewrite_urls()` transforms all internal URLs in saved HTML to relative paths for offline viewing:
- `<a href>`, `<link href>` → HTML pages, CSS, or media files
- `<img src>`, `<script src>`, `<source>`, `<video>`, `<audio>` → Images, JS, media
- `srcset` attributes → Each URL individually rewritten
- CSS `url()` → Image references
- Absolute same-domain URLs (`base_url + /path`)
- Out-of-scope links (with `--stay-in-path`) → Full live URLs

### `scrapepy/processing/errors.py`

Three detection functions:
- `is_error_page_html(html)`: Fast string check: NoSuchKey, AccessDenied, >404<, >Not Found<, "The specified key does not exist"
- `is_error_page_content(title, h1_text, body_text)`: Pattern matching: ERROR_PATTERNS tuple (404, 403, 401, 500, 502, 503, not found, forbidden, nosuchkey), short body heuristic
- `is_cloudflare_challenge(html)`: "Verifying you are human" / "Just a moment"

### `scrapepy/processing/assets.py`

- `discover_and_download_assets(images_only=False)`: Extracts asset URLs from HTML via regex, classifies them, downloads concurrently with asyncio.Semaphore(config.concurrent). When `images_only=True`, skips non-image/media assets (used for section pages to only get thumbnails). Accepts `http_client` for persistent connection reuse.
- `_extract_asset_urls()`: Regex patterns: `<img src>`, `<img srcset>`, `<link stylesheet>`, CSS `.css` hrefs, `<script src>`, CSS `url()`. Images always extracted; CSS/JS only with `-a`.
- `_download_one()`: Sends conditional headers (If-None-Match/If-Modified-Since) for incremental mode, handles 304 responses

### `scrapepy/output/writer.py`

File system operations:
- `create_output_dirs()`: Creates output directory tree: css/, js/, images/, fonts/, media/
- `_flatten_numeric_segments(path)`: Walks segments right-to-left, collects consecutive numeric segments before filename, appends as hyphenated suffix (e.g., `news/1090365/1/slug` -> `news/slug-1090365-1`)
- `extract_slug(url_path) -> str`: Extract article slug from URL path with numeric flattening. Strips common web extensions. Used for section-based MarkdownSites filenames.
- `article_filepath_in_section(url_path, section_path, config, ext=".md") -> str`: Map an article URL to a filepath under its section directory in MarkdownSites. Section path becomes directory structure, slug becomes filename.
- `url_to_filepath()`: Converts URL path to local file path: strip query/fragment, `--stay-in-path` prefix stripping, numeric flattening, add `.html` if no extension, hash fragment support
- `get_relative_path()`: `os.path.relpath()` with `./` prefix normalization
- `write_file()`: Writes string (UTF-8) or bytes, creating parent dirs

### `scrapepy/output/cache.py`

`CrawlCache` manages incremental crawl state via `.scrapepy_cache.json`:

Per page entry: `etag`, `last_modified`, `content_hash` (SHA-256), `links` (for 304 replay), `timestamp`, optional `published_date` and `page_type`

Per asset entry: `etag`, `last_modified`, `timestamp`

Workflow:
1. On re-crawl, `get_page_headers()` builds conditional request headers
2. Server returns 304 → page skipped, cached links replayed
3. Server returns 200 → `is_page_unchanged()` compares SHA-256 → skip if same
4. Changed pages → `update_page()` stores new metadata

Instagram incremental mode does not use CrawlCache (browser-fetched pages lack HTTP conditional headers). Instead, `instagram.py` scans existing post directories for shortcodes.

### `scrapepy/output/broken_links.py`

`BrokenLinkTracker` collects broken URLs during crawling (HTTP >= 400, error pages) and writes `broken_links.txt` with timestamp, source URL, and sorted list.

### `scrapepy/convert/to_markdown.py`

`HTMLToMarkdown` class (880 lines, 3rd largest file) with content-type-specific extraction:

Extraction chain (tries each in order, returns first with > 100 chars):
1. Error page detection → skip
2. API docs (Readme.io, Stoplight): detects `readme` class, `api-method` blocks → extracts endpoints
3. Swagger UI: detects `swagger-ui` / `opblock` classes → extracts operations grouped by tag
4. RoboHelp/MadCap Flare: detects generator meta tag → unwraps layout tables, removes chrome
5. Standard CMS: tries 10 CSS selectors: `.entry-content` (WordPress), `.post-content`, `article .content`, `[role='main'] article`, `#content article`, `article`, `[role='main']`, `main`, `#content`, `.content`
6. trafilatura: content extraction with `output_format="markdown"`
7. Generic: strip nav/header/footer/aside, markdownify body

Pre-conversion skipping: XML feeds, manifests, WordPress boilerplate paths

Post-conversion: clean empty rows/headings, promote bold to headings, ensure title, convert links (.html -> .md), copy images/ + media/ directories

Module-level function: `generate_section_index(section_label, section_url, domain, articles, sub_sections)`: Generates section `_index.md` with YAML frontmatter (page_type: section, scraped_at, content_hash), article listings with thumbnails, and sub-section links. `convert_file()` accepts optional `output_path` parameter for section-based output placement.

### `scrapepy/convert/openapi_to_markdown.py`

Converts OpenAPI/Swagger spec files (JSON or YAML) to structured Markdown:
- API info, version, servers
- Authentication schemes
- Endpoints grouped by tag with parameters, request bodies, responses
- Schema definitions with property tables
- `$ref` resolution and cross-linking

### `scrapepy/convert/instagram_to_markdown.py`

Two conversion functions:
- `post_to_markdown(post_data)`: Full markdown with: date, author (verified badge), co-authors, paid partnership, location, engagement stats, content type, video metadata, music/audio, post IDs, caption (edited flag), alt text, hashtags, mentions, tagged users, media embeds (image/video), comments with timestamps
- `profile_to_markdown(profile_data)`: Profile summary with: name, verified/private badges, category, user ID, stats table (posts/followers/following), bio, website link

## Tests

```bash
python -m pytest tests/ -v    # a few seconds, no network I/O
```

All tests are pure logic: no network calls, no file system side effects (except temp dirs).

| File | Covers |
|------|--------|
| `test_urls.py` | `is_in_scope()`, `normalize_link()`, `extract_links_from_html()`, `has_article_slug()`, `is_likely_article_path()`, `discover_section_pages()`, `classify_asset_url()`, `NON_CONTENT_PATHS`, `SKIP_EXTENSIONS` |
| `test_navigation.py` | Nav hierarchy detection, source tagging (header/footer/nav), link filtering, mega-menu/dropdown parsing, `_find_dropdown_container()`, `_extract_dropdown_links()` |
| `test_outline.py` | URL-to-section assignment (incl. sub_sections), outline building (article counts, cutoff dates, orphan children, hierarchical sub-sections), `all_section_paths()`, JSON serialization |
| `test_frontmatter.py` | Frontmatter generation, word count, metadata extraction, convert_file integration |
| `test_section_index.py` | `generate_section_index()`: frontmatter fields, article listings, thumbnails, sub-section links, empty cases |
| `test_discovery_parsers.py` | `_parse_sitemap()`, `_parse_rss()`, `_parse_atom()`, `_discover_feeds_from_html()`, `parse_robots_sitemaps()`: XML parsing, external URL filtering, malformed input |
| `test_config.py` | `_parse_recent()`, `_parse_since()`, `ScrapeConfig` derived fields, properties, new defaults (markdown=True, incremental=True, include_assets=True, concurrent=4) |
| `test_writer.py` | `_flatten_numeric_segments()`, `url_to_filepath()`, `extract_slug()`, `article_filepath_in_section()` |
| `test_dates.py` | ISO/RFC2822 parsing, URL/HTML/header date extraction, priority ranking, `url_has_old_date()` |
| `test_audit_fixtures.py` | Fixture-driven from `--audit` runs: nav parsing, page classification, date extraction, robots.txt |
| `test_cache.py` | `_get_header()`, page/asset cache operations, save/load roundtrip, corrupt/version handling |
| `test_rewriter.py` | `rewrite_urls()`: href, src, srcset, CSS url(), absolute URLs, stay-in-path scope |
| `test_classifier.py` | `is_section_url()`, `_compute_link_density()`, `classify_page()` (URL patterns, link density, date signals) |
| `test_crawl_helpers.py` | Article slug detection, article path detection, section link splitting, `max_articles` config |
| `test_errors.py` | `is_error_page_html()`, `is_error_page_content()`, `is_cloudflare_challenge()` |
| `test_discovery.py` | robots.txt `Sitemap:` directive parsing |
| `test_robots.py` | `RobotsPolicy` (Disallow, user agent groups, Crawl-delay, missing robots.txt, `--ignore-robots`), `HostRateLimiter`, discovery skipping disallowed URLs |
| `test_crawl_run.py` | The crawl loop against in-memory pages: robots.txt enforcement, pacing of prefetches, `run_summary.json`, unchanged and changed pages on a second run, `--no-index-pages` |
| `test_run_summary.py` | Summary schema, counts, relative paths, no credentials in options, exit codes |
| `test_output_dir.py` | `--output-dir`, `derive_output_dirs()`, writer and converter paths |
| `test_cli.py` | Bad arguments exit 2, `--version`, `--user-agent`, `--ignore-robots` warning, `--no-index-pages` |

## Scrapling API Integration

### Response Object

Scrapling returns a custom `Response` object:

```python
response.status      # HTTP status code (int)
response.body        # Raw bytes
response.encoding    # Detected encoding (str)
response.url         # Final URL after redirects
response.headers     # Response headers (dict-like)
response.text        # Text content only (no tags); do not use for HTML
response.html_content  # Parsed HTML (TextHandler)

# For HTML content (ALWAYS use this, not .text):
html = response.body.decode(response.encoding or "utf-8")
```

### StealthyFetcher Parameters

Key parameters used:
```python
StealthyFetcher.async_fetch(
    url,
    headless=True/False,      # Browser visibility
    network_idle=True,         # Wait for network quiet
    page_action=callback,      # async fn(page) -> page
    timeout=120000,            # Navigation timeout (ms)
    wait=5,                    # Additional wait (seconds), must be int, not float
    solve_cloudflare=True,     # Auto Cloudflare bypass
    proxy="http://...",        # Proxy URL (optional)
    user_data_dir="~/.../",    # Persistent cookies (optional, uses launch_persistent_context)
)
```

### page_action Pattern

```python
async def my_action(page):
    # page is a raw Patchright Page object
    # Full Playwright API available
    await page.evaluate("document.querySelectorAll('button').forEach(b => b.click())")
    await page.wait_for_selector('.content')
    return page  # Must return page

response = await StealthyFetcher.async_fetch(url, page_action=my_action)
```

## Data Flow Diagram

```
URL Input
    |
    v
ScrapeConfig ────────────> Mode Selection
    |                           |
    |     ┌─────────────────────┼──────────────────────┐
    |     |                     |                      |
    v     v                     v                      v
Phase 0: Recon           Browser Modes          Instagram Mode
    |                     (page_action)         (user_data_dir)
    v                          |                      |
robots.txt                     v                      v
sitemaps ───> DiscoveryMap    StealthyFetcher     StealthyFetcher
RSS/Atom ─┘                    |                      |
    |                          v                      v
    v                    Raw HTML Response       Profile + Posts
Homepage Nav                   |                      |
    |                          v                      v
    v                    Save + Convert         Media Downloads
SiteOutline                    |                      |
    |                          v                      v
    v                   MarkdownSites/        ScrapedSites/instagram/
Queue Seeding
    |
    v
BFS Crawl Loop
    |
    +──> Prefetch Buffer (asyncio tasks, -c concurrent)
    |
    +──> Fetch (HTTP with semaphore / Browser)
    |
    +──> Error Detection ────────────> Skip + Track in BrokenLinkTracker
    |
    +──> Date Classification ────────> Skip old articles / Create section batches
    |
    +──> Asset Discovery ────────────> Concurrent Download (httpx, semaphore)
    |
    +──> URL Rewriting ──────────────> Relative Paths for offline viewing
    |
    +──> Link Extraction ────────────> Queue (section batch or regular deque)
    |
    v
Write to Disk (url_to_filepath with numeric flattening)
    |
    +──> Inline Markdown Conversion (default on, --no-markdown to disable)
    |      Section batch articles → article_filepath_in_section() → section dir
    |
    +──> Cache Update (default on, --no-incremental to disable)
    |
    v
Post-loop: Generate section index.md + copy section images
    |
    v
ScrapedSites/<domain>/     MarkdownSites/<domain>/
    site_outline.json           index.md
    index.html                  about.md
    section/page.html           section/
    images/                         index.md (generated, article listings)
    css/ js/ fonts/                 images/ (section-specific)
    media/                          article-slug.md
    broken_links.txt                sub-section/
    .scrapepy_cache.json                index.md
                                        images/
                                        article.md
                                images/ (global, copied)
                                media/ (copied)
```
