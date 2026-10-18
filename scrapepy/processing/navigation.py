"""Parse homepage navigation structure to extract site sections."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup, Tag

from .urls import NON_CONTENT_PATHS

# Common navigation class/id patterns
_NAV_PATTERNS = re.compile(
    r'nav|menu|main-menu|navigation|primary-nav|site-nav|global-nav',
    re.IGNORECASE,
)

# Dropdown/mega-menu class patterns
_DROPDOWN_PATTERNS = re.compile(
    r'dropdown|submenu|sub-menu|mega-menu|megamenu|flyout|panel',
    re.IGNORECASE,
)


@dataclass
class NavLink:
    label: str
    path: str
    parent_label: str | None
    level: int
    source: str  # "header", "footer", "nav"


def extract_navigation(html: str, base_url: str) -> list[NavLink]:
    """Extract navigation links from homepage HTML.

    Detection strategy (priority order):
    1. <header> containing <nav> or <a> elements
    2. <nav> elements (standalone or with role="navigation")
    3. Elements with nav-related classes
    4. <footer> containing <nav> or <a> elements
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []

    parsed_base = urlparse(base_url)
    nav_links: list[NavLink] = []
    seen_paths: set[str] = set()

    # 1. Header navigation — process each <nav> inside the header separately
    #    so a language-selector <ul> doesn't short-circuit the main nav <section>s
    header_navs_processed: set[int] = set()
    for header in soup.find_all("header"):
        inner_navs = header.find_all("nav")
        if inner_navs:
            for nav in inner_navs:
                _extract_from_container(nav, "header", base_url, parsed_base, nav_links, seen_paths)
                header_navs_processed.add(id(nav))
        else:
            _extract_from_container(header, "header", base_url, parsed_base, nav_links, seen_paths)

    # 2. Standalone <nav> elements (skip those already inside header)
    for nav in soup.find_all("nav"):
        if id(nav) in header_navs_processed:
            continue  # Already processed as part of header
        if nav.find_parent("header"):
            continue  # Inside header but not directly found (shouldn't happen, safety net)
        if nav.find_parent("footer"):
            continue  # Will process in footer pass
        _extract_from_container(nav, "nav", base_url, parsed_base, nav_links, seen_paths)

    # 3. Elements with nav-related classes (if we didn't find much above)
    if len(nav_links) < 3:
        for el in soup.find_all(attrs={"class": _NAV_PATTERNS}):
            if el.find_parent("header") or el.find_parent("footer"):
                continue  # Already processed
            if el.name in ("header", "footer", "nav"):
                continue  # Already handled
            _extract_from_container(el, "nav", base_url, parsed_base, nav_links, seen_paths)

    # 4. Footer navigation
    for footer in soup.find_all("footer"):
        _extract_from_container(footer, "footer", base_url, parsed_base, nav_links, seen_paths)

    return nav_links


def _extract_from_container(
    container: Tag,
    source: str,
    base_url: str,
    parsed_base,
    nav_links: list[NavLink],
    seen_paths: set[str],
):
    """Extract links from a navigation container, respecting hierarchy."""
    # Look for structured nav: <ul>/<li> nesting
    lists = container.find_all("ul", recursive=True)
    if lists:
        for ul in lists:
            # Skip deeply nested lists (they're handled via parent traversal)
            if ul.find_parent("ul", recursive=False):
                continue
            _extract_from_list(ul, source, base_url, parsed_base, nav_links, seen_paths, level=0, parent_label=None)
        return

    # Look for nested <section>/<div> containers (nested-section pattern):
    #   <section class="dd title"><a>F1</a>
    #     <section class="dd-menu"><a>News</a>...</section>
    #   </section>
    nested = _find_nested_nav_containers(container)
    if nested:
        _extract_from_nested_containers(
            nested, source, base_url, parsed_base, nav_links, seen_paths,
        )
        return

    # Flat list of <a> tags
    for a_tag in container.find_all("a", href=True):
        path = _resolve_nav_link(a_tag["href"], base_url, parsed_base)
        if path and path not in seen_paths:
            label = _clean_label(a_tag.get_text(strip=True))
            if label and len(label) <= 60:
                seen_paths.add(path)
                nav_links.append(NavLink(
                    label=label,
                    path=path,
                    parent_label=None,
                    level=0,
                    source=source,
                ))


