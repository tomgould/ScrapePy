# ScrapePy Roadmap

## Phase 1: Foundation (Complete)

- [x] **ScrapeConfig dataclass** (128 lines) — 24 CLI option fields, 7 derived fields (`base_url`, `domain`, `starting_path`, `starting_hash`, `output_dir`, `output_dir_name`, `cutoff_date`), 3 computed properties (`wait_time`, `needs_browser`, `has_date_filter`). Helper functions: `_parse_recent()` for duration strings, `_parse_since()` for absolute dates.
- [x] **CLI** (196 lines) — argparse with 3 argument groups (date filtering, audit, Instagram), validation rules (URL or --accounts required, --recent/--since mutually exclusive, no date filters with --instagram), `main()` entry point with `asyncio.run()`
- [x] **URL processing** (249 lines) — `SKIP_EXTENSIONS` regex (24 extensions), `_NON_SECTION_PATHS` regex, `is_in_scope()` (prefix match + wildcard), `normalize_link()`, `extract_links_from_html()` (regex-based), `is_likely_article_path()`, `discover_section_pages()`, `classify_asset_url()` (images/css/js/fonts/media categories, filename truncation with MD5)
- [x] **File output** (118 lines) — `create_output_dirs()`, `url_to_filepath()` with numeric segment flattening, `get_relative_path()`, `write_file()` (string/bytes), `ensure_parent_dir()`
- [x] **Scraper dispatch** (79 lines) — Mode router: audit → instagram → swagger/docs/single_page/crawl. Crawl returns early (handles markdown inline). Other modes run `_run_markdown_conversion()` with OpenAPI spec fallback.
- [x] **Project scaffolding** — `main.py` (3 lines), `pyproject.toml` (version 0.1.0, requires-python >=3.11), `requirements.txt` (7 packages), `.gitignore`

## Phase 2: Single-Page Mode (Complete)

- [x] **Fetcher strategy** (87 lines) — `fetch_page_http()` via `Fetcher.get()` classmethod (stealthy_headers, follow_redirects, timeout=30); `fetch_page_browser()` via `StealthyFetcher.async_fetch()` (network_idle, solve_cloudflare, timeout=120000, wait=int(...), user_data_dir); `download_asset()` via httpx (Chrome UA, follow_redirects, timeout=30.0)
- [x] **Browser actions** (264 lines) — `scroll_full_page()`, `wait_for_js_content()`, `extract_links_browser()`, `expand_swagger_sections()`, `find_spec_url()`, `expand_docs_sections()`, `find_nav_links()`, `get_main_content_html()`
- [x] **SPA mode** (38 lines) — Full single-page mode: fetch via browser, scroll, wait for lazy content, save `index.html`
- [x] **Asset downloading** — `download_asset()` via httpx with `asyncio.Semaphore` for concurrency control

## Phase 3: Multi-Page Crawl (Complete)

- [x] **Error detection** (39 lines) — `is_error_page_html()` (fast string check), `is_error_page_content()` (rendered text patterns), `is_cloudflare_challenge()` ("Verifying you are human")
- [x] **Asset pipeline** (117 lines) — `discover_and_download_assets()` with regex URL extraction (`<img src/srcset>`, `<link stylesheet>`, `<script src>`, CSS url()), concurrent downloads via httpx semaphore, conditional headers for incremental mode (304 handling)
- [x] **URL rewriting** (174 lines) — `rewrite_urls()` for offline viewing: href/src attributes, srcset, CSS `url()`, absolute same-domain URLs, relative path calculation. Out-of-scope links (--stay-in-path) preserved as full live URLs.
- [x] **Broken link tracking** (37 lines) — `BrokenLinkTracker` with `add()`, `write_report()` (timestamped, sorted)
- [x] **BFS crawl loop** (940 lines) — Section batch priority queue, prefetch buffer (asyncio tasks), visited/hash dedup, HTTP + browser fetch, error detection (HTTP status + content + Cloudflare), hash-based SPA support, inline markdown conversion, comprehensive summary stats

## Phase 4: Specialized Modes (Complete)

