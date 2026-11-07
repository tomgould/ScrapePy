"""Removing an element must not break the loop that later visits an element
nested inside it (a decomposed tag has no attributes left to read).

The pages are synthetic: nested sidebar and widget containers of the kind
WordPress themes produce, with no real site text."""

from scrapepy.convert.to_markdown import HTMLToMarkdown

WORDS = " ".join(["Plain readable words make this article long enough to convert."] * 8)


def convert(tmp_path, body):
    html = f"<html><head><title>Oat Cakes</title></head><body>{body}</body></html>"
    conv = HTMLToMarkdown(tmp_path, output_dir=tmp_path / "out", base_url="https://site.test",
                          domain="site.test", absolute_links=True)
    page = conv.prepare(tmp_path / "page.html", html=html,
                        metadata={"url": "https://site.test/oat-cakes", "page_type": "article",
                                  "domain": "site.test"})
    assert page is not None, conv.last_error
    return page.text


def test_section_nested_in_a_removed_sidebar_section(tmp_path):
    text = convert(tmp_path, f"""
        <article><h1>Oat Cakes</h1><p>{WORDS}</p></article>
        <section class="sidebar-primary">
          <section id="widget-search" class="widget"><p>Search the site</p></section>
          <aside class="widget"><section><p>Popular posts</p></section></aside>
        </section>""")
    assert "# Oat Cakes" in text
    assert "Search the site" not in text and "Popular posts" not in text


def test_aside_nested_in_a_removed_aside(tmp_path):
    text = convert(tmp_path, f"""
        <main><article><h1>Oat Cakes</h1><p>{WORDS}</p></article></main>
        <aside id="secondary"><aside class="menu-box"><p>Menu items</p></aside></aside>""")
    assert "Menu items" not in text


def test_form_controls_nested_in_removed_controls(tmp_path):
    # Buttons, selects and textareas inside a button that is removed
    text = convert(tmp_path, f"""
        <article><h1>Oat Cakes</h1><p>{WORDS}</p>
        <button>Close<button aria-expanded="false">Is it free?</button>
          <select name="qty"><option>1</option><option>2</option></select>
          <textarea>Inner note</textarea></button></article>""")
    assert "# Oat Cakes" in text
