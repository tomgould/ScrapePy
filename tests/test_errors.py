"""Tests for processing/errors.py, error page detection."""

import pytest

from scrapepy.processing.errors import (
    is_error_page_html,
    is_error_page_content,
    is_cloudflare_challenge,
    is_error_heading,
)


# ── Raw HTML error detection ────────────────────────────────────


class TestIsErrorPageHtml:
    def test_nosuchkey(self):
        assert is_error_page_html("<Error><Code>NoSuchKey</Code></Error>") is True

    def test_access_denied(self):
        assert is_error_page_html("<Error><Code>AccessDenied</Code></Error>") is True

    def test_404_tag(self):
        assert is_error_page_html("<title>404</title><body>Not found</body>") is True

    def test_not_found_tag(self):
        # ">Not Found<" matches the closing > and opening < of HTML tags
        assert is_error_page_html("<h1>Not Found</h1>") is True
        assert is_error_page_html("<title>Not Found</title>") is True

    def test_specified_key_short_doc(self):
        html = "<p>The specified key does not exist.</p>"
        assert is_error_page_html(html) is True

    def test_specified_key_long_doc_not_detected(self):
        html = "x" * 2000 + "The specified key does not exist"
        assert is_error_page_html(html) is False

    def test_normal_page(self):
        html = "<html><head><title>Welcome</title></head><body><p>Hello world</p></body></html>"
        assert is_error_page_html(html) is False

    def test_empty_html(self):
        assert is_error_page_html("") is False

    def test_404_in_content_not_tag(self):
        # "404" as a plain string isn't detected, must be in >404<
        assert is_error_page_html("Error code 404 occurred") is False


# ── Rendered content error detection ────────────────────────────


class TestIsErrorPageContent:
    def test_404_in_title(self):
        assert is_error_page_content("404 Not Found", "", "") is True

    def test_403_in_title(self):
        assert is_error_page_content("403 Forbidden", "", "") is True

    def test_500_in_title(self):
        assert is_error_page_content("500 Internal Server Error", "", "") is True

    def test_not_found_in_h1(self):
        assert is_error_page_content("", "Page Not Found", "") is True

    def test_error_in_title(self):
        assert is_error_page_content("Error", "", "") is True

    # Body text is never read: a short storage error document is caught by
    # is_error_page_html, and a page with no error title or H1 is kept
    def test_nosuchkey_in_body_ignored(self):
        assert is_error_page_content("", "", "NoSuchKey encountered") is False

    def test_specified_key_in_body_ignored(self):
        assert is_error_page_content("", "", "the specified key does not exist") is False

    def test_page_not_found_in_short_body_ignored(self):
        assert is_error_page_content("", "", "Sorry, page not found") is False

    def test_file_not_found_in_short_body_ignored(self):
        assert is_error_page_content("", "", "file not found") is False

    def test_page_you_requested_in_short_body_ignored(self):
        assert is_error_page_content("", "", "the page you requested could not be found") is False

    def test_not_found_in_long_body_ignored(self):
        # body > 1000 chars → generic phrases not checked
        long_body = "Some content " * 100 + " page not found"
        assert is_error_page_content("", "", long_body) is False

    def test_normal_page(self):
        assert is_error_page_content("My Blog", "Welcome", "Hello world, this is a long article...") is False

    def test_case_insensitive(self):
        assert is_error_page_content("PAGE NOT FOUND", "", "") is True

    def test_all_empty(self):
        assert is_error_page_content("", "", "") is False


# ── Cloudflare challenge detection ──────────────────────────────


class TestIsCloudflareChallenge:
    def test_verifying_human(self):
        html = '<div>Verifying you are human</div>'
        assert is_cloudflare_challenge(html) is True

    def test_just_a_moment(self):
        html = '<title>Just a moment</title>'
        assert is_cloudflare_challenge(html) is True

    def test_normal_page(self):
        html = '<html><body>Normal content</body></html>'
        assert is_cloudflare_challenge(html) is False

    def test_empty(self):
        assert is_cloudflare_challenge("") is False


# ── Error titles and headings ───────────────────────────────────


class TestErrorHeadings:
    @pytest.mark.parametrize("text", [
        "404", "HTTP 404", "Error 404", "404 Not Found", "HTTP 403: Forbidden",
        "Example | 404 Not Found", "Example - Error 500", "500 Internal Server Error",
        "503 Service Unavailable", "Gateway Timeout", "Page not found",
        "Oops! That page can't be found.", "This page does not exist", "Not Found",
        "Access Denied", "Error", "Server Error", "NoSuchKey",
    ])
    def test_error_headings(self, text):
        assert is_error_heading(text) is True

    @pytest.mark.parametrize("text", [
        "8. Errors and Exceptions", "4. More Control Flow Tools", "Top 500 companies",
        "500 Days of Summer", "Error handling in Python", "Not found: a lost and found story",
        "HTTP status 404 explained", "Errors", "", "Fortune 500 list",
        "When to raise an error", "Files that are not found",
    ])
    def test_ordinary_headings(self, text):
        assert is_error_heading(text) is False

    def test_code_sample_with_404_is_not_an_error_page(self):
        html = ("<html><head><title>4. More Control Flow Tools</title></head><body>"
                "<h1>4. More Control Flow Tools</h1><pre><span>case</span> <span>404</span>: "
                "<span>return</span> <span>Not Found</span></pre></body></html>")
        assert is_error_page_html(html) is False

    def test_error_title_in_html(self):
        assert is_error_page_html("<html><head><title>Page not found</title></head></html>") is True

    def test_long_page_mentioning_nosuchkey_is_kept(self):
        html = ("<html><head><title>Storage errors explained</title></head><body>"
                + "<p>A missing object answers NoSuchKey.</p>" * 100 + "</body></html>")
        assert is_error_page_html(html) is False
