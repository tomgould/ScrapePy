"""Tests for run_summary.json and exit codes (pure logic)."""

import json
import os

from scrapepy.config import ScrapeConfig
from scrapepy.output.run_summary import (
    EXIT_TERMINATED,
    EXIT_BAD_ARGS,
    EXIT_INTERRUPTED,
    EXIT_NOTHING_FETCHED,
    EXIT_OK,
    SCHEMA_VERSION,
    SUMMARY_FILENAME,
    RunStats,
    build_summary,
    exit_code_for,
    options_used,
    write_summary,
)
from scrapepy.scraper import exit_code_for_pages

TOP_LEVEL_KEYS = {
    "schema_version", "status", "start_url", "started_at", "ended_at", "output_dir",
    "html_dir", "markdown_dir", "options", "counts", "new_markdown",
    "changed_markdown", "unchanged_urls", "failed", "blocked_by_robots",
    "interrupted", "interrupted_by", "exit_code", "duplicates",
}
COUNT_KEYS = {"fetched", "new", "changed", "unchanged", "failed", "blocked_by_robots", "skipped_old",
              "duplicates"}


def _config(tmp_path, **kw):
    return ScrapeConfig(url="https://example.com/", output_root=str(tmp_path), **kw)


def _stats(config):
    stats = RunStats(started_at="2026-01-01T00:00:00+00:00")
    stats.fetched = 5
    stats.record_saved("https://example.com/", False,
                       os.path.join(config.markdown_dir, "index.md"))
    stats.record_saved("https://example.com/a", True,
                       os.path.join(config.markdown_dir, "a.md"))
    stats.record_saved("https://example.com/short", False, None)
    stats.unchanged.append("https://example.com/b")
    stats.record_failed("https://example.com/c", "HTTP 404")
    stats.record_blocked("https://example.com/private/x")
    stats.record_blocked("https://example.com/private/x")
    return stats


class TestSummarySchema:
    def test_top_level_keys(self, tmp_path):
        config = _config(tmp_path)
        summary = build_summary(config, _stats(config), ended_at="2026-01-01T00:01:00+00:00")
        assert set(summary) == TOP_LEVEL_KEYS
        assert summary["schema_version"] == SCHEMA_VERSION
        assert summary["start_url"] == "https://example.com/"
        assert summary["started_at"] == "2026-01-01T00:00:00+00:00"
        assert summary["ended_at"] == "2026-01-01T00:01:00+00:00"

    def test_counts(self, tmp_path):
        config = _config(tmp_path)
        counts = build_summary(config, _stats(config))["counts"]
        assert set(counts) == COUNT_KEYS
        assert counts == {
            "fetched": 5, "new": 2, "changed": 1, "unchanged": 1,
            "failed": 1, "blocked_by_robots": 1, "skipped_old": 0, "duplicates": 0,
        }

    def test_paths_relative_to_output_root(self, tmp_path):
        config = _config(tmp_path)
        summary = build_summary(config, _stats(config))
        assert summary["output_dir"] == str(tmp_path)
        assert summary["html_dir"] == "ScrapedSites/example_com"
        assert summary["markdown_dir"] == "MarkdownSites/example_com"
        assert summary["new_markdown"] == ["MarkdownSites/example_com/index.md"]
        assert summary["changed_markdown"] == ["MarkdownSites/example_com/a.md"]

    def test_lists(self, tmp_path):
        config = _config(tmp_path)
        summary = build_summary(config, _stats(config))
        assert summary["unchanged_urls"] == ["https://example.com/b"]
        assert summary["failed"] == [{"url": "https://example.com/c", "reason": "HTTP 404"}]
        assert summary["blocked_by_robots"] == ["https://example.com/private/x"]

    def test_markdown_dir_null_without_markdown(self, tmp_path):
        config = _config(tmp_path, markdown=False)
        assert build_summary(config, RunStats())["markdown_dir"] is None

    def test_is_json_serialisable(self, tmp_path):
        config = _config(tmp_path)
        json.dumps(build_summary(config, _stats(config)))


class TestOptionsUsed:
    def test_never_includes_credentials(self, tmp_path):
        config = _config(tmp_path, auth="user:secret-pass", proxy="http://u:p@proxy:8080")
        opts = options_used(config)
        text = json.dumps(opts)
        assert "secret-pass" not in text
        assert "proxy:8080" not in text
        assert opts["auth_used"] is True
        assert opts["proxy_used"] is True

    def test_records_responsible_use_settings(self, tmp_path):
        config = _config(tmp_path, user_agent="Bot/1.0", ignore_robots=True, index_pages=False)
        opts = options_used(config)
        assert opts["user_agent"] == "Bot/1.0"
        assert opts["ignore_robots"] is True
        assert opts["index_pages"] is False
        assert opts["concurrent"] == 4


class TestWriteSummary:
    def test_writes_into_site_output_folder(self, tmp_path):
        config = _config(tmp_path)
        summary = build_summary(config, _stats(config))
        path = write_summary(config, summary)
        assert path == os.path.join(config.output_dir, SUMMARY_FILENAME)
        with open(path, encoding="utf-8") as f:
            assert json.load(f) == summary