def _find_nested_nav_containers(container: Tag) -> list[Tag]:
    """Find <section>/<div> elements that form a nested navigation.

    Searches all descendants for elements that have both:
    - A direct <a> tag (the parent nav item)
    - A nested child <section>/<div> containing more <a> tags (child items)

    Only returns the outermost matching containers (skips elements whose
    parent is already a match).
    """
    candidates: list[Tag] = []
    for child in container.find_all(["section", "div"]):
        # Must have a direct <a> tag
        direct_a = child.find("a", href=True, recursive=False)
        if not direct_a:
            continue
        # Must have a nested <section>/<div> with its own <a> tags
        for nested in child.find_all(["section", "div"], recursive=False):
            if nested == direct_a:
                continue
            if nested.find("a", href=True):
                candidates.append(child)
                break

    # Deduplicate: remove candidates that are descendants of other candidates
    if len(candidates) > 1:
        top_level = []
        for c in candidates:
            if not any(c != other and c in other.descendants for other in candidates):
                top_level.append(c)
        candidates = top_level

    return candidates


def _extract_from_nested_containers(
    containers: list[Tag],
    source: str,
    base_url: str,
    parsed_base,
    nav_links: list[NavLink],
    seen_paths: set[str],
):
    """Extract links from nested <section>/<div> navigation containers.

    Each container has a direct <a> (level 0 parent) and nested
    <section>/<div> children containing <a> tags (level 1 children).
    """
    for parent_container in containers:
        # Extract the parent link (direct <a> in the container)
        parent_a = parent_container.find("a", href=True, recursive=False)
        parent_label = None
        if parent_a:
            path = _resolve_nav_link(parent_a["href"], base_url, parsed_base)
            label = _clean_label(parent_a.get_text(strip=True))
            if path and label and len(label) <= 60 and path not in seen_paths:
                seen_paths.add(path)
                parent_label = label
                nav_links.append(NavLink(
                    label=label,
                    path=path,
                    parent_label=None,
                    level=0,
                    source=source,
                ))
            elif label and len(label) <= 60:
                parent_label = label

        # Extract child links from nested <section>/<div> elements
        for nested in parent_container.find_all(["section", "div"], recursive=False):
            if nested == parent_a:
                continue
            for a_tag in nested.find_all("a", href=True):
                path = _resolve_nav_link(a_tag["href"], base_url, parsed_base)
                if path and path not in seen_paths:
                    label = _clean_label(a_tag.get_text(strip=True))
                    if label and len(label) <= 60:
                        seen_paths.add(path)
                        nav_links.append(NavLink(
                            label=label,
                            path=path,
                            parent_label=parent_label,
                            level=1,
                            source=source,
                        ))


def _extract_from_list(
    ul: Tag,
    source: str,
    base_url: str,
    parsed_base,
    nav_links: list[NavLink],
    seen_paths: set[str],
    level: int,
    parent_label: str | None,
):
    """Extract links from a <ul>/<li> navigation list with hierarchy."""
    for li in ul.find_all("li", recursive=False):
        # Find the direct <a> in this <li>
        a_tag = li.find("a", href=True, recursive=False)
        if not a_tag:
            # Sometimes <a> is wrapped in <span> or <div>
            a_tag = li.find("a", href=True)
            # But make sure it's not from a nested <ul> or dropdown <div>
            if a_tag:
                nested_ul = li.find("ul")
                if nested_ul and a_tag in nested_ul.find_all("a"):
                    a_tag = None
                elif not nested_ul:
                    dropdown = _find_dropdown_container(li)
                    if dropdown and a_tag in dropdown.find_all("a"):
                        a_tag = None

        current_label = None
        if a_tag:
            path = _resolve_nav_link(a_tag["href"], base_url, parsed_base)
            label = _clean_label(a_tag.get_text(strip=True))
            if path and label and len(label) <= 60 and path not in seen_paths:
                seen_paths.add(path)
                current_label = label
                nav_links.append(NavLink(
                    label=label,
                    path=path,
                    parent_label=parent_label,
                    level=level,
                    source=source,
                ))
        else:
            # Check for <button> trigger (no <a> tag, but has a button label)
            button = li.find("button", recursive=False)
            if button:
                current_label = _clean_label(button.get_text(strip=True))
                if not current_label or len(current_label) > 60:
                    current_label = None

        # Recurse into nested <ul> (may be wrapped in a <div> in modern navs)
        nested_ul = li.find("ul")
        if nested_ul:
            _extract_from_list(
                nested_ul, source, base_url, parsed_base,
                nav_links, seen_paths,
                level=level + 1,
                parent_label=current_label or parent_label,
            )
        else:
            # Fall back to <div>-based dropdown menus
            dropdown = _find_dropdown_container(li)
            if dropdown:
                _extract_dropdown_links(
                    dropdown, source, base_url, parsed_base,
                    nav_links, seen_paths,
                    level=level + 1,
                    parent_label=current_label or parent_label,
                )


def _find_dropdown_container(li: Tag) -> Tag | None:
    """Find a dropdown <div> inside a <li> element.

    Checks direct child <div> elements for:
    1. Dropdown-related CSS classes (dropdown, submenu, mega-menu, etc.)
    2. role="menu" attribute
    3. Any direct child <div> containing <a> links (fallback)
    """
    for div in li.find_all("div", recursive=False):
        # Check for dropdown-related classes
        css_classes = " ".join(div.get("class", []))
        if css_classes and _DROPDOWN_PATTERNS.search(css_classes):
            return div
        # Check for role="menu"
        if div.get("role") == "menu":
            return div

    # Fallback: any direct child <div> that contains links
    for div in li.find_all("div", recursive=False):
        if div.find("a", href=True):
            return div

    return None


