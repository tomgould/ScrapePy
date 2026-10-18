"""Convert scraped HTML files to clean Markdown.

Handles standard HTML, API docs,
Swagger UI, Adobe RoboHelp, and MadCap Flare documentation.
"""

import re
import shutil
from pathlib import Path

from bs4 import BeautifulSoup
import markdownify

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False


def _format_visible_date(date_str: str) -> str:
    """Format an ISO date/datetime string for display in article body.

    Returns "27 Feb 2026, 10:30" for datetimes with time,
    or "27 Feb 2026" for date-only values.
    """
    from datetime import datetime as _dt
    try:
        dt = _dt.fromisoformat(date_str)
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and "T" not in date_str:
            return dt.strftime("%-d %b %Y")
        return dt.strftime("%-d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return date_str


_JUNK_SECTION_RE = re.compile(
    r'^#{2,6}\s+(?:'
    r'Most Read|Subscribe\b|Sign up\b|In this article|More (?:News|Stories)|'
    r'Shopping Best Buys?|More About|Related (?:Articles?|Stories|Topics)|'
    r'Trending|Popular|Recommended|You May (?:Also )?Like|'
    r'Also Read|Don\'t Miss|Latest\s+(?:News|Stories|Articles)|'
    r"Editor'?s? Picks?|Sponsored|Top Stories|What to Read Next|"
    r'Promoted Stories|Around the Web|From the Web|More from\b|'
    r'Around (?:Sky|the Site|the BBC)|'
    r'Comments?\s*(?:\d+\s*)?$|Add (?:your |a )?comment|'
    r'Join (?:the |our )(?:conversation|discussion|commenting)|'
    r'Take (?:my|our|the) quiz\b'
    r').*?(?=^#{1,3}\s|\Z)',
    re.MULTILINE | re.IGNORECASE | re.DOTALL
)

# Inline "Read More" blocks: heading + link-only lines (not greedy to EOF)
_READ_MORE_RE = re.compile(
    r'^#{2,3}\s+Read More\s*$\n+'
    r'(?:\s*\[.+?\]\(.+?\).*$\n*)+',
    re.MULTILINE | re.IGNORECASE
)

_JUNK_CLASS_KEYWORDS = (
    'advert', 'ad-slot', 'ad-container', 'ad-wrapper', 'dfp-ad',
    'google-ad', 'gpt-ad', 'ad-unit', 'ad-banner', 'ad-break',
    'newsletter', 'signup', 'sign-up', 'subscribe', 'email-capture',
    'most-read', 'most_read', 'mostread', 'trending', 'top-stories',
    'popular-stories', 'recommended', 'related-articles', 'related-stories',
    'more-stories', 'more-news', 'commercial-slot', 'commercial-container',
    'commercial-wrapper', 'sponsorship',
    'social-share', 'share-buttons', 'social-links',
    'cookie-banner', 'cookie-consent', 'consent-banner',
    'puff', 'dms-puff', 'rotator',
)


class HTMLToMarkdown:
    def __init__(self, input_dir, output_dir=None, flatten=False, base_url=None, domain=None):
        self.input_dir = Path(input_dir)
        self.flatten = flatten
        self.base_url = base_url
        self.domain = domain

        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            parent = self.input_dir.parent.parent
            site_name = self.input_dir.name
            self.output_dir = parent / "MarkdownSites" / site_name

        self.converted = 0
        self.failed = 0
        self.skipped = 0

    def is_asset_directory(self, rel_path):
        first_part = rel_path.parts[0] if rel_path.parts else ""
        return first_part in {"css", "js", "images", "fonts"}

    def is_error_page(self, soup, html):
        from ..processing.errors import is_error_page_html, is_error_page_content
        # Quick raw HTML check
        if is_error_page_html(html):
            return True
        # Rendered content check
        title = soup.find("title")
        title_text = title.get_text() if title else ""
        h1 = soup.find("h1")
        h1_text = h1.get_text() if h1 else ""
        body_text = soup.get_text()[:2000]
        return is_error_page_content(title_text, h1_text, body_text)

    def unwrap_layout_tables(self, soup):
        for table in soup.find_all("table"):
            table_class = " ".join(table.get("class", []))
            is_layout = (
                "Table-NoLines" in table_class
                or "layout" in table_class.lower()
                or len([td for td in table.find_all("td") if td.get_text(strip=True)])
                    < len(table.find_all("td")) * 0.5
            )
            if is_layout:
                table.unwrap()

        for tag_name in ("tbody", "tr", "td", "th", "colgroup", "col"):
            for tag in soup.find_all(tag_name):
                tag.unwrap()

    def extract_api_docs_content(self, soup):
        for tag in soup.find_all(["script", "style", "noscript", "svg", "link"]):
            tag.decompose()

        for selector in ("sidebar", "nav", "menu", "toc"):
            for tag in soup.find_all(class_=lambda x: x and selector in str(x).lower()):
                if not tag.find(class_=lambda x: x and "content" in str(x).lower() if x else False):
                    tag.decompose()

        md_parts = []

        title = ""
        title_el = soup.find("h1") or soup.find(
            class_=lambda x: x and "title" in str(x).lower() if x else False
        )
        if title_el:
            title = title_el.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True)

        if title:
            md_parts.append(f"# {title}\n")

        main_content = (
            soup.find("main")
            or soup.find("article")
            or soup.find(class_=lambda x: x and "content" in str(x).lower() if x else False)
        )
        if not main_content:
            main_content = soup.find("body")

        if main_content:
            endpoint_blocks = main_content.find_all(
                class_=lambda x: x and (
                    "endpoint" in str(x).lower()
                    or "api-method" in str(x).lower()
                    or "operation" in str(x).lower()
                ) if x else False
            )

            if endpoint_blocks:
                for block in endpoint_blocks:
                    method_el = block.find(
                        class_=lambda x: x and ("method" in str(x).lower() or "http" in str(x).lower()) if x else False
                    )
                    method = method_el.get_text(strip=True).upper() if method_el else ""

                    path_el = block.find(
                        class_=lambda x: x and ("path" in str(x).lower() or "url" in str(x).lower() or "endpoint" in str(x).lower()) if x else False
                    )
                    path = path_el.get_text(strip=True) if path_el else ""

                    if method or path:
                        md_parts.append(f"\n### `{method}` {path}\n")

                    desc_el = block.find(class_=lambda x: x and "description" in str(x).lower() if x else False)
                    if desc_el:
                        md_parts.append(f"{desc_el.get_text(strip=True)}\n")

                    params_section = block.find(class_=lambda x: x and "param" in str(x).lower() if x else False)
                    if params_section:
                        params = params_section.find_all(
                            ["tr", "li", "div"],
                            class_=lambda x: x and "param" in str(x).lower() if x else True,
                        )
                        if params:
                            md_parts.append("\n**Parameters:**\n")
                            for param in params:
                                param_text = param.get_text(strip=True)
                                if param_text:
                                    md_parts.append(f"- {param_text}\n")

                    response_section = block.find(class_=lambda x: x and "response" in str(x).lower() if x else False)
                    if response_section:
                        md_parts.append("\n**Response:**\n")
                        md_parts.append(f"{response_section.get_text(strip=True)}\n")
            else:
                md = markdownify.markdownify(str(main_content), heading_style="ATX", bullets="-")
                if title and not md.strip().startswith("# "):
                    md = f"# {title}\n\n{md}"
                return md

        result = "\n".join(md_parts)
        return result if len(result.strip()) > 100 else None

    def extract_swagger_content(self, soup):
        for tag in soup.find_all(["script", "style", "noscript", "svg", "link"]):
            tag.decompose()

        for selector in ("topbar", "auth-wrapper", "information-container-wrapper"):
            for tag in soup.find_all(class_=lambda x: x and selector in x):
                tag.decompose()

        for tag in soup.find_all(["button", "input", "select"]):
            tag.decompose()

        for tag in soup.find_all(class_=lambda x: x and ("try-out" in str(x) or "execute" in str(x))):
            tag.decompose()

        body = soup.find("body")
        if not body:
            return None

        title = ""
        info_title = soup.find(class_="title") or soup.find("h1") or soup.find("h2")
        if info_title:
            title = info_title.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True).replace("Swagger UI", "").strip(" -")

        md_parts = []
        if title:
            md_parts.append(f"# {title}\n")

        info_section = soup.find(class_="info") or soup.find(class_="information-container")
        if info_section:
            desc = info_section.find(class_="description") or info_section.find("p")
            if desc:
                md_parts.append(desc.get_text(strip=True) + "\n")

        opblocks = soup.find_all(class_="opblock")
        current_tag = None

        for opblock in opblocks:
            summary = opblock.find(class_="opblock-summary")
            if summary:
                method_el = summary.find(class_="opblock-summary-method")
                path_el = (
                    summary.find(class_="opblock-summary-path")
                    or summary.find(class_="opblock-summary-path__deprecated")
                )

                method = method_el.get_text(strip=True) if method_el else ""
                path = path_el.get_text(strip=True) if path_el else ""

                tag_el = opblock.find_previous(class_="opblock-tag")
                if tag_el:
                    tag_name = tag_el.get_text(strip=True)
                    if tag_name != current_tag:
                        current_tag = tag_name
                        md_parts.append(f"\n## {tag_name}\n")

                md_parts.append(f"\n### `{method}` {path}\n")

                desc_el = summary.find(class_="opblock-summary-description")
                if desc_el:
                    md_parts.append(f"\n{desc_el.get_text(strip=True)}\n")

            body_el = opblock.find(class_="opblock-body")
            if body_el:
                params_section = body_el.find(class_="parameters")
                if params_section:
                    md_parts.append("\n**Parameters:**\n")
                    for param in params_section.find_all(class_="parameter"):
                        name_el = param.find(class_="parameter__name")
                        type_el = param.find(class_="parameter__type")
                        desc_el = param.find(class_="parameter__description") or param.find(class_="markdown")

                        name = name_el.get_text(strip=True) if name_el else ""
                        ptype = type_el.get_text(strip=True) if type_el else ""
                        desc = desc_el.get_text(strip=True) if desc_el else ""
                        required = param.find(class_="parameter__required")
                        req_str = " *(required)*" if required else ""
                        md_parts.append(f"- `{name}` ({ptype}){req_str}: {desc}\n")

                responses_section = body_el.find(class_="responses")
                if responses_section:
                    md_parts.append("\n**Responses:**\n")
                    for resp in responses_section.find_all(class_="response"):
                        code_el = resp.find(class_="response-col_status")
                        desc_el = resp.find(class_="response-col_description")
                        code = code_el.get_text(strip=True) if code_el else ""
                        desc = ""
                        if desc_el:
                            desc_text = desc_el.find(class_="markdown") or desc_el.find("p")
                            if desc_text:
                                desc = desc_text.get_text(strip=True)
                        md_parts.append(f"- `{code}`: {desc}\n")

        models_section = soup.find(class_="models")
        if models_section:
            md_parts.append("\n## Schemas\n")
            for model in models_section.find_all(class_="model-container"):
                name_el = model.find(class_="model-title")
                if name_el:
                    md_parts.append(f"\n### {name_el.get_text(strip=True)}\n")
                for prop in model.find_all(class_="property"):
                    prop_text = prop.get_text(strip=True)
                    if prop_text:
                        md_parts.append(f"- {prop_text}\n")

        result = "\n".join(md_parts)
        return result if len(result.strip()) > 100 else None

    def extract_robohelp_content(self, soup):
        for tag in soup.find_all(["script", "style", "noscript", "colgroup"]):
            tag.decompose()

        for selector in ("topic-header", "topic-header-shadow", "breadcrumbs", "minitoc"):
            for tag in soup.find_all(class_=selector):
                tag.decompose()

        for tag in soup.find_all(id=lambda x: x and x.startswith("rh-")):
            tag.decompose()

        for a in soup.find_all("a"):
            if a.get("onclick"):
                del a["onclick"]

        title = ""
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True)

        body = soup.find("body")
        if not body:
            return None

        for p in body.find_all("p", class_="NormalFooter"):
            parent_table = p.find_parent("table")
            if parent_table:
                parent_table.decompose()

        for element in body.find_all(["p", "div"]):
            text = element.get_text(strip=True).lower()
            if "associated press" in text and ("copyright" in text or "all rights reserved" in text):
                parent_table = element.find_parent("table")
                if parent_table:
                    parent_table.decompose()
                else:
                    element.decompose()

        self.unwrap_layout_tables(body)

        for p in body.find_all("p"):
            text = p.get_text(strip=True)
            if not text or text == "\xa0":
                p.decompose()

        for img in body.find_all("img"):
            src = img.get("src", "")
            if "icon" in src.lower():
                img.decompose()

        md = markdownify.markdownify(str(body), heading_style="ATX", bullets="-", strip=["img"])

        if title and not md.strip().startswith(f"# {title}") and not md.strip().startswith("# "):
            md = f"# {title}\n\n{md}"

        return md

    def _remove_junk_elements(self, soup):
        """Remove non-content HTML elements before content extraction."""
        # Structural non-content
        for tag in soup.find_all(["script", "style", "noscript", "svg", "link"]):
            tag.decompose()
        for tag in soup.find_all(["nav", "header", "footer"]):
            tag.decompose()
        for tag in soup.find_all(attrs={"role": "navigation"}):
            tag.decompose()
        for tag in soup.find_all(id=lambda x: x and x in ("access", "branding", "colophon")):
            tag.decompose()

        # Sidebars and widget areas
        for tag in soup.find_all(["aside", "section"]):
            classes = " ".join(tag.get("class", []))
            tag_id = tag.get("id", "")
            if any(kw in classes.lower() or kw in tag_id.lower()
                   for kw in ("sidebar", "widget", "secondary", "nav", "menu")):
                tag.decompose()

        # Ad, newsletter, trending, and related containers by CSS class/id
        for tag in list(soup.find_all(True)):
            if not tag.parent:
                continue  # Already decomposed with a parent element
            classes = " ".join(tag.get("class", []))
            tag_id = tag.get("id", "")
            combined = (classes + " " + tag_id).lower()
            if any(kw in combined for kw in _JUNK_CLASS_KEYWORDS):
                tag.decompose()

    def extract_standard_content(self, soup):
        """Extract content from standard sites (WordPress, blogs, general CMS)."""
        self._remove_junk_elements(soup)

        # Try precise content selectors (most specific first)
        content = None
        selectors = [
            ".entry-content",           # WordPress
            ".post-content",            # Common blog theme
            ".article-text",            # Daily Mail
            ".article-body",            # Various CMS
            "article .content",         # Generic article
            "[role='main'] article",    # ARIA main > article
            "#content article",         # ID content > article
            "article",                  # Bare article tag
            "[role='main']",            # ARIA main
            "main",                     # HTML5 main
            "#main",                    # ID main (styled-components sites)
            "#content",                 # ID content
            ".content",                 # Class content
        ]
        for sel in selectors:
            content = soup.select_one(sel)
            if content and len(content.get_text(strip=True).split()) > 50:
                break
        else:
            return None

        # Extract title
        title = ""
        h1 = content.find("h1") or soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True)

        # Convert to markdown
        md = markdownify.markdownify(str(content), heading_style="ATX", bullets="-")

        if title and not md.strip().startswith("# "):
            md = f"# {title}\n\n{md}"

        return md

    def extract_content(self, html, filepath):
        soup = BeautifulSoup(html, "lxml")

        if self.is_error_page(soup, html):
            return None

        # Detect API documentation platforms
        is_readme = (
            soup.find(class_=lambda x: x and "readme" in str(x).lower())
            or soup.find("meta", attrs={"name": "readme-deploy"})
            or soup.find(class_=lambda x: x and "api-" in str(x).lower() and "content" in str(x).lower())
        )
        has_api_content = (
            soup.find(class_=lambda x: x and ("endpoint" in str(x).lower() or "api-method" in str(x).lower()))
            or soup.find("code", class_=lambda x: x and "http" in str(x).lower() if x else False)
        )
        if is_readme or has_api_content:
            result = self.extract_api_docs_content(soup)
            if result and len(result.strip()) > 100:
                return result

        # Detect Swagger UI
        is_swagger = (
            soup.find(class_="swagger-ui")
            or soup.find(class_="opblock")
            or ("swagger" in html.lower()[:5000] and soup.find(class_="info"))
        )
        if is_swagger:
            result = self.extract_swagger_content(soup)
            if result and len(result.strip()) > 100:
                return result

        # Detect RoboHelp/MadCap
        generator = soup.find("meta", attrs={"name": "generator"})
        is_robohelp = generator and (
            "RoboHelp" in generator.get("content", "") or "MadCap" in generator.get("content", "")
        )
        has_rh_structure = soup.find(class_="topic-header") or soup.find(
            id=lambda x: x and x.startswith("rh-")
        )
        if is_robohelp or has_rh_structure:
            result = self.extract_robohelp_content(soup)
            if result and len(result.strip()) > 100:
                return result

        # Standard site extraction (WordPress, blogs, CMS)
        # Re-parse only if soup was mutated by earlier extractors
        if is_readme or has_api_content or is_swagger or is_robohelp or has_rh_structure:
            standard_soup = BeautifulSoup(html, "lxml")
        else:
            standard_soup = soup
        result = self.extract_standard_content(standard_soup)
        if result and len(result.strip()) > 100:
            return result

        # Fallback: trafilatura
        if HAS_TRAFILATURA:
            try:
                extracted = trafilatura.extract(
                    html,
                    include_images=True,
                    include_links=True,
                    include_tables=True,
                    include_formatting=True,
                    output_format="markdown",
                )
                if extracted and len(extracted.strip()) > 100:
                    return extracted
            except (ImportError, MemoryError):
                raise
            except Exception:
                pass

        # Fallback: generic
        try:
            for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
                tag.decompose()

            main_content = soup.select_one("main, article, [role='main'], #main, .content, #content, body")
            if main_content:
                title = ""
                if soup.find("title"):
                    title = soup.find("title").get_text().strip()
                if soup.find("h1"):
                    title = soup.find("h1").get_text().strip()

                md = markdownify.markdownify(str(main_content), heading_style="ATX", bullets="-")
                if title and not md.strip().startswith("# "):
                    md = f"# {title}\n\n{md}"

                if len(md.strip()) > 100:
                    return md
        except (ImportError, MemoryError):
            raise
        except Exception:
            pass

        return None

    def clean_markdown(self, md):
        if not md:
            return None

        # ===== INLINE JUNK PATTERNS =====

        # Advertisement markers (common ad-slot style)
        md = re.sub(r'^\s*Article continues below\s*(?:ADVERTISEMENT)?\s*$',
                     '', md, flags=re.MULTILINE)
        # Hearst/general "Advertisement - Continue Reading Below"
        md = re.sub(r'^\s*Advertisement\s*[-\u2013\u2014]\s*Continue Reading Below\s*$',
                     '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Standalone "Advertisement" text
        md = re.sub(r'^\s*Advertisement\s*$', '', md, flags=re.MULTILINE)

        # Google preferred source badges (multiple formats)
        md = re.sub(
            r'\[!\[Add\s+.+?as a preferred source\]\([^)]*\)\]\([^)]*\)',
            '', md, flags=re.IGNORECASE)
        # Google preferred source full-line blocks (Express style)
        md = re.sub(
            r'^\[.*?(?:Preferred Source|preferred source|More of Our News on Google).*?'
            r'!\[.*?preferred source.*?\]\([^)]*\)\]\([^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Relative date strings (standalone lines)
        md = re.sub(
            r'^\s*(?:just now|yesterday|'
            r'(?:Updated\s+)?(?:\d+|an?|one)\s+'
            r'(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago'
            r')\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Affiliate link disclaimers
        md = re.sub(
            r'^This article contains affiliate links.*?\[Learn more\]\([^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter form artifacts
        md = re.sub(r'^\s*Leave this field blank\s*$', '', md, flags=re.MULTILINE)

        # Standalone "Sign up" / "Subscribe" / "Follow" buttons
        md = re.sub(r'^\s*(?:Sign up|Subscribe|Follow)\s*$', '', md, flags=re.MULTILINE)

        # Topic/author follow widget text (Verge, Vox Media)
        md = re.sub(
            r'^.*?(?:Posts from this (?:topic|author) will be added to your|'
            r'daily email digest and your homepage feed).*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^\s*\[See All (?:by )?.+?\]\([^)]*\)\s*$',
                     '', md, flags=re.MULTILINE)

        # "Follow topics and authors" footer blocks
        md = re.sub(
            r'^\*\*Follow topics and authors\*\*.*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Advertiser content blocks
        md = re.sub(
            r'^Advertiser Content.*$(?:\s*\n.*?(?:Sponsor|native ad|thumbnail).*$)*',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Footer action links (HuffPost, general)
        md = re.sub(
            r'^\s*(?:Go To Homepage|Suggest a correction|Submit a tip)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Share/view comment counts (Daily Mail, etc.)
        md = re.sub(r'^\s*\*?\*?\d+\*?\*?\s+shares?\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^\s*\[?\d+\s+View\s+comments?\]?\(?\#?[^)]*\)?\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^\s*Share what you think\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^\s*(?:Submit Comment|Enter your comment|Clear)\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)

        # "Related" / "Related Topics" label followed by topic tag links
        md = re.sub(
            r'^\s*[-*]?\s*Related(?:\s+Topics?)?\s*$\n+(?:\s*[-*]?\s*\[.+?\]\(.+?\)\s*$\n*)+',
            '', md, flags=re.MULTILINE)

        # Fox News Flash / video promo blocks (image link + heading + description)
        md = re.sub(
            r'^\[?!\[.*?(?:Fox News Flash|top headlines).*?\]\([^)]*\).*?\]\([^)]*\)\s*\n+'
            r'(?:#{2,4}\s+\[.*?(?:Fox News Flash|top headlines).*?\]\([^)]*\)\s*\n+'
            r'.*?(?:Check out what\'s clicking|FoxNews\.com).*$\n*)?',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter consent/email preference text
        md = re.sub(
            r'^.*?(?:I would like to be emailed about|'
            r'For more information see our \[Privacy).*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter/edition embedded images
        md = re.sub(
            r'^!\[.*?(?:newsletter|edition|subscribe|sign.?up).*?\]\([^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter description lines between image and "Sign up"
        md = re.sub(
            r'^(?:Get the latest|News from |Sign up to our|'
            r'The best .+ direct to your inbox|'
            r'Stay up to date with|Never miss).+(?:inbox|newsletter|updates).*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Social share icon/link blocks (GB News: image + Facebook/Twitter/E-mail links)
        md = re.sub(
            r'^\s*!\[desktop-(?:share|like|comment)\]\([^)]*\).*$',
            '', md, flags=re.MULTILINE)
        md = re.sub(
            r'^\s*\[Facebook\]\(https://www\.facebook\.com/sharer[^)]*\)\s+'
            r'\[Twitter\]\(https://twitter\.com/intent[^)]*\)\s+'
            r'\[E-mail\]\(mailto:[^)]*\)\s*$',
            '', md, flags=re.MULTILINE)

        # Byline blocks: "By" + author link on separate lines
        md = re.sub(
            r'^By\s*\n+\[([^\]]+)\]\([^)]*\)\s*,?\s*$',
            r'By \1', md, flags=re.MULTILINE)

        # Published date/time in body (redundant with frontmatter)
        md = re.sub(
            r'^\s*Published:\s+\d{1,2}/\d{1,2}/\d{4}\s*$',
            '', md, flags=re.MULTILINE)
        md = re.sub(r'^\s*-\s+\d{1,2}:\d{2}\s*$', '', md, flags=re.MULTILINE)

        # Relative timestamps with optional comments link (Standard: "2 minutes ago[COMMENTS](#...)")
        md = re.sub(
            r'^\s*\d+\s+(?:seconds?|minutes?|hours?|days?)\s+ago'
            r'(?:\s*\[COMMENTS\]\([^)]*\))?\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Newsletter embed images (Standard: "![The Weekender](../images/newsletter_*_embed_*.png)")
        md = re.sub(
            r'^\s*!\[[^\]]*\]\([^)]*newsletter[^)]*embed[^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Rating/score widget text (Pitchfork)
        md = re.sub(
            r'^\s*(?:No score yet, be the first to add\.|Reader Score)\s*$',
            '', md, flags=re.MULTILINE)
        md = re.sub(r'^\s*0\.0\s*$', '', md, flags=re.MULTILINE)

        # Standalone "Latest" heading (GB News uses this before article body)
        md = re.sub(r'^#{2,3}\s+Latest\s*$', '', md, flags=re.MULTILINE)

        # "Most Read" sidebar blocks (non-heading, followed by list)
        md = re.sub(
            r'^Most Read\s+\w+\s*$\n+'
            r'(?:\s*[-*]?\s*\[.+?\]\(.+?\).*$\n*)+',
            '', md, flags=re.MULTILINE)

        # UI element text (gallery viewers, bookmark popovers, close buttons, video players)
        md = re.sub(
            r'^\s*(?:open image in gallery|close popover|close panel|'
            r'Removed from bookmarks|Video Player Placeholder|'
            r'View \d+ Images?|Save this story|'
            r'Skip to main content|Scroll (?:Left|Right)|'
            r'Please use Chrome browser for a more accessible video player)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Close button links after title (Fox News)
        md = re.sub(r'^\s*\[close\]\(#[^)]*\)\s*$', '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Concatenated "NEW" banner (Fox News "NEWYou can now listen to...")
        md = re.sub(r'^NEW\s*You can now listen to .+? articles!?\s*$',
                     '', md, flags=re.MULTILINE)
        # App download CTAs
        md = re.sub(
            r'^\s*\[?\*?\*?CLICK HERE TO (?:DOWNLOAD|GET) THE .+? APP\*?\*?\]?\(?[^)]*\)?\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Icon text artifacts (Hearst "Caret Right/Left", "Chevron" etc.)
        md = re.sub(r'\s*Caret (?:Right|Left|Down|Up)\s*', ' ', md)
        md = re.sub(r'^\s*Chevron\s*$', '', md, flags=re.MULTILINE)
        # Concatenated follow/unfollow button text (BBC Sport)
        md = re.sub(r'Follow\w*(?:Follow|Unfollow)\w*', '', md)
        # Comments links (standalone)
        md = re.sub(r'^\s*\[Comments?\]\(#[^)]*\)\s*$', '', md, flags=re.MULTILINE)
        # Zero-width characters on standalone lines
        md = re.sub(r'^\s*[\u200b\u200c\u200d\ufeff]+\s*$', '', md, flags=re.MULTILINE)
        # Bookmark popover heading (Independent)
        md = re.sub(r'^#{2,3}\s+Bookmark popover\s*$', '', md, flags=re.MULTILINE)
        # "In full" related article blocks (Independent bulletin)
        md = re.sub(
            r'^In full\s*$\n+(?:\s*[-*]\s*\[.+?\]\(.+?\).*$\n*)*',
            '', md, flags=re.MULTILINE)

        # Social follow promos (WhatsApp, Telegram, etc.)
        md = re.sub(
            r'^\[?!\[Follow .+? on WhatsApp\]\([^)]*\)\s*'
            r'(?:Follow .+? on WhatsApp[^\]]*\]\([^)]*\))?\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Placeholder/logo images (SVGs, brand logos, hero video posters)
        md = re.sub(
            r'^!\[(?:Video Player Placeholder|Hero video poster|logo)[^\]]*\]\([^)]*\)\s*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # "View image in fullscreen" links (Guardian), standalone or inline
        md = re.sub(r'\[View image in fullscreen\]\(#[^)]*\)',
                     '', md, flags=re.IGNORECASE)

        # "Prefer X on Google" links (Guardian)
        md = re.sub(r'^\s*\[Prefer .+? on Google\]\([^)]*\)\s*$',
                     '', md, flags=re.MULTILINE)

        # "Reuse this content" links
        md = re.sub(r'^\s*\[Reuse this content\]\([^)]*\)\s*$',
                     '', md, flags=re.MULTILINE)

        # Share mailto links: [Share](mailto:...) or [e-mail](mailto:...)
        md = re.sub(r'^\s*[-*]?\s*\[(?:Share|e-?mail)\]\(mailto:[^)]*\)\s*$',
                     '', md, flags=re.MULTILINE | re.IGNORECASE)

        # "Supported by" sponsor blocks (Guardian): "Supported by" + sponsor image + about link
        md = re.sub(
            r'^Supported by\s*\n+(?:\s*\[?!\[.*?\]\([^)]*\)\]?\([^)]*\)\s*\n*)*'
            r'(?:\s*\[About this content\]\([^)]*\)\s*$)?',
            '', md, flags=re.MULTILINE)

        # Duplicate image captions: alt text repeated as standalone line
        md = re.sub(
            r'(!\[([^\]]+)\]\([^)]+\))\s*\n+\2\s*$',
            r'\1', md, flags=re.MULTILINE)
        # Duplicate caption lines after image (caption\n\ncaption -> caption)
        md = re.sub(
            r'(!\[[^\]]*\]\([^)]+\)\s*\n+)(.+(?:Photograph|Photo|Image|Illustration|Credit|Getty|Reuters|AP|PA|AFP|Alamy|Shutterstock|iStock).+)\s*\n+\2',
            r'\1\2', md, flags=re.IGNORECASE)

        # "Explore more on these topics" + topic link list (Guardian)
        md = re.sub(
            r'^Explore more on these topics\s*$'
            r'(?:\s*\n\s*[-*]\s*\[.+?\]\(.+?\)\s*$)*',
            '', md, flags=re.MULTILINE)

        # ===== SECTION-LEVEL JUNK =====

        # Small heading + thumbnail link lists (Hearst: "Related Story", "For More on That...")
        md = re.sub(
            r'^#{4,6}\s+(?:Related Stor(?:y|ies)|For More on That\.{0,3}|'
            r'Key Points?)\s*$\n+'
            r'(?:\s*[-*]?\s*\[?!\[.*?\]\([^)]*\).*$\n*)*',
            '', md, flags=re.MULTILINE | re.IGNORECASE)

        # Linked heading + thumbnail grid (Hearst related content sections)
        # Pattern: ## [Section Title](link) followed by image+text link blocks
        md = re.sub(
            r'^#{2,3}\s+\[.+?\]\(.+?\)\s*$\n+'
            r'(?:\s*\[?!\[.*?\]\(https?://[^)]*\)[\s\S]*?\]\([^)]*\)\s*$\n*)*',
            '', md, flags=re.MULTILINE)

        # Inline "Read More" blocks (heading + link lines only, not greedy)
        md = _READ_MORE_RE.sub('', md)

        # Remove entire junk sections (heading to next heading or EOF)
        md = _JUNK_SECTION_RE.sub('', md)

        # "MORE ABOUT" topic tag blocks (not a heading, uppercase text + links)
        md = re.sub(
            r'^MORE ABOUT\s*$(?:\s*\n\s*(?:\[.+?\]\(.+?\)|[A-Z][\w\s]*)\s*$)*',
            '', md, flags=re.MULTILINE)

        # ===== BLOCK PATTERNS =====

        # Inline event promotion blocks (TechCrunch): "Techcrunch event" ... "REGISTER NOW"
        md = re.sub(
            r'^Techcrunch event\s*$.*?(?:^\[REGISTER NOW\]\([^)]*\)\s*$)',
            '', md, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)

        # Previous/Next navigation blocks (broken multi-line links)
        md = re.sub(
            r'\[#{1,3}\s*(?:Previous|Next)\b[\s\S]*?\.(?:html|md)\)',
            '', md)

        # Shopping/ecommerce recommendation blocks (to end of string)
        md = re.sub(
            r'^\[Shopping\s+Best\s+Buys?\]\([^)]*\)[\s\S]*$',
            '', md, flags=re.MULTILINE)

        # Breadcrumb/category link lines (adjacent linked labels with "View all posts" titles)
        md = re.sub(
            r'^\s*(?:\[[\w\s&/]+\]\([^)]*(?:"View all [^"]*")?\)\s*){2,}\s*$',
            '', md, flags=re.MULTILINE)

        # Comment moderation disclaimers
        md = re.sub(
            r'^.*(?:comments below have not been moderated|'
            r'views expressed .* do not necessarily reflect|'
            r'By posting your comment you agree).*$',
            '', md, flags=re.MULTILINE | re.IGNORECASE)
        # Comment sort tabs (Newest/Oldest/Best rated/Worst rated)
        md = re.sub(
            r'^\s*[-*]\s*\[(?:Newest|Oldest|Best rated|Worst rated)\]\(#\)\s*$',
            '', md, flags=re.MULTILINE)
        # Standalone "View all" links
        md = re.sub(r'^\s*View all\s*$', '', md, flags=re.MULTILINE)

        # ===== STRUCTURAL FIXES =====

        # Strip site name suffixes from H1: "# Title | Site.com" -> "# Title"
        # Match domain-style suffixes
        md = re.sub(
            r'^(# .+?)\s+[|\u2013\u2014-]\s+[\w][\w\s]*?'
            r'\.(?:com|co\.uk|net|org|io|uk|us|info|news)\s*$',
            r'\1', md, flags=re.MULTILINE)
        # Match brand name suffixes (short text after separator, max 40 chars)
        md = re.sub(
            r'^(# .{20,}?)\s+[|\u2013\u2014-]\s+(?:The\s+)?[\w][\w\s]{1,35}$',
            r'\1', md, flags=re.MULTILINE)

        # Duplicate H1: keep first, remove subsequent identical ones
        lines = md.split('\n')
        first_h1 = None
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('# ') and not stripped.startswith('## '):
                h1_text = stripped[2:].strip()
                if first_h1 is None:
                    first_h1 = h1_text
                    cleaned.append(line)
                elif h1_text == first_h1:
                    continue
                else:
                    cleaned.append(line)
            else:
                cleaned.append(line)
        md = '\n'.join(cleaned)

        # Duplicate consecutive images (same URL appearing twice)
        md = re.sub(
            r'(!\[[^\]]*\]\(([^)]+)\))\s*\n+!\[[^\]]*\]\(\2\)',
            r'\1', md)

        # Fix multi-line markdown links
        def _fix_multiline_link(m):
            text = re.sub(r'\s*\n\s*', ' ', m.group(1))
            return f'[{text}]({m.group(2)})'
        md = re.sub(r'\[([^\]]*\n[^\]]*)\]\(([^)]+)\)', _fix_multiline_link, md)

        # ===== STANDARD CLEANUP =====

        # Empty table rows
        md = re.sub(r'^\|(?:\s*\|)+\s*$', '', md, flags=re.MULTILINE)
        # Broken/empty links
        md = re.sub(r'\[([^\]]*)\]\(\s*\)', r'\1', md)
        # Empty headings
        md = re.sub(r'^#{1,6}\s*$', '', md, flags=re.MULTILINE)
        # RoboHelp cruft
        md = re.sub(r'\[?Click here to see this page in full context\]?\(?[^)]*\)?', '', md)
        # Copyright/footer
        md = re.sub(r'^©\s*\d{4}.*$', '', md, flags=re.MULTILINE)
        md = re.sub(r'^Copyright\s+\d{4}\s+\S+\.\s*All rights reserved\.?\s*$',
                     '', md, flags=re.MULTILINE | re.IGNORECASE)
        md = re.sub(r'^All rights reserved\.?\s*$', '', md, flags=re.MULTILINE)
        md = re.sub(r'^\d+ queries\..*seconds\.\s*$', '', md, flags=re.MULTILINE)

        # Collapse blank lines and strip
        md = re.sub(r'\n{3,}', '\n\n', md)
        md = '\n'.join(line.rstrip() for line in md.split('\n'))
        md = md.strip()

        return md if len(md) > 50 else None

    def promote_bold_headings(self, md):
        """Convert standalone bold lines that act as section headings to ## headings."""
        lines = md.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            # Line is purely bold text (no other content), min 3 chars inside
            m = re.match(r'^\*\*(.{3,})\*\*$', stripped)
            if m:
                heading_text = m.group(1)
                # Don't promote if it looks like inline emphasis (contains sentences)
                if len(heading_text) < 80 and '.' not in heading_text[:-1]:
                    result.append(f'## {heading_text}')
                    continue
            result.append(line)
        return '\n'.join(result)

    def convert_links(self, md, filepath):
        current_filename = filepath.name.replace(".html", ".md").replace(".htm", ".md")

        def replace_link(match):
            text = match.group(1)
            url = match.group(2)

            if url.startswith(("http://", "https://", "#", "mailto:", "tel:")):
                return match.group(0)

            if url.endswith(".html"):
                url = url[:-5] + ".md"
            elif url.endswith(".htm"):
                url = url[:-4] + ".md"
            elif url.endswith("/"):
                url = (url.rstrip("/") or "index") + ".md"
            elif "." not in url.split("/")[-1]:
                url += ".md"

            clean_url = url.lstrip("./")
            if clean_url in ("index.md", "_index.md", current_filename):
                return text

            return f"[{text}]({url})"

        return re.sub(r'\[([^\]]*)\]\(([^)]+)\)', replace_link, md)

    def get_output_path(self, input_path):
        rel_path = input_path.relative_to(self.input_dir)
        if self.is_asset_directory(rel_path):
            return None
        if self.flatten:
            output_path = self.output_dir / rel_path.name
        else:
            output_path = self.output_dir / rel_path
        return output_path.with_suffix(".md")

    @staticmethod
    def _build_frontmatter(metadata: dict) -> str:
        """Build YAML frontmatter string from metadata dict.

        Fixed field order. Core fields (date, date_source, page_type) are
        always included (as null when missing) for predictable parsing.
        Other fields are only included when non-None.
        """
        field_order = [
            "title", "url", "date", "date_source", "page_type",
            "word_count", "description", "domain",
            "articles", "sub_sections",
        ]
        # Core fields always present for predictable frontmatter schema
        _always_include = {"date", "date_source", "page_type"}
        lines = ["---"]
        for key in field_order:
            val = metadata.get(key)
            if val is None:
                if key in _always_include:
                    lines.append(f"{key}: null")
                continue
            if isinstance(val, int):
                lines.append(f"{key}: {val}")
            else:
                s = str(val)
                if any(ch in s for ch in (":", '"', "#", "'", "{", "}", "[", "]", ",", "&", "*", "?", "|", ">", "!", "%", "@", "`")):
                    # Escape internal double quotes, then wrap in double quotes
                    s = s.replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'{key}: "{s}"')
                else:
                    lines.append(f"{key}: {s}")
        lines.append("---")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _compute_word_count(md: str) -> int:
        """Count words in markdown, stripping syntax markers."""
        text = md
        # Strip image/link syntax: ![alt](url) -> alt, [text](url) -> text
        text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
        text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
        # Strip heading markers
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Strip bold/italic markers
        text = re.sub(r'\*{1,3}|_{1,3}', '', text)
        # Strip frontmatter if present
        text = re.sub(r'^---\n.*?\n---\n', '', text, count=1, flags=re.DOTALL)
        return len(text.split())

    @staticmethod
    def _extract_description(html: str, soup=None) -> str | None:
        """Extract meta description from HTML. Reuses soup if provided."""
        if soup is None:
            soup = BeautifulSoup(html[:10000], "lxml")
        for attr in ({"name": "description"}, {"property": "og:description"}):
            tag = soup.find("meta", attrs=attr)
            if tag and tag.get("content"):
                desc = tag["content"].strip()
                if desc:
                    return desc
        return None

    def _extract_metadata_from_html(self, html: str, filepath: Path) -> dict:
        """Extract metadata from HTML for batch mode (no crawl-time metadata)."""
        from ..processing.dates import get_best_date
        from ..processing.classifier import classify_page

        metadata: dict = {}

        # Reconstruct URL from base_url + relative file path
        if self.base_url:
            rel = filepath.relative_to(self.input_dir)
            url_path = "/" + str(rel).replace("\\", "/")
            # Strip .html/.htm extension for URL
            if url_path.endswith(".html"):
                url_path = url_path[:-5]
            elif url_path.endswith(".htm"):
                url_path = url_path[:-4]
            if url_path.endswith("/index"):
                url_path = url_path[:-6] or "/"
            metadata["url"] = self.base_url + url_path

        if self.domain:
            metadata["domain"] = self.domain

        # Date extraction
        date_result = get_best_date(url_path if self.base_url else "", html)
        if date_result:
            metadata["date"] = date_result.date.isoformat()
            metadata["date_source"] = date_result.source.name

        # Page type
        page_type = classify_page(url_path if self.base_url else "", html)
        metadata["page_type"] = page_type.value

        # Description
        desc = self._extract_description(html)
        if desc:
            metadata["description"] = desc

        return metadata

    def convert_file(self, filepath, metadata=None, output_path=None):
        explicit_output = output_path is not None
        if output_path:
            output_path = Path(output_path)
        else:
            output_path = self.get_output_path(filepath)
            if not output_path:
                return False

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                html = f.read()

            # Skip RSS/Atom XML feeds
            if html.lstrip().startswith("<?xml") or "<rss" in html[:500] or "<feed" in html[:500]:
                print(f"  Skipped (XML/RSS feed): {filepath.name}")
                self.skipped += 1
                return False

            # Skip XML manifests and non-HTML content
            if html.lstrip().startswith("<manifest") or html.lstrip().startswith("<wlw"):
                print(f"  Skipped (XML manifest): {filepath.name}")
                self.skipped += 1
                return False

            # Skip known low-value WordPress paths
            rel_path = filepath.relative_to(self.input_dir)
            rel_str = str(rel_path)
            wp_skip_patterns = ("/feed", "/author/", "/category/", "/tag/", "/wp-includes/", "/wp-json/")
            if any(pat in "/" + rel_str for pat in wp_skip_patterns):
                print(f"  Skipped (WordPress boilerplate): {filepath.name}")
                self.skipped += 1
                return False

            if "<title>404" in html or "<title>403" in html or "NoSuchKey" in html:
                print(f"  Skipped (error page): {filepath.name}")
                self.skipped += 1
                return False

            # Build metadata if not provided (batch mode)
            if metadata is None:
                metadata = self._extract_metadata_from_html(html, filepath)

            # Extract description from HTML if not already in metadata
            if "description" not in metadata:
                desc = self._extract_description(html)
                if desc:
                    metadata["description"] = desc

            # Section pages become _index.md in their own directory
            # (but not topic/tag/author pages which happen to look like sections)
            url_for_check = metadata.get("url", "")
            from urllib.parse import urlparse
            url_path_for_check = urlparse(url_for_check).path if url_for_check else ""
            from ..processing.urls import is_non_section_path
            if (not explicit_output
                    and metadata.get("page_type") == "section"
                    and not is_non_section_path(url_path_for_check)):
                # Redirect: news.md -> news/_index.md
                stem = output_path.stem  # e.g. "news"
                output_path = output_path.parent / stem / "_index.md"

            md = self.extract_content(html, filepath)
            if not md:
                print(f"  Skipped (no content/error page): {filepath.name}")
                self.skipped += 1
                return False

            # Promote bold lines to headings first so junk section patterns
            # (which match ## headings) can catch promoted junk headings
            md = self.promote_bold_headings(md)

            md = self.clean_markdown(md)
            if not md:
                print(f"  Skipped (content too short): {filepath.name}")
                self.skipped += 1
                return False

            # Ensure page title is always present as # Heading
            if not md.lstrip().startswith('# '):
                title_soup = BeautifulSoup(html[:5000], "lxml")
                title = ""
                h1 = title_soup.find("h1")
                if h1:
                    title = h1.get_text(strip=True)
                elif title_soup.find("title"):
                    title = title_soup.find("title").get_text(strip=True)
                if title:
                    md = f"# {title}\n\n{md}"

            # Capture title from first # Heading for metadata
            title_match = re.match(r'^# (.+)$', md.lstrip(), re.MULTILINE)
            if title_match and "title" not in metadata:
                metadata["title"] = title_match.group(1).strip()

            md = self.convert_links(md, filepath)

            # Compute word count, skip list/section pages with no real content
            word_count = self._compute_word_count(md)
            if word_count < 50:
                print(f"  Skipped (list/section page, {word_count} words): {filepath.name}")
                self.skipped += 1
                return False

            metadata["word_count"] = word_count

            # Insert visible date/time after the title heading
            date_str = metadata.get("date")
            if date_str and date_str != "null":
                visible_date = _format_visible_date(date_str)
                heading_match = re.match(r'^(# .+\n)', md)
                if heading_match:
                    md = heading_match.group(1) + f"\n**{visible_date}**\n" + md[heading_match.end():]

            frontmatter = self._build_frontmatter(metadata)
            md = frontmatter + "\n" + md

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md)

            print(f"  Converted: {filepath.name}")
            self.converted += 1
            return True

        except Exception as e:
            print(f"  Error converting {filepath.name}: {type(e).__name__}: {e}")
            self.failed += 1
            return False

    def copy_assets(self):
        """Copy asset directories (images, media) to the markdown output directory."""
        asset_dirs = ("images", "media")
        copied = 0
        for dirname in asset_dirs:
            src = self.input_dir / dirname
            if src.is_dir():
                dst = self.output_dir / dirname
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                file_count = sum(1 for _ in dst.rglob("*") if _.is_file())
                print(f"  Copied {dirname}/ ({file_count} files)")
                copied += file_count
        return copied

    def convert_all(self):
        print(f"Input:  {self.input_dir}")
        print(f"Output: {self.output_dir}")
        if self.flatten:
            print("Mode:   Flattened")
        print()

        from itertools import chain
        filtered = sorted(
            f for f in chain(self.input_dir.rglob("*.html"), self.input_dir.rglob("*.htm"))
            if not self.is_asset_directory(f.relative_to(self.input_dir))
        )

        print(f"Found {len(filtered)} HTML files")
        print()

        for filepath in filtered:
            self.convert_file(filepath)

        # Copy asset directories so image/css/font references work
        asset_count = self.copy_assets()

        print()
        print("Done!")
        print(f"  Converted: {self.converted}")
        print(f"  Skipped:   {self.skipped}")
        print(f"  Failed:    {self.failed}")
        if asset_count:
            print(f"  Assets:    {asset_count} files copied")
        print(f"Output: {self.output_dir}")


def generate_section_index(
    section_label: str,
    section_url: str,
    domain: str,
    articles: list[dict],
    sub_sections: list[dict],
) -> str:
    """Generate a section index.md with article listings and sub-section links.

    Args:
        section_label: Section name (e.g., "Sport")
        section_url: Full URL of the section page
        domain: Site domain
        articles: List of dicts with keys: title, filename, date, word_count,
                  description, thumbnail (path or None)
        sub_sections: List of dicts with keys: label, dir_name

    Returns:
        Complete markdown string with YAML frontmatter
    """
    metadata = {
        "title": section_label,
        "url": section_url,
        "page_type": "SECTION",
        "domain": domain,
        "articles": len(articles),
    }
    if sub_sections:
        metadata["sub_sections"] = len(sub_sections)

    frontmatter = HTMLToMarkdown._build_frontmatter(metadata)

    parts = [frontmatter, "", f"# {section_label}", ""]

    # Sub-sections listed before articles
    if sub_sections:
        parts.append("## Sub-sections")
        parts.append("")
        for sub in sub_sections:
            parts.append(f"- [{sub['label']}]({sub['dir_name']}/_index.md)")
        parts.append("")

    # Articles listing
    if articles:
        parts.append("## Articles")
        parts.append("")
        for article in articles:
            # Heading link
            parts.append(f"### [{article['title']}]({article['filename']})")

            # Optional thumbnail
            thumbnail = article.get("thumbnail")
            if thumbnail:
                if not thumbnail.startswith("images/"):
                    thumbnail = f"images/{thumbnail}"
                parts.append(f"![]({thumbnail})")

            # Metadata line: date and word count
            meta_parts = []
            if article.get("date"):
                meta_parts.append(_format_visible_date(article["date"]))
            if article.get("word_count"):
                meta_parts.append(f"{article['word_count']:,} words")
            if meta_parts:
                parts.append(" | ".join(meta_parts))

            # Optional description
            if article.get("description"):
                parts.append("")
                parts.append(article["description"])

            parts.append("")

    return "\n".join(parts)