- [x] **Swagger/OpenAPI** (89 lines) — `expand_swagger_sections()` with multi-strategy endpoint expansion (opblocks, models, toggles), `find_spec_url()` to locate spec (meta tags, scripts, inputs), spec file download via `page.evaluate()` fetch
- [x] **Documentation** (184 lines) — `expand_docs_sections()`, `find_nav_links()`, `get_main_content_html()`, per-page fetching with combined document generation

## Phase 5: Markdown Conversion (Complete)

- [x] **HTML-to-Markdown** (802 lines) — `HTMLToMarkdown` class with 7-step extraction chain: error detection → API docs (Readme.io/Stoplight) → Swagger UI → RoboHelp/MadCap → Standard CMS (10 CSS selectors) → trafilatura fallback → generic. YAML frontmatter (title, url, date, date_source, page_type, word_count, description, domain). Post-processing: clean_markdown(), promote_bold_headings(), title safety, convert_links(.html→.md), copy_assets(images/ + media/). Pre-skip: XML feeds, manifests, WordPress boilerplate.
- [x] **OpenAPI-to-Markdown** (266 lines) — `convert_openapi_file()`: JSON/YAML loading, `$ref` resolution, `schema_to_string()` recursive rendering, endpoints grouped by tag with parameters/request bodies/responses, schema property tables

## Phase 6: Incremental & Caching (Complete)

- [x] **CrawlCache** (132 lines) — `.scrapepy_cache.json` with version tracking. Per-page: ETag, Last-Modified, SHA-256 content hash, links (for 304 replay), timestamp, published_date, page_type. Per-asset: ETag, Last-Modified, timestamp.
- [x] **Conditional requests** — `get_page_headers()` builds `If-None-Match` / `If-Modified-Since` headers for 304 skipping
- [x] **Content hash fallback** — `is_page_unchanged()` compares SHA-256 when server doesn't support conditional headers
- [x] **Asset caching** — Separate cache entries for assets with conditional header tracking and 304 handling

## Phase 7: Additional Core Features (Complete)

- [x] **Cloudflare bypass** — `StealthyFetcher` with `solve_cloudflare=True`, `is_cloudflare_challenge()` detection with 5s retry sleep
- [x] **Proxy support** — `config.proxy` passed to `Fetcher.get()`, `StealthyFetcher.async_fetch()`, and `httpx.AsyncClient()`
- [x] **Concurrency control** — `config.concurrent` (default 5) via `asyncio.Semaphore` for asset downloads and page prefetching
- [x] **Headless mode** — `--headless` flag for all browser modes

---

## Phase 8: Instagram Mode (Complete)

### Core Infrastructure

- [x] **Config** — `ScrapeConfig` fields: `instagram`, `accounts_file`, `ig_login`, `ig_max_posts`, `ig_update_meta`
- [x] **CLI** — `--instagram`, `--accounts`, `--ig-login`, `--ig-max-posts`, `--update-meta` flags; `url` optional when `--accounts` used; validation: --accounts requires --instagram, --recent/--since not with --instagram
- [x] **Scraper dispatch** — Instagram mode dispatched before `create_output_dirs` in `scraper.py`
- [x] **Fetcher** — `user_data_dir` parameter on `fetch_page_browser()` for persistent cookies via Patchright's `launch_persistent_context`

### Profile Scraping (474 lines total in instagram.py)

- [x] **Profile page fetch** — Stealth browser via `StealthyFetcher.async_fetch()` with cookie persistence
- [x] **Profile data extraction** (721 lines in instagram_actions.py) — 3-tier: embedded JSON (ScheduledServerJS `<script type="application/json">` payloads) > meta tags (og:description, og:image) > DOM; 14 fields (username, full_name, bio, followers, following, post_count, profile_pic_url, is_private, is_verified, category, external_url, user_id, etc.)
- [x] **Save profile.json** — Structured JSON with scraped_posts_count
- [x] **Save profile.md** (294 lines in instagram_to_markdown.py) — Markdown summary with stats table (posts/followers/following), bio, website link

### Post Discovery & Extraction

