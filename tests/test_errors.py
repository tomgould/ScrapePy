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
        "How to build a custom 404 page for your static site with plain HTML today",
        "Errors", "", "Fortune 500 list", "Python 3.10.4 release notes", "Release v1.404",
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


class TestErrorHeadingsInOtherLanguages:
    @pytest.mark.parametrize("text", [
        "Fehler 404", "Seite nicht gefunden (404)",                 # German
        "Erreur 404 - Page introuvable", "Erreur 500",              # French
        "Error 404 - Página no encontrada", "404 No encontrado",    # Spanish
        "Ошибка 404", "404 Страница не найдена", "Ошибка 502",      # Russian
        "404 ページが見つかりません", "404エラー", "エラー 503",     # Japanese
        "خطأ 404 - الصفحة غير موجودة", "الصفحة غير موجودة 404",      # Arabic
        "Gone 410", "410 Gone", "403", "410", "HTTP 503", "Zugriff verweigert 403", "403 \u7981\u6b62",
        "\u062e\u0637\u0623 500", "Error 410",
        # under 12 words: 11 here
        "one two three four five six seven eight nine ten 404",
    ])
    def test_status_code_in_a_short_heading(self, text):
        assert is_error_heading(text) is True

    @pytest.mark.parametrize("text", [
        "Top 500 companies", "500 Days of Summer", "Fortune 500 list", "Die 500 besten Rezepte",
        "Как настроить страницу 404 на своём сайте с помощью nginx и простого HTML шаблона",
        "Code 14040", "E404X", "Highway 410 construction update", "Route 410",
        # 12 words is not short
        "one two three four five six seven eight nine ten eleven 404",
    ])
    def test_codes_in_ordinary_headings(self, text):
        assert is_error_heading(text) is False

    def test_only_a_prefix_of_the_html_is_read(self):
        # The title and first H1 are near the start; a late "404" heading in a
        # long page is not read
        html = ("<html><head><title>Guide</title></head><body><p>"
                + "x" * 25000 + "</p><h1>404</h1></body></html>")
        assert is_error_page_html(html) is False


def test_error_title_after_a_long_head_is_found():
    """The title and H1 are looked for in the first 20,000 characters, not
    only the first few hundred: a long inline style comes first here."""
    html = ("<html><head><style>" + "body { margin: 0; padding: 0; } " * 40
            + "</style><title>Not Found</title></head><body></body></html>")
    assert html.index("<title>") > 1000
    assert is_error_page_html(html) is True
