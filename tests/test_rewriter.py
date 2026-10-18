"""Tests for processing/rewriter.py — HTML URL rewriting for offline viewing."""

import pytest

from scrapepy.processing.rewriter import rewrite_urls
from scrapepy.config import ScrapeConfig


def _make_config(url="https://example.com", stay_in_path=False, **kw):
    return ScrapeConfig(url=url, stay_in_path=stay_in_path, **kw)


# ── href rewriting ──────────────────────────────────────────────


class TestRewriteHrefs:
    def test_internal_link(self):
        config = _make_config()
        html = '<a href="/other-page">Link</a>'
        result = rewrite_urls(html, "/current", config)
        assert ".html" in result
        assert "other-page" in result

    def test_external_link_unchanged(self):
        config = _make_config()
        html = '<a href="https://other.com/page">Link</a>'
        result = rewrite_urls(html, "/current", config)
        assert "https://other.com/page" in result

    def test_css_link(self):
        config = _make_config()
        html = '<link href="/style.css" rel="stylesheet">'
        result = rewrite_urls(html, "/current", config)
        assert "css/" in result
        assert "style.css" in result

    def test_mailto_unchanged(self):
        config = _make_config()
        html = '<a href="mailto:test@example.com">Email</a>'
        result = rewrite_urls(html, "/current", config)
        assert "mailto:test@example.com" in result

    def test_anchor_unchanged(self):
        config = _make_config()
        html = '<a href="#section">Jump</a>'
        result = rewrite_urls(html, "/current", config)
        assert '#section' in result

    def test_pdf_link(self):
        config = _make_config()
        html = '<a href="/docs/report.pdf">PDF</a>'
        result = rewrite_urls(html, "/current", config)
        assert "media/" in result
        assert "report.pdf" in result


# ── src rewriting ───────────────────────────────────────────────


class TestRewriteSrc:
    def test_image_src(self):
        config = _make_config()
        html = '<img src="/images/photo.jpg">'
        result = rewrite_urls(html, "/current", config)
        assert "images/" in result
        assert "photo.jpg" in result

    def test_script_src(self):
        config = _make_config()
        html = '<script src="/app.js"></script>'
        result = rewrite_urls(html, "/current", config)
        assert "js/" in result
        assert "app.js" in result

    def test_video_src(self):
        config = _make_config()
        html = '<video src="/media/clip.mp4"></video>'
        result = rewrite_urls(html, "/current", config)
        assert "media/" in result
        assert "clip.mp4" in result

    def test_external_src_unchanged(self):
        config = _make_config()
        html = '<img src="https://cdn.other.com/photo.jpg">'
        result = rewrite_urls(html, "/current", config)
        assert "https://cdn.other.com/photo.jpg" in result


# ── srcset rewriting ────────────────────────────────────────────


class TestRewriteSrcset:
    def test_single_srcset(self):
        config = _make_config()
        html = '<img srcset="/images/photo-2x.jpg 2x">'
        result = rewrite_urls(html, "/current", config)
        assert "photo-2x.jpg" in result

    def test_multiple_srcset(self):
        config = _make_config()
        html = '<img srcset="/images/small.jpg 300w, /images/large.jpg 600w">'
        result = rewrite_urls(html, "/current", config)
        assert "small.jpg" in result
        assert "large.jpg" in result


# ── CSS url() rewriting ─────────────────────────────────────────


class TestRewriteCssUrl:
    def test_background_image(self):
        config = _make_config()
        html = "<style>body { background: url('/images/bg.jpg') }</style>"
        result = rewrite_urls(html, "/current", config)
        assert "images/" in result
        assert "bg.jpg" in result

    def test_non_image_url_unchanged(self):
        config = _make_config()
        html = "<style>@font-face { src: url('/fonts/roboto.woff2') }</style>"
        result = rewrite_urls(html, "/current", config)
        # Font URLs aren't rewritten by CSS url() handler (not in image extensions list)
        assert "/fonts/roboto.woff2" in result


# ── Absolute URL rewriting ──────────────────────────────────────


class TestRewriteAbsoluteUrls:
    def test_absolute_same_domain(self):
        config = _make_config()
        html = '<a href="https://example.com/page">Link</a>'
        result = rewrite_urls(html, "/current", config)
        assert "page.html" in result or ".html" in result

    def test_absolute_image(self):
        config = _make_config()
        html = '<img src="https://example.com/images/photo.jpg">'
        result = rewrite_urls(html, "/current", config)
        assert "images/" in result
        assert "photo.jpg" in result


# ── Stay-in-path scope ──────────────────────────────────────────


class TestStayInPathRewriting:
    def test_out_of_scope_becomes_live_url(self):
        config = _make_config("https://example.com/docs", stay_in_path=True)
        html = '<a href="/blog/post">Link</a>'
        result = rewrite_urls(html, "/docs/page", config)
        assert "https://example.com/blog/post" in result

    def test_in_scope_rewritten(self):
        config = _make_config("https://example.com/docs", stay_in_path=True)
        html = '<a href="/docs/getting-started">Link</a>'
        result = rewrite_urls(html, "/docs/page", config)
        assert ".html" in result
