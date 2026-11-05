"""Swagger/OpenAPI documentation mode, expands all sections then saves."""

import asyncio
import os
from urllib.parse import urlparse

from ..config import ScrapeConfig
from ..engine.fetcher import fetch_page_browser
from ..engine.browser_actions import expand_swagger_sections, find_spec_url, scroll_full_page
from ..output.writer import write_file


async def run_swagger_mode(config: ScrapeConfig) -> int:
    """Returns the number of pages saved."""
    print(f"Fetching Swagger/OpenAPI documentation: {config.url}")

    spec_file = None

    async def page_action(page):
        nonlocal spec_file

        print("  Page loaded, waiting for Swagger UI to render...")
        await asyncio.sleep(config.wait_time)

        # Wait for Swagger UI selectors
        try:
            await page.wait_for_selector(
                ".swagger-ui, .opblock, [class*='swagger'], [class*='openapi']",
                timeout=30000,
            )
        except Exception:
            print("  Note: Standard Swagger UI selectors not found, continuing...")

        # Try to find and download the OpenAPI spec
        spec_url = await find_spec_url(page, config.base_url)
        if spec_url:
            print(f"  Found OpenAPI spec URL: {spec_url}")
            spec_file = await _download_spec(page, spec_url, config)

        # Expand all sections
        print("  Expanding all API sections...")
        await expand_swagger_sections(page)

        return page

    try:
        response = await fetch_page_browser(config.url, config, page_action=page_action)
    except Exception as e:
        print(f"\nError: Failed to load page ({type(e).__name__}: {e})")
        print("Check that the URL is correct and the browser is installed (scrapling install).")
        return 0

    html = response.body.decode(response.encoding or "utf-8")

    output_file = os.path.join(config.output_dir, "index.html")
    write_file(output_file, html)
    print(f"  Saved: {output_file}")
    print(f"  File size: {len(html) / 1024:.1f} KB")

    print("\n--- Scraping Complete ---")
    print("Pages saved: 1")
    if spec_file:
        print("OpenAPI spec: saved")
    print(f"Output folder: {config.output_dir}")
    return 1


async def _download_spec(page, spec_url: str, config: ScrapeConfig) -> str | None:
    """Download the OpenAPI spec file."""
    try:
        # Resolve relative URLs
        full_url = spec_url
        if spec_url.startswith("/"):
            full_url = config.base_url + spec_url
        elif not spec_url.startswith("http"):
            parsed = urlparse(config.url)
            current_path = parsed.path.rsplit("/", 1)[0]
            full_url = config.base_url + current_path + "/" + spec_url

        print(f"  Fetching spec from: {full_url}")
        spec_content = await page.evaluate("""async (url) => {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return await resp.text();
        }""", full_url)

        is_yaml = spec_url.endswith((".yaml", ".yml"))
        ext = "yaml" if is_yaml else "json"
        spec_path = os.path.join(config.output_dir, f"openapi-spec.{ext}")
        write_file(spec_path, spec_content)
        print(f"  Saved OpenAPI spec: {spec_path}")
        print(f"  Spec size: {len(spec_content) / 1024:.1f} KB")
        return spec_path
    except Exception as e:
        print(f"  Could not download spec file: {e}")
        return None
