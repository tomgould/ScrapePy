"""CLI argument parsing and main entry point."""

import argparse
import asyncio
import signal
import sys

from . import __version__, interrupts
from .config import DEFAULT_CONCURRENCY, ScrapeConfig, _parse_recent, _parse_since
from .output.run_summary import EXIT_BAD_ARGS, EXIT_INTERRUPTED
from .scraper import run_scraper


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got '{value}'")
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def _non_negative_float(value: str) -> float:
    try:
        n = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number of seconds, got '{value}'")
    if n < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return n


def _document_types(value: str) -> list[str]:
    from .processing.documents import parse_document_types
    try:
        return parse_document_types(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _document_mb(value: str) -> int:
    from .config import MAX_DOCUMENT_MB_LIMIT
    n = _positive_int(value)
    if n > MAX_DOCUMENT_MB_LIMIT:
        raise argparse.ArgumentTypeError(f"must be {MAX_DOCUMENT_MB_LIMIT} or less")
    return n


def _sitemap_host(value: str) -> str:
    """A host name or IP address for --allow-sitemap-host, lowercased."""
    host = value.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or any(c in host for c in "/@?# \t") or (":" in host and not _is_ipv6(host)):
        raise argparse.ArgumentTypeError(
            "give a host name such as cdn.example.com, without a scheme, port or path")
    return host


def _is_ipv6(host: str) -> bool:
    import ipaddress
    try:
        return ipaddress.ip_address(host).version == 6
    except ValueError:
        return False


def parse_args(argv: list[str] | None = None) -> ScrapeConfig:
    parser = argparse.ArgumentParser(
        prog="scrapepy",
        description="ScrapePy - Python web scraper using Scrapling + Patchright",
        epilog="Exit codes: 0 success, 1 nothing fetched or every fetch failed, "
               "2 bad arguments, 130 interrupted (Ctrl+C), 143 terminated (SIGTERM).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    parser.add_argument("--wait", type=_non_negative_float, default=None,
                        help="Minimum seconds between page requests to the site "
                             "(default: 3 HTTP, 5 browser; with --obey-robots a robots.txt "
                             "Crawl-delay raises it)")
    parser.add_argument("-c", "--concurrent", type=_positive_int, default=DEFAULT_CONCURRENCY,
                        help=f"Max concurrent requests (default: {DEFAULT_CONCURRENCY})")
    parser.add_argument("--user-agent", type=str, default=None,
                        help="User-Agent header for all requests, also used to match "
                             "robots.txt rules with --obey-robots (default: a browser-like "
                             "user agent)")
    parser.add_argument("--obey-robots", action="store_true",
                        help="Apply robots.txt: skip URLs it disallows and wait out its "
                             "Crawl-delay (default: robots.txt rules and Crawl-delay are not "
                             "applied)")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="Accepted and ignored: robots.txt is not applied unless "
                             "--obey-robots is given")
    parser.add_argument("-o", "--output-dir", type=str, default=".",
                        help="Folder under which ScrapedSites/ and MarkdownSites/ are created "
                             "(default: current directory)")
    parser.add_argument("--markdown-only", action="store_true",
                        help="Write only Markdown: no HTML mirror and no image or asset "
                             "downloads; links and images stay absolute URLs "
                             "(default crawl and -j only)")
    parser.add_argument("--feed", action="store_true",
                        help="Treat the URL as an RSS or Atom feed: one Markdown file per "
                             "entry, from one request (automatic for URLs such as /feed or "
                             "/rss.xml). --max-articles caps the entries (default 50)")
    target = parser.add_argument_group("Targeted crawling")
    target.add_argument("--include", action="append", default=[], metavar="REGEX",
                        help="Follow a link only when its path matches (repeatable, Python "
                             "regular expression on the path without query, fragment or "
                             "trailing slash, case-sensitive, first 2048 characters; not "
                             "time-limited, so a pathological pattern can stall a run); "
                             "links inside the --stay-in-path scope are followed too")
    target.add_argument("--exclude", action="append", default=[], metavar="REGEX",
                        help="Never follow a link whose path matches (repeatable, matched "
                             "like --include)")
    target.add_argument("--max-depth", type=int, default=None, metavar="N",
                        help="Follow links at most N steps from the start URLs (start = 0)")
    target.add_argument("--max-pages", type=int, default=0, metavar="N",
                        help="Stop after N pages fetched (default: 0 = no limit). Retries of "
                             "one page, robots.txt, reconnaissance and asset requests are not "
                             "counted; document requests (--documents) are")
    target.add_argument("--urls-file", type=str, default=None, metavar="FILE",
                        help="Start URLs, one per line (# comments), all on one site, in one run")
    target.add_argument("--only-recipes", action="store_true",
                        help="Write only pages with schema.org Recipe data; other pages are "
                             "fetched to find links but not written")
    target.add_argument("--site-name", type=str, default=None, metavar="NAME",
                        help="Output folder name for the site, so several runs share one folder")
    target.add_argument("--follow-next", action="store_true",
                        help="Follow pagination (rel=next and /page/N links of an in-scope "
                             "listing) without counting it as depth. Only --max-pages bounds "
                             "it: without it, it continues while the site offers another page")
    docs_group = parser.add_argument_group("Documents")
    docs_group.add_argument("--documents", type=_document_types, default=None, metavar="TYPES",
                            help="Also download linked documents of these types on the crawl's "
                                 "site and convert them to Markdown: a comma list of pdf, docx, "
                                 "xlsx, csv (default: off). Originals go under files/<site>/. "
                                 "Needs pip install \"scrapepy[documents]\" for pdf, docx and xlsx")
    docs_group.add_argument("--max-document-mb", type=_document_mb, default=None, metavar="MB",
                            help="Largest document downloaded, in MB (1 to 200, default: 25)")
    parser.add_argument("--index-pages", action=argparse.BooleanOptionalAction, default=True,
                        help="Write generated _index.md listing pages for sections (default: on)")
    parser.add_argument("--auth", type=str, default=None,
                        help="HTTP Basic Auth credentials (user:pass)")
    parser.add_argument("--proxy", type=str, default=None,
                        help="Proxy URL for requests")
    net_group = parser.add_argument_group("Network safety")
    net_group.add_argument("--allow-sitemap-host", action="append", default=[], metavar="HOST",
                           type=_sitemap_host,
                           help="Also fetch sitemaps on this host when robots.txt or a sitemap "
                                "index lists them (repeatable; default: only sitemaps on the "
                                "site's own host are fetched)")
    net_group.add_argument("--refuse-private-redirects", action="store_true",
                           help="Do not follow a redirect to a loopback, link-local, private "
                                "or carrier-grade NAT address, or to a host that resolves to "
                                "one; the page is recorded as failed. Covers HTTP fetches only: "
                                "in the browser modes neither the browser's redirects nor the "
                                "images, scripts and icons it loads while rendering are "
                                "checked. The start URL and redirects back to its "
                                "own host and port are not checked, and a host whose DNS "
                                "answer changes between the check and the request (DNS "
                                "rebinding) is not caught")
    net_group.add_argument("--no-cloudflare-solve", action="store_true",
                           help="Do not try to solve Cloudflare challenges in the browser modes "
                                "(default: try)")
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

    # Validate: need a URL, --urls-file or --accounts
    if not args.url and not args.accounts and not args.urls_file:
        parser.error("either provide a URL, --urls-file, or --accounts with a file of usernames")

    # Instagram mode requires --instagram flag
    if args.accounts and not args.instagram:
        parser.error("--accounts requires --instagram")

    if args.obey_robots and args.ignore_robots:
        parser.error("--obey-robots and --ignore-robots cannot be combined")

    # Auth validation
    if args.auth and ":" not in args.auth:
        parser.error("--auth must be in user:pass format")

    # Date filtering validation
    if args.recent and args.since:
        parser.error("--recent and --since are mutually exclusive")
    if (args.recent or args.since) and args.instagram:
        parser.error("--recent/--since are not supported in Instagram mode")
    try:
        if args.recent:
            _parse_recent(args.recent)
        if args.since:
            _parse_since(args.since)
    except ValueError as e:
        parser.error(str(e))
    if args.markdown_only:
        if not args.markdown:
            parser.error("--markdown-only cannot be combined with --no-markdown")
        for flag, on in (("-1/--single-page", args.single_page), ("--swagger", args.swagger),
                         ("-d/--docs", args.docs), ("--instagram", args.instagram),
                         ("--audit", args.audit)):
            if on:
                parser.error(f"--markdown-only works with the default crawl and -j, not {flag}")
    if args.feed:
        if not args.markdown:
            parser.error("--feed writes Markdown and cannot be combined with --no-markdown")
        for flag, on in (("-1/--single-page", args.single_page), ("--swagger", args.swagger),
                         ("-d/--docs", args.docs), ("--instagram", args.instagram),
                         ("--audit", args.audit), ("-j/--js-render", args.js_render)):
            if on:
                parser.error(f"--feed cannot be combined with {flag}")
    if args.max_document_mb is not None and not args.documents:
        parser.error("--max-document-mb needs --documents")
    if args.documents:
        if not args.markdown:
            parser.error("--documents writes Markdown and cannot be combined with --no-markdown")
        for flag, on in (("-1/--single-page", args.single_page), ("--swagger", args.swagger),
                         ("-d/--docs", args.docs), ("--instagram", args.instagram),
                         ("--audit", args.audit), ("--feed", args.feed)):
            if on:
                parser.error(f"--documents works with the default crawl and -j, not {flag}")
    start_urls: list[str] = []
    targeted = (args.include or args.exclude or args.max_depth is not None or args.max_pages
                or args.urls_file or args.only_recipes or args.follow_next)
    if targeted or args.site_name:
        # --site-name names the output folder, which -1 shares with the crawl
        for flag, on in (("-1/--single-page", args.single_page and targeted),
                         ("--swagger", args.swagger),
                         ("-d/--docs", args.docs), ("--instagram", args.instagram),
                         ("--audit", args.audit), ("--feed", args.feed)):
            if on:
                if targeted:
                    parser.error(f"targeted-crawl options work with the default crawl and -j, "
                                 f"not {flag}")
                parser.error(f"--site-name works with the default crawl, -j and -1, not {flag}")
    from .processing.targeting import check_pattern
    for pattern in args.include + args.exclude:
        problem = check_pattern(pattern)
        if problem:
            parser.error(problem)
    if args.max_depth is not None and args.max_depth < 0:
        parser.error("--max-depth must be 0 or more")
    if args.max_pages < 0:
        parser.error("--max-pages must be 0 or more")
    if args.site_name is not None:
        from .config import validate_site_name
        try:
            validate_site_name(args.site_name)
        except ValueError as e:
            parser.error(str(e))
    if args.urls_file:
        from .processing.targeting import read_urls_file
        try:
            start_urls = read_urls_file(args.urls_file, args.url)
        except OSError as e:
            parser.error(f"--urls-file could not be read: {e.strerror or e}")
        except ValueError as e:
            parser.error(str(e))
        if args.url and args.url not in start_urls:
            start_urls.insert(0, args.url)
    if args.proxy:
        from .proxy import validate_proxy
        try:
            validate_proxy(args.proxy)
        except ValueError as e:
            parser.error(str(e))
    if args.user_agent is not None and not args.user_agent.strip():
        parser.error("--user-agent must not be empty")

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
        user_agent=args.user_agent.strip() if args.user_agent else None,
        obey_robots=args.obey_robots,
        output_root=args.output_dir,
        index_pages=args.index_pages,
        markdown_only=args.markdown_only,
        feed=args.feed,
        include=list(args.include),
        exclude=list(args.exclude),
        max_depth=args.max_depth,
        max_pages=args.max_pages,
        urls_file=args.urls_file,
        start_urls=start_urls,
        only_recipes=args.only_recipes,
        site_name=args.site_name,
        follow_next=args.follow_next,
        documents=list(args.documents or []),
        allow_sitemap_hosts=list(args.allow_sitemap_host),
        refuse_private_redirects=args.refuse_private_redirects,
        solve_cloudflare=not args.no_cloudflare_solve,
        **({"max_document_mb": args.max_document_mb} if args.max_document_mb else {}),
    )


