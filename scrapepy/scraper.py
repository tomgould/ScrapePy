"""Orchestrator — dispatches to the correct scraping mode."""

import sys

from .config import ScrapeConfig
from .output.writer import create_output_dirs


async def run_scraper(config: ScrapeConfig):
    """Main entry point: create output dirs and dispatch to the right mode."""
    if config.audit:
        from .modes.audit import run_audit_mode
        await run_audit_mode(config)
        return

    if config.instagram:
        from .modes.instagram import run_instagram_mode
        await run_instagram_mode(config)
        return

    create_output_dirs(config)

    if config.swagger:
        from .modes.swagger import run_swagger_mode
        await run_swagger_mode(config)

    elif config.docs:
        from .modes.docs import run_docs_mode
        await run_docs_mode(config)

    elif config.single_page:
        from .modes.single_page import run_single_page_mode
        await run_single_page_mode(config)

    else:
        from .modes.crawl import run_crawl_mode
        await run_crawl_mode(config)
        # Crawl mode converts markdown inline — skip batch conversion
        return

    # Markdown conversion for non-crawl modes (if requested)
    if config.markdown:
        await _run_markdown_conversion(config)


async def _run_markdown_conversion(config: ScrapeConfig):
    """Run markdown conversion after scraping completes."""
    print("\n--- Starting Markdown Conversion ---\n")

    if config.swagger:
        # Try OpenAPI spec conversion first
        import os
        for ext in ("yaml", "yml", "json"):
            spec_file = os.path.join(config.output_dir, f"openapi-spec.{ext}")
            if os.path.exists(spec_file):
                try:
                    from .convert.openapi_to_markdown import convert_openapi_file
                    md_output = os.path.join(config.output_dir, "..", "..",
                                             "MarkdownSites", config.output_dir_name,
                                             "api-documentation.md")
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
            base_url=config.base_url,
            domain=config.domain,
        )
        converter.convert_all()
    except Exception as e:
        print(f"Markdown conversion failed: {type(e).__name__}: {e}")
        if config.verbose:
            import traceback
            traceback.print_exc()
