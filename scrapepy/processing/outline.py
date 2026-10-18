"""Build and serialize a site outline from navigation + discovery data."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import ScrapeConfig
from .navigation import NavLink
from .discovery import DiscoveryMap


@dataclass
class NavSection:
    label: str
    path: str
    children: list[str] = field(default_factory=list)
    sub_sections: list['NavSection'] = field(default_factory=list)
    source: str = "header"
    article_count: int = 0
    recent_count: int = 0


@dataclass
class SiteOutline:
    url: str
    domain: str
    timestamp: str
    nav_sections: list[NavSection] = field(default_factory=list)
    footer_links: list[dict] = field(default_factory=list)
    sitemap_url_count: int = 0
    rss_feeds: list[str] = field(default_factory=list)
    robots_sitemaps: list[str] = field(default_factory=list)
    uncategorized_articles: int = 0

    def all_section_paths(self) -> set[str]:
        """Return flat set of all section paths including sub-sections."""
        paths: set[str] = set()

        def _collect(sections: list[NavSection]):
            for s in sections:
                paths.add(s.path.rstrip("/") or "/")
                _collect(s.sub_sections)

        _collect(self.nav_sections)
        return paths

    def to_json(self) -> str:
        """Serialize to JSON string."""

        def _section_to_dict(s: NavSection) -> dict:
            return {
                "label": s.label,
                "path": s.path,
                "children": s.children,
                "sub_sections": [_section_to_dict(sub) for sub in s.sub_sections],
                "source": s.source,
                "article_count": s.article_count,
                "recent_count": s.recent_count,
            }

        data = {
            "url": self.url,
            "domain": self.domain,
            "timestamp": self.timestamp,
            "nav_sections": [_section_to_dict(s) for s in self.nav_sections],
            "footer_links": self.footer_links,
            "sitemap_url_count": self.sitemap_url_count,
            "rss_feeds": self.rss_feeds,
            "robots_sitemaps": self.robots_sitemaps,
            "uncategorized_articles": self.uncategorized_articles,
        }
        return json.dumps(data, indent=2, ensure_ascii=False)

    def save(self, output_dir: str):
        """Save outline as site_outline.json in the output directory."""
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, "site_outline.json")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.to_json())


def _assign_url_to_section(
    url_path: str, sections: list[NavSection],
) -> NavSection | None:
    """Find the nav section with the longest matching path prefix for a URL."""
    best_match: NavSection | None = None
    best_len = 0

    def _check(section_list: list[NavSection]):
        nonlocal best_match, best_len
        for section in section_list:
            prefix = section.path.rstrip("/") + "/"
            if url_path.startswith(prefix) or url_path == section.path:
                if len(section.path) > best_len:
                    best_match = section
                    best_len = len(section.path)

            # Also check children as prefixes
            for child in section.children:
                child_prefix = child.rstrip("/") + "/"
                if url_path.startswith(child_prefix) or url_path == child:
                    if len(child) > best_len:
                        best_match = section
                        best_len = len(child)

            _check(section.sub_sections)

    _check(sections)
    return best_match


def build_site_outline(
    nav_links: list[NavLink],
    discovery_map: DiscoveryMap,
    feed_urls: list[str],
    robots_sitemaps: list[str],
    config: ScrapeConfig,
) -> SiteOutline:
    """Build a SiteOutline from navigation links and discovery data."""
    outline = SiteOutline(
        url=config.url,
        domain=config.domain,
        timestamp=datetime.now(timezone.utc).isoformat(),
        sitemap_url_count=len(discovery_map),
        rss_feeds=feed_urls,
        robots_sitemaps=robots_sitemaps,
    )

    # Build sections from top-level nav links
    sections_by_path: dict[str, NavSection] = {}
    all_sections_by_path: dict[str, NavSection] = {}  # Flat lookup for all levels
    footer_links: list[dict] = []

    for link in nav_links:
        if link.source == "footer":
            footer_links.append({"label": link.label, "path": link.path})
            continue

        if link.level == 0:
            # Top-level = new section
            if link.path not in sections_by_path:
                section = NavSection(
                    label=link.label,
                    path=link.path,
                    source=link.source,
                )
                sections_by_path[link.path] = section
                all_sections_by_path[link.path] = section
        else:
            # Child link, find parent section by label matching
            parent_section = None
            for nav in nav_links:
                if nav.label == link.parent_label and nav.level == link.level - 1:
                    parent_section = all_sections_by_path.get(nav.path)
                    break

            if parent_section:
                child_section = NavSection(
                    label=link.label,
                    path=link.path,
                    source=link.source,
                )
                parent_section.sub_sections.append(child_section)
                all_sections_by_path[link.path] = child_section
                # Also add to children for backward compat
                if link.path not in parent_section.children and link.path != parent_section.path:
                    parent_section.children.append(link.path)
            elif link.path not in sections_by_path and link.path not in all_sections_by_path:
                # Orphan child, promote to top-level section
                section = NavSection(
                    label=link.label,
                    path=link.path,
                    source=link.source,
                )
                sections_by_path[link.path] = section
                all_sections_by_path[link.path] = section

    outline.nav_sections = list(sections_by_path.values())
    outline.footer_links = footer_links

    # Count sitemap URLs per section
    if discovery_map and outline.nav_sections:
        uncategorized = 0
        for url_path, dt in discovery_map.items():
            section = _assign_url_to_section(url_path, outline.nav_sections)
            if section:
                section.article_count += 1
                if config.cutoff_date and dt >= config.cutoff_date:
                    section.recent_count += 1
            else:
                uncategorized += 1
        outline.uncategorized_articles = uncategorized

    return outline
