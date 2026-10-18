"""Tests for scrapepy.processing.navigation — homepage nav parsing."""

import pytest
from scrapepy.processing.navigation import extract_navigation, NavLink


BASE = "https://example.com"


# ── Hierarchy detection ────────────────────────────────────────────


def test_nested_ul_li_hierarchy():
    """Nested <ul>/<li> inside <nav> produces parent→child relationships."""
    html = """
    <header><nav>
      <ul>
        <li><a href="/f1">F1</a>
          <ul>
            <li><a href="/f1/news">News</a></li>
            <li><a href="/f1/results">Results</a></li>
          </ul>
        </li>
        <li><a href="/motogp">MotoGP</a></li>
      </ul>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 4
    # Top-level
    assert links[0] == NavLink("F1", "/f1", None, 0, "header")
    assert links[3] == NavLink("MotoGP", "/motogp", None, 0, "header")
    # Children
    assert links[1].label == "News"
    assert links[1].path == "/f1/news"
    assert links[1].parent_label == "F1"
    assert links[1].level == 1
    assert links[2].label == "Results"
    assert links[2].parent_label == "F1"


def test_three_level_nesting():
    """Three levels of nesting: grandparent → parent → child."""
    html = """
    <header><nav><ul>
      <li><a href="/sport">Sport</a>
        <ul>
          <li><a href="/sport/f1">F1</a>
            <ul>
              <li><a href="/sport/f1/news">News</a></li>
            </ul>
          </li>
        </ul>
      </li>
    </ul></nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 3
    assert links[0].level == 0
    assert links[0].label == "Sport"
    assert links[1].level == 1
    assert links[1].parent_label == "Sport"
    assert links[2].level == 2
    assert links[2].parent_label == "F1"


# ── Source detection ───────────────────────────────────────────────


def test_header_vs_footer_source():
    """Links in <header> get source='header', links in <footer> get source='footer'."""
    html = """
    <header><nav><a href="/news">News</a></nav></header>
    <footer><nav><a href="/about">About</a></nav></footer>
    """
    links = extract_navigation(html, BASE)

    header_links = [l for l in links if l.source == "header"]
    footer_links = [l for l in links if l.source == "footer"]
    assert len(header_links) == 1
    assert header_links[0].path == "/news"
    assert len(footer_links) == 1
    assert footer_links[0].path == "/about"


def test_standalone_nav():
    """<nav> outside header/footer gets source='nav'."""
    html = """
    <nav><a href="/products">Products</a></nav>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].source == "nav"


def test_nav_class_fallback():
    """Elements with nav-related classes are found when <header>/<nav> yield few links."""
    html = """
    <div class="main-menu">
      <a href="/section-a">Section A</a>
      <a href="/section-b">Section B</a>
      <a href="/section-c">Section C</a>
    </div>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 3
    labels = {l.label for l in links}
    assert labels == {"Section A", "Section B", "Section C"}


# ── Filtering ──────────────────────────────────────────────────────


def test_external_links_skipped():
    """Links to other domains are excluded."""
    html = """
    <header><nav>
      <a href="/local">Local</a>
      <a href="https://other.com/page">Other</a>
      <a href="//cdn.example.org/thing">CDN</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].path == "/local"


def test_anchor_and_js_links_skipped():
    """Hash-only, javascript:, and mailto: links are excluded."""
    html = """
    <header><nav>
      <a href="#top">Top</a>
      <a href="javascript:void(0)">JS</a>
      <a href="mailto:hi@example.com">Email</a>
      <a href="/real">Real</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].path == "/real"


def test_non_content_paths_skipped():
    """Login, cart, admin paths are excluded."""
    html = """
    <header><nav>
      <a href="/news">News</a>
      <a href="/login">Login</a>
      <a href="/cart">Cart</a>
      <a href="/wp-admin">Admin</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].path == "/news"


def test_file_extensions_skipped():
    """Links to images, PDFs, etc. are excluded."""
    html = """
    <header><nav>
      <a href="/docs">Docs</a>
      <a href="/files/report.pdf">Report</a>
      <a href="/logo.png">Logo</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].path == "/docs"