def _extract_dropdown_links(
    container: Tag,
    source: str,
    base_url: str,
    parsed_base,
    nav_links: list[NavLink],
    seen_paths: set[str],
    level: int,
    parent_label: str | None,
):
    """Extract links from a dropdown <div> container.

    Handles nested sub-dropdown divs for multi-level hierarchy:
    - First checks for nested <ul> inside the dropdown (delegates to _extract_from_list)
    - Otherwise extracts direct <a> tags and recurses into sub-dropdown <div> elements
    """
    # If the dropdown contains a <ul>, delegate to the standard list handler
    nested_ul = container.find("ul", recursive=False)
    if nested_ul:
        _extract_from_list(
            nested_ul, source, base_url, parsed_base,
            nav_links, seen_paths, level=level, parent_label=parent_label,
        )
        return

    # Find sub-dropdown divs (children that themselves contain links in nested divs)
    sub_dropdowns: set[Tag] = set()
    for div in container.find_all("div", recursive=False):
        css_classes = " ".join(div.get("class", []))
        if css_classes and _DROPDOWN_PATTERNS.search(css_classes):
            sub_dropdowns.add(div)
        elif div.get("role") == "menu":
            sub_dropdowns.add(div)
        # Also check grandchild divs (e.g. <div class="dropdown-section"> wrapping
        # an <a> plus a <div class="sub-dropdown">)
        for inner_div in div.find_all("div", recursive=False):
            inner_classes = " ".join(inner_div.get("class", []))
            if inner_classes and _DROPDOWN_PATTERNS.search(inner_classes):
                sub_dropdowns.add(inner_div)
            elif inner_div.get("role") == "menu":
                sub_dropdowns.add(inner_div)

    # Extract direct <a> tags (skip those inside sub-dropdowns)
    for a_tag in container.find_all("a", href=True):
        # Skip links that are inside a sub-dropdown (they'll be handled recursively)
        if any(a_tag in sd.find_all("a") for sd in sub_dropdowns):
            continue
        path = _resolve_nav_link(a_tag["href"], base_url, parsed_base)
        label = _clean_label(a_tag.get_text(strip=True))
        if path and label and len(label) <= 60 and path not in seen_paths:
            seen_paths.add(path)
            nav_links.append(NavLink(
                label=label,
                path=path,
                parent_label=parent_label,
                level=level,
                source=source,
            ))

    # Recurse into sub-dropdown divs for deeper hierarchy
    for sd in sub_dropdowns:
        # Determine parent label for this sub-dropdown: look for a sibling <a> tag
        sd_parent = parent_label
        # Check if there's an <a> sibling in the same parent div
        sd_container = sd.parent
        if sd_container and sd_container != container:
            sibling_a = sd_container.find("a", href=True, recursive=False)
            if sibling_a:
                sibling_label = _clean_label(sibling_a.get_text(strip=True))
                if sibling_label:
                    sd_parent = sibling_label
        _extract_dropdown_links(
            sd, source, base_url, parsed_base,
            nav_links, seen_paths,
            level=level + 1,
            parent_label=sd_parent,
        )


def _resolve_nav_link(href: str, base_url: str, parsed_base) -> str | None:
    """Resolve a navigation href to an absolute path, or None if invalid."""
    if not href:
        return None
    href = href.strip()

    # Skip non-page links
    if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:", "blob:")):
        return None

    # Skip external links
    if href.startswith("//"):
        other_host = href.split("/")[2] if len(href.split("/")) > 2 else ""
        if other_host != parsed_base.netloc:
            return None

    if href.startswith(("http://", "https://")):
        parsed = urlparse(href)
        if parsed.netloc != parsed_base.netloc:
            return None
        path = parsed.path
    elif href.startswith("/"):
        path = href
    else:
        path = "/" + href

    # Strip query and fragment
    path = path.split("?")[0].split("#")[0]
    if not path or path == "/":
        return None

    # Normalize trailing slash
    path = path.rstrip("/")
    if not path:
        return None

    # Skip non-content paths
    if NON_CONTENT_PATHS.search(path):
        return None

    # Skip file extensions (images, PDFs, etc.)
    from .urls import SKIP_EXTENSIONS
    if SKIP_EXTENSIONS.search(path):
        return None

    return path


def _clean_label(text: str) -> str:
    """Clean up navigation label text."""
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Remove common icon/arrow characters
    text = re.sub(r'[→←↓↑▶▼►▾›»«‹]', '', text).strip()
    return text
