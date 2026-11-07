"""Orchestrator, dispatches to the correct scraping mode."""

import os

from .config import ScrapeConfig
from .output.run_summary import EXIT_NOTHING_FETCHED, EXIT_OK
from .output.writer import create_output_dirs


def exit_code_for_pages(pages_saved: int | None) -> int:
    """Exit code for modes that report a page count. None means not reported."""
    if pages_saved is None:
        return EXIT_OK
    return EXIT_OK if pages_saved > 0 else EXIT_NOTHING_FETCHED


async def run_scraper(config: ScrapeConfig) -> int:
    """Create output dirs, dispatch to the right mode and return an exit code."""
    if config.audit:
        from .modes.audit import run_audit_mode
        await run_audit_mode(config)
        return EXIT_OK

    if config.instagram:
        from .modes.instagram import run_instagram_mode
        await run_instagram_mode(config)
        return EXIT_OK

    if not (config.swagger or config.docs or config.single_page):
        from .processing.feeds import looks_like_feed_url
        if config.feed or (config.markdown and not config.js_render
                           and looks_like_feed_url(config.url)):
            from .modes.feed import run_feed_mode
            from .processing.targeting import is_active as targeting_active
            if targeting_active(config) or config.only_recipes or config.max_pages \
                    or config.site_name or config.start_urls:
                print("Note: the URL looks like a feed, so feed mode runs and the "
                      "targeted-crawl options (and any other --urls-file URLs) do not apply.")
            summary = await run_feed_mode(config, explicit=config.feed)
            if summary is not None:
                return summary["exit_code"]
            print("Not an RSS or Atom feed; crawling it as a site.\n")
        create_output_dirs(config)
        from .modes.crawl import run_crawl_mode
        summary = await run_crawl_mode(config)
        # Crawl mode converts markdown inline, skip batch conversion
        return summary["exit_code"]

    # Browser modes start from one URL the user named; check it against robots.txt.
    from .processing.discovery import load_robots_policy
    robots, _ = load_robots_policy(config)
    if not robots.can_fetch(config.url):
        if robots.note:
            print(f"{robots.note}: {config.url}")
        else:
            print(f"Blocked by robots.txt: {config.url}")
        print("Nothing fetched. Use --ignore-robots only if you have permission.")
        return EXIT_NOTHING_FETCHED

    create_output_dirs(config)

    if config.swagger:
        from .modes.swagger import run_swagger_mode
        pages = await run_swagger_mode(config)
    elif config.docs:
        from .modes.docs import run_docs_mode
        pages = await run_docs_mode(config, robots=robots)
    else:
        # Saved and converted like a crawled page, with its own run summary
        from .modes.single_page import run_single_page_mode
        summary = await run_single_page_mode(config, robots=robots)
        return summary["exit_code"]

    # Markdown conversion for non-crawl modes (if requested)
    if config.markdown and pages:
        await _run_markdown_conversion(config)
    return exit_code_for_pages(pages)


async def _run_markdown_conversion(config: ScrapeConfig):
    """Run markdown conversion after scraping completes."""
    print("\n--- Starting Markdown Conversion ---\n")

    if config.swagger:
        # Try OpenAPI spec conversion first
        for ext in ("yaml", "yml", "json"):
            spec_file = os.path.join(config.output_dir, f"openapi-spec.{ext}")
            if os.path.exists(spec_file):
                try:
                    from .convert.openapi_to_markdown import convert_openapi_file
                    md_output = os.path.join(config.markdown_dir, "api-documentation.md")
                    os.makedirs(os.path.dirname(md_output), exist_ok=True)
                    convert_openapi_file(spec_file, md_output)
                    print(f"OpenAPI spec converted to: {md_output}")
                    return
                except Exception as e:
                    print(f"OpenAPI conversion failed: {e}, falling back to HTML conversion")
                    break

    # HTML-to-Markdown conversion
    try:
        from .convert.to_markdown import HTMLToMarkdown
        converter = HTMLToMarkdown(
            config.output_dir,
            output_dir=config.markdown_dir,
            base_url=config.base_url,
            domain=config.domain,
        )
        converter.convert_all()
    except Exception as e:
        print(f"Markdown conversion failed: {type(e).__name__}: {e}")
        if config.verbose:
            import traceback
            traceback.print_exc()