def test_root_slash_skipped():
    """A link to '/' (homepage) is not included as a nav link."""
    html = """
    <header><nav>
      <a href="/">Home</a>
      <a href="/news">News</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].path == "/news"


def test_deduplication():
    """Same path appearing in multiple <nav> elements is only included once."""
    html = """
    <header>
      <nav><a href="/news">News</a></nav>
      <nav><a href="/news">Latest News</a></nav>
    </header>
    """
    links = extract_navigation(html, BASE)

    paths = [l.path for l in links]
    assert paths.count("/news") == 1


# ── URL resolution ─────────────────────────────────────────────────


def test_absolute_url_same_domain():
    """Absolute URLs on the same domain are resolved to paths."""
    html = """
    <header><nav>
      <a href="https://example.com/section">Section</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].path == "/section"


def test_query_and_fragment_stripped():
    """Query strings and fragments are stripped from paths."""
    html = """
    <header><nav>
      <a href="/news?page=1#top">News</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].path == "/news"


def test_trailing_slash_stripped():
    """Trailing slashes are normalized off paths."""
    html = """
    <header><nav>
      <a href="/section/">Section</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert links[0].path == "/section"


# ── Label cleaning ─────────────────────────────────────────────────


def test_label_whitespace_collapsed():
    """Labels with excess whitespace are cleaned up."""
    html = """
    <header><nav>
      <a href="/news">  Latest\n  News  </a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert links[0].label == "Latest News"


def test_arrow_characters_stripped():
    """Arrow/icon characters are removed from labels."""
    html = """
    <header><nav>
      <a href="/news">News ›</a>
      <a href="/sport">Sport →</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert links[0].label == "News"
    assert links[1].label == "Sport"


def test_long_labels_skipped():
    """Labels longer than 60 chars are skipped (probably not nav items)."""
    html = """
    <header><nav>
      <a href="/ok">Short</a>
      <a href="/bad">This is an extremely long navigation label that no real site would ever use for a nav item</a>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 1
    assert links[0].label == "Short"


# ── Edge cases ─────────────────────────────────────────────────────


def test_empty_html():
    """Empty or minimal HTML returns empty list."""
    assert extract_navigation("", BASE) == []
    assert extract_navigation("<html><body></body></html>", BASE) == []


def test_no_nav_elements():
    """Page without any nav/header/footer returns empty list."""
    html = "<html><body><main><a href='/page'>Link</a></main></body></html>"
    links = extract_navigation(html, BASE)

    assert len(links) == 0


def test_flat_nav_in_header():
    """Flat <a> tags inside <header> without <ul>/<li> structure."""
    html = """
    <header>
      <a href="/news">News</a>
      <a href="/sport">Sport</a>
      <a href="/tech">Tech</a>
    </header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 3
    assert all(l.level == 0 for l in links)
    assert all(l.parent_label is None for l in links)


# ── Mega-menu & dropdown patterns ─────────────────────────────────


def test_div_dropdown_inside_li():
    """Dropdown <div> inside <li> containing links is extracted as children."""
    html = """
    <header><nav><ul>
      <li>
        <a href="/sport">Sport</a>
        <div class="dropdown">
          <a href="/sport/football">Football</a>
          <a href="/sport/cricket">Cricket</a>
        </div>
      </li>
      <li><a href="/news">News</a></li>
    </ul></nav></header>
    """
    links = extract_navigation(html, BASE)
    labels = {l.label for l in links}
    assert "Sport" in labels
    assert "Football" in labels
    assert "Cricket" in labels
    assert "News" in labels
    football = [l for l in links if l.label == "Football"][0]
    assert football.parent_label == "Sport"
    assert football.level == 1


def test_mega_menu_with_role_menu():
    """Elements with role='menu' are treated as navigation containers."""
    html = """
    <header><nav><ul>
      <li>
        <a href="/sport">Sport</a>
        <div role="menu">
          <a href="/sport/football">Football</a>
          <a href="/sport/rugby">Rugby</a>
        </div>
      </li>
    </ul></nav></header>
    """
    links = extract_navigation(html, BASE)
    assert len(links) == 3
    football = [l for l in links if l.label == "Football"][0]
    assert football.parent_label == "Sport"
    assert football.level == 1


def test_button_trigger_dropdown():
    """A <button> followed by a dropdown panel with links."""
    html = """
    <header><nav><ul>
      <li>
        <button aria-expanded="false">Sections</button>
        <div class="submenu">
          <a href="/news">News</a>
          <a href="/sport">Sport</a>
        </div>
      </li>
    </ul></nav></header>
    """
    links = extract_navigation(html, BASE)
    assert len(links) == 2
    assert {l.label for l in links} == {"News", "Sport"}


