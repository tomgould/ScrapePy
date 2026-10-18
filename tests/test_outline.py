"""Tests for scrapepy.processing.outline, site outline building."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from scrapepy.config import ScrapeConfig
from scrapepy.processing.navigation import NavLink
from scrapepy.processing.outline import (
    NavSection,
    SiteOutline,
    build_site_outline,
    _assign_url_to_section,
)


NOW = datetime.now(timezone.utc)


def _config(**kwargs):
    """Create a ScrapeConfig with sensible defaults."""
    defaults = {"url": "https://example.com"}
    defaults.update(kwargs)
    return ScrapeConfig(**defaults)


# ── URL-to-section assignment ──────────────────────────────────────


class TestAssignUrlToSection:
    def setup_method(self):
        self.sections = [
            NavSection(label="F1", path="/f1", children=["/f1/news", "/f1/results"]),
            NavSection(label="MotoGP", path="/motogp", children=["/motogp/news"]),
            NavSection(label="News", path="/news"),
        ]

    def test_direct_child(self):
        """URL directly under a section path matches that section."""
        result = _assign_url_to_section("/f1/some-article", self.sections)
        assert result is not None
        assert result.label == "F1"

    def test_deep_child(self):
        """Deeply nested URL matches the section."""
        result = _assign_url_to_section("/f1/news/2026/02/some-article", self.sections)
        assert result is not None
        assert result.label == "F1"

    def test_child_path_match(self):
        """URL under a child path still maps to the parent section."""
        result = _assign_url_to_section("/f1/news/article-slug", self.sections)
        assert result is not None
        assert result.label == "F1"

    def test_longest_prefix_wins(self):
        """When multiple sections match, the longest prefix wins."""
        # /news/article could match /news, but /f1/news/article matches /f1 via child
        result = _assign_url_to_section("/news/article", self.sections)
        assert result is not None
        assert result.label == "News"

    def test_no_match(self):
        """URL not under any section returns None."""
        result = _assign_url_to_section("/about-us", self.sections)
        assert result is None

    def test_exact_section_path(self):
        """URL exactly matching a section path matches."""
        result = _assign_url_to_section("/f1", self.sections)
        assert result is not None
        assert result.label == "F1"

    def test_partial_path_no_match(self):
        """/f1-special should NOT match /f1 (must be prefix + /)."""
        result = _assign_url_to_section("/f1-special/article", self.sections)
        assert result is None


# ── Outline building ───────────────────────────────────────────────


class TestBuildSiteOutline:
    def test_basic_structure(self):
        """Build outline from nav links and discovery map."""
        nav_links = [
            NavLink("F1", "/f1", None, 0, "header"),
            NavLink("News", "/f1/news", "F1", 1, "header"),
            NavLink("MotoGP", "/motogp", None, 0, "header"),
            NavLink("About", "/about", None, 0, "footer"),
        ]
        discovery_map = {
            "/f1/article-1": NOW - timedelta(days=1),
            "/f1/article-2": NOW - timedelta(days=2),
            "/motogp/article-1": NOW - timedelta(days=3),
            "/unmatched/article": NOW - timedelta(days=1),
        }
        config = _config(recent="1w")

        outline = build_site_outline(nav_links, discovery_map, [], [], config)

        assert len(outline.nav_sections) == 2
        assert outline.nav_sections[0].label == "F1"
        assert "/f1/news" in outline.nav_sections[0].children
        assert outline.nav_sections[1].label == "MotoGP"
        assert len(outline.footer_links) == 1
        assert outline.footer_links[0]["label"] == "About"
        assert outline.sitemap_url_count == 4
        assert outline.uncategorized_articles == 1

    def test_article_counts_with_cutoff(self):
        """Recent counts respect cutoff_date."""
        nav_links = [NavLink("News", "/news", None, 0, "header")]
        discovery_map = {
            "/news/recent-1": NOW - timedelta(days=1),
            "/news/recent-2": NOW - timedelta(days=3),
            "/news/old-1": NOW - timedelta(days=30),
        }
        config = _config(recent="1w")

        outline = build_site_outline(nav_links, discovery_map, [], [], config)

        section = outline.nav_sections[0]
        assert section.article_count == 3
        assert section.recent_count == 2

    def test_empty_nav_links(self):
        """Outline with no nav links has empty sections."""
        discovery_map = {"/article": NOW}
        config = _config()

        outline = build_site_outline([], discovery_map, [], [], config)

        assert len(outline.nav_sections) == 0
        assert outline.uncategorized_articles == 0  # No sections to count against

    def test_empty_discovery_map(self):
        """Outline with nav links but no discovery data has zero counts."""
        nav_links = [NavLink("News", "/news", None, 0, "header")]
        config = _config()

        outline = build_site_outline(nav_links, {}, [], [], config)

        assert len(outline.nav_sections) == 1
        assert outline.nav_sections[0].article_count == 0

    def test_orphan_children_promoted(self):
        """Child nav links without a matching parent become their own sections."""
        nav_links = [
            NavLink("Sub", "/orphan/sub", "Missing Parent", 1, "header"),
        ]
        config = _config()

        outline = build_site_outline(nav_links, {}, [], [], config)

        assert len(outline.nav_sections) == 1
        assert outline.nav_sections[0].label == "Sub"
        assert outline.nav_sections[0].path == "/orphan/sub"

    def test_feeds_and_robots_stored(self):
        """Feed URLs and robots sitemaps are stored in the outline."""
        config = _config()
        feeds = ["https://example.com/rss"]
        robots = ["https://example.com/sitemap-news.xml"]

        outline = build_site_outline([], {}, feeds, robots, config)

        assert outline.rss_feeds == feeds
        assert outline.robots_sitemaps == robots

    def test_no_cutoff_date_skips_recent_count(self):
        """Without a cutoff date, recent_count stays 0."""
        nav_links = [NavLink("News", "/news", None, 0, "header")]
        discovery_map = {"/news/article": NOW - timedelta(days=1)}
        config = _config()  # No --recent or --since

        outline = build_site_outline(nav_links, discovery_map, [], [], config)

        assert outline.nav_sections[0].article_count == 1
        assert outline.nav_sections[0].recent_count == 0


# ── Serialization ──────────────────────────────────────────────────


class TestSiteOutlineSerialization:
    def test_to_json_roundtrip(self):
        """to_json produces valid JSON with all fields."""
        outline = SiteOutline(
            url="https://example.com",
            domain="example.com",
            timestamp="2026-02-25T12:00:00Z",
            nav_sections=[
                NavSection("News", "/news", ["/news/tech"], source="header", article_count=42, recent_count=10),
            ],
            footer_links=[{"label": "About", "path": "/about"}],
            sitemap_url_count=100,
            rss_feeds=["https://example.com/rss"],
            robots_sitemaps=["https://example.com/sitemap.xml"],
            uncategorized_articles=5,
        )

        data = json.loads(outline.to_json())

        assert data["url"] == "https://example.com"
        assert data["domain"] == "example.com"
        assert len(data["nav_sections"]) == 1
        assert data["nav_sections"][0]["label"] == "News"
        assert data["nav_sections"][0]["children"] == ["/news/tech"]
        assert data["nav_sections"][0]["article_count"] == 42
        assert data["nav_sections"][0]["recent_count"] == 10
        assert data["footer_links"] == [{"label": "About", "path": "/about"}]
        assert data["sitemap_url_count"] == 100
        assert data["uncategorized_articles"] == 5

    def test_save_creates_file(self):
        """save() writes site_outline.json to disk."""
        outline = SiteOutline(
            url="https://example.com",
            domain="example.com",
            timestamp="2026-02-25T12:00:00Z",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            outline.save(tmpdir)
            filepath = os.path.join(tmpdir, "site_outline.json")
            assert os.path.exists(filepath)
            with open(filepath) as f:
                data = json.load(f)
            assert data["url"] == "https://example.com"

    def test_save_creates_directory(self):
        """save() creates the output directory if it doesn't exist."""
        outline = SiteOutline(
            url="https://example.com",
            domain="example.com",
            timestamp="2026-02-25T12:00:00Z",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "deep", "nested")
            outline.save(nested)
            assert os.path.exists(os.path.join(nested, "site_outline.json"))


