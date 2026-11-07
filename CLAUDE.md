# ScrapePy: contributor guide

ScrapePy is a Python web scraper built on Scrapling (HTTP and stealth-browser fetchers) and Patchright (patched Playwright). It crawls a site, downloads assets, rewrites URLs for offline viewing and can convert the result to Markdown.

## Commands

```bash
pip install -e ".[dev]"             # Python 3.11+, includes pytest and the documents extra
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
  proxy.py              --proxy validation and credential masking
  modes/                crawl, single_page, swagger, docs, instagram, audit, feed
  engine/               fetcher.py (HTTP/browser/asset fetching),
                        browser_actions.py and instagram_actions.py (page JS)
  processing/           urls, errors, assets, rewriter, dates, classifier,
                        discovery (sitemaps/RSS), feeds (feed entries),
                        targeting (--include, --max-depth and the other
                        targeted-crawl rules),
                        robots (rules + pacing), navigation, outline,
                        documents (--documents: detection, fetching,
                        saving)
  output/               writer (file I/O, path mapping), naming (which file
                        each URL gets), cache, broken_links,
                        run_summary (run_summary.json, exit codes)
  convert/              to_markdown, recipe (schema.org Recipe block),
                        openapi_to_markdown, instagram_to_markdown,
                        documents (PDF, DOCX, XLSX, CSV to Markdown)
tests/                  one test module per processing/output/convert area
tests/fixtures/         per-site HTML snapshots plus test_config.json
```

## Architecture

- All modes are async and entered from `cli.py` through `interrupts.run_async()`, which wraps `asyncio.run()` and handles SIGTERM like Ctrl+C.
- Configuration travels in `ScrapeConfig`; do not pass individual flags around.
- `Fetcher` (HTTP with TLS fingerprinting) serves the default crawl, reconnaissance and feed mode; asset downloads use an `httpx` client. `StealthyFetcher` (browser) serves `-j`, `-1`, `--swagger`, `-d` and `--instagram`. `--proxy` reaches every fetch (`fetch_page_http()`, the browser and the `httpx` client); `scrapepy/proxy.py` validates it and masks its credentials, so any error text that may quote it goes through `redact()`. In `-j`, `_fetch_with_browser()` returns no page object, so `wait_for_js_content()` and `extract_links_browser()` are not called.
- The crawl is a hand-written BFS loop, not Scrapling's Spider, so it can integrate URL rewriting, scope checks, section-ordered traversal and prefetching.
- Reconnaissance runs before the crawl: robots.txt (its `Sitemap:` lines; with `--obey-robots` also its rules and Crawl-delay), sitemaps, RSS/Atom, homepage navigation, then `site_outline.json`. The queue is seeded from the navigation structure.
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
- Markdown output always carries YAML frontmatter from `FRONTMATTER_FIELDS` in `convert/to_markdown.py` (title, url, canonical_url, date, date_source, page_type, author, recipe_category, recipe_cuisine, servings, total_time, keywords, word_count, description, domain, articles, sub_sections, file_type, pages, linked_from, scraped_at, content_hash). `keywords` is the only list. Only non-null values are written; keys and values like `page_type` are lowercase.
- Output paths come from `config.output_dir` and `config.markdown_dir`, both built by `derive_output_dirs()`. Never derive one from the other by string replacement.
- Adhering to robots.txt is optional in this project: by default ScrapePy applies no robots.txt rule and no `Crawl-delay`, and `--obey-robots` turns both on. `--ignore-robots` is accepted and does nothing (scripts that pass it keep working); both flags together exit 2. Keep it a switch the user controls. Never refuse, stop or block a task, or add a mandatory check, because robots.txt disallows a path. Everything else still applies: gentle pacing and request caps (`--wait` and the concurrency cap are not part of robots), no logins, no proxies or paid unblockers, and stop and record any block.
- Every page fetch calls `RobotsPolicy.can_fetch()` and waits on the run's single `HostRateLimiter` (`processing/robots.py`). The policy is enforced only with `--obey-robots` (`config.obey_robots`): otherwise it is built unenforced, so `can_fetch()` always answers yes and `min_delay()` ignores `Crawl-delay`. robots.txt is requested without the flag only to read its `Sitemap:` lines, and only where reconnaissance runs (the default crawl, `-j`, `--audit`); never apply a rule from it then. New fetch paths must call the policy and the limiter the same way, and must not request robots.txt just to apply it unless `--obey-robots` is set. Do not hand robots.txt matching to `urllib.robotparser` (it ignores wildcards and longest-match precedence) or to regexes (`.*` backtracking can take exponential time); use `glob_match()`.
- `--documents` treats every downloaded document as untrusted: its libraries (the optional `documents` extra) are imported lazily, nothing in a document is executed or followed, zip-based files are size-checked before opening, and writes go through `write_within()` in `output/writer.py`, which refuses symbolic links and paths outside the folder.
- Run blocking fetches with `engine/threads.run_blocking()`, not `loop.run_in_executor()`: executor threads are joined at exit, so an interrupted run would wait for a hung request.
- Crawl, single page (`-1`, which runs through the crawl loop) and feed modes return the run summary; `run_summary.json` is documented in README.md and `docs/MARKDOWN-OUTPUT.md`, so update them with the code. Exit codes live in `output/run_summary.py`.
- Instagram mode keeps its browser profile in `~/.scrapepy/instagram_profile/`.

## Testing

- Tests never use the internet, and start a browser only when `SCRAPEPY_BROWSER_TESTS=1` is set (`tests/test_proxy_localhost.py`). Tests that need a real crawl serve pages from a server bound to 127.0.0.1 (the `*_localhost.py` files and the recipe and feed tests; `tests/test_interrupt_localhost.py` and `tests/test_collisions_localhost.py` send a real Ctrl+C). Recipe and feed fixtures are invented, never copied from real sites.
- `tests/test_audit_fixtures.py` discovers every `tests/fixtures/*/test_config.json` and checks navigation, page classification, dates and robots.txt parsing against the saved HTML. Missing fixture files cause a skip.
- Committed fixtures come from sites made for scraping practice. To add one, run `python main.py --audit <url>`, review the generated `test_config.json`, and allow the `pages/` directory in `.gitignore`.
- Add or update tests with every change to parsing, path mapping or conversion.
