"""Markdown quality for indexing: structure, boilerplate, links and titles."""

import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scrapepy.convert.html_prep import (
    absolutize_links,
    canonical_url,
    clean_title,
    code_language,
    dominant_container,
    expand_truncated_link_text,
    fix_block_links,
    normalize_headings,
    safe_urljoin,
    same_site,
)
from scrapepy.convert.to_markdown import HTMLToMarkdown

FIXTURES = Path(__file__).parent / "fixtures"


def convert(html: str, url: str = "https://example.com/page", absolute=False):
    conv = HTMLToMarkdown("/nonexistent", absolute_links=absolute)
    meta = {"url": url}
    prepared = conv._prepare_html(html, meta)
    md = conv.extract_content(prepared, Path("/nonexistent/page.html"))
    md = conv.clean_markdown(conv.promote_bold_headings(md))
    return normalize_headings(md), meta


def headings(md):
    return [len(m.group(1)) for m in re.finditer(r"^(#{1,6}) ", md, re.M)]


def levels_out(*levels):
    """Normalise headings of the given levels (named h0, h1, ...) and return
    the output levels, checking each heading line is otherwise unchanged."""
    md = "\n\n".join("#" * lv + f" h{i}" for i, lv in enumerate(levels))
    out = normalize_headings(md).split("\n\n")
    result = []
    for i, line in enumerate(out):
        hashes, text = line.split(" ", 1)
        assert text == f"h{i}" and set(hashes) == {"#"}
        result.append(len(hashes))
    return result