# ── Hierarchical sub-sections ─────────────────────────────────────


class TestNavSectionSubSections:
    def test_sub_sections_field_exists(self):
        """NavSection has an empty sub_sections list by default."""
        section = NavSection(label="Sport", path="/sport")
        assert section.sub_sections == []

    def test_sub_sections_populated(self):
        """NavSection can hold nested sub-sections."""
        child = NavSection(label="Football", path="/sport/football")
        section = NavSection(label="Sport", path="/sport", sub_sections=[child])
        assert len(section.sub_sections) == 1
        assert section.sub_sections[0].label == "Football"


class TestBuildHierarchicalOutline:
    def test_two_level_hierarchy(self):
        """Level-1 nav links become sub_sections of their parent."""
        nav_links = [
            NavLink("Sport", "/sport", None, 0, "header"),
            NavLink("Football", "/sport/football", "Sport", 1, "header"),
            NavLink("Cricket", "/sport/cricket", "Sport", 1, "header"),
            NavLink("News", "/news", None, 0, "header"),
        ]
        config = _config()
        outline = build_site_outline(nav_links, {}, [], [], config)
        sport = [s for s in outline.nav_sections if s.label == "Sport"][0]
        assert len(sport.sub_sections) == 2
        sub_labels = {s.label for s in sport.sub_sections}
        assert sub_labels == {"Football", "Cricket"}

    def test_three_level_hierarchy(self):
        """Level-2 nav links become sub_sections of their level-1 parent."""
        nav_links = [
            NavLink("Sport", "/sport", None, 0, "header"),
            NavLink("Football", "/sport/football", "Sport", 1, "header"),
            NavLink("Premier League", "/sport/football/premier-league", "Football", 2, "header"),
        ]
        config = _config()
        outline = build_site_outline(nav_links, {}, [], [], config)
        sport = outline.nav_sections[0]
        football = sport.sub_sections[0]
        assert football.label == "Football"
        assert len(football.sub_sections) == 1
        assert football.sub_sections[0].label == "Premier League"

    def test_sub_sections_are_crawl_targets(self):
        """all_section_paths() returns paths from all levels."""
        nav_links = [
            NavLink("Sport", "/sport", None, 0, "header"),
            NavLink("Football", "/sport/football", "Sport", 1, "header"),
        ]
        config = _config()
        outline = build_site_outline(nav_links, {}, [], [], config)
        all_paths = outline.all_section_paths()
        assert "/sport" in all_paths
        assert "/sport/football" in all_paths

    def test_serialization_includes_sub_sections(self):
        """to_json() includes sub_sections recursively."""
        section = NavSection("Sport", "/sport", sub_sections=[
            NavSection("Football", "/sport/football"),
        ])
        outline = SiteOutline(
            url="https://example.com",
            domain="example.com",
            timestamp="2026-02-26T12:00:00Z",
            nav_sections=[section],
        )
        data = json.loads(outline.to_json())
        assert "sub_sections" in data["nav_sections"][0]
        assert data["nav_sections"][0]["sub_sections"][0]["label"] == "Football"

    def test_backward_compat_children_still_work(self):
        """Sub-section paths are also added to children for backward compat."""
        nav_links = [
            NavLink("Sport", "/sport", None, 0, "header"),
            NavLink("Football", "/sport/football", "Sport", 1, "header"),
        ]
        discovery_map = {"/sport/football/article-1": NOW - timedelta(days=1)}
        config = _config(recent="1w")
        outline = build_site_outline(nav_links, discovery_map, [], [], config)
        sport = outline.nav_sections[0]
        assert "/sport/football" in sport.children
        assert sport.sub_sections[0].path == "/sport/football"

    def test_assign_url_checks_sub_sections(self):
        """_assign_url_to_section recurses into sub_sections for matching."""
        football = NavSection(label="Football", path="/sport/football")
        sport = NavSection(label="Sport", path="/sport", sub_sections=[football])
        sections = [sport]
        # Article under /sport/football should match sport (via sub-section)
        result = _assign_url_to_section("/sport/football/match-report", sections)
        assert result is not None
