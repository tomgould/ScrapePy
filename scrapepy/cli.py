"""CLI argument parsing and main entry point."""

import argparse
import asyncio
import sys

from .config import ScrapeConfig
from .scraper import run_scraper


def parse_args(argv: list[str] | None = None) -> ScrapeConfig:
    parser = argparse.ArgumentParser(
        prog="scrapepy",
        description="ScrapePy - Python web scraper using Scrapling + Patchright",
    )
    parser.add_argument("url", nargs="?", default=None, help="URL to scrape")
    parser.add_argument("-m", "--markdown", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Convert to Markdown after scraping (default: on)")
    parser.add_argument("-s", "--stay-in-path", action="store_true",
                        help="Only crawl URLs under the starting path")
    parser.add_argument("-w", "--path-wildcard", action="store_true",
                        help="Match URLs containing the path (use with -s)")
    parser.add_argument("-j", "--js-render", action="store_true",
                        help="Wait for JavaScript to render content")
    parser.add_argument("-1", "--single-page", action="store_true",
                        help="Single page SPA mode (no crawling)")
    parser.add_argument("--swagger", "--openapi", action="store_true",
                        help="Swagger/OpenAPI documentation mode")
    parser.add_argument("-d", "--docs", action="store_true",
                        help="Documentation site mode (Readme.io, Stoplight, etc.)")
    parser.add_argument("-a", "--include-assets", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Include CSS/JS/font files (default: on, images always included)")
    parser.add_argument("-i", "--incremental", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="Only re-download pages/assets modified since last crawl (default: on)")
    parser.add_argument("--wait", type=float, default=None,
                        help="Custom wait time per page in seconds")
    parser.add_argument("-c", "--concurrent", type=int, default=10,
                        help="Max concurrent requests (default: 10)")
    parser.add_argument("--auth", type=str, default=None,
                        help="HTTP Basic Auth credentials (user:pass)")
    parser.add_argument("--proxy", type=str, default=None,
                        help="Proxy URL for requests")
    parser.add_argument("--headless", action="store_true",
                        help="Run browser headless (default: visible)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging")

    date_group = parser.add_argument_group("Date filtering")
    date_group.add_argument("--recent", type=str, default=None,
                            help="Only keep articles from the last N period: e.g. 2w, 1d, 6h")
    date_group.add_argument("--since", type=str, default=None,
                            help="Only keep articles published after this date: YYYY-MM-DD")
    date_group.add_argument("--min-articles", type=int, default=0,
                            help="Minimum articles to keep per section even if old (default: 0)")
    date_group.add_argument("--max-articles", type=int, default=0,
                            help="Max articles per section (newest N, default: 0 = no limit)")

    audit_group = parser.add_argument_group("Audit mode")
    audit_group.add_argument("--audit", action="store_true",
                            help="Reconnaissance only: analyze site structure and save fixtures (no crawl)")

    ig_group = parser.add_argument_group("Instagram mode")
    ig_group.add_argument("--instagram", action="store_true",
                          help="Instagram profile scraping mode")
    ig_group.add_argument("--accounts", type=str, default=None,
                          help="Path to file with Instagram usernames/URLs (one per line)")
    ig_group.add_argument("--ig-login", action="store_true",
                          help="Enable Instagram login (visible browser, saves cookies)")
    ig_group.add_argument("--ig-max-posts", type=int, default=0,
                          help="Max posts to scrape per account (0 = all)")
    ig_group.add_argument("--update-meta", action="store_true",
                          help="Re-scrape posts to update metadata (likes, views, comments) but skip re-downloading existing media")

    args = parser.parse_args(argv)

    # Validate: need either a URL or --accounts
    if not args.url and not args.accounts:
        parser.error("either provide a URL or use --accounts with a file of usernames")

    # Instagram mode requires --instagram flag
    if args.accounts and not args.instagram:
        parser.error("--accounts requires --instagram")

    # Auth validation
    if args.auth and ":" not in args.auth:
        parser.error("--auth must be in user:pass format")

    # Date filtering validation
    if args.recent and args.since:
        parser.error("--recent and --since are mutually exclusive")
    if (args.recent or args.since) and args.instagram:
        parser.error("--recent/--since are not supported in Instagram mode")

    # Default URL for instagram mode when only --accounts is provided
    url = args.url or ""

    return ScrapeConfig(
        url=url,
        markdown=args.markdown,
        stay_in_path=args.stay_in_path,
        path_wildcard=args.path_wildcard,
        js_render=args.js_render,
        single_page=args.single_page,
        swagger=args.swagger,
        docs=args.docs,
        include_assets=args.include_assets,
        incremental=args.incremental,
        wait=args.wait,
        concurrent=args.concurrent,
        auth=args.auth,
        proxy=args.proxy,
        headless=args.headless,
        verbose=args.verbose,
        instagram=args.instagram,
        accounts_file=args.accounts,
        ig_login=args.ig_login,
        ig_max_posts=args.ig_max_posts,
        ig_update_meta=args.update_meta,
        recent=args.recent,
        since=args.since,
        min_articles=args.min_articles,
        max_articles=args.max_articles,
        audit=args.audit,
    )


def main(argv: list[str] | None = None):
    config = parse_args(argv)

    if config.audit:
        print(f"Audit mode: {config.base_url}")
        print(f"Output: tests/fixtures/{config.output_dir_name}/")
        if config.verbose:
            print("Verbose: enabled")
        print()
        asyncio.run(run_scraper(config))
        return

    if config.instagram:
        print("Instagram mode: enabled")
        if config.accounts_file:
            print(f"Accounts file: {config.accounts_file}")
        if config.url:
            print(f"URL: {config.url}")
        if config.ig_login:
            print("Login: enabled (browser will open for manual login)")
        if config.ig_max_posts:
            print(f"Max posts per account: {config.ig_max_posts}")
        if config.ig_update_meta:
            print("Update metadata: enabled (skip existing media)")
        if config.incremental:
            print("Incremental: enabled (skip already-scraped posts)")
        if config.markdown:
            print("Markdown conversion: enabled")
        print(f"Output folder: {config.output_dir}")
        print()
        asyncio.run(run_scraper(config))
        return

    print(f"Base URL: {config.base_url}")
    print(f"Domain: {config.domain}")

    if config.stay_in_path:
        print(f"Starting path: {config.starting_path or '/'}")
        if config.path_wildcard:
            print(f"Scope: URLs containing {config.starting_path}")
        else:
            print(f"Scope: Only URLs under {config.base_url}{config.starting_path}/")

    if config.js_render:
        print("JS Render mode: enabled (waiting for JavaScript)")
    if config.single_page:
        print("Single page mode: enabled (SPA - no crawling)")
    if config.swagger:
        print("Swagger mode: enabled (will expand all API sections)")
    if config.docs:
        print("Docs mode: enabled (will navigate through sidebar)")
    if config.incremental:
        print("Incremental mode: only re-downloading changed pages/assets")
    if config.has_date_filter:
        cutoff_str = config.cutoff_date.strftime("%Y-%m-%d %H:%M UTC")
        if config.recent:
            print(f"Date filter: articles from last {config.recent} (since {cutoff_str})")
        else:
            print(f"Date filter: articles since {cutoff_str}")
        if config.min_articles:
            print(f"Min articles per section: {config.min_articles}")
        if config.max_articles:
            print(f"Max articles per section: {config.max_articles}")
        if config.single_page or config.docs or config.swagger:
            print("  (date filter has no effect in this mode; all pages saved)")
    if config.include_assets:
        print("Include assets: CSS and JS files will be downloaded")
    else:
        print("Assets: images only (use -a to include CSS/JS)")

    if config.auth:
        print(f"HTTP Auth: {config.auth.split(':')[0]}:****")
    print(f"Wait time: {config.wait_time}s per page")
    print(f"Output folder: {config.output_dir}")
    if config.markdown:
        print("Markdown conversion: enabled")
    print()

    asyncio.run(run_scraper(config))