def main(argv: list[str] | None = None) -> int:
    """Run ScrapePy and return the process exit code."""
    try:
        config = parse_args(argv)
    except SystemExit as e:
        # argparse exits 2 on bad arguments and 0 for --help/--version
        return e.code if isinstance(e.code, int) else EXIT_BAD_ARGS

    if config.documents:
        from .processing.documents import missing_libraries
        problem = missing_libraries(config.documents)
        if problem:
            print(problem, file=sys.stderr)
            return EXIT_BAD_ARGS

    try:
        return _run(config)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return EXIT_INTERRUPTED
    except asyncio.CancelledError:
        # SIGTERM in a mode that does not catch the cancellation itself
        print("\nTerminated.")
        return interrupts.exit_code(interrupts.received() or signal.SIGTERM)


def _run(config: ScrapeConfig) -> int:
    if config.audit:
        print(f"Audit mode: {config.base_url}")
        print(f"Output: tests/fixtures/{config.output_dir_name}/")
        if config.verbose:
            print("Verbose: enabled")
        print()
        return interrupts.run_async(run_scraper(config))

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
        print("robots.txt: not checked in Instagram mode")
        print(f"Output folder: {config.output_dir}")
        print()
        return interrupts.run_async(run_scraper(config))

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
    if config.refuse_private_redirects:
        print("Redirects to private addresses: refused (HTTP fetches)")
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
    if config.documents:
        print(f"Documents: {', '.join(config.documents)} up to {config.max_document_mb} MB, "
              f"originals in {config.files_dir}")
    if config.markdown_only:
        print("Markdown only: no HTML mirror, no asset downloads, absolute links")
    elif config.include_assets:
        print("Include assets: CSS and JS files will be downloaded")
    else:
        print("Assets: images only (use -a to include CSS/JS)")

    if config.auth:
        print(f"HTTP Auth: {config.auth.split(':')[0]}:****")
    if config.user_agent:
        print(f"User agent: {config.user_agent}")
    print(f"Wait time: at least {config.wait_time}s between page requests")
    print(f"Concurrency: {config.concurrent}")
    print(f"Output folder: {config.output_dir}")
    if config.markdown:
        print(f"Markdown folder: {config.markdown_dir}")
        if not config.index_pages:
            print("Section index pages: off")
    print()

    return interrupts.run_async(run_scraper(config))