class TestNormalizeHeadings:
    def test_single_h1(self):
        assert normalize_headings("# A\n\n# B\n\n# C") == "# A\n\n## B\n\n## C"

    def test_no_skipped_levels(self):
        assert levels_out(1, 4, 6, 2) == [1, 2, 3, 2]

    def test_first_heading_becomes_h1(self):
        # The first heading is the page title whatever its level
        assert normalize_headings("### Title\n\ntext\n\n#### Sub") == "# Title\n\ntext\n\n## Sub"

    def test_siblings_stay_siblings(self):
        assert levels_out(1, 2, 4, 4) == [1, 2, 3, 3]

    def test_sibling_h4s_under_h1_are_all_h2(self):
        md = "# T\n\n#### Card one\n\nA\n\n#### Card two\n\nB\n\n#### Card three\n\nC"
        assert normalize_headings(md) == (
            "# T\n\n## Card one\n\nA\n\n## Card two\n\nB\n\n## Card three\n\nC")

    def test_return_to_an_ancestor_level(self):
        assert levels_out(1, 3, 5, 3) == [1, 2, 3, 2]

    def test_heading_between_two_levels_becomes_child_of_the_shallower(self):
        # The H3 pops the H4 and sits under the H2
        assert levels_out(1, 2, 4, 3) == [1, 2, 3, 3]
        # Same shape with an H2 as the first heading (which becomes the H1)
        assert levels_out(2, 4, 3) == [1, 2, 2]

    def test_deep_heading_then_h1_resets(self):
        assert levels_out(1, 2, 3, 4, 1, 2, 4) == [1, 2, 3, 4, 2, 3, 4]
        assert levels_out(1, 6, 1, 6) == [1, 2, 2, 3]

    def test_never_deeper_than_h6(self):
        assert levels_out(6, 1, 2, 3, 4, 5, 6) == [1, 2, 3, 4, 5, 6, 6]

    def test_code_fences_untouched(self):
        md = "# T\n\n```bash\n# not a heading\n### nor this\n```\n\n### Real"
        assert normalize_headings(md) == (
            "# T\n\n```bash\n# not a heading\n### nor this\n```\n\n## Real")

    def test_text_lines_untouched(self):
        assert normalize_headings("#hashtag\n# T") == "#hashtag\n# T"

    def test_comment_lines_in_code_fences_are_never_headings(self):
        md = ("# T\n\n```bash\n# install\n## step two\n```\n\n### Real\n\n"
              "~~~\n# also code\n~~~\n\n#### Deeper")
        assert normalize_headings(md) == (
            "# T\n\n```bash\n# install\n## step two\n```\n\n## Real\n\n"
            "~~~\n# also code\n~~~\n\n### Deeper")

    def test_tilde_line_does_not_close_backtick_fence(self):
        md = "# T\n\n```\n~~~\n# not heading\n```\n\n#### After"
        assert normalize_headings(md) == "# T\n\n```\n~~~\n# not heading\n```\n\n## After"

    def test_shorter_fence_does_not_close_longer_one(self):
        md = "# T\n\n````md\n```\n# not heading\n```\n````\n\n#### After"
        assert normalize_headings(md) == (
            "# T\n\n````md\n```\n# not heading\n```\n````\n\n## After")

    def test_fence_with_info_string_does_not_close(self):
        md = "# T\n\n```\n```python\n# not heading\n```\n\n#### After"
        assert normalize_headings(md) == (
            "# T\n\n```\n```python\n# not heading\n```\n\n## After")

    def test_unclosed_fence_runs_to_the_end(self):
        md = "# T\n\n```\n# not heading\n\n#### nor this"
        assert normalize_headings(md) == md

    def test_tilde_inside_pre_from_html(self):
        html = form_page("<pre>~~~\n# not heading</pre><h4>After</h4><p>More text.</p>")
        md, _ = convert(html)
        assert "## not heading" not in md
        assert "\n# not heading\n" in md
        assert "## After" in lines(md)

    def test_code_comments_survive_conversion(self):
        html = form_page('<pre><code class="language-bash"># install the tool\n'
                         'pip install thing\n## then run it\nthing --help</code></pre>'
                         '<h4>After the code</h4><p>More text.</p>')
        md, _ = convert(html)
        assert "\n# install the tool\npip install thing\n## then run it\n" in md
        assert "## After the code" in lines(md)
        assert "## install the tool" not in md and "### then run it" not in md

    def test_headings_in_list_items_are_normalised(self):
        md = "# T\n\n- #### Card one\n\n  Blurb\n- #### Card two\n\n1. ###### Step\n\n### After"
        assert normalize_headings(md) == (
            "# T\n\n- ## Card one\n\n  Blurb\n- ## Card two\n\n1. ### Step\n\n## After")

    def test_list_item_headings_from_html_do_not_skip(self):
        html = form_page('<ul><li><h4>First card</h4><p>About the first.</p></li>'
                         '<li><h4>Second card</h4><p>About the second.</p></li></ul>')
        md, _ = convert(html)
        assert "- ## First card" in lines(md)
        assert "- ## Second card" in lines(md)

    def test_headings_in_blockquotes_are_normalised(self):
        md = "# T\n\n> #### Quoted\n>\n> text\n\n> > ###### Deeper\n\n#### Next"
        assert normalize_headings(md) == (
            "# T\n\n> ## Quoted\n>\n> text\n\n> > ### Deeper\n\n## Next")

    def test_blockquote_heading_from_html(self):
        html = form_page("<blockquote><h4>Quoted title</h4><p>Quoted words.</p></blockquote>")
        md, _ = convert(html)
        assert "> ## Quoted title" in lines(md)


FILLER = " ".join(["The service covers every part of the country and runs all year."] * 6)


def form_page(inner):
    return (f"<html><head><title>Help</title></head><body><main><h1>Help</h1>"
            f"<p>{FILLER}</p>{inner}</main></body></html>")


def lines(md):
    return [ln.strip() for ln in md.split("\n") if ln.strip()]


