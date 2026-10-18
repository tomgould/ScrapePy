"""Tests for config.py — ScrapeConfig, _parse_recent, _parse_since."""

import base64
import pytest
from datetime import datetime, timezone

from scrapepy.config import ScrapeConfig, _parse_recent, _parse_since


# ── Duration parsing (_parse_recent) ────────────────────────────


class TestParseRecent:
    def test_weeks(self):
        cutoff = _parse_recent("2w")
        assert cutoff < datetime.now(timezone.utc)

    def test_days(self):
        cutoff = _parse_recent("3d")
        assert cutoff < datetime.now(timezone.utc)

    def test_hours(self):
        cutoff = _parse_recent("6h")
        assert cutoff < datetime.now(timezone.utc)

    def test_whitespace_stripped(self):
        cutoff = _parse_recent("  1d  ")
        assert cutoff < datetime.now(timezone.utc)

    def test_case_insensitive(self):
        cutoff = _parse_recent("2W")
        assert cutoff < datetime.now(timezone.utc)

    def test_invalid_unit(self):
        with pytest.raises(ValueError, match="Invalid --recent value"):
            _parse_recent("5m")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid --recent value"):
            _parse_recent("abc")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Invalid --recent value"):
            _parse_recent("")


# ── Absolute date parsing (_parse_since) ────────────────────────


class TestParseSince:
    def test_date_only(self):
        dt = _parse_since("2025-01-15")
        assert dt.year == 2025
        assert dt.month == 1
        assert dt.day == 15
        assert dt.tzinfo == timezone.utc

    def test_datetime_no_tz(self):
        dt = _parse_since("2025-06-01T10:30:00")
        assert dt.hour == 10
        assert dt.minute == 30
        assert dt.tzinfo == timezone.utc

    def test_datetime_with_tz(self):
        dt = _parse_since("2025-06-01T10:30:00+00:00")
        assert dt.tzinfo is not None

    def test_whitespace_stripped(self):
        dt = _parse_since("  2025-01-15  ")
        assert dt.year == 2025

    def test_invalid_date(self):
        with pytest.raises(ValueError, match="Invalid --since value"):
            _parse_since("not-a-date")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Invalid --since value"):
            _parse_since("")


# ── ScrapeConfig derived fields ─────────────────────────────────


