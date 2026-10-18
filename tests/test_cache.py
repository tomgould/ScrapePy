"""Tests for output/cache.py, CrawlCache in-memory operations."""

import json
import os
import pytest
import tempfile

from scrapepy.output.cache import CrawlCache, _get_header, CACHE_VERSION


# ── Case-insensitive header lookup ──────────────────────────────


class TestGetHeader:
    def test_lowercase_key(self):
        assert _get_header({"etag": '"abc"'}, "etag") == '"abc"'

    def test_mixed_case_key(self):
        assert _get_header({"ETag": '"abc"'}, "etag") == '"abc"'

    def test_missing_key(self):
        assert _get_header({}, "etag") == ""

    def test_prefers_lowercase(self):
        headers = {"etag": "lower", "ETag": "upper"}
        assert _get_header(headers, "etag") == "lower"


# ── Page cache operations ───────────────────────────────────────


class TestCrawlCachePages:
    def _make_cache(self):
        return CrawlCache("/tmp/test_output")

    def test_get_page_headers_empty(self):
        cache = self._make_cache()
        assert cache.get_page_headers("/page") == {}

    def test_update_then_get_headers(self):
        cache = self._make_cache()
        cache.update_page("/page", {"etag": '"v1"', "last-modified": "Mon, 01 Jan 2025 00:00:00 GMT"}, "content")
        headers = cache.get_page_headers("/page")
        assert headers["If-None-Match"] == '"v1"'
        assert headers["If-Modified-Since"] == "Mon, 01 Jan 2025 00:00:00 GMT"

    def test_update_stores_content_hash(self):
        cache = self._make_cache()
        cache.update_page("/page", {}, "hello world")
        assert cache.is_page_unchanged("/page", "hello world") is True
        assert cache.is_page_unchanged("/page", "different content") is False

    def test_is_page_unchanged_no_entry(self):
        cache = self._make_cache()
        assert cache.is_page_unchanged("/missing", "content") is False

    def test_get_page_links(self):
        cache = self._make_cache()
        cache.update_page("/page", {}, "content", links=["/a", "/b"])
        assert cache.get_page_links("/page") == ["/a", "/b"]

    def test_get_page_links_empty(self):
        cache = self._make_cache()
        assert cache.get_page_links("/missing") == []

    def test_get_page_date(self):
        cache = self._make_cache()
        cache.update_page("/page", {}, "content", published_date="2025-03-15")
        assert cache.get_page_date("/page") == "2025-03-15"

    def test_get_page_date_none(self):
        cache = self._make_cache()
        assert cache.get_page_date("/missing") is None

    def test_get_page_type(self):
        cache = self._make_cache()
        cache.update_page("/page", {}, "content", page_type="article")
        assert cache.get_page_type("/page") == "article"

    def test_get_page_type_none(self):
        cache = self._make_cache()
        assert cache.get_page_type("/missing") is None


# ── Asset cache operations ──────────────────────────────────────


class TestCrawlCacheAssets:
    def _make_cache(self):
        return CrawlCache("/tmp/test_output")

    def test_has_asset_false(self):
        cache = self._make_cache()
        assert cache.has_asset("https://cdn.example.com/img.jpg") is False

    def test_update_then_has_asset(self):
        cache = self._make_cache()
        cache.update_asset("https://cdn.example.com/img.jpg", {"etag": '"v1"'})
        assert cache.has_asset("https://cdn.example.com/img.jpg") is True

    def test_get_asset_headers_empty(self):
        cache = self._make_cache()
        assert cache.get_asset_headers("https://cdn.example.com/img.jpg") == {}

    def test_update_then_get_asset_headers(self):
        cache = self._make_cache()
        cache.update_asset("https://cdn.example.com/img.jpg", {"etag": '"v2"', "last-modified": "Sat, 15 Mar 2025 10:30:00 GMT"})
        headers = cache.get_asset_headers("https://cdn.example.com/img.jpg")
        assert headers["If-None-Match"] == '"v2"'
        assert headers["If-Modified-Since"] == "Sat, 15 Mar 2025 10:30:00 GMT"


# ── Save and load ──────────────────────────────────────────────


class TestCrawlCachePersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save
            cache1 = CrawlCache(tmpdir)
            cache1.update_page("/page", {"etag": '"v1"'}, "content", links=["/a"])
            cache1.save()

            # Load
            cache2 = CrawlCache(tmpdir)
            cache2.load()
            assert cache2.get_page_links("/page") == ["/a"]
            assert cache2.is_page_unchanged("/page", "content") is True

    def test_load_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CrawlCache(tmpdir)
            cache.load()  # Should not raise
            assert cache.get_page_links("/page") == []

    def test_load_corrupt_file(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".scrapepy_cache.json")
            with open(path, "w") as f:
                f.write("not valid json")
            cache = CrawlCache(tmpdir)
            cache.load()
            captured = capsys.readouterr()
            assert "corrupt" in captured.out.lower()

    def test_load_wrong_version(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, ".scrapepy_cache.json")
            with open(path, "w") as f:
                json.dump({"version": 999, "pages": {}, "assets": {}}, f)
            cache = CrawlCache(tmpdir)
            cache.load()
            captured = capsys.readouterr()
            assert "version mismatch" in captured.out.lower()

    def test_compact_json_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = CrawlCache(tmpdir)
            cache.update_page("/page", {}, "content")
            cache.save()
            path = os.path.join(tmpdir, ".scrapepy_cache.json")
            raw = open(path).read()
            # Compact format: no indentation, no spaces after colons/commas
            assert "\n" not in raw.strip() or raw.count("\n") <= 1
