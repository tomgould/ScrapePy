"""Tests for CLI flags, argument errors and exit codes (no network)."""

import pytest

from scrapepy import __version__
from scrapepy import cli
from scrapepy.cli import main, parse_args
from scrapepy.config import ScrapeConfig
from scrapepy.engine.fetcher import (
    DEFAULT_ASSET_USER_AGENT,
    asset_user_agent,
    page_headers,
)
from scrapepy.output.run_summary import EXIT_BAD_ARGS, EXIT_INTERRUPTED, EXIT_OK


class TestBadArguments:
    @pytest.mark.parametrize("argv", [
        ["https://example.com", "--since", "not-a-date"],
        ["https://example.com", "--recent", "soon"],
        ["https://example.com", "-c", "0"],
        ["https://example.com", "-c", "many"],
        ["https://example.com", "--wait", "-1"],
        ["https://example.com", "--user-agent", "  "],
        ["https://example.com", "--recent", "1d", "--since", "2026-01-01"],
        [],
    ])
    def test_returns_bad_args_code(self, argv, capsys):
        assert main(argv) == EXIT_BAD_ARGS
        assert "error:" in capsys.readouterr().err

    def test_bad_since_is_argparse_error_not_traceback(self):
        with pytest.raises(SystemExit) as exc:
            parse_args(["https://example.com", "--since", "2026-13-45"])
        assert exc.value.code == EXIT_BAD_ARGS

    def test_valid_since_accepted(self):
        config = parse_args(["https://example.com", "--since", "2026-01-01"])
        assert config.cutoff_date is not None


class TestVersion:
    def test_version_flag(self, capsys):
        assert main(["--version"]) == EXIT_OK
        assert __version__ in capsys.readouterr().out

    def test_version_string(self):
        assert __version__ == "0.2.0"


class TestMainReturnsCode:
    def test_returns_code_from_run(self, monkeypatch):
        monkeypatch.setattr(cli, "_run", lambda config: 1)
        assert main(["https://example.com"]) == 1

    def test_keyboard_interrupt(self, monkeypatch):
        def boom(config):
            raise KeyboardInterrupt
        monkeypatch.setattr(cli, "_run", boom)
        assert main(["https://example.com"]) == EXIT_INTERRUPTED

    def test_ignore_robots_prints_warning(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_run", lambda config: 0)
        main(["https://example.com", "--ignore-robots"])
        out = capsys.readouterr().out
        assert out.count("--ignore-robots") == 1
        assert out.startswith("Warning:")

    def test_no_warning_by_default(self, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_run", lambda config: 0)
        main(["https://example.com"])
        assert "ignore-robots" not in capsys.readouterr().out


class TestNewFlags:
    def test_defaults(self):
        config = parse_args(["https://example.com"])
        assert config.user_agent is None
        assert config.ignore_robots is False
        assert config.index_pages is True
        assert config.concurrent == 4

    def test_user_agent(self):
        config = parse_args(["https://example.com", "--user-agent", " MyBot/1.0 "])
        assert config.user_agent == "MyBot/1.0"

    def test_ignore_robots(self):
        assert parse_args(["https://example.com", "--ignore-robots"]).ignore_robots is True

    def test_no_index_pages(self):
        assert parse_args(["https://example.com", "--no-index-pages"]).index_pages is False


class TestUserAgentHeaders:
    def test_default_page_headers_leave_user_agent_to_scrapling(self):
        headers = page_headers(ScrapeConfig(url="https://example.com"))
        assert "User-Agent" not in headers

    def test_custom_user_agent_on_pages(self):
        config = ScrapeConfig(url="https://example.com", user_agent="MyBot/1.0")
        assert page_headers(config)["User-Agent"] == "MyBot/1.0"

    def test_request_headers_and_auth_kept(self):
        config = ScrapeConfig(url="https://example.com", user_agent="MyBot/1.0", auth="u:p")
        headers = page_headers(config, {"If-None-Match": "abc"})
        assert headers["If-None-Match"] == "abc"
        assert headers["Authorization"].startswith("Basic ")
        assert headers["User-Agent"] == "MyBot/1.0"

    def test_asset_user_agent(self):
        assert asset_user_agent(ScrapeConfig(url="https://example.com")) == DEFAULT_ASSET_USER_AGENT
        config = ScrapeConfig(url="https://example.com", user_agent="MyBot/1.0")
        assert asset_user_agent(config) == "MyBot/1.0"
