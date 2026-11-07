# ScrapePy: contributor guide

ScrapePy is a Python web scraper built on Scrapling (HTTP and stealth-browser fetchers) and Patchright (patched Playwright). It crawls a site, downloads assets, rewrites URLs for offline viewing and can convert the result to Markdown.

## Commands

```bash
pip install -e ".[dev]"             # Python 3.11+, includes pytest
scrapling install                   # browser, needed for browser modes only
python main.py <url> [options]      # or the installed `scrapepy` entry point
python main.py --audit <url> -v     # recon only, writes test fixtures
python -m pytest tests/ -v          # no internet; some tests use a server on 127.0.0.1
python -m pytest tests/test_navigation.py -v
```

Markdown conversion, incremental mode and asset download are on by default; the matching `--no-*` flags turn them off.

## Layout

```
main.py                 entry point
scrapepy/
  cli.py                argparse, builds ScrapeConfig
  config.py             ScrapeConfig dataclass and derived values
  scraper.py            dispatches to a mode, runs Markdown conversion
  modes/                crawl, single_page, swagger, docs, instagram, audit, feed
  engine/               fetcher.py (HTTP/browser/asset fetching),
                        browser_actions.py and instagram_actions.py (page JS)
  processing/           urls, errors, assets, rewriter, dates, classifier,
                        discovery (sitemaps/RSS), feeds (feed entries),
                        robots (rules + pacing), navigation, outline
  output/               writer (file I/O, path mapping), naming (which file
                        each URL gets), cache, broken_links,
                        run_summary (run_summary.json, exit codes)
  convert/              to_markdown, recipe (schema.org Recipe block),
                        openapi_to_markdown, instagram_to_markdown
tests/                  one test module per processing/output/convert area
tests/fixtures/         per-site HTML snapshots plus test_config.json
```

## Architecture

- All modes are async and entered from `cli.py` through `interrupts.run_async()`, which wraps `asyncio.run()` and handles SIGTERM like Ctrl+C.
- Configuration travels in `ScrapeConfig`; do not pass individual flags around.
- `Fetcher` (HTTP with TLS fingerprinting) serves the default crawl, reconnaissance and feed mode; asset downloads use an `httpx` client. `StealthyFetcher` (browser) serves `-j`, `-1`, `--swagger`, `-d` and `--instagram`. `--proxy` reaches the browser and the `httpx` client only; `fetch_page_http()` does not pass it. In `-j`, `_fetch_with_browser()` returns no page object, so `wait_for_js_content()` and `extract_links_browser()` are not called.
- The crawl is a hand-written BFS loop, not Scrapling's Spider, so it can integrate URL rewriting, scope checks, section-ordered traversal and prefetching.
- Reconnaissance runs before the crawl: robots.txt (rules, Crawl-delay, sitemaps), sitemaps, RSS/Atom, homepage navigation, then `site_outline.json`. The queue is seeded from the navigation structure.
- Section pages split their links into article batches (DOM order) and navigation links. With `--recent` or `--since`, articles are placed under the section that discovered them in the Markdown output, and each section gets a generated `_index.md`.
- Mega-menu and dropdown navigation is parsed in `processing/navigation.py`; `NavSection.sub_sections` forms a tree that `SiteOutline.all_section_paths()` flattens.
- Date filtering (`--recent`, `--since`) is conservative: unknown-date pages and section pages are always kept; only articles with a confirmed old date are skipped. Date source priority: meta tag, JSON-LD, URL pattern, `<time>` element, HTTP header.

## Conventions

- All file I/O goes through `output/writer.py`; URL handling lives in `processing/urls.py`.
- Keep browser JavaScript strings in `engine/`, not in mode files.
- Scrapling fetchers are classmethods: call `Fetcher.get()` and `StealthyFetcher.async_fetch()` on the class.
- Read bodies with `response.body.decode(response.encoding or "utf-8")` and status from `response.status`.
- The `wait` argument to `StealthyFetcher.async_fetch` must be an `int`; cast it.
- Normalise URL paths with `posixpath.normpath()` before fetching.
- `url_to_filepath()` flattens numeric path segments into the filename (`/news/12345/1/slug` becomes `news/slug-12345-1.html`).
- `docs/MARKDOWN-OUTPUT.md` is the output contract for third-party readers; change it with any change to file layout, frontmatter, the body conventions or run_summary.json.
- Frontmatter is built only by `build_frontmatter()` (yaml.safe_dump); never format YAML by hand.
- In `--markdown-only` mode nothing but pages is fetched and no HTML is written: the converter gets the HTML through `convert_file(..., html=...)`.
- Markdown output always carries YAML frontmatter from `FRONTMATTER_FIELDS` in `convert/to_markdown.py` (title, url, canonical_url, date, date_source, page_type, author, recipe_category, recipe_cuisine, servings, total_time, keywords, word_count, description, domain, articles, sub_sections, scraped_at, content_hash). `keywords` is the only list. Only non-null values are written; keys and values like `page_type` are lowercase.
- Output paths come from `config.output_dir` and `config.markdown_dir`, both built by `derive_output_dirs()`. Never derive one from the other by string replacement.
- Every page fetch checks `RobotsPolicy.can_fetch()` and waits on the run's single `HostRateLimiter` (`processing/robots.py`). New fetch paths must do the same. Do not hand robots.txt matching to `urllib.robotparser` (it ignores wildcards and longest-match precedence) or to regexes (`.*` backtracking can take exponential time); use `glob_match()`.
- Run blocking fetches with `engine/threads.run_blocking()`, not `loop.run_in_executor()`: executor threads are joined at exit, so an interrupted run would wait for a hung request.
- Crawl and feed modes return the run summary; `run_summary.json` is documented in README.md and `docs/MARKDOWN-OUTPUT.md`, so update them with the code. Exit codes live in `output/run_summary.py`.
- Instagram mode keeps its browser profile in `~/.scrapepy/instagram_profile/`.

## Testing

- Tests never use the internet or a browser. Tests that need a real crawl serve pages from a server bound to 127.0.0.1 (the `*_localhost.py` files and the recipe and feed tests; `tests/test_interrupt_localhost.py` sends a real Ctrl+C). Recipe and feed fixtures are invented, never copied from real sites.
- `tests/test_audit_fixtures.py` discovers every `tests/fixtures/*/test_config.json` and checks navigation, page classification, dates and robots.txt parsing against the saved HTML. Missing fixture files cause a skip.
- Committed fixtures come from sites made for scraping practice. To add one, run `python main.py --audit <url>`, review the generated `test_config.json`, and allow the `pages/` directory in `.gitignore`.
- Add or update tests with every change to parsing, path mapping or conversion.