- [x] **Dismiss popups** — Cookie banners, "Not Now" dialogs, login prompts
- [x] **Infinite scroll** — Grid scrolling with stability detection (3 stable rounds), 1.5s intervals, 120s timeout
- [x] **Post link extraction** — All `/p/` and `/reel/` URLs from grid
- [x] **Max posts limit** — `--ig-max-posts` caps posts scraped
- [x] **Embedded JSON parsing** — Parse ScheduledServerJS payloads from `<script type="application/json">`
- [x] **Meta tag fallback** — `og:description`, `og:title`, `og:image` for missing fields
- [x] **40+ fields extracted** — Identifiers (shortcode, post_id), content (caption, alt_text, hashtags, mentions), engagement (likes, comments, views, plays), media (image_versions2, video_versions, carousel), video metadata (duration, has_audio), audio/music info, location, author, collaboration/coauthors, usertags, preview comments
- [x] **Carousel handling** — `navigate_carousel()` click-through navigation collecting all slide media URLs
- [x] **Video interception** — `setup_video_interceptor()` network response listener for CDN URLs
- [x] **Video playback trigger** — `trigger_video_playback()` force play to emit CDN request
- [x] **Comments extraction** — Preview comments with username, text, likes, timestamp, id

### Media Downloads

- [x] **Image downloads** — JPG/PNG/WebP from image_versions2
- [x] **Video downloads** — MP4 from video_versions + CDN interceptor
- [x] **Carousel media** — All slides (mixed image/video)
- [x] **Video poster/thumbnail** — Downloaded as fallback image
- [x] **Blob URL filtering** — Skip undownloadable `blob:` URLs
- [x] **Extension guessing** — `_guess_extension()` from URL path or media type

### Output Structure