class TestButtonsAndFormText:
    def test_bootstrap_accordion_questions_stay_headings(self):
        html = form_page(
            '<div class="accordion" id="faq">'
            '<div class="accordion-item"><h2 class="accordion-header">'
            '<button class="accordion-button" type="button" data-bs-toggle="collapse" '
            'data-bs-target="#c1" aria-expanded="true" aria-controls="c1">'
            'How long do refunds take?</button></h2>'
            '<div id="c1" class="accordion-collapse collapse show">'
            '<div class="accordion-body">Refunds reach your account within five days.</div>'
            '</div></div>'
            '<div class="accordion-item"><h2 class="accordion-header">'
            '<button class="accordion-button collapsed" type="button" '
            'data-bs-toggle="collapse" data-bs-target="#c2" aria-expanded="false" '
            'aria-controls="c2">Can I cancel an order?</button></h2>'
            '<div id="c2" class="accordion-collapse collapse">'
            '<div class="accordion-body">Orders can be cancelled until they ship.</div>'
            '</div></div></div>'
        )
        md, _ = convert(html)
        assert "## How long do refunds take?" in lines(md)
        assert "## Can I cancel an order?" in lines(md)
        assert "Refunds reach your account within five days." in md
        assert md.index("How long do refunds take?") < md.index("Refunds reach")

    def test_plain_button_faq_keeps_questions(self):
        html = form_page(
            '<div class="faq-list">'
            '<div class="faq-item"><button class="faq-question" aria-expanded="false" '
            'aria-controls="a1">Can I change my delivery address?</button>'
            '<div class="faq-answer" id="a1">Yes, until the parcel is dispatched.</div></div>'
            '<div class="faq-item"><button class="faq-question" aria-expanded="false" '
            'aria-controls="a2">Opening hours</button>'
            '<div class="faq-answer" id="a2">We are open from nine to five.</div></div>'
            '</div>'
        )
        md, _ = convert(html)
        assert "Can I change my delivery address?" in lines(md)
        assert "Opening hours" in lines(md)
        assert md.index("Can I change") < md.index("Yes, until the parcel")

    def test_select_options_are_kept(self):
        html = form_page(
            '<label for="size">Sizes we stock</label>'
            '<select id="size"><option value="">Choose a size</option>'
            '<option value="s">Small (up to 40 cm)</option>'
            '<option value="m">Medium (40 to 60 cm)</option>'
            '<option value="l">Large (over 60 cm)</option></select>'
        )
        md, _ = convert(html)
        for option in ("Small (up to 40 cm)", "Medium (40 to 60 cm)", "Large (over 60 cm)"):
            assert f"- {option}" in lines(md)
        assert "Choose a size" not in md

    def test_textarea_text_is_kept(self):
        html = form_page(
            '<p>Paste this notice into your project:</p>'
            '<textarea readonly rows="3">Permission is granted to copy this guide '
            'for teaching.</textarea>'
            '<textarea placeholder="Your message"></textarea>'
        )
        md, _ = convert(html)
        assert "Permission is granted to copy this guide for teaching." in md
        assert "Your message" not in md

    def test_ui_chrome_buttons_are_dropped(self):
        html = form_page(
            '<button type="button" aria-expanded="false" aria-controls="menu">Menu</button>'
            '<button type="button" class="close" aria-label="Close">&times;</button>'
            '<form><input type="email" name="email"><button type="submit">Submit</button></form>'
            '<button type="button">Add to basket</button>'
        )
        md, _ = convert(html)
        out = lines(md)
        for label in ("Menu", "Submit", "\u00d7", "Add to basket"):
            assert label not in out
        assert "Menu" not in md and "Submit" not in md and "basket" not in md


class TestBlockLinks:
    def test_heading_inside_link_gets_the_link(self):
        soup = BeautifulSoup('<a href="/x"><div><h2>Card title</h2><p>Blurb</p></div></a>', "lxml")
        fix_block_links(soup)
        md = HTMLToMarkdown._md(str(soup))
        assert "## [Card title](/x)" in md
        assert "[##" not in md
        assert "Blurb" in md

    def test_inline_links_left_alone(self):
        soup = BeautifulSoup('<p>see <a href="/x">this <b>bold</b></a></p>', "lxml")
        fix_block_links(soup)
        assert '<a href="/x">this <b>bold</b></a>' in str(soup)

    def test_linked_heading_survives_cleaning(self):
        html = ('<html><body><main><h1>List</h1>' + "<p>" + "word " * 60 + "</p>"
                + '<a href="/a"><div><h2>First card</h2><p>About the first card.</p></div></a>'
                + "</main></body></html>")
        md, _ = convert(html)
        assert "## [First card](/a)" in md