class TestScrapeConfigDerived:
    def test_base_url(self):
        config = ScrapeConfig(url="https://example.com/path")
        assert config.base_url == "https://example.com"

    def test_domain(self):
        config = ScrapeConfig(url="https://www.example.com/path")
        assert config.domain == "www.example.com"

    def test_starting_path(self):
        config = ScrapeConfig(url="https://example.com/docs/guide")
        assert config.starting_path == "/docs/guide"

    def test_starting_path_root(self):
        config = ScrapeConfig(url="https://example.com/")
        assert config.starting_path == ""

    def test_starting_hash(self):
        config = ScrapeConfig(url="https://example.com/page#section")
        assert config.starting_hash == "section"

    def test_output_dir(self):
        config = ScrapeConfig(url="https://www.example.com/")
        assert "example_com" in config.output_dir_name

    def test_output_dir_with_stay_in_path(self):
        config = ScrapeConfig(url="https://example.com/docs", stay_in_path=True)
        assert "docs" in config.output_dir_name

    def test_cutoff_from_recent(self):
        config = ScrapeConfig(url="https://example.com", recent="2w")
        assert config.cutoff_date is not None
        assert config.cutoff_date < datetime.now(timezone.utc)

    def test_cutoff_from_since(self):
        config = ScrapeConfig(url="https://example.com", since="2025-01-01")
        assert config.cutoff_date is not None
        assert config.cutoff_date.year == 2025

    def test_no_cutoff_by_default(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.cutoff_date is None

    def test_instagram_output_dir(self):
        config = ScrapeConfig(url="https://instagram.com/user", instagram=True)
        assert config.output_dir_name == "instagram"


# ── ScrapeConfig properties ─────────────────────────────────────


class TestScrapeConfigProperties:
    def test_wait_time_default(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.wait_time == 3.0

    def test_wait_time_browser(self):
        config = ScrapeConfig(url="https://example.com", js_render=True)
        assert config.wait_time == 5.0

    def test_wait_time_custom(self):
        config = ScrapeConfig(url="https://example.com", wait=10.0)
        assert config.wait_time == 10.0

    def test_needs_browser_false(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.needs_browser is False

    def test_needs_browser_js(self):
        config = ScrapeConfig(url="https://example.com", js_render=True)
        assert config.needs_browser is True

    def test_needs_browser_single_page(self):
        config = ScrapeConfig(url="https://example.com", single_page=True)
        assert config.needs_browser is True

    def test_has_date_filter_false(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.has_date_filter is False

    def test_has_date_filter_recent(self):
        config = ScrapeConfig(url="https://example.com", recent="1w")
        assert config.has_date_filter is True

    def test_has_date_filter_since(self):
        config = ScrapeConfig(url="https://example.com", since="2025-01-01")
        assert config.has_date_filter is True


# ── ScrapeConfig default values ──────────────────────────────────


class TestScrapeConfigDefaults:
    def test_markdown_default_true(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.markdown is True

    def test_incremental_default_true(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.incremental is True

    def test_include_assets_default_true(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.include_assets is True

    def test_concurrent_default_10(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.concurrent == 10

    def test_markdown_explicit_false(self):
        config = ScrapeConfig(url="https://example.com", markdown=False)
        assert config.markdown is False

    def test_incremental_explicit_false(self):
        config = ScrapeConfig(url="https://example.com", incremental=False)
        assert config.incremental is False

    def test_include_assets_explicit_false(self):
        config = ScrapeConfig(url="https://example.com", include_assets=False)
        assert config.include_assets is False


# ── CLI default values ───────────────────────────────────────────


from scrapepy.cli import parse_args


class TestCLIDefaults:
    def test_markdown_default_on(self):
        config = parse_args(["https://example.com"])
        assert config.markdown is True

    def test_no_markdown_flag(self):
        config = parse_args(["https://example.com", "--no-markdown"])
        assert config.markdown is False

    def test_no_incremental_flag(self):
        config = parse_args(["https://example.com", "--no-incremental"])
        assert config.incremental is False

    def test_no_include_assets_flag(self):
        config = parse_args(["https://example.com", "--no-include-assets"])
        assert config.include_assets is False

    def test_concurrent_default_10(self):
        config = parse_args(["https://example.com"])
        assert config.concurrent == 10

    def test_m_flag_still_works(self):
        config = parse_args(["https://example.com", "-m"])
        assert config.markdown is True


# ── HTTP Auth ───────────────────────────────────────────────────


class TestAuth:
    def test_auth_default_none(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.auth is None

    def test_auth_header_empty_when_no_auth(self):
        config = ScrapeConfig(url="https://example.com")
        assert config.auth_header == {}

    def test_auth_header_basic(self):
        config = ScrapeConfig(url="https://example.com", auth="user:pass")
        expected_token = base64.b64encode(b"user:pass").decode()
        assert config.auth_header == {"Authorization": f"Basic {expected_token}"}

    def test_auth_header_with_colon_in_password(self):
        config = ScrapeConfig(url="https://example.com", auth="user:p@ss:word")
        expected_token = base64.b64encode(b"user:p@ss:word").decode()
        assert config.auth_header == {"Authorization": f"Basic {expected_token}"}

    def test_auth_cli_flag(self):
        config = parse_args(["https://example.com", "--auth", "admin:secret"])
        assert config.auth == "admin:secret"

    def test_auth_cli_invalid_format(self):
        with pytest.raises(SystemExit):
            parse_args(["https://example.com", "--auth", "nocolon"])

    def test_auth_cli_default_none(self):
        config = parse_args(["https://example.com"])
        assert config.auth is None

    def test_auth_from_url_credentials(self):
        config = ScrapeConfig(url="https://demo:review@beta.example.com/")
        assert config.auth == "demo:review"
        assert config.domain == "beta.example.com"
        assert config.base_url == "https://beta.example.com"
        assert "demo" not in config.url
        assert "review" not in config.url

    def test_auth_from_url_strips_credentials(self):
        config = ScrapeConfig(url="https://user:pass@example.com/path?q=1#frag")
        assert config.url == "https://example.com/path?q=1#frag"
        assert config.starting_path == "/path"
        assert config.starting_hash == "frag"

    def test_auth_from_url_no_password(self):
        config = ScrapeConfig(url="https://user@example.com/")
        assert config.auth == "user:"

    def test_auth_flag_overrides_url_credentials(self):
        config = ScrapeConfig(url="https://demo:review@example.com/", auth="real:creds")
        assert config.auth == "real:creds"
        assert config.domain == "example.com"

    def test_auth_from_url_with_port(self):
        config = ScrapeConfig(url="https://user:pass@example.com:8080/path")
        assert config.auth == "user:pass"
        assert config.domain == "example.com:8080"
        assert config.base_url == "https://example.com:8080"
