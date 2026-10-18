# Setup Guide

## Requirements

- Python 3.11 or higher
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

This installs 7 packages:

| Package | Version | Size | Purpose |
|---------|---------|------|---------|
| scrapling[fetchers] | >=0.4.0 | ~50MB | Core framework (includes patchright, curl_cffi, browserforge) |
| beautifulsoup4 | >=4.12.0 | ~500KB | HTML parsing (used with lxml parser) |
| lxml | >=5.0.0 | ~15MB | Fast XML/HTML parser (sitemap parsing, BeautifulSoup backend) |
| markdownify | >=0.13.0 | ~50KB | HTML-to-Markdown conversion |
| pyyaml | >=6.0 | ~600KB | YAML parsing (OpenAPI specs) |
| trafilatura | >=1.12.0 | ~5MB | Content extraction fallback (optional but recommended) |
| httpx | >=0.27.0 | ~1MB | Async HTTP client (asset downloads) |

### Step 2: Browser Installation

Browser modes (`-j`, `-1`, `--swagger`, `-d`, `--instagram`) require a Chromium browser:

```bash
scrapling install
```

This downloads and installs Patchright's Chromium build (~200MB). The browser is stored in a platform-specific location managed by Patchright.

If you only need HTTP-only crawling (default mode without any browser flags), you can skip this step. The default crawl mode uses `Fetcher.get()` (curl_cffi) which doesn't need a browser.

### Step 3: Verify Installation

```bash
# Should print the help text without errors
python main.py --help

# Quick test with HTTP-only fetcher (no browser needed)
python main.py https://example.com

# Test browser mode (requires Step 2)
python main.py https://example.com -1

# Run all 457 tests (pure logic, no network, ~0.5s)
python -m pytest tests/ -v
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

Package metadata: version 0.1.0, requires-python >=3.11.

## Proxy Configuration

If you're behind a corporate proxy:

```bash
python main.py https://example.com --proxy http://proxy.corp:8080

# With authentication
python main.py https://example.com --proxy http://user:pass@proxy.corp:8080
```

The proxy is used for all request types:
- HTTP fetching: `Fetcher.get()` (via `config.proxy`)
- Browser modes: `StealthyFetcher.async_fetch()` (via `proxy` kwarg)
- Asset downloads: `httpx.AsyncClient()` (via `proxy` kwarg)

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

ScrapePy creates these directories in the current working directory:

| Directory | Mode | Contents |
|-----------|------|----------|
| `ScrapedSites/<domain>/` | All web modes | HTML + assets (images/, css/, js/, fonts/, media/) |
| `ScrapedSites/instagram/<username>/` | `--instagram` | Profile + post data + media |
| `MarkdownSites/<domain>/` | Default (use `--no-markdown` to skip) | Markdown files + images/ + media/ (copied) |
| `tests/fixtures/<domain>/` | `--audit` | Test fixtures (HTML snapshots + expected values) |

The `<domain>` directory name is derived from the URL: `www.` prefix stripped, dots replaced with underscores. With `--stay-in-path`, the path is appended (e.g., `docs_example_com_api_v2`).

## Troubleshooting

### "No module named 'scrapling'"

```bash
pip install "scrapling[fetchers]"
```

### Browser not installed

```
Error: Browser not installed
```

Run `scrapling install` to download Chromium (~200MB).

### Cloudflare blocking

ScrapePy handles Cloudflare automatically via `solve_cloudflare=True` in `StealthyFetcher.async_fetch()`. If you're still blocked:

1. Try running without `--headless` (default) so the browser window is visible
2. Increase wait time: `--wait 10`
3. Use a proxy: `--proxy http://...`

The crawl mode also detects Cloudflare challenges via `is_cloudflare_challenge()` and sleeps 5 seconds before retrying.

### Timeout errors

Increase the wait time:

```bash
python main.py https://slow-site.com -j --wait 15
```

The browser navigation timeout is 120 seconds (2 minutes). The `--wait` flag controls the additional wait after page load.

### Permission errors on output

Make sure the current directory is writable. ScrapePy creates `ScrapedSites/` and `MarkdownSites/` in the working directory.

### Missing trafilatura (optional)

trafilatura is used as a fallback content extractor for Markdown conversion (step 6 in the extraction chain). If it fails to install, ScrapePy will still work — it falls back to markdownify for generic HTML-to-Markdown conversion (step 7).

### Scrapling deprecation warnings

If you see warnings about instantiating `Fetcher()` or `StealthyFetcher()`, this means code is calling them as instances instead of using the class methods directly:

```python
# Wrong — triggers deprecation warning
fetcher = Fetcher()
response = fetcher.get(url)

# Correct — call classmethod directly
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

The profile may not exist or may have been deleted. The scraper detects this and skips to the next account.

### Instagram: private profile

If the profile is private and you're not logged in, the scraper will detect "This account is private" and prompt you to use `--ig-login`. Cookies persist at `~/.scrapepy/instagram_profile/`.
