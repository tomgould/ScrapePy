"""Fixture-driven tests that validate parsing against real HTML from --audit runs.

Discovers all tests/fixtures/*/test_config.json files and validates:
- Homepage navigation parsing (link counts, expected section labels)
- Page classification (section vs article vs unknown)
- Article date extraction (expected dates match)
- Robots.txt sitemap parsing

All tests skip if fixtures don't exist. No network calls.
"""

import json
import os
from glob import glob

import pytest

from scrapepy.processing.navigation import extract_navigation
from scrapepy.processing.classifier import classify_page
from scrapepy.processing.dates import get_best_date
from scrapepy.processing.discovery import parse_robots_sitemaps

# Discover all fixture directories with test_config.json
_FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures")
_FIXTURE_CONFIGS = glob(os.path.join(_FIXTURE_ROOT, "*", "test_config.json"))


def _load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_fixture_html(fixture_dir: str, relative_path: str) -> str | None:
    filepath = os.path.join(fixture_dir, relative_path)
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Parametrize: one test per fixture site
# ---------------------------------------------------------------------------

def _fixture_ids() -> list[str]:
    """Generate readable test IDs from fixture directory names."""
    return [os.path.basename(os.path.dirname(p)) for p in _FIXTURE_CONFIGS]


@pytest.mark.skipif(not _FIXTURE_CONFIGS, reason="No audit fixtures found (run --audit first)")
class TestHomepageNavigation:
    """Validate homepage navigation parsing against real HTML."""

    @pytest.fixture(params=_FIXTURE_CONFIGS, ids=_fixture_ids())
    def fixture_data(self, request):
        config_path = request.param
        fixture_dir = os.path.dirname(config_path)
        tc = _load_config(config_path)
        return fixture_dir, tc

    def test_min_links(self, fixture_data):
        fixture_dir, tc = fixture_data
        nav = tc.get("navigation")
        if not nav:
            pytest.skip("No navigation config in test_config.json")

        html = _load_fixture_html(fixture_dir, nav["file"])
        if html is None:
            pytest.skip(f"Fixture file missing: {nav['file']}")

        base_url = tc["url"].rstrip("/")
        links = extract_navigation(html, base_url)
        assert len(links) >= nav["min_links"], (
            f"Expected >= {nav['min_links']} nav links, got {len(links)}"
        )

    def test_expected_sections(self, fixture_data):
        fixture_dir, tc = fixture_data
        nav = tc.get("navigation")
        if not nav or not nav.get("expected_sections"):
            pytest.skip("No expected_sections in test_config.json")

        html = _load_fixture_html(fixture_dir, nav["file"])
        if html is None:
            pytest.skip(f"Fixture file missing: {nav['file']}")

        base_url = tc["url"].rstrip("/")
        links = extract_navigation(html, base_url)
        found_labels = {l.label for l in links}

        for expected in nav["expected_sections"]:
            assert expected in found_labels, (
                f"Expected section '{expected}' not found in nav labels: {sorted(found_labels)}"
            )


@pytest.mark.skipif(not _FIXTURE_CONFIGS, reason="No audit fixtures found (run --audit first)")
class TestPageClassification:
    """Validate page classification against real HTML samples."""

    @staticmethod
    def _page_params():
        """Generate (fixture_dir, page_entry) pairs for parametrize."""
        params = []
        for config_path in _FIXTURE_CONFIGS:
            fixture_dir = os.path.dirname(config_path)
            tc = _load_config(config_path)
            site = os.path.basename(fixture_dir)
            for page in tc.get("pages", []):
                if "expected_type" in page:
                    params.append(
                        pytest.param(
                            (fixture_dir, page),
                            id=f"{site}/{page['path']}",
                        )
                    )
        return params

    @pytest.mark.parametrize("fixture_page", _page_params.__func__())
    def test_page_type(self, fixture_page):
        fixture_dir, page = fixture_page
        html = _load_fixture_html(fixture_dir, page["file"])
        if html is None:
            pytest.skip(f"Fixture file missing: {page['file']}")

        result = classify_page(page["path"], html)
        assert result.value == page["expected_type"], (
            f"Expected {page['expected_type']} for {page['path']}, got {result.value}"
        )


@pytest.mark.skipif(not _FIXTURE_CONFIGS, reason="No audit fixtures found (run --audit first)")
class TestArticleDates:
    """Validate date extraction from article fixture HTML."""

    @staticmethod
    def _article_params():
        """Generate (fixture_dir, page_entry) pairs for articles with expected dates."""
        params = []
        for config_path in _FIXTURE_CONFIGS:
            fixture_dir = os.path.dirname(config_path)
            tc = _load_config(config_path)
            site = os.path.basename(fixture_dir)
            for page in tc.get("pages", []):
                if page.get("expected_date"):
                    params.append(
                        pytest.param(
                            (fixture_dir, page),
                            id=f"{site}/{page['path']}",
                        )
                    )
        return params

    @pytest.mark.parametrize("fixture_page", _article_params.__func__())
    def test_date_extraction(self, fixture_page):
        fixture_dir, page = fixture_page
        html = _load_fixture_html(fixture_dir, page["file"])
        if html is None:
            pytest.skip(f"Fixture file missing: {page['file']}")

        result = get_best_date(page["path"], html)
        assert result is not None, f"No date found for {page['path']}"
        extracted_date = result.date.strftime("%Y-%m-%d")
        assert extracted_date == page["expected_date"], (
            f"Expected date {page['expected_date']} for {page['path']}, "
            f"got {extracted_date} (source: {result.source.name})"
        )


@pytest.mark.skipif(not _FIXTURE_CONFIGS, reason="No audit fixtures found (run --audit first)")
class TestRobotsParsing:
    """Validate robots.txt sitemap parsing against real fixtures."""

    @pytest.fixture(params=_FIXTURE_CONFIGS, ids=_fixture_ids())
    def fixture_data(self, request):
        config_path = request.param
        fixture_dir = os.path.dirname(config_path)
        tc = _load_config(config_path)
        return fixture_dir, tc

    def test_min_sitemaps(self, fixture_data):
        fixture_dir, tc = fixture_data
        discovery = tc.get("discovery", {})
        robots_file = discovery.get("robots_file")
        if not robots_file:
            pytest.skip("No robots_file in test_config.json")

        robots_path = os.path.join(fixture_dir, robots_file)
        if not os.path.exists(robots_path):
            pytest.skip(f"Fixture file missing: {robots_file}")

        with open(robots_path, "r", encoding="utf-8") as f:
            content = f.read()

        sitemaps = parse_robots_sitemaps(content)
        min_expected = discovery.get("min_sitemaps", 0)
        assert len(sitemaps) >= min_expected, (
            f"Expected >= {min_expected} sitemaps, got {len(sitemaps)}: {sitemaps}"
        )