- [x] **Per-account directories** — `ScrapedSites/instagram/{username}/`
- [x] **Date-prefixed post dirs** — `YYYY-MM-DD_{shortcode}/`
- [x] **post.html, post.json, post.md, media/**

### Instagram Markdown (294 lines)

- [x] **Post markdown** — Date, author (verified badge), co-authors, paid partnership, location, engagement stats, content type, video metadata (duration, has_audio), music/audio info, post IDs, caption (edited flag), alt text, hashtags, mentions, tagged users, media embeds, comments (with timestamps), link
- [x] **Profile markdown** — Name, verified badge, private badge, category, user ID, stats table (posts/followers/following), bio, website, link

### Multi-Account

- [x] **Accounts file** — One per line, URLs / `@username` / bare usernames, `#` comments
- [x] **URL + file combo** — Both `--accounts` and URL can be used together
- [x] **Username normalization** — `_normalize_username()`: extract from full URLs, strip `@` prefix
- [x] **5s delay between accounts**

### Login & Cookies

- [x] **Manual login flow** — Browser opens visibly, navigates to login page, `wait_for_login()` polls for completion (3 min timeout)
- [x] **Cookie persistence** — `~/.scrapepy/instagram_profile/` via Patchright `launch_persistent_context()`
- [x] **Cookie reuse** — Subsequent runs skip login when cookies exist
- [x] **Private profile detection** — "This account is private" with login prompt

### Rate Limiting

- [x] **Post fetch delay** — 2-5s deterministic jitter (MD5 hash of shortcode for reproducibility)
- [x] **Scroll intervals** — 1.5s between scroll events
- [x] **Media download delay** — 0.5s between files within a post
- [x] **Account delay** — 5s between accounts

### Incremental & Update Modes

- [x] **Incremental (`-i`)** — `_get_existing_shortcodes()` scans existing post dirs for `post.json` files, skips already-scraped (does not use CrawlCache)
- [x] **Update metadata (`--update-meta`)** — Re-scrape posts for fresh engagement data, `_get_existing_media()` skips re-downloading existing media files
- [x] **Mode precedence** — `--update-meta` overrides `-i`

### Error Handling

- [x] **Profile not found** — "Sorry, this page isn't available" detection
- [x] **Private profiles** — Detection with login prompt guidance
- [x] **Failed media downloads** — Per-file error handling, continues to next
- [x] **Shortcode fallback** — `_shortcode_from_url()` extracts from URL if page extraction fails

---

## Phase 9: Recent Content Filtering (Complete)

### CLI & Config

- [x] **`--recent` flag** — Human-friendly duration: `2w`, `1d`, `6h` (weeks, days, hours) → `_parse_recent()` → `cutoff_date`
- [x] **`--since` flag** — Absolute date cutoff: `YYYY-MM-DD` (+ ISO datetime + timezone formats) → `_parse_since()` → `cutoff_date`
- [x] **`--min-articles N`** — Per-section minimum, enforced independently per section batch
- [x] **`--max-articles N`** — Per-section maximum, applied at batch creation (truncation) and processing (stop batch)

### Page Classification (110 lines)

- [x] **Section page detection** — `_SECTION_URL_PATTERNS` regex (homepage, category, tag, topic, archive, author, pagination, section roots, feed) + link density > 0.4 with no date signals
- [x] **Article page detection** — `_ARTICLE_URL_PATTERNS` regex (date patterns) + date signals from URL or HTML + link density < 0.5
- [x] **PageType enum** — `SECTION`, `ARTICLE`, `UNKNOWN`; unknown treated conservatively as fresh article

### Date Detection (211 lines)

Multi-signal approach with confidence ranking (`DateSource` IntEnum):

- [x] **META_TAG (5)** — 8 property names: `article:published_time`, `og:article:published_time`, `datePublished`, `date`, `publish_date`, `article:published`, `sailthru.date`, `parsely-pub-date`
- [x] **JSON_LD (4)** — `datePublished`, `dateCreated` in JSON-LD objects, including `@graph` array traversal
- [x] **URL_PATTERN (3)** — 3 regex patterns: `/YYYY/MM/DD/`, `/YYYY-MM-DD-`, `/YYYYMMDD-`
- [x] **TIME_ELEMENT (2)** — `<time datetime="...">` elements
- [x] **HTTP_HEADER (1)** — `Last-Modified` header (RFC 2822 format)
- [x] **`get_best_date()`** — Combines all signals, returns `max(candidates, key=source.value)`
- [x] **`url_has_old_date()`** — Quick pre-filter using only URL patterns (no HTML needed)

### Pre-Crawl Discovery (319 lines)

- [x] **`run_discovery()`** — Orchestrator: sitemap.xml + sub-sitemaps (cap 20 per level) + robots.txt sitemaps + RSS/Atom feeds (cap 10) + 7 common feed paths
- [x] **DiscoveryMap** — `dict[str, datetime]` type alias mapping URL path → publish date
- [x] **Homepage HTML reuse** — Homepage fetched during discovery is reused for nav parsing (saves one HTTP request)
- [x] **Silent error handling** — Missing sitemaps/feeds are normal, all errors caught

### Navigation Parsing (218 lines)

- [x] **`extract_navigation()`** — 4-tier: header → standalone nav → class-matched → footer
- [x] **NavLink dataclass** — label, path, parent_label, level, source
- [x] **Hierarchy detection** — `<ul>/<li>` nesting for parent→children relationships
- [x] **Filtering** — External links, anchors, JavaScript, non-content paths (login, cart, admin), file extensions

### Outline Building (163 lines)

- [x] **NavSection dataclass** — label, path, children, source, article_count, recent_count
- [x] **SiteOutline dataclass** — Full site structure with JSON serialization and file I/O
- [x] **`_assign_url_to_section()`** — Longest-prefix match against section paths + children
- [x] **Article counting** — Per-section article_count and recent_count from discovery map

### Crawl Integration

- [x] **Pre-filter from discovery** — Skip enqueueing URLs that are known-old from sitemap/RSS dates
- [x] **URL date pre-filter** — Skip URLs with old date patterns without fetching
- [x] **Post-fetch classification** — `classify_page()` on fetched HTML
- [x] **Section pages: always keep** — Save and follow all links regardless of date
- [x] **Root page behavior** — Filters out article-slug links, only queues section/nav links
- [x] **Section-ordered traversal** — Split article links (slug detection: >= 15 chars with hyphens, or >= 2 hyphens) into DOM-ordered batch
- [x] **Concurrent page prefetching** — Prefetch upcoming section batch articles using asyncio tasks with `-c` concurrency
- [x] **Section cutoff** — When old article found in batch, discard remaining (articles newest-first), cancel prefetch tasks
- [x] **Asset optimization** — Section pages + min-articles filler skip asset downloads
- [x] **Min-articles per section** — Per-section independent tracking via `section_article_counts` dict
- [x] **Max-articles per section** — Applied at batch creation (truncation) + processing (stop + cancel)
- [x] **Stats reporting** — Sections kept, articles kept/skipped, pre-crawl/section cutoff skips, min-articles kept

### Compatibility

- [x] **Works with `--incremental`** — `--incremental --recent 1d` for daily re-scans
- [x] **Works with `-m`** — Markdown conversion inline during crawl
- [x] **Works with `-j`** — JS-rendered pages get classified and date-checked after rendering
- [x] **Works with `-s`** — Stay-in-path scope applied before date filtering
- [x] **Works with `-1`** — Single page mode: warning printed that date filter has no effect
- [x] **Works with `-d`** — Docs mode: warning printed that date filter has no effect

---

## Phase 10: Audit Mode (Complete)

- [x] **`--audit` flag** — Reconnaissance only, no crawl
- [x] **3-phase process** (360 lines) — Recon (identical to crawl Phase 0) → Sample pages (homepage + 3 sections + 3 articles) → Save fixtures
- [x] **Test fixtures** — `tests/fixtures/<domain>/` with `audit_report.json`, `test_config.json`, `pages/*.html`, `meta/robots.txt`
- [x] **Committable expected values** — `test_config.json` with section labels, page types, dates (no site content)
- [x] **Fixture-driven tests** (199 lines) — `test_audit_fixtures.py` with dynamic test generation from test_config.json

---

## Phase 11: Tests (Complete)

- [x] **test_navigation.py** (322 lines, 20 tests) — Hierarchy, sources, filtering, URL resolution, label cleaning, edge cases
- [x] **test_outline.py** (246 lines, 14 tests) — Section assignment, article counts, cutoff dates, serialization, file I/O
- [x] **test_discovery.py** (58 lines, 8 tests) — robots.txt Sitemap: parsing
- [x] **test_crawl_helpers.py** (129 lines, 18 tests) — Article slug detection, article path detection, section link splitting, max_articles
- [x] **test_audit_fixtures.py** (199 lines, dynamic) — Fixture-driven from --audit runs
- [x] **test_writer.py** (120 lines, 21 tests) — Numeric segment flattening, url_to_filepath integration
- [x] **test_frontmatter.py** (273 lines, 25 tests) — Frontmatter generation, word count, metadata extraction, convert_file integration

**Phase 11 total: 1,347 test lines, 121 tests, all pure logic (no network I/O), ~0.35s runtime**
**After Phase 15: 404 tests across 14 test files, ~0.48s runtime**

---

## Documentation (Complete)

- [x] **README.md** — Full flags table, all examples, output structure, modes, dependencies, project stats
- [x] **CLAUDE.md** — Project structure with line counts, architecture patterns, key conventions, full module API reference
- [x] **Architecture.md** — Flow diagrams for all modes, package responsibilities with function signatures, data flow diagram, test coverage table
- [x] **Examples.md** — All modes with detailed examples: crawl, scoped, JS render, SPA, Swagger, docs, incremental, recon, date filtering, audit, Instagram (all variants), proxy/stealth, combined flags, programmatic usage
- [x] **Setup.md** — Installation steps, dependency table, browser setup, package installation, proxy config, Instagram login, output directories, troubleshooting (10 scenarios)
- [x] **Roadmap.md** — This file: phased development history with line counts, completion status

---

## Phase 12: Safety & Correctness (Complete)

> From comprehensive code review (Feb 2026). 5 specialized agents reviewed all 34 source files.

### 12a. Security Fixes

- [x] **Path traversal protection in `url_to_filepath()`** — Added `os.path.normpath` + prefix check ensuring resolved path stays within `output_dir`. Hash-derived filenames sanitized to strip path separators.
- [x] **HTML injection in docs mode** — Added `html.escape()` for title/nav text in per-page and combined document HTML generation.

### 12b. Error Handling Overhaul

- [x] **Stop fabricating HTTP status codes** — Fetch functions now return status `0` with `{"error": "ExceptionType: message"}` instead of fake 500. Caller shows actual error. Prefetch catches `CancelledError` separately.
- [x] **Narrow the crawl loop catch-all** — Added `KeyboardInterrupt` handler (clean break). Error messages now include exception type. Verbose mode prints full traceback. Orphaned prefetch tasks cancelled on exit.
- [x] **Fix silent asset download failures** — `download_asset()` now logs error type in verbose mode and returns error info dict instead of bare `(None, 0, {})`.
- [x] **Add error handling to swagger/single-page/docs initial fetch** — All three modes now wrap `fetch_page_browser()` with try/except, printing helpful error message with browser install hint.
- [x] **Cache save error handling** — `save()` now catches `OSError` and `TypeError`, warns instead of crashing.
- [x] **Cache load warning on corruption** — `load()` now prints specific warnings for corrupt JSON, version mismatch, and permission errors.
- [x] **Discovery phase verbose logging** — `_fetch_url()` now logs fetch failures with exception type when `--verbose` is enabled.
- [x] **Markdown conversion error context** — `convert_file()` and `_run_markdown_conversion()` now include exception type in error messages. Verbose mode prints traceback. Trafilatura/generic fallback re-raise `ImportError` and `MemoryError`.
- [x] **Audit mode file write handling** — Phase 3 report/config saves wrapped in try/except with clean error messages.

### 12c. Variable Scoping & Correctness

- [x] **Fix `date_result` scoping bug** — `date_result` now initialized to `None` at the top of each loop iteration alongside `page_type_str` and `pub_date_str`.
- [x] **Fix `_queue_links` type annotation** — Changed `to_visit: list[str]` to `to_visit: deque[str]`.
- [x] **Replace deprecated `asyncio.get_event_loop()`** — Changed to `asyncio.get_running_loop()`.

---

## Phase 13: Performance Optimization (Code Review) — Complete

### 13a. I/O Parallelization

- [x] **Parallelize discovery phase** — Converted `run_discovery()` to async. Sitemap.xml + homepage fetched concurrently via `asyncio.gather()`. All sub-sitemaps fetched in parallel (level 1 + level 2). All feeds fetched in parallel. Added `_fetch_url_async()` thread-pool wrapper. Updated callers in crawl.py and audit.py to `await`.
- [x] **Persistent httpx client for asset downloads** — Added `create_asset_client(config)` factory in `fetcher.py`. `download_asset()` accepts optional `client` param for connection reuse. Crawl loop creates one `httpx.AsyncClient` for entire session, passed through `discover_and_download_assets()` → `_download_one()` → `download_asset()`. Closed after crawl loop exits.

### 13b. Redundant Parsing Elimination

- [x] **Eliminate redundant soup in `to_markdown.py`** — `extract_content()` now conditionally re-parses: only creates fresh soup for `extract_standard_content()` if an earlier extractor (API docs, Swagger, RoboHelp) was invoked and mutated the original. Non-mutating paths reuse the original soup.
- [x] **Reuse soup for description extraction** — `_extract_description()` now accepts optional `soup` param to skip re-parsing when caller already has one.
- _Deferred:_ Cross-module soup caching (crawl.py + classifier.py + dates.py) — would require significant API changes across the pipeline for moderate gain; not worth the complexity now.

### 13c. Algorithm & Data Structure Fixes

- [x] **Use `deque` for section batch URLs** — Section batches now use `deque[str]` instead of `list[str]`. `pop(0)` → `popleft()` for O(1) dequeue. Updated type annotations on `section_batches`, `current_section_batch`, `_exhaust_section_batch`.
- [x] **Precompile hot-path regexes** — Added module-level compiled patterns in `crawl.py` (`_RE_HREF`, `_RE_DATA_HREF`, `_RE_UI_ELEMENT_ID`, `_RE_RSS_FEED`) and `assets.py` (`_RE_IMG_SRC`, `_RE_IMG_SRCSET`, `_RE_LINK_STYLESHEET`, `_RE_LINK_CSS`, `_RE_SCRIPT_SRC`, `_RE_CSS_URL`). All hot-path functions updated to use precompiled patterns.
- _Already done in Phase 12:_ Cancel orphaned prefetch tasks on crawl exit — cleanup added in Phase 12 Task 4.

### 13d. Low-Priority Optimizations

- [x] **Remove JSON indentation in cache** — `cache.py` now uses `separators=(",",":")` for compact JSON, reducing cache file size ~2-3x.
- [x] **Generator-based file iteration in markdown converter** — `convert_all()` now uses `itertools.chain` with generator expression for lazy filtering instead of materializing two separate lists.

---

## Phase 14: Code Quality & DRY (Code Review) — Complete

### 14a. Deduplicate Shared Logic

- [x] **Deduplicate `_has_article_slug()`** — Moved to `processing/urls.py` as `has_article_slug()`. Removed duplicate definitions from `crawl.py` and `audit.py`, both now import from `urls.py`. Updated test imports in `test_crawl_helpers.py`.
- [x] **Deduplicate `_NON_CONTENT_PATHS` regex** — Added `NON_CONTENT_PATHS` to `processing/urls.py` as public constant. Removed duplicate definitions from `crawl.py` and `navigation.py`, both now import from `urls.py`.
- _Deferred:_ Unify link normalization — 4 implementations have subtly different behavior needed for their contexts (crawl queuing vs. section discovery vs. general normalization). Consolidating would risk breaking each caller's edge cases.
- [x] **Unify error page detection** — `to_markdown.py:is_error_page()` now delegates to `processing/errors.py` functions (`is_error_page_html` + `is_error_page_content`). Added missing patterns ("page not found", "file not found", "the page you requested", "error") to `errors.py` `ERROR_PATTERNS`.

### 14b. Type Safety

- [x] **Case-insensitive HTTP header access** — Added `_get_header()` helper in `cache.py` with case-insensitive fallback scan. Replaced all `headers.get("etag") or headers.get("ETag")` patterns in `update_page()` and `update_asset()`.
- [x] **Use `hashlib.sha256` instead of MD5 for Instagram jitter** — Changed `hashlib.md5()` → `hashlib.sha256()` in `instagram.py` for FIPS-compliant environments.

### 14c. Code Hygiene

- [x] **Add timeout to `scroll_full_page()` JS** — Added 60-second safety timeout to the `setInterval` scroll loop in `browser_actions.py`. Prevents infinite scroll pages from running forever.
- [x] **Consolidate inline imports** — Moved `from bs4 import BeautifulSoup` to module level in `crawl.py`, `classifier.py`, and `dates.py`. These modules already load bs4 transitively, so no startup cost change.

---

## Phase 15: Test Coverage Expansion (Code Review) — Complete

> Grew from 121 tests to 404 tests (+283). All pure logic, no network I/O, ~0.48s runtime.

### 15a. Critical Gap Tests

- [x] **`processing/dates.py` tests** (34 tests in `test_dates.py`) — ISO/RFC2822 parsing, URL date extraction (slash/hyphen/compact formats), HTML date extraction (meta tags, JSON-LD, JSON-LD @graph, time elements), header date extraction, best-date priority ranking, url_has_old_date pre-filter, edge cases (empty, garbage, whitespace).
- [x] **`processing/errors.py` tests** (27 tests in `test_errors.py`) — `is_error_page_html()` (NoSuchKey, AccessDenied, >404<, >Not Found<, specified key + length threshold), `is_error_page_content()` (title/h1/body patterns, short vs long body, case insensitive), `is_cloudflare_challenge()`.
- [x] **`processing/urls.py` tests** (78 tests in `test_urls.py`) — `is_in_scope()` (exact/child/outside/wildcard/partial prefix), `normalize_link()` (absolute/relative/external/extensions/mailto/data/query/fragment/dot-dot), `extract_links_from_html()` (href/data-href/data-url/data-link/dedup), `has_article_slug()`, `is_likely_article_path()` (3+ hyphens/long slug/CMS ID/section names), `discover_section_pages()` (filtering/sorting/dedup), `classify_asset_url()` (images/css/js/fonts/media/truncation/protocol-relative), `NON_CONTENT_PATHS` regex, `SKIP_EXTENSIONS` regex.
- [x] **`processing/classifier.py` tests** (27 tests in `test_classifier.py`) — `is_section_url()` (14 parametrized section paths, 3 non-section), `_compute_link_density()` (all-links/no-links/mixed/empty body/no body/nav excluded/short text), `classify_page()` (homepage/category/date URL patterns/unknown/high link density/meta date/section roots/two-segment).
- [x] **`processing/rewriter.py` tests** (18 tests in `test_rewriter.py`) — `rewrite_urls()` for href (internal/external/css/mailto/anchor/pdf), src (image/script/video/external), srcset (single/multiple), CSS url() (background image/non-image), absolute same-domain URLs, stay-in-path scope (out-of-scope → live URL, in-scope → rewritten).

### 15b. Important Gap Tests

- [x] **`config.py` tests** (34 tests in `test_config.py`) — `_parse_recent()` (weeks/days/hours/whitespace/case/invalid unit/invalid format/empty), `_parse_since()` (date/datetime/tz/whitespace/invalid/empty), `ScrapeConfig` derived fields (base_url/domain/starting_path/hash/output_dir/cutoff/instagram), properties (wait_time/needs_browser/has_date_filter).
- [x] **`output/cache.py` tests** (23 tests in `test_cache.py`) — `_get_header()` (lowercase/mixed case/missing/prefers lowercase), page ops (headers/content hash/links/date/type), asset ops (has_asset/update/headers), persistence (save+load roundtrip/missing file/corrupt file/wrong version/compact JSON format).
- [x] **`processing/discovery.py` tests** (27 tests in `test_discovery_parsers.py`) — `_parse_sitemap()` (URLs/index/no lastmod/external/malformed/empty), `_parse_rss()` (items/no date/external/empty/malformed), `_parse_atom()` (entries/updated fallback/relative links/empty/malformed), `_discover_feeds_from_html()` (RSS/Atom/absolute/non-feed), `parse_robots_sitemaps()` (single/multiple/case/empty/whitespace).
- _Deferred:_ `cli.py` `parse_args()` tests — Requires mocking `sys.argv` and capturing `SystemExit`; lower ROI than pure-logic tests.
- _Deferred:_ `convert/openapi_to_markdown.py` tests — Recursive schema rendering; useful but lower priority than core crawl path tests.

---

## Phase 16: Documentation Sync (Code Review) ✅

> All 5 documentation files updated to reflect current codebase after Phases 12–15.

- [x] **Update README.md** — Updated total lines (9,841), source (6,982), tests (2,859), test count (404), file count (49), test description expanded to cover all 15 test files, project stats section refreshed
- [x] **Update Architecture.md** — Updated ASCII art diagram with all current line counts, total (9,841), test table rewritten from 7 rows to 15 rows, urls.py section expanded with `has_article_slug()`/`is_likely_article_path()`/`discover_section_pages()`, to_markdown.py line count updated
- [x] **Update Setup.md** — Updated test count (404), runtime (~0.5s)
- [x] **Update Roadmap.md** — Phase 15 marked complete with detailed descriptions; Phase 11 totals updated
- [x] **Update CLAUDE.md** — Updated codebase size (9,841 across 49 files), project structure tree (all line counts for engine/, processing/, output/, convert/), tests section rewritten with all 15 test files, all 17 API reference section headers updated with current line counts

---

## Backlog: Future Enhancements

> Ideas for future development. No timeline or priority assigned.

- [ ] **`/instagram`** — Quick-run Instagram scrape from conversation (specify account + options inline)
- [ ] **`/instagram-monitor`** — Run daily monitoring workflow: update-meta on accounts file, summarize changes
- [ ] **`/instagram-report`** — Generate engagement report from scraped data (compare metrics across runs)
- [ ] **`/instagram-export`** — Export scraped data to CSV/JSON for analytics tools
- [ ] **Stories support** — Scrape Instagram Stories (requires login, ephemeral content)
- [ ] **Highlights support** — Scrape saved Story Highlights from profiles
- [ ] **Full comments** — Click "View all X comments" to get complete comment threads (not just preview)
- [ ] **Engagement history** — Track likes/views/comments over time across `--update-meta` runs (append to `history.json`)
- [ ] **Custom selectors** — User-defined CSS selectors for content extraction
- [ ] **Output formats** — JSON output option alongside HTML/Markdown
- [ ] **Progress bar** — Real-time crawl progress indicator
- [ ] **Retry logic** — Configurable retry count for failed fetches
- [ ] **Robots.txt compliance** — Optional respect for `Disallow:` directives
- [ ] **Logging module** — Replace `print()` with `logging` for log levels, file output, and verbose stack traces