class TestTruncatedLinks:
    def test_expands_from_title(self):
        soup = BeautifulSoup('<a href="/b" title="A Light in the Attic">A Light in the ...</a>', "lxml")
        expand_truncated_link_text(soup)
        assert soup.a.get_text() == "A Light in the Attic"

    def test_unicode_ellipsis(self):
        soup = BeautifulSoup('<a href="/b" title="Long Name Here">Long Na…</a>', "lxml")
        expand_truncated_link_text(soup)
        assert soup.a.get_text() == "Long Name Here"

    def test_unrelated_title_ignored(self):
        soup = BeautifulSoup('<a href="/b" title="Something else">Read more...</a>', "lxml")
        expand_truncated_link_text(soup)
        assert soup.a.get_text() == "Read more..."


class TestCodeAndTables:
    def test_fence_language_from_class(self):
        html = ('<html><body><main><h1>Doc</h1><p>' + "word " * 60 + '</p>'
                '<pre><code class="language-python">print("hi")</code></pre></main></body></html>')
        md, _ = convert(html)
        assert '```python\nprint("hi")\n```' in md

    @pytest.mark.parametrize("attrs,lang", [
        ('class="language-js"', "js"), ('class="lang-ruby"', "ruby"),
        ('class="highlight-bash"', "bash"), ('data-lang="go"', "go"),
        ('class="language-plaintext"', None), ("", None),
    ])
    def test_code_language(self, attrs, lang):
        el = BeautifulSoup(f"<pre {attrs}><code>x</code></pre>", "lxml").pre
        assert code_language(el) == lang

    def test_tables_stay_tables(self):
        html = ('<html><body><main><h1>Prices</h1><p>' + "word " * 60 + '</p><table>'
                '<tr><th>Item</th><th>Price</th></tr><tr><td>Tea</td><td>2</td></tr>'
                '</table></main></body></html>')
        md, _ = convert(html)
        assert "| Item | Price |\n| --- | --- |\n| Tea | 2 |" in md


class TestLinks:
    def test_absolutize(self):
        soup = BeautifulSoup('<a href="b">x</a><a href="/c">y</a><a href="#top">z</a>'
                             '<a href="mailto:a@b">m</a><img src="i.png">', "lxml")
        absolutize_links(soup, "https://example.com/dir/page")
        hrefs = [a["href"] for a in soup.find_all("a")]
        assert hrefs == ["https://example.com/dir/b", "https://example.com/c", "#top", "mailto:a@b"]
        assert soup.img["src"] == "https://example.com/dir/i.png"

    def test_base_href_respected(self):
        soup = BeautifulSoup('<head><base href="https://cdn.example.com/x/"></head><a href="y">y</a>', "lxml")
        absolutize_links(soup, "https://example.com/page")
        assert soup.a["href"] == "https://cdn.example.com/x/y"

    def test_link_title_not_corrupted(self):
        conv = HTMLToMarkdown("/x")
        out = conv.convert_links('[Acer](/product/66 "Acer Aspire 3")', Path("/x/p.html"))
        assert out == '[Acer](/product/66.md "Acer Aspire 3")'

    def test_canonical(self):
        soup = BeautifulSoup('<link rel="canonical" href="/real#frag">', "lxml")
        assert canonical_url(soup, "https://example.com/a?x=1") == "https://example.com/real"
        assert canonical_url(BeautifulSoup("<p>none</p>", "lxml"), "https://example.com/") is None
        assert canonical_url(BeautifulSoup('<link rel="canonical" href="ftp://x/y">', "lxml"),
                             "https://example.com/") is None

    def test_same_site(self):
        assert same_site("https://www.example.com/a", "https://example.com/b")
        assert same_site("https://example.com:443/a", "https://example.com/b")
        assert not same_site("https://other.com/a", "https://example.com/b")

    @pytest.mark.parametrize("bad", ["http://[::1", "http://[your-server]:8080/api",
                                     "https://[ip]/", "http://example.com:99999/"])
    def test_safe_urljoin_returns_none_for_malformed(self, bad):
        assert safe_urljoin("https://example.com/a", bad) is None

    def test_safe_urljoin_joins(self):
        assert safe_urljoin("https://example.com/a/b", "../c") == "https://example.com/c"

    def test_malformed_canonical_and_links_do_not_raise(self):
        soup = BeautifulSoup('<link rel="canonical" href="http://[::1">'
                             '<a href="http://[x]/">x</a><a href="/ok">ok</a>', "lxml")
        assert canonical_url(soup, "https://example.com/p") is None
        absolutize_links(soup, "https://example.com/p")
        hrefs = [a["href"] for a in soup.find_all("a")]
        assert hrefs == ["http://[x]/", "https://example.com/ok"]

    def test_same_site_compares_scheme_and_port(self):
        assert not same_site("https://example.com/a", "http://example.com/a")
        assert not same_site("http://example.com:8080/a", "http://example.com/a")
        assert not same_site("http://example.com:8080/a", "http://example.com:9090/a")
        assert same_site("http://example.com:8080/a", "http://example.com:8080/b")


