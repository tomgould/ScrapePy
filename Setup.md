# Setup Guide

## Requirements

- Python 3.11 or later (the tests run on 3.11, 3.12 and 3.13)
- pip

## Quick Start

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install the Patchright browser (required for browser modes)
scrapling install

# 3. Test with a simple crawl
python main.py https://example.com

# 4. Run tests to verify installation
python -m pytest tests/ -v
```

## Detailed Installation

### Step 1: Python Dependencies

```bash
pip install -r requirements.txt
```

This installs the 7 packages listed in `requirements.txt` (the same list as `pyproject.toml`):

| Package | Version | Purpose |
|---------|---------|---------|
| scrapling[fetchers] | >=0.4.0 | HTTP and stealth-browser fetchers (brings patchright and curl_cffi) |
| beautifulsoup4 | >=4.12.0 | HTML parsing |
| lxml | >=5.0.0 | HTML and XML parser behind BeautifulSoup (pages, sitemaps, feeds) |
| markdownify | >=0.13.0 | HTML-to-Markdown conversion |
| pyyaml | >=6.0 | YAML frontmatter and OpenAPI YAML specs |
| trafilatura | >=1.12.0 | Content extraction fallback (the converter still works without it) |
| httpx | >=0.27.0 | Async HTTP client for asset downloads |

### Step 2: Browser Installation

Browser modes (`-j`, `-1`, `--swagger`, `-d`, `--instagram`) require a Chromium browser:

```bash
scrapling install
```

This downloads Patchright's Chromium build into a platform-specific location managed by Patchright.

The default crawl, `--markdown-only` and feed mode use `Fetcher.get()` (curl_cffi) and need no browser, so you can skip this step if you only use them.

### Step 3: Verify Installation

```bash
# Should print the help text without errors
python main.py --help

# Quick test with HTTP-only fetcher (no browser needed)
python main.py https://example.com

# Test browser mode (requires Step 2)
python main.py https://example.com -1

# Run the tests (no internet access needed, a minute or two)
python -m pytest tests/ -q
```

## Installing as a Package

For system-wide or venv installation:

```bash
pip install -e .
```

This registers the `scrapepy` command via the `[project.scripts]` entry in `pyproject.toml`:

```bash
scrapepy https://example.com -m
```

`scrapepy --version` prints the installed version. For development, install the test dependencies too:

```bash
pip install -e ".[dev]"
```

## Proxy Configuration

If you're behind a corporate proxy:

```bash
python main.py https://example.com --proxy http://proxy.corp:8080

# With authentication
python main.py https://example.com --proxy http://user:pass@proxy.corp:8080
```

The proxy is used by every request: page fetches with `Fetcher.get()` (the default crawl, reconnaissance, robots.txt, sitemaps and feed mode), browser modes (`StealthyFetcher.async_fetch()`) and asset downloads (`httpx.AsyncClient`). The URL must use the `http`, `https`, `socks4`, `socks4a`, `socks5` or `socks5h` scheme; anything else exits with code 2. A user name and password in it are masked (`http://***@proxy.corp:8080`) in console output, logs, errors and `run_summary.json`.

## Instagram Login Setup

To scrape private profiles or avoid rate-limiting on public profiles, you can log in:

```bash
python main.py https://www.instagram.com/someuser/ --instagram --ig-login
```

1. The browser opens visibly (not headless) and navigates to Instagram's login page
2. Log in manually in the browser window (you have up to 3 minutes)
3. Cookies are saved to `~/.scrapepy/instagram_profile/` via Patchright's `launch_persistent_context`
4. On subsequent runs with `--ig-login`, cookies are reused automatically

To reset login state, delete the cookie directory:

```bash
rm -rf ~/.scrapepy/instagram_profile/
```

## Output Directories

ScrapePy creates these directories in the current working directory, or under the folder given with `--output-dir`:

| Directory | Mode | Contents |
|-----------|------|----------|
| `ScrapedSites/<domain>/` | Default crawl, `-j` | HTML and assets (images/, css/, js/, fonts/, media/), `run_summary.json` and the cache. With `--markdown-only`, and in feed mode, only the run state |
| `ScrapedSites/<domain>/` | `-1`, `--swagger`, `-d` | The saved HTML (`index.html`, plus `openapi-spec.json` or `.yaml` for `--swagger` and each page and `combined.html` for `-d`); no assets are downloaded |
| `ScrapedSites/instagram/<username>/` | `--instagram` | Profile, post data and media |
| `MarkdownSites/<domain>/` | Default (use `--no-markdown` to skip) | Markdown files, plus images/ and media/ copied from `ScrapedSites` (not with `--markdown-only`) |
| `tests/fixtures/<domain>/` | `--audit` | Test fixtures (HTML snapshots + expected values) |

The `<domain>` directory name is derived from the URL: `www.` prefix stripped, dots replaced with underscores. With `--stay-in-path`, the path is appended (e.g., `docs_example_com_api_v2`). `--site-name NAME` replaces it, so targeted crawls of one site (`--include`, `--exclude`, `--max-depth`, `--max-pages`, `--urls-file`, `--only-recipes`, `--follow-next`) can share one folder. These options work with the default crawl and `-j` only; the other modes exit 2.

## Troubleshooting

### "No module named 'scrapling'"

```bash
pip install "scrapling[fetchers]"
```

### Browser not installed

`-1`, `--swagger` and `-d` print `Error: Failed to load page (...)` with a reminder to run `scrapling install` when the browser cannot start; `-j` prints `Fetch failed (...)` for each page. Run `scrapling install` to download Chromium.

### Cloudflare blocking

ScrapePy handles Cloudflare automatically via `solve_cloudflare=True` in `StealthyFetcher.async_fetch()`. If you're still blocked:

1. Try running without `--headless` (default) so the browser window is visible
2. Increase wait time: `--wait 10`
3. Use a proxy: `--proxy http://...`

The crawl also detects a Cloudflare challenge page with `is_cloudflare_challenge()`, pauses 5 seconds and then processes the page as fetched; it does not fetch it again.

### Timeout errors

Increase the wait time:

```bash
python main.py https://slow-site.com -j --wait 15
```

The browser navigation timeout is 120 seconds. In `-1`, `--swagger` and on `-d`'s first page, the mode also pauses `--wait` seconds after the page loads; each further `-d` sidebar page gets a fixed 1.5 second pause, and `--wait` only spaces those requests. In `-j`, `--wait` is only the gap between page requests: the browser returns the HTML once the page has loaded and Scrapling's Cloudflare check has finished (a few seconds), without scrolling, so a longer `--wait` does not give slow JavaScript more time.

### Permission errors on output

Make sure the output folder is writable. ScrapePy creates `ScrapedSites/` and `MarkdownSites/` in the working directory, or under the folder given with `-o`.

### Missing trafilatura (optional)

trafilatura is the fallback content extractor when none of the built-in extractors finds the content. Without it, ScrapePy uses its generic extractor instead.

### Scrapling deprecation warnings

If you see warnings about instantiating `Fetcher()` or `StealthyFetcher()`, this means code is calling them as instances instead of using the class methods directly:

```python
# Wrong, triggers deprecation warning
fetcher = Fetcher()
response = fetcher.get(url)

# Correct, call classmethod directly
response = Fetcher.get(url)
```

### "Expected int, got float" error

The `StealthyFetcher.async_fetch(wait=...)` parameter must be an integer, not a float:

```python
# Wrong
await StealthyFetcher.async_fetch(url, wait=5.0)

# Correct
await StealthyFetcher.async_fetch(url, wait=5)
# or
await StealthyFetcher.async_fetch(url, wait=int(config.wait_time))
```

### Instagram: "Sorry, this page isn't available"

The profile does not exist or is not accessible. The scraper prints that and moves on to the next account.

### Instagram: private profile

If the profile is private and you are not logged in, the scraper detects "This account is private", prints a hint to use `--ig-login` and moves on. Cookies persist at `~/.scrapepy/instagram_profile/`.
