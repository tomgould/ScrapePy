"""Browser page_action callbacks for scroll, expand, click operations."""

import asyncio


async def scroll_full_page(page):
    """Scroll through the entire page to trigger lazy loading."""
    await page.evaluate("""async () => {
        await new Promise((resolve) => {
            let totalHeight = 0;
            const distance = 500;
            const startTime = Date.now();
            const maxTime = 60000; // 60s safety timeout
            const timer = setInterval(() => {
                if (Date.now() - startTime > maxTime) {
                    clearInterval(timer);
                    window.scrollTo(0, 0);
                    resolve();
                    return;
                }
                const scrollHeight = document.body.scrollHeight;
                window.scrollBy(0, distance);
                totalHeight += distance;
                if (totalHeight >= scrollHeight) {
                    clearInterval(timer);
                    window.scrollTo(0, 0);
                    resolve();
                }
            }, 100);
        });
    }""")


async def expand_swagger_sections(page):
    """Expand all sections in Swagger UI."""

    # Click global expand button
    await page.evaluate("""() => {
        const expandBtn = document.querySelector('[aria-label="Expand all"], .expand-operation');
        if (expandBtn) { expandBtn.click(); return; }
        const buttons = document.querySelectorAll('button');
        for (const btn of buttons) {
            if (btn.textContent.includes('Expand')) { btn.click(); return; }
        }
    }""")
    await asyncio.sleep(1)

    # Expand all operation blocks
    expanded = await page.evaluate("""async () => {
        let count = 0;
        // Newer Swagger UI
        const summaryControls = document.querySelectorAll('.opblock-summary-control[aria-expanded="false"]');
        for (const btn of summaryControls) {
            btn.click(); count++;
            await new Promise(r => setTimeout(r, 150));
        }
        // Older Swagger UI
        const opblockSummaries = document.querySelectorAll('.opblock:not(.is-open) .opblock-summary');
        for (const summary of opblockSummaries) {
            summary.click(); count++;
            await new Promise(r => setTimeout(r, 150));
        }
        // Arrow buttons
        const arrowBtns = document.querySelectorAll('.opblock-control-arrow[aria-expanded="false"]');
        for (const btn of arrowBtns) {
            btn.click(); count++;
            await new Promise(r => setTimeout(r, 150));
        }
        return count;
    }""")
    print(f"  Expanded {expanded} endpoint sections")
    await asyncio.sleep(2)

    # Expand models/schemas
    models_expanded = await page.evaluate("""async () => {
        let count = 0;
        const modelContainers = document.querySelectorAll(
            '.model-container:not(.is-open), .models-control[aria-expanded="false"]');
        for (const model of modelContainers) {
            model.click(); count++;
            await new Promise(r => setTimeout(r, 50));
        }
        const models = document.querySelectorAll(
            '.model-toggle:not(.expanded), .model-box-control[aria-expanded="false"]');
        for (const model of models) {
            model.click(); count++;
            await new Promise(r => setTimeout(r, 50));
        }
        const modelsHeader = document.querySelector('.models h4, .models-title');
        if (modelsHeader) { modelsHeader.click(); count++; }
        return count;
    }""")
    print(f"  Expanded {models_expanded} model/schema sections")
    await asyncio.sleep(2)

    # Expand show/hide toggles
    await page.evaluate("""async () => {
        const toggles = document.querySelectorAll(
            '.response-col_description button, .body-toggle, [class*="show-hide"], .btn-toggle:not(.active)');
        for (const toggle of toggles) {
            try { toggle.click(); } catch (e) {}
            await new Promise(r => setTimeout(r, 50));
        }
    }""")
    await asyncio.sleep(1)

    # Scroll through to ensure everything loaded
    await scroll_full_page(page)
    await asyncio.sleep(2)


async def find_spec_url(page, base_url: str) -> str | None:
    """Try to locate the OpenAPI spec URL from the page."""
    return await page.evaluate("""(baseUrl) => {
        // Meta tag
        const meta = document.querySelector('meta[name="spec-url"]');
        if (meta) return meta.content;
        // window.ui
        if (window.ui && window.ui.specUrl) return window.ui.specUrl;
        // Script tags
        const scripts = document.querySelectorAll('script');
        for (const script of scripts) {
            const content = script.textContent || script.innerText || '';
            const urlMatch = content.match(/url\\s*:\\s*['"]([^'"]+\\.(yaml|yml|json))['"]/i);
            if (urlMatch) return urlMatch[1];
            const configMatch = content.match(/(?:configUrl|spec|specUrl)\\s*:\\s*['"]([^'"]+)['"]/i);
            if (configMatch && (configMatch[1].includes('.yaml') || configMatch[1].includes('.json')
                || configMatch[1].includes('api-docs'))) return configMatch[1];
        }
        // URL input field
        const urlInput = document.querySelector(
            '.download-url-input, input[type="text"][value*=".json"], input[type="text"][value*=".yaml"]');
        if (urlInput && urlInput.value) return urlInput.value;
        // Links
        const specLink = document.querySelector(
            'a[href*="swagger.json"], a[href*="swagger.yaml"], a[href*="openapi.json"], '
            + 'a[href*="openapi.yaml"], a[href*="api-docs"], a[href$=".yaml"], a[href$=".yml"]');
        if (specLink) return specLink.href;
        return null;
    }""", base_url)


