"""Recipe pages and feed mode together with the file naming, title,
heading and button rules: each keeps its own behaviour."""

import os
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

from scrapepy.output.naming import url_suffix
from tests.test_collisions_localhost import Handler as SiteHandler, md_files, run as crawl
from tests.test_feed import Handler as FeedHandler, XML, files_by_url, run as run_feed
from tests.test_recipe import BASIC, BASIC_PROSE, STORY, TIPS, convert, ld, page, section


def recipe_block(body):
    """The recipe sections, from "## Recipe details" up to "## Page text"."""
    start = body.index("## Recipe details")
    return body[start:body.index("## Page text")]




@pytest.fixture
def plain_block(tmp_path_factory):
    _, body = convert(tmp_path_factory.mktemp("plain"), page(ld(BASIC), BASIC_PROSE))
    return recipe_block(body)


class TestRecipeTitlesAndHeadings:
    def test_site_header_and_brand_h1_do_not_change_the_recipe(self, tmp_path, plain_block):
        html = page(ld(BASIC, {"@type": "WebSite", "name": "Kitchen"}), BASIC_PROSE,
                    title="Rainbow Oat Bites")
        html = html.replace("<title>Rainbow Oat Bites</title>",
                            "<title>Rainbow Oat Bites | Kitchen</title>")
        html = html.replace("<nav>", '<header class="masthead"><h1>Kitchen</h1></header><nav>')
        front, body = convert(tmp_path, html)
        assert front["title"] == "Rainbow Oat Bites"
        assert front["page_type"] == "recipe"
        assert body.startswith("# Rainbow Oat Bites\n")
        assert recipe_block(body) == plain_block

    def test_kicker_above_the_recipe_h1(self, tmp_path, plain_block):
        html = page(ld(BASIC), BASIC_PROSE).replace(
            "<article><h1>", "<article><h3>Family baking</h3><h1>")
        front, body = convert(tmp_path, html)
        assert front["title"] == "Rainbow Oat Bites"
        assert "# Family baking" not in body and "## Family baking" not in body
        assert recipe_block(body) == plain_block

    def test_late_h1_page_keeps_recipe_heading_levels(self, tmp_path, plain_block):
        long_text = " ".join(["A long opening section about the cafe and its history."] * 6)
        html = page(ld(BASIC), f"<h2>About us</h2><p>{long_text}</p><h2>Visit</h2>"
                    f"<p>Open daily.</p><h1>Newsletter</h1><p>Sign up for news.</p>")
        html = html.replace("<article><h1>Rainbow Oat Bites</h1>", "<article>")
        front, body = convert(tmp_path, html)
        assert front["page_type"] == "recipe"
        levels = {line for line in body.split("\n") if line.startswith("#")}
        for heading in ("## Recipe details", "## Ingredients", "## Method",
                        "## Nutrition", "## Page text"):
            assert heading in levels, heading
        assert recipe_block(body) == plain_block
        assert sum(1 for line in body.split("\n") if line.startswith("# ")) == 1

    def test_howto_sections_stay_at_level_three(self, tmp_path):
        recipe = dict(BASIC, recipeInstructions=[
            {"@type": "HowToSection", "name": "Mix", "itemListElement": [
                {"@type": "HowToStep", "text": "Tip the oats into a large bowl."}]},
            {"@type": "HowToSection", "name": "Shape", "itemListElement": [
                {"@type": "HowToStep", "text": "Roll into 16 balls."}]},
        ])
        html = page(ld(recipe), BASIC_PROSE).replace(
            "<article><h1>", "<article><h4>Kicker</h4><h1>")
        _, body = convert(tmp_path, html)
        assert section(body, "Method") == [
            "### Mix", "1. Tip the oats into a large bowl.",
            "### Shape", "1. Roll into 16 balls.",
        ]

    def test_accordion_notes_in_page_text_keep_their_text(self, tmp_path, plain_block):
        notes = ('<h2 class="accordion-header"><button aria-expanded="false" '
                 'aria-controls="n1">Can I freeze them?</button></h2>'
                 '<div id="n1"><p>Yes, for up to a month in a sealed tub.</p></div>'
                 '<button aria-expanded="false" aria-controls="n2">Storage tips</button>'
                 '<div id="n2"><p>Keep them in a cool, dry cupboard.</p></div>'
                 '<button type="button">Print</button>')
        front, body = convert(tmp_path, page(ld(BASIC), BASIC_PROSE + notes))
        text = section(body, "Page text")
        assert "### Can I freeze them?" in text
        assert "Yes, for up to a month in a sealed tub." in text
        assert "Storage tips" in text
        assert "Print" not in text
        assert recipe_block(body) == plain_block
        assert STORY in body and TIPS in body