class TestExitCodes:
    def test_codes_are_distinct(self):
        assert len({EXIT_OK, EXIT_NOTHING_FETCHED, EXIT_BAD_ARGS, EXIT_INTERRUPTED}) == 4

    def test_success(self):
        stats = RunStats(fetched=3)
        stats.record_failed("u", "HTTP 500")
        assert exit_code_for(stats) == EXIT_OK

    def test_nothing_fetched(self):
        assert exit_code_for(RunStats()) == EXIT_NOTHING_FETCHED

    def test_everything_blocked_counts_as_nothing_fetched(self):
        stats = RunStats()
        stats.record_blocked("https://example.com/")
        assert exit_code_for(stats) == EXIT_NOTHING_FETCHED

    def test_every_fetch_failed(self):
        stats = RunStats(fetched=2)
        stats.record_failed("a", "HTTP 404")
        stats.record_failed("b", "HTTP 500")
        assert exit_code_for(stats) == EXIT_NOTHING_FETCHED

    def test_all_unchanged_is_success(self):
        stats = RunStats(fetched=2, unchanged=["a", "b"])
        assert exit_code_for(stats) == EXIT_OK

    def test_interrupted(self):
        assert exit_code_for(RunStats(fetched=4, interrupted=True)) == EXIT_INTERRUPTED

    def test_summary_carries_exit_code(self, tmp_path):
        assert build_summary(_config(tmp_path), RunStats())["exit_code"] == EXIT_NOTHING_FETCHED

    def test_page_count_modes(self):
        assert exit_code_for_pages(1) == EXIT_OK
        assert exit_code_for_pages(0) == EXIT_NOTHING_FETCHED
        assert exit_code_for_pages(None) == EXIT_OK


class TestStatus:
    def test_in_progress_placeholder(self, tmp_path):
        summary = build_summary(_config(tmp_path), RunStats(), in_progress=True)
        assert summary["status"] == "running"
        assert summary["ended_at"] is None
        assert summary["exit_code"] is None

    def test_complete(self, tmp_path):
        assert build_summary(_config(tmp_path), RunStats(fetched=1))["status"] == "complete"

    def test_interrupted(self, tmp_path):
        summary = build_summary(_config(tmp_path), RunStats(fetched=1, interrupted=True))
        assert summary["status"] == "interrupted"
        assert summary["exit_code"] == EXIT_INTERRUPTED


class TestAtomicWrite:
    def test_replaces_existing_file_and_leaves_no_temp(self, tmp_path):
        config = _config(tmp_path)
        write_summary(config, {"old": True})
        write_summary(config, {"new": True})
        with open(os.path.join(config.output_dir, SUMMARY_FILENAME), encoding="utf-8") as f:
            assert json.load(f) == {"new": True}
        assert os.listdir(config.output_dir) == [SUMMARY_FILENAME]

    def test_failed_write_keeps_previous_file(self, tmp_path):
        import pytest
        config = _config(tmp_path)
        write_summary(config, {"old": True})
        with pytest.raises(TypeError):
            write_summary(config, {"bad": object()})
        with open(os.path.join(config.output_dir, SUMMARY_FILENAME), encoding="utf-8") as f:
            assert json.load(f) == {"old": True}
        assert os.listdir(config.output_dir) == [SUMMARY_FILENAME]


class TestSigterm:
    def test_sigterm_exit_code(self, tmp_path):
        stats = RunStats(fetched=2, interrupted=True, interrupted_by="SIGTERM")
        assert exit_code_for(stats) == EXIT_TERMINATED == 143
        summary = build_summary(_config(tmp_path), stats)
        assert summary["interrupted_by"] == "SIGTERM"
        assert summary["exit_code"] == 143

    def test_interrupt_helpers(self):
        import signal
        from scrapepy import interrupts
        assert interrupts.exit_code(signal.SIGINT) == 130
        assert interrupts.exit_code(signal.SIGTERM) == 143
        assert interrupts.signal_name(None) == "SIGINT"
        interrupts.reset()
        interrupts.record(signal.SIGTERM)
        interrupts.record(signal.SIGINT)
        assert interrupts.received() == signal.SIGTERM
        interrupts.reset()


class TestReadersNeverSeePartialFile:
    def test_final_path_untouched_until_complete(self, tmp_path, monkeypatch):
        """Fails if the summary is written straight to its final path."""
        import scrapepy.output.run_summary as rs
        config = _config(tmp_path)
        path = os.path.join(config.output_dir, SUMMARY_FILENAME)
        write_summary(config, {"old": True})
        seen_during_write = []
        real_dump = json.dump

        def spying_dump(obj, f, **kw):
            real_dump(obj, f, **kw)
            f.flush()
            with open(path, encoding="utf-8") as reader:
                seen_during_write.append(reader.read())

        monkeypatch.setattr(rs.json, "dump", spying_dump)
        write_summary(config, {"new": True})
        assert len(seen_during_write) == 1
        assert json.loads(seen_during_write[0]) == {"old": True}
        with open(path, encoding="utf-8") as f:
            assert json.load(f) == {"new": True}

    def test_uses_os_replace_from_same_folder(self, tmp_path, monkeypatch):
        import scrapepy.output.run_summary as rs
        config = _config(tmp_path)
        calls = []
        real_replace = os.replace

        def spy(src, dst):
            calls.append((src, dst))
            real_replace(src, dst)

        monkeypatch.setattr(rs.os, "replace", spy)
        path = write_summary(config, {"x": 1})
        assert len(calls) == 1
        src, dst = calls[0]
        assert dst == path
        assert os.path.dirname(src) == os.path.dirname(path)