def test_nested_div_dropdown_hierarchy():
    """Nested dropdown divs create multi-level hierarchy."""
    html = """
    <header><nav><ul>
      <li>
        <a href="/sport">Sport</a>
        <div class="dropdown">
          <div class="dropdown-section">
            <a href="/sport/football">Football</a>
            <div class="sub-dropdown">
              <a href="/sport/football/premier-league">Premier League</a>
            </div>
          </div>
          <a href="/sport/cricket">Cricket</a>
        </div>
      </li>
    </ul></nav></header>
    """
    links = extract_navigation(html, BASE)
    labels = {l.label for l in links}
    assert "Sport" in labels
    assert "Football" in labels
    assert "Premier League" in labels
    assert "Cricket" in labels


# ── Nested <section>/<div> navigation (no <ul>) ─────────────────


def test_section_based_nav():
    """<section> elements with nested <section> children (nested-section pattern)."""
    html = """
    <header><nav>
      <section class="dd title">
        <a href="/f1">F1</a>
        <section class="dd-menu">
          <a href="/f1/news">News</a>
          <a href="/f1/results">Results</a>
        </section>
      </section>
      <section class="dd title">
        <a href="/motogp">MotoGP</a>
        <section class="dd-menu">
          <a href="/motogp/news">News</a>
          <a href="/motogp/results">Results</a>
        </section>
      </section>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    labels = {l.label for l in links}
    assert "F1" in labels
    assert "MotoGP" in labels
    assert "News" in labels
    assert "Results" in labels

    # Check hierarchy: F1 children have parent_label "F1"
    f1_children = [l for l in links if l.parent_label == "F1"]
    assert len(f1_children) == 2
    assert {l.path for l in f1_children} == {"/f1/news", "/f1/results"}
    assert all(l.level == 1 for l in f1_children)

    # MotoGP children
    mg_children = [l for l in links if l.parent_label == "MotoGP"]
    assert len(mg_children) == 2


def test_div_based_nested_nav():
    """<div> elements with nested <div> children work the same way."""
    html = """
    <header><nav>
      <div class="nav-item">
        <a href="/sport">Sport</a>
        <div class="nav-dropdown">
          <a href="/sport/football">Football</a>
          <a href="/sport/cricket">Cricket</a>
        </div>
      </div>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 3
    sport = [l for l in links if l.label == "Sport"][0]
    assert sport.level == 0
    children = [l for l in links if l.parent_label == "Sport"]
    assert len(children) == 2
    assert all(l.level == 1 for l in children)


def test_nested_section_without_parent_href():
    """Parent <section> with no href still provides label for children."""
    html = """
    <header><nav>
      <section class="dd title">
        <a href="/f1">F1</a>
        <section class="dd-menu">
          <a href="/f1/news">News</a>
        </section>
      </section>
    </nav></header>
    """
    links = extract_navigation(html, BASE)

    assert len(links) == 2
    news = [l for l in links if l.label == "News"][0]
    assert news.parent_label == "F1"


def test_header_with_mixed_nav_types():
    """Header with language selector <ul> and section-based main nav should find both.

    The language selector <ul> must not short-circuit detection of the
    <section>-based main navigation.
    """
    html = """
    <header>
      <nav class="language-selector">
        <ul><li><a href="/id">Indonesian</a></li></ul>
      </nav>
      <nav class="main-navigation">
        <section class="dd title">
          <a href="/f1">F1</a>
          <section class="dd-menu">
            <a href="/f1/news">News</a>
            <a href="/f1/results">Results</a>
          </section>
        </section>
        <section class="dd title">
          <a href="/motogp">MotoGP</a>
          <section class="dd-menu">
            <a href="/motogp/news">News</a>
          </section>
        </section>
      </nav>
    </header>
    """
    links = extract_navigation(html, BASE)

    labels = {l.label for l in links}
    assert "F1" in labels
    assert "MotoGP" in labels
    assert "News" in labels

    # F1 children should have parent_label
    f1_news = [l for l in links if l.path == "/f1/news"][0]
    assert f1_news.parent_label == "F1"
    assert f1_news.level == 1