class TestRecipeDuplicates:
    def test_recipe_page_with_the_same_body_as_another_is_a_duplicate(self, tmp_path):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            recipe_html = page(ld(BASIC), BASIC_PROSE)
            home = ("<html><head><title>Home</title></head><body><main><h1>Home</h1><p>"
                    + "Words for the home page so it is long enough to keep. " * 8
                    + '</p><a href="/recipes/oat-bites">a</a> <a href="/print/oat-bites">b</a>'
                    "</main></body></html>")
            SiteHandler.pages = {"/": home, "/recipes/oat-bites": recipe_html,
                                 "/print/oat-bites": recipe_html}
            SiteHandler.delay = {}
            config, summary = crawl(base, tmp_path)
            files = md_files(config)
            urls = [u for u, _ in files.values()]
            assert urls.count(base + "/recipes/oat-bites") == 1
            assert base + "/print/oat-bites" not in urls
            assert [d["url"] for d in summary["duplicates"]] == [base + "/print/oat-bites"]
            same = [t for u, t in files.values() if u == base + "/recipes/oat-bites"][0]
            assert "## Ingredients" in same
        finally:
            server.shutdown()
            server.server_close()


def feed_xml(links):
    items = "".join(
        f"<item><title>Post {link}</title><link>{link}</link>"
        f"<description>Entry {link} has enough words to stand on its own here.</description></item>"
        for link in links)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>F</title>{items}</channel></rss>'


class TestFeedNaming:
    def test_entry_keeps_its_file_when_a_colliding_entry_arrives(self, server, tmp_path):
        FeedHandler.routes = {"/feed": (XML, feed_xml(["/post"]))}
        config, code, summary = run_feed(server + "/feed", tmp_path)
        assert code == 0
        first = {u: os.path.relpath(p, config.markdown_dir) for u, (_, _, p) in files_by_url(config).items()}
        assert first == {server + "/post": "post.md"}
        # A second entry with the same name arrives, in front
        FeedHandler.routes = {"/feed": (XML, feed_xml(["/post.html", "/post"]))}
        config, code, summary = run_feed(server + "/feed", tmp_path)
        files = {u: os.path.relpath(p, config.markdown_dir) for u, (_, _, p) in files_by_url(config).items()}
        assert files[server + "/post"] == "post.md"
        assert files[server + "/post.html"] == f"post-{url_suffix(server + '/post.html')}.md"
        assert [r["url"] for r in summary["renamed"]] == [server + "/post.html"]
        assert len(files) == 2
        # Reordered: nothing moves
        FeedHandler.routes = {"/feed": (XML, feed_xml(["/post", "/post.html"]))}
        config, code, summary = run_feed(server + "/feed", tmp_path)
        again = {u: os.path.relpath(p, config.markdown_dir) for u, (_, _, p) in files_by_url(config).items()}
        assert again == files
        assert summary["counts"]["changed"] == 0
        assert [r["url"] for r in summary["renamed"]] == [server + "/post.html"]

    def test_colliding_entries_in_one_feed_both_get_the_suffix(self, server, tmp_path):
        FeedHandler.routes = {"/feed": (XML, feed_xml(["/post", "/post.html"]))}
        config, code, summary = run_feed(server + "/feed", tmp_path)
        files = {u: os.path.relpath(p, config.markdown_dir) for u, (_, _, p) in files_by_url(config).items()}
        assert files == {
            server + "/post": f"post-{url_suffix(server + '/post')}.md",
            server + "/post.html": f"post-{url_suffix(server + '/post.html')}.md",
        }
        assert sorted(r["url"] for r in summary["renamed"]) == sorted(files)
        # Reordered on the next run: same names, nothing changed
        FeedHandler.routes = {"/feed": (XML, feed_xml(["/post.html", "/post"]))}
        config, code, summary = run_feed(server + "/feed", tmp_path)
        again = {u: os.path.relpath(p, config.markdown_dir) for u, (_, _, p) in files_by_url(config).items()}
        assert again == files
        assert summary["counts"]["changed"] == 0