class TestTitles:
    def test_clean_title(self):
        assert clean_title("\n    Mystery | \n     Books to Scrape - Sandbox\n") == \
            "Mystery | Books to Scrape - Sandbox"


class TestDominantContainer:
    def test_picks_main_column(self):
        html = ("<body><div><div class='header'>Site name Login</div>"
                "<div class='row'><div class='main'>" + "<p>quote words here</p>" * 30 + "</div>"
                "<div class='side'>tag one tag two</div></div></div></body>")
        node = dominant_container(BeautifulSoup(html, "lxml"))
        assert "Login" not in node.get_text()
        assert "tag one" not in node.get_text()
        assert node.get_text().count("quote words here") == 30


class TestFixturePages:
    """Real pages from the practice sites: content in, boilerplate out."""

    def _md(self, site, page, url):
        html = (FIXTURES / site / "pages" / page).read_text(encoding="utf-8")
        return convert(html, url)[0]

    def test_books_listing_keeps_titles_and_drops_sidebar(self):
        md = self._md("books_toscrape_com", "section_mystery.html",
                      "https://books.toscrape.com/catalogue/category/books/mystery_3/index.html")
        assert md.startswith("# Mystery\n")
        assert "Sharp Objects" in md
        assert "In a Dark, Dark Wood" in md  # full title from the link's title attribute
        assert "Add to basket" not in md
        assert "Travel" not in md  # category sidebar
        assert headings(md).count(1) == 1

    def test_quotes_drops_header_and_tag_sidebar(self):
        md = self._md("quotes_toscrape_com", "homepage.html", "https://quotes.toscrape.com/")
        assert "Albert Einstein" in md
        assert "Login" not in md
        assert "Top Ten tags" not in md

    def test_webscraper_card_headings_kept(self):
        md = self._md("webscraper_io", "homepage.html", "https://webscraper.io/test-sites")
        assert "## [Test site with pagination links](https://webscraper.io/test-sites/pagination)" in md
        assert "[##" not in md

    def test_markdown_only_links_absolute(self):
        html = (FIXTURES / "books_toscrape_com" / "pages" / "homepage.html").read_text(encoding="utf-8")
        md, _ = convert(html, "https://books.toscrape.com/", absolute=True)
        links = re.findall(r"\]\(([^) ]+)", md)
        assert links and all(u.startswith("https://books.toscrape.com/") for u in links)


class TestVisibleDate:
    BODY = "<p>" + "word " * 60 + "</p>"

    def _convert(self, tmp_path, page_type):
        d = tmp_path / "in"
        d.mkdir(exist_ok=True)
        f = d / f"{page_type}.html"
        f.write_text(f"<html><body><article><h1>T</h1>{self.BODY}</article></body></html>")
        conv = HTMLToMarkdown(d, output_dir=tmp_path / "md")
        conv.convert_file(f, metadata={"url": "https://example.com/x", "page_type": page_type,
                                       "date": "2026-02-20T10:00:00+00:00"},
                          output_path=tmp_path / "md" / f"{page_type}.md")
        return open(conv.last_output_path, encoding="utf-8").read()

    def test_article_gets_visible_date(self, tmp_path):
        assert "**20 Feb 2026" in self._convert(tmp_path, "article")

    def test_section_does_not(self, tmp_path):
        assert "**20 Feb 2026" not in self._convert(tmp_path, "section")