async def expand_docs_sections(page):
    """Expand collapsed sections on documentation pages."""
    await page.evaluate("""() => {
        document.querySelectorAll(
            '[class*="expand"], [class*="collapse"], [aria-expanded="false"], details:not([open])'
        ).forEach(el => {
            try {
                if (el.tagName === 'DETAILS') { el.setAttribute('open', ''); }
                else { el.click(); }
            } catch (e) {}
        });
    }""")
    await asyncio.sleep(0.5)


async def find_nav_links(page) -> list[dict]:
    """Find all navigation/sidebar links on a documentation page."""
    return await page.evaluate("""() => {
        const links = [];
        const seen = new Set();
        const selectors = [
            'nav a[href]',
            '[class*="sidebar"] a[href]',
            '[class*="nav"] a[href]',
            '[class*="menu"] a[href]',
            '[class*="toc"] a[href]',
            'a[href*="/reference"]',
            'a[href*="/docs"]',
            'a[href*="/api"]',
            '[role="navigation"] a[href]',
            'aside a[href]'
        ];
        for (const selector of selectors) {
            document.querySelectorAll(selector).forEach(a => {
                const href = a.href;
                const text = a.textContent.trim();
                if (href && !seen.has(href) &&
                    href.startsWith(window.location.origin) &&
                    text.length > 0 && text.length < 100 &&
                    (!href.includes('#') || href.includes(window.location.pathname))) {
                    seen.add(href);
                    links.push({ href, text });
                }
            });
        }
        return links;
    }""")


async def get_main_content_html(page) -> str:
    """Extract the main content area HTML from the page."""
    return await page.evaluate("""() => {
        const main = document.querySelector(
            'main, article, [class*="content"], [class*="markdown"], [role="main"]');
        return main ? main.innerHTML : document.body.innerHTML;
    }""")


async def wait_for_js_content(page, wait_time: float):
    """Wait for JavaScript content to render."""
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    await scroll_full_page(page)

    try:
        await page.wait_for_function("""() => {
            const hasContent = document.querySelector('main, article, .content, #content, [role="main"]');
            const bodyText = document.body?.innerText || '';
            return hasContent || bodyText.length > 500;
        }""", timeout=10000)
    except Exception:
        pass

    await asyncio.sleep(wait_time)


async def extract_links_browser(page, base_url: str) -> list[str]:
    """Extract links from the browser page including JS-rendered content."""
    links = await page.evaluate("""(baseUrl) => {
        const foundLinks = new Set();

        // Standard anchor links
        document.querySelectorAll('a[href]').forEach(a => {
            const href = a.getAttribute('href');
            if (href) foundLinks.add(href);
        });

        // onclick handlers
        document.querySelectorAll('[onclick]').forEach(el => {
            const onclick = el.getAttribute('onclick');
            const matches = onclick.match(/['"]([^'"]*\\.html?[^'"]*)['"]/gi) || [];
            matches.forEach(m => foundLinks.add(m.replace(/['"]/g, '')));
            const hashMatches = onclick.match(/#t=[A-Za-z0-9_\\-\\.]+\\.htm/g) || [];
            hashMatches.forEach(m => foundLinks.add(m));
        });

        // Data attributes
        document.querySelectorAll('[data-href], [data-url], [data-link], [data-src]').forEach(el => {
            ['data-href', 'data-url', 'data-link', 'data-src'].forEach(attr => {
                const val = el.getAttribute(attr);
                if (val) foundLinks.add(val);
            });
        });

        // Navigation items
        document.querySelectorAll(
            '.toc a, .nav a, .sidebar a, .menu a, [class*="nav"] a, [class*="menu"] a, [class*="toc"] a'
        ).forEach(a => {
            const href = a.getAttribute('href');
            if (href) foundLinks.add(href);
        });

        // Tree navigation
        document.querySelectorAll('.tree-item a, .treenode a, [role="treeitem"] a').forEach(a => {
            const href = a.getAttribute('href');
            if (href) foundLinks.add(href);
        });

        // Hash-based patterns in HTML
        const bodyHtml = document.body?.innerHTML || '';
        const hashPatterns = bodyHtml.match(/#t=[A-Za-z0-9_\\-\\.]+\\.htm/g) || [];
        hashPatterns.forEach(p => foundLinks.add(p));

        return Array.from(foundLinks);
    }""", base_url)

    return links