@pytest.fixture
def server():
    FeedHandler.routes = {}
    FeedHandler.log = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FeedHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


STORY_P = "<p>" + "Story text about cooking with plenty of real words in it today. " * 8 + "</p>"


def heading_lines(body):
    return [line for line in body.split("\n") if re.match(r"^#{1,6} ", line)]


RECIPE_HEADINGS = ["## Recipe details", "## Ingredients", "## Method", "## Nutrition"]


class TestRecipeHeadingLevels:
    @pytest.mark.parametrize("case,body,expected", [
        ("kicker", f"<main><h3>Cakes</h3><h1>Rainbow Oat Bites</h1>{STORY_P}<h2>Tips</h2>{STORY_P}</main>",
         ["# Rainbow Oat Bites", *RECIPE_HEADINGS, "## Page text", "### Tips"]),
        ("late_h1", f"<main><h2>Intro</h2>{STORY_P}<h2>Tips</h2>{STORY_P}<h1>Newsletter</h1><p>Sign up.</p></main>",
         ["# Doc", *RECIPE_HEADINGS, "## Page text", "### Intro", "### Tips", "### Newsletter"]),
        ("two_h1s", f"<main><h1>Rainbow Oat Bites</h1>{STORY_P}<h1>More bakes</h1>{STORY_P}<h2>Cake</h2><p>x</p></main>",
         ["# Rainbow Oat Bites", *RECIPE_HEADINGS, "## Page text", "### More bakes", "#### Cake"]),
        ("no_h1", f"<main><h2>Story</h2>{STORY_P}<h3>Tips</h3>{STORY_P}</main>",
         ["# Doc", *RECIPE_HEADINGS, "## Page text", "### Story", "#### Tips"]),
        ("article_header", f"<article><header><h1>Rainbow Oat Bites</h1></header>{STORY_P}<h2>Tips</h2>{STORY_P}</article>",
         ["# Rainbow Oat Bites", *RECIPE_HEADINGS, "## Page text", "### Tips"]),
    ])
    def test_heading_levels(self, tmp_path, case, body, expected):
        html = (f"<html><head><title>Doc</title>{ld(BASIC)}</head><body>{body}</body></html>")
        front, md = convert(tmp_path, html)
        assert heading_lines(md) == expected
        if case == "kicker":
            assert "Cakes" in section(md, "Page text")

    def test_several_recipes(self, tmp_path):
        second = dict(BASIC, name="Second bites", recipeIngredient=["1 egg"],
                      recipeInstructions=[{"@type": "HowToStep", "text": "Crack the egg."}])
        html = (f"<html><head><title>Doc</title>{ld(BASIC, second)}</head><body><main>"
                f"<h4>Snacks</h4><h1>Two bites</h1>{STORY_P}<h2>Notes</h2>{STORY_P}</main></body></html>")
        _, md = convert(tmp_path, html)
        levels = heading_lines(md)
        assert levels[0] == "# Two bites"
        assert levels[-2:] == ["## Page text", "### Notes"]
        assert all(not h.startswith("# ") for h in levels[1:])
        assert levels.count("## Page text") == 1
