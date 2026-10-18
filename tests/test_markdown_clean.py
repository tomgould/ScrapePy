"""Tests for clean_markdown() junk removal patterns."""

import pytest

from scrapepy.convert.to_markdown import HTMLToMarkdown

# Enough filler to keep clean_markdown() from returning None (>50 chars)
FILLER = "This is enough real article content to pass the minimum word count check easily."


@pytest.fixture
def converter(tmp_path):
    """Minimal HTMLToMarkdown instance for testing clean_markdown."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    return HTMLToMarkdown(str(input_dir))


class TestAdvertisementRemoval:
    def test_removes_crash_net_ad_markers(self, converter):
        md = f"# Title\n\n{FILLER}\n\nArticle continues below ADVERTISEMENT\n\nArticle continues below ADVERTISEMENT\n\nSecond paragraph here."
        result = converter.clean_markdown(md)
        assert "ADVERTISEMENT" not in result
        assert FILLER in result
        assert "Second paragraph" in result

    def test_removes_ad_marker_with_extra_whitespace(self, converter):
        md = f"# Title\n\n{FILLER}\n\n  Article continues below   ADVERTISEMENT  \n\nMore content."
        result = converter.clean_markdown(md)
        assert "ADVERTISEMENT" not in result

    def test_removes_ad_marker_without_advertisement_word(self, converter):
        md = f"# Title\n\n{FILLER}\n\nArticle continues below\n\nMore content."
        result = converter.clean_markdown(md)
        assert "Article continues below" not in result


class TestGoogleBadgeRemoval:
    def test_removes_google_preferred_source_badge(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "[![Add Evening Standard as a preferred source]"
            "(../../images/google_preferred_source_badge_dark.png)]"
            "(https://google.com/preferences/source?q=https://www.example-news.co.uk/)\n\n"
            "Article content."
        )
        result = converter.clean_markdown(md)
        assert "preferred source" not in result
        assert "Article content." in result

    def test_preserves_normal_image_links(self, converter):
        md = f"# Title\n\n[![Photo](img.jpg)](page.html)\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "[![Photo](img.jpg)](page.html)" in result


class TestRelativeDateRemoval:
    @pytest.mark.parametrize("date_text", [
        "4 minutes ago",
        "2 hours ago",
        "1 day ago",
        "3 weeks ago",
        "6 months ago",
        "just now",
        "yesterday",
        "an hour ago",
        "a minute ago",
        "Updated 2 hours ago",
    ])
    def test_removes_relative_dates(self, converter, date_text):
        md = f"# Title\n\n[Author](author.md)\n\n{date_text}\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert date_text not in result
        assert FILLER in result

    def test_preserves_relative_date_in_sentence(self, converter):
        md = f"# Title\n\nHe said this happened 2 hours ago during the meeting.\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "2 hours ago" in result


class TestNewsletterRemoval:
    def test_removes_leave_this_field_blank(self, converter):
        md = f"# Title\n\n{FILLER}\n\nLeave this field blank\n\nMore content."
        result = converter.clean_markdown(md)
        assert "Leave this field blank" not in result

    def test_removes_standalone_sign_up(self, converter):
        md = f"# Title\n\n{FILLER}\n\nSign up\n\nMore content."
        result = converter.clean_markdown(md)
        assert "\nSign up\n" not in result

    def test_removes_newsletter_image_embed(self, converter):
        md = f"# Title\n\n{FILLER}\n\n![City Edition Newsletter](images/newsletter.png)\n\nMore."
        result = converter.clean_markdown(md)
        assert "newsletter" not in result.lower()

    def test_removes_email_consent_text(self, converter):
        md = f"# Title\n\n{FILLER}\n\nI would like to be emailed about offers, events and updates.\n\nMore."
        result = converter.clean_markdown(md)
        assert "emailed about" not in result

    def test_removes_privacy_info_line(self, converter):
        md = f"# Title\n\n{FILLER}\n\nFor more information see our [Privacy Policy](privacy.md)\n\nMore."
        result = converter.clean_markdown(md)
        assert "Privacy Policy" not in result

    def test_removes_newsletter_description(self, converter):
        md = f"# Title\n\n{FILLER}\n\nGet the latest F1 news direct to your inbox\n\nMore."
        result = converter.clean_markdown(md)
        assert "direct to your inbox" not in result


class TestJunkSectionRemoval:
    def test_removes_most_read_section(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "## Most Read\n\n"
            "- 1\n\n  [Football](sport.md)\n\n  [Some headline](article.md)\n"
            "- 2\n\n  [Crime](news.md)\n"
        )
        result = converter.clean_markdown(md)
        assert "Most Read" not in result
        assert "Football" not in result
        assert FILLER in result

    def test_removes_subscribe_section(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "## Subscribe to our F1 Newsletter\n\n"
            "Get the latest F1 news.\n\n"
            "Leave this field blank\n"
        )
        result = converter.clean_markdown(md)
        assert "Subscribe" not in result
        assert "F1 news" not in result

    def test_removes_in_this_article_section(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "## In this article\n\n"
            "[F1](f1.md)\n\n"
            "[Hamilton](hamilton.md)\n"
        )
        result = converter.clean_markdown(md)
        assert "In this article" not in result
        assert FILLER in result

    def test_removes_read_more_inline_section(self, converter):
        md = (
            f"# Title\n\nParagraph one.\n\n{FILLER}\n\n"
            "## Read More\n\n"
            "[Other article](other.md)   [Another article](another.md)\n\n"
            "Paragraph two that continues the story.\n"
        )
        result = converter.clean_markdown(md)
        assert "Read More" not in result
        assert "Paragraph one." in result
        assert "Paragraph two" in result

    def test_removes_related_articles_section(self, converter):
        md = f"# Title\n\n{FILLER}\n\n## Related Articles\n\n[Link](a.md)\n[Link2](b.md)"
        result = converter.clean_markdown(md)
        assert "Related Articles" not in result

    def test_preserves_legitimate_headings(self, converter):
        md = f"# Title\n\n## Introduction\n\n{FILLER}\n\n## Methodology\n\nMore content."
        result = converter.clean_markdown(md)
        assert "## Introduction" in result
        assert "## Methodology" in result

    def test_removes_h3_more_news(self, converter):
        md = f"# Title\n\n{FILLER}\n\n### More News\n\n[Link](a.md)\n\n[Link](b.md)"
        result = converter.clean_markdown(md)
        assert "More News" not in result

    def test_removes_comments_section(self, converter):
        md = f"# Title\n\n{FILLER}\n\n## Comments\n\nJoin the discussion below.\n\nUser1: Great article!"
        result = converter.clean_markdown(md)
        assert "Comments" not in result
        assert "discussion" not in result


class TestMoreAboutRemoval:
    def test_removes_more_about_with_links(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "MORE ABOUT\n\n"
            "[Head](topic/head.md)\n\n"
            "[World Economic Forum](topic/wef.md)\n\n"
            "[Norwegian](topic/norwegian.md)\n"
        )
        result = converter.clean_markdown(md)
        assert "MORE ABOUT" not in result
        assert "World Economic Forum" not in result
        assert FILLER in result


class TestNavigationBlockRemoval:
    def test_removes_previous_navigation_block(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "[### Previous\n\n"
            "Some other article title\n\n"
            "![Image](img.jpg)](./other-article.html)\n\n"
        )
        result = converter.clean_markdown(md)
        assert "Previous" not in result
        assert "other article" not in result

    def test_removes_next_navigation_block(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "[### Next\n\n"
            "Next article title\n\n"
            "![Image](img.jpg)](./next-article.html)\n\n"
        )
        result = converter.clean_markdown(md)
        assert "Next article title" not in result


class TestShoppingRemoval:
    def test_removes_shopping_best_buys(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "[Shopping Best Buys](shopping/esbest.md)\n\n"
            "[![img](photo.jpg)\n\n"
            "10 stylish items](shopping/items.html)\n"
        )
        result = converter.clean_markdown(md)
        assert "Shopping Best Buys" not in result
        assert "stylish items" not in result


class TestSiteNameStripping:
    def test_strips_pipe_site_name(self, converter):
        md = f"# Lewis Hamilton verdict | Example.com\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert result.startswith("# Lewis Hamilton verdict\n")
        assert "Example.com" not in result

    def test_strips_dash_site_name(self, converter):
        md = f"# Breaking news story - BBC.co.uk\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "# Breaking news story\n" in result
        assert "BBC.co.uk" not in result

    def test_strips_emdash_site_name(self, converter):
        md = f"# Article title \u2014 The Guardian.com\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Guardian.com" not in result

    def test_preserves_pipe_in_non_domain(self, converter):
        md = f"# Results | Round 5\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Results | Round 5" in result


class TestDuplicateH1:
    def test_removes_duplicate_h1(self, converter):
        md = f"# My Article Title\n\nBreadcrumb\n\n# My Article Title\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert result.count("# My Article Title") == 1
        assert FILLER in result

    def test_keeps_different_h1s(self, converter):
        md = f"# First Title\n\n{FILLER}\n\n# Second Title\n\nMore content."
        result = converter.clean_markdown(md)
        assert "# First Title" in result
        assert "# Second Title" in result


class TestMultilineLinks:
    def test_fixes_multiline_link_text(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "[Champions League draw simulated\n\n"
            "Arsenal get Atalanta](sport/article.md)\n\n"
        )
        result = converter.clean_markdown(md)
        # The newlines in link text should be replaced with spaces
        assert "[Champions League draw simulated Arsenal get Atalanta](sport/article.md)" in result


class TestHtmlJunkRemoval:
    def test_removes_ad_container_from_html(self):
        from bs4 import BeautifulSoup
        html = '<article><p>Content.</p><div class="ad-container">AD</div><p>More.</p></article>'
        soup = BeautifulSoup(html, "lxml")
        converter = HTMLToMarkdown.__new__(HTMLToMarkdown)
        converter._remove_junk_elements(soup)
        assert soup.find(class_="ad-container") is None
        assert "Content." in soup.get_text()

    def test_removes_newsletter_container(self):
        from bs4 import BeautifulSoup
        html = '<article><p>Content.</p><div class="newsletter-signup">Subscribe</div><p>More.</p></article>'
        soup = BeautifulSoup(html, "lxml")
        converter = HTMLToMarkdown.__new__(HTMLToMarkdown)
        converter._remove_junk_elements(soup)
        assert soup.find(class_="newsletter-signup") is None

    def test_removes_most_read_container(self):
        from bs4 import BeautifulSoup
        html = '<article><p>Content.</p><section class="most-read"><h2>Most Read</h2><ul><li>Item</li></ul></section><p>More.</p></article>'
        soup = BeautifulSoup(html, "lxml")
        converter = HTMLToMarkdown.__new__(HTMLToMarkdown)
        converter._remove_junk_elements(soup)
        text = soup.get_text()
        assert "Most Read" not in text
        assert "Content." in text

    def test_preserves_article_content(self):
        from bs4 import BeautifulSoup
        html = '<article><h1>Title</h1><p>Real content here.</p><p>More real content.</p></article>'
        soup = BeautifulSoup(html, "lxml")
        converter = HTMLToMarkdown.__new__(HTMLToMarkdown)
        converter._remove_junk_elements(soup)
        assert "Real content here." in soup.get_text()
        assert "More real content." in soup.get_text()

    def test_handles_decomposed_children(self):
        """When parent is decomposed, children shouldn't cause errors."""
        from bs4 import BeautifulSoup
        html = '<article><p>Content.</p><div class="ad-container"><div class="ad-wrapper"><span>AD</span></div></div></article>'
        soup = BeautifulSoup(html, "lxml")
        converter = HTMLToMarkdown.__new__(HTMLToMarkdown)
        converter._remove_junk_elements(soup)  # Should not raise
        assert "AD" not in soup.get_text()


class TestIntegration:
    """Test clean_markdown with real-world article patterns."""

    def test_standard_co_uk_article_cleanup(self, converter):
        """Simulate a news-site article junk removal."""
        md = (
            "# Head of WEF quits\n\n"
            "[Business](business.md) | [Business News](news.md)\n\n"
            "# Head of WEF quits\n\n"  # Duplicate H1
            "Description text.\n\n"
            "[![Add Evening Standard as a preferred source](images/badge.png)]"
            "(https://google.com/preferences/source?q=https://www.example-news.co.uk/)\n\n"
            "![](photo.jpg)\n\n"
            "Caption text here.\n\n"
            "[Author Name](author/name.md)\n\n"
            "4 minutes ago\n\n"
            "![City Edition](images/newsletter_city_edition_embed_desktop.png)\n\n"
            "News from The City updates.\n\n"
            "Sign up\n\n"
            "I would like to be emailed about offers and updates. Read our [privacy notice](privacy.md).\n\n"
            "The actual article content goes here with several paragraphs of text "
            "that are meaningful and discuss the topic at length.\n\n"
            "A second paragraph continues the discussion with more details "
            "and quotes from relevant sources.\n\n"
            "MORE ABOUT\n\n"
            "[Topic One](topic/one.md)\n\n"
            "[Topic Two](topic/two.md)\n\n"
            "## Most Read\n\n"
            "- 1\n\n  [Football](sport.md)\n\n  [Article title](article.md)\n\n"
            "[Shopping Best Buys](shopping/esbest.md)\n\n"
            "[![img](photo.jpg)\n\n10 items](shopping/items.html)\n"
        )
        result = converter.clean_markdown(md)
        assert result.count("# Head of WEF quits") == 1
        assert "preferred source" not in result
        assert "4 minutes ago" not in result
        assert "newsletter" not in result.lower()
        assert "Sign up" not in result.split("\n")  # Not as standalone line
        assert "emailed about" not in result
        assert "MORE ABOUT" not in result
        assert "Most Read" not in result
        assert "Shopping Best Buys" not in result
        assert "actual article content" in result
        assert "second paragraph" in result

    def test_crash_net_article_cleanup(self, converter):
        """Simulate example.com article junk removal."""
        md = (
            "# Hamilton prediction | Example.com\n\n"
            "Will Buxton believes Ferrari could be back in contention.\n\n"
            "Article continues below ADVERTISEMENT\n\n"
            "Article continues below ADVERTISEMENT\n\n"
            "More article content with detailed analysis of the situation.\n\n"
            "## Read More\n\n"
            "[Other article](other.md)   [Another](another.md)\n\n"
            "Even more content with quotes and analysis from experts.\n\n"
            "Article continues below ADVERTISEMENT\n\n"
            "Article continues below ADVERTISEMENT\n\n"
            "## Subscribe to our F1 Newsletter\n\n"
            "Get the latest F1 news direct to your inbox\n\n"
            "For more information see our [Privacy Policy](privacy.md)\n\n"
            "Leave this field blank\n\n"
            "## In this article\n\n"
            "[F1](f1.md)\n\n"
            "[Hamilton](hamilton.md)\n\n"
            "### More News\n\n"
            "[F1](f1.md)  [Feature](features.md)\n\n"
            "[Old article link](old.md)\n"
        )
        result = converter.clean_markdown(md)
        assert "# Hamilton prediction\n" in result
        assert "Example.com" not in result
        assert "ADVERTISEMENT" not in result
        assert "Read More" not in result
        assert "Subscribe" not in result
        assert "In this article" not in result
        assert "More News" not in result
        assert "Will Buxton believes" in result
        assert "More article content" in result
        assert "Even more content" in result


class TestGuardianPatterns:
    def test_removes_view_image_fullscreen(self, converter):
        md = f"# Title\n\n{FILLER}\n\n[View image in fullscreen](#img-1)\n\nMore text here."
        result = converter.clean_markdown(md)
        assert "View image in fullscreen" not in result
        assert FILLER in result

    def test_removes_prefer_on_google(self, converter):
        md = f"# Title\n\n{FILLER}\n\n[Prefer the Guardian on Google](https://www.google.com/preferences/source?q=theguardian.com)\n\nMore text here."
        result = converter.clean_markdown(md)
        assert "Prefer the Guardian on Google" not in result

    def test_removes_reuse_content(self, converter):
        md = f"# Title\n\n{FILLER}\n\n[Reuse this content](https://syndication.theguardian.com/?url=...)\n\nMore text."
        result = converter.clean_markdown(md)
        assert "Reuse this content" not in result

    def test_removes_share_mailto(self, converter):
        md = f"# Title\n\n{FILLER}\n\n[Share](mailto:?subject=Some+Article&body=https://example.com)\n\nMore text."
        result = converter.clean_markdown(md)
        assert "[Share](mailto:" not in result

    def test_removes_supported_by_block(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "Supported by\n\n"
            "[![theguardian.org](https://static.theguardian.com/sponsor.png)](https://theguardian.org/)\n\n"
            "[About this content](./reworked-a-guardian-series-16.md)\n\n"
            "More article text here."
        )
        result = converter.clean_markdown(md)
        assert "Supported by" not in result
        assert "About this content" not in result
        assert "More article text" in result

    def test_removes_explore_topics_block(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "Explore more on these topics\n\n"
            "- [AI (artificial intelligence)](../../ai.md)\n"
            "- [Global economy](../../../business/global-economy.md)\n"
            "- [Economics](../../../business/economics.md)\n\n"
            "More text after topics."
        )
        result = converter.clean_markdown(md)
        assert "Explore more on these topics" not in result
        assert "Global economy" not in result
        assert "More text after topics" in result

    def test_removes_duplicate_image_caption(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "![Robinson with official in DC](https://example.com/image.jpg)\n\n"
            "Robinson with official in DC\n\n"
            "More article content here."
        )
        result = converter.clean_markdown(md)
        # Image should remain
        assert "![Robinson with official in DC]" in result
        # The duplicate standalone caption should be gone
        lines = result.split('\n')
        caption_count = sum(1 for l in lines if l.strip() == "Robinson with official in DC")
        assert caption_count == 0  # Only in the image alt text, not standalone

    def test_guardian_integration(self, converter):
        """Simulate a Guardian article with all junk patterns."""
        md = (
            "# Workers on training AI to do their jobs\n\n"
            "![Figurine at desk](https://example.com/image.jpg)\n\n"
            "Illustration: Guardian Design/Getty Images\n\n"
            "[View image in fullscreen](#img-1)\n\n"
            "Illustration: Guardian Design/Getty Images\n\n"
            "[Reworked](../../series/reworked.md)[AI](../../ai.md)\n\n"
            "Some say the technology is devaluing their work, while others reckon "
            "it is not yet good enough to replace them entirely. "
            "This long article explores the impact of AI on workers across many industries and sectors.\n\n"
            "Supported by\n\n"
            "[![theguardian.org](https://static.theguardian.com/sponsor.png)](https://theguardian.org/)\n\n"
            "[About this content](./reworked.md)\n\n"
            "[Jane Clinton](../../../profile/jane-clinton.md)\n\n"
            "Thu 26 Feb 2026 10.00 GMT\n\n"
            "[Share](mailto:?subject=Workers+on+AI&body=https://example.com)\n\n"
            "[Prefer the Guardian on Google](https://www.google.com/preferences/source?q=theguardian.com)\n\n"
            "Explore more on these topics\n\n"
            "- [AI (artificial intelligence)](../../ai.md)\n"
            "- [Global economy](../../../business/global-economy.md)\n\n"
            "[Share](mailto:?subject=Workers+on+AI&body=https://example.com)\n\n"
            "[Reuse this content](https://syndication.theguardian.com/?url=...)\n"
        )
        result = converter.clean_markdown(md)
        assert "Workers on training AI" in result
        assert "devaluing their work" in result
        assert "View image in fullscreen" not in result
        assert "Prefer the Guardian on Google" not in result
        assert "Reuse this content" not in result
        assert "Supported by" not in result
        assert "Explore more on these topics" not in result
        assert "[Share](mailto:" not in result


class TestIndependentPatterns:
    def test_removes_bookmark_popover(self, converter):
        md = f"# Title\n\n{FILLER}\n\n## Bookmark popover\n\nRemoved from bookmarks\n\nClose popover\n\nMore text."
        result = converter.clean_markdown(md)
        assert "Bookmark popover" not in result
        assert "Removed from bookmarks" not in result
        assert "Close popover" not in result

    def test_removes_open_image_in_gallery(self, converter):
        md = f"# Title\n\n{FILLER}\n\n![Photo](https://example.com/img.jpg)\n\nopen image in gallery\n\nCaption text.\n\nMore content."
        result = converter.clean_markdown(md)
        assert "open image in gallery" not in result
        assert "Caption text" in result

    def test_removes_join_commenting_forum(self, converter):
        md = f"# Title\n\n{FILLER}\n\n## Join our commenting forum\n\nJoin thought-provoking conversations\n\n[Comments](#comments-area)"
        result = converter.clean_markdown(md)
        assert "commenting forum" not in result
        assert "thought-provoking" not in result

    def test_removes_video_player_placeholder_svg(self, converter):
        md = f"# Title\n\n{FILLER}\n\n![Video Player Placeholder](../../images/logo-white-out.svg)\n\nMore text."
        result = converter.clean_markdown(md)
        assert "Video Player Placeholder" not in result
        assert "More text" in result

    def test_strips_brand_name_from_h1(self, converter):
        md = f"# Great article about something important | The Independent\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "# Great article about something important\n" in result
        assert "The Independent" not in result

    def test_strips_brand_name_emdash(self, converter):
        md = f"# Some long headline about an event \u2014 BBC News\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "# Some long headline about an event\n" in result
        assert "BBC News" not in result


class TestTechCrunchPatterns:
    def test_removes_event_promotion_block(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "The Galaxy S26 starts at $899.\n\n"
            "Techcrunch event\n\n"
            "### Save up to $300 to TechCrunch Founder Summit\n\n"
            "#### 1,000+ founders and investors come together.\n\n"
            "### Save up to $300 to TechCrunch Founder Summit\n\n"
            "#### 1,000+ founders and investors come together.\n\n"
            "Boston, MA\n|\nJune 9, 2026\n\n"
            "[REGISTER NOW](./events/techcrunch-founder-summit-2026.md)\n\n"
            "## Privacy display is the marquee feature\n\n"
            "Important article content continues here with more details."
        )
        result = converter.clean_markdown(md)
        assert "Techcrunch event" not in result
        assert "REGISTER NOW" not in result
        assert "Save up to $300" not in result
        assert "$899" in result
        assert "Privacy display" in result
        assert "Important article content" in result


class TestVergePatterns:
    def test_removes_topic_follow_widgets(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "Posts from this topic will be added to your daily email digest and your homepage feed.\n\n"
            "Follow\n\n"
            "[See All Tech](../tech.md)\n\n"
            "More article content here with enough words to pass."
        )
        result = converter.clean_markdown(md)
        assert "daily email digest" not in result
        assert "See All Tech" not in result
        assert "More article content" in result

    def test_removes_follow_topics_footer(self, converter):
        md = f"# Title\n\n{FILLER}\n\n**Follow topics and authors** from this story to see more like this.\n\nMore text."
        result = converter.clean_markdown(md)
        assert "Follow topics and authors" not in result

    def test_removes_advertiser_content(self, converter):
        md = f"# Title\n\n{FILLER}\n\nAdvertiser Content From\n\n!Sponsor Logo\n\nThis is the title for the native ad\n\n!Sponsor thumbnail"
        result = converter.clean_markdown(md)
        assert "Advertiser Content" not in result
        assert "Sponsor" not in result

    def test_removes_duplicate_consecutive_images(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "![Hero Image](https://example.com/hero.jpg)\n\n"
            "![Hero Image](https://example.com/hero.jpg)\n\n"
            "Caption text here."
        )
        result = converter.clean_markdown(md)
        assert result.count("https://example.com/hero.jpg") == 1
        assert "Caption text" in result


class TestMirrorPatterns:
    def test_removes_view_images_text(self, converter):
        md = f"# Title\n\n{FILLER}\n\n![Photo](https://example.com/img.jpg)\n\nView 2 Images\n\nCaption (Image: Getty)"
        result = converter.clean_markdown(md)
        assert "View 2 Images" not in result

    def test_removes_comments_link(self, converter):
        md = f"# Title\n\n{FILLER}\n\n[Comments](#comments-wrapper)\n\nArticle content."
        result = converter.clean_markdown(md)
        assert "[Comments]" not in result

    def test_removes_zero_width_characters(self, converter):
        md = f"# Title\n\n{FILLER}\n\n\u200c\n\nMore content here."
        result = converter.clean_markdown(md)
        # The zero-width line should be gone, content preserved
        assert "More content" in result


class TestWiredPatterns:
    def test_removes_save_this_story(self, converter):
        md = f"# Who's Your Daddy? A Chatbot\nSave this story\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Save this story" not in result
        assert "Chatbot" in result

    def test_removes_skip_to_main_content(self, converter):
        md = f"# Title\n\nSkip to main content\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Skip to main content" not in result

    def test_strips_brand_name_with_hyphen_separator(self, converter):
        md = f"# New AirSnitch attack bypasses Wi-Fi encryption - Ars Technica\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Ars Technica" not in result
        assert "AirSnitch" in result


class TestHearstPatterns:
    def test_removes_related_story_h6_block(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "###### Related Story\n\n"
            "- [![](https://img.com/thumb.jpg)Who's the New Lady?](./article.html)\n\n"
            "More content after."
        )
        result = converter.clean_markdown(md)
        assert "Related Story" not in result
        assert "More content after" in result

    def test_removes_for_more_on_that_block(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "###### For More on That...\n\n"
            "- [![](https://img.com/thumb.jpg)Article One](./a1.html)\n"
            "- [![](https://img.com/thumb2.jpg)Article Two](./a2.html)\n\n"
            "Final paragraph."
        )
        result = converter.clean_markdown(md)
        assert "For More on That" not in result
        assert "Article One" not in result
        assert "Final paragraph" in result

    def test_removes_caret_right_from_heading(self, converter):
        md = f"# Title\n\n### Key Points Caret Right\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Caret Right" not in result
        assert "Key Points" in result

    def test_removes_standalone_chevron(self, converter):
        md = f"# Title\n\nChevron\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Chevron" not in result

    def test_removes_linked_heading_thumbnail_grid(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "## [Celebrity Relationship Timelines](../timelines.md)\n\n"
            "[![thumb](https://hips.hearstapps.com/img1.jpg)Article One](./a1.html)"
            "[![thumb](https://hips.hearstapps.com/img2.jpg)Article Two](./a2.html)\n\n"
            "Should keep this."
        )
        result = converter.clean_markdown(md)
        assert "Celebrity Relationship" not in result
        assert "Article One" not in result
        assert "Should keep this" in result

    def test_removes_advertisement_continue_reading(self, converter):
        md = f"# Title\n\n{FILLER}\n\nAdvertisement - Continue Reading Below\n\nMore text."
        result = converter.clean_markdown(md)
        assert "Advertisement" not in result
        assert "More text" in result


class TestNMEPatterns:
    def test_removes_standalone_advertisement(self, converter):
        md = f"# Title\n\n{FILLER}\n\nAdvertisement\n\nMore content."
        result = converter.clean_markdown(md)
        assert "Advertisement" not in result
        assert "More content" in result

    def test_removes_related_topics_list(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "- Related Topics\n"
            "- [BRIT Awards](./tag/brit-awards.md)\n"
            "- [Harry Styles](./artists/harry-styles.md)\n"
        )
        result = converter.clean_markdown(md)
        assert "Related Topics" not in result
        assert "BRIT Awards" not in result

    def test_removes_breadcrumb_links(self, converter):
        md = (
            f'# Title\n\n'
            f'[News](../../news.html "View all posts in News")'
            f'[Music News](../music.html "View all posts in Music News")\n\n'
            f'{FILLER}'
        )
        result = converter.clean_markdown(md)
        assert "View all posts" not in result
        assert FILLER in result

    def test_removes_h5_recommended_section(self, converter):
        md = f"# Title\n\n{FILLER}\n\n##### Recommended\n\nSome recommended content here.\n\nMore junk."
        result = converter.clean_markdown(md)
        assert "Recommended" not in result

    def test_removes_more_stories_section(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "### More Stories\n\n"
            "[Music News](./music.md)\n\n"
            "[Music News](./music.md)\n\n"
        )
        result = converter.clean_markdown(md)
        assert "More Stories" not in result


class TestDailyMailPatterns:
    def test_removes_email_share_link(self, converter):
        md = f"# Title\n\n{FILLER}\n\n- [e-mail](mailto:?subject=Read%20this&body=Full%20content)\n\nContent."
        result = converter.clean_markdown(md)
        assert "e-mail" not in result
        assert "Content." in result

    def test_removes_share_count(self, converter):
        md = f"# Title\n\n{FILLER}\n\n**35** shares\n\nContent."
        result = converter.clean_markdown(md)
        assert "shares" not in result

    def test_removes_view_comments_link(self, converter):
        md = f"# Title\n\n{FILLER}\n\n 453 View comments\n\nContent."
        result = converter.clean_markdown(md)
        assert "View comments" not in result

    def test_removes_comments_section(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "### Comments 457\n\n"
            "Share what you think\n\n"
            "- [Newest](#)\n- [Oldest](#)\n- [Best rated](#)\n- [Worst rated](#)\n\n"
            "View all\n\n"
            "The comments below have not been moderated.\n\n"
            "### Add your comment\n\n"
            "Enter your comment\n\n"
            "By posting your comment you agree to our [house rules](./rules.html)\n\n"
            "Submit Comment\nClear\n"
        )
        result = converter.clean_markdown(md)
        assert "Comments 457" not in result
        assert "Share what you think" not in result
        assert "Newest" not in result
        assert "have not been moderated" not in result
        assert "Add your comment" not in result
        assert "Submit Comment" not in result

    def test_removes_hero_video_poster(self, converter):
        md = f"# Title\n\n{FILLER}\n\n![Hero video poster](https://img.com/poster.jpg)\n\nContent."
        result = converter.clean_markdown(md)
        assert "Hero video poster" not in result


class TestExpressPatterns:
    def test_removes_google_preferred_source_express_style(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            "[### Daily Express logoGet More of Our News on Google Set Daily Express as a "
            "'Preferred Source' to get quicker access to the news you value. "
            "![Add as a preferred source on Google](https://cdn.images.express.co.uk/img/google-preferred-source.png)]"
            "(https://www.google.com/preferences/source?q=express.co.uk)\n\n"
            "More content."
        )
        result = converter.clean_markdown(md)
        assert "Preferred Source" not in result
        assert "More content" in result

    def test_removes_affiliate_disclaimer(self, converter):
        md = f"# Title\n\n{FILLER}\n\nThis article contains affiliate links, we will receive a commission. [Learn more](./info.md)\n\nArticle starts."
        result = converter.clean_markdown(md)
        assert "affiliate links" not in result
        assert "Article starts" in result


class TestFoxNewsPatterns:
    """Fox News site-specific junk patterns."""

    def test_removes_close_button_link(self, converter):
        md = f"# Amazon ends robot program\n[close](#)\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "[close]" not in result
        assert "Amazon ends robot program" in result

    def test_removes_new_listen_banner(self, converter):
        md = f"# Title\n\nNEWYou can now listen to Fox News articles!\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "listen to Fox News" not in result
        assert FILLER in result

    def test_removes_app_download_cta(self, converter):
        md = f"# Title\n\n{FILLER}\n\n[**CLICK HERE TO DOWNLOAD THE FOX NEWS APP**](https://foxnews.onelink.me/xLDS)"
        result = converter.clean_markdown(md)
        assert "CLICK HERE TO DOWNLOAD" not in result
        assert FILLER in result

    def test_removes_fox_flash_promo(self, converter):
        md = (
            f"# Title\n\n"
            f"[![Fox News Flash top headlines for February 26](https://example.com/img.jpg) Video](../video/123.html)\n\n"
            f"#### [Fox News Flash top headlines for February 26](../video/123.md)\n\n"
            f"Fox News Flash top headlines are here. Check out what's clicking on FoxNews.com.\n\n"
            f"{FILLER}"
        )
        result = converter.clean_markdown(md)
        assert "Fox News Flash" not in result
        assert FILLER in result

    def test_removes_signup_section(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            f"## Sign up for my FREE CyberGuy Report\n"
            f"Get my best tech tips delivered straight to your inbox.\n\n"
            f"## Next Real Section\nMore content."
        )
        result = converter.clean_markdown(md)
        assert "CyberGuy Report" not in result
        assert "Next Real Section" in result

    def test_removes_quiz_section(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            f"## Take my quiz: How safe is your online security?\n"
            f"Think your devices and data are truly protected?\n\n"
            f"## Real Heading\nReal content."
        )
        result = converter.clean_markdown(md)
        assert "Take my quiz" not in result
        assert "Real Heading" in result

    def test_removes_copyright_with_domain(self, converter):
        md = f"# Title\n\n{FILLER}\n\nCopyright 2026 CyberGuy.com. All rights reserved."
        result = converter.clean_markdown(md)
        assert "Copyright 2026" not in result
        assert FILLER in result

    def test_removes_related_article_singular(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            f"### Related Article\n"
            f"[![Robots learn tasks](https://example.com/img.jpg)](./robots.html)\n\n"
            f"[Robots learn tasks](./robots.md)"
        )
        result = converter.clean_markdown(md)
        assert "Related Article" not in result


class TestGBNewsPatterns:
    """GB News-specific junk patterns."""

    def test_removes_social_share_icons(self, converter):
        md = (
            f"# Title\n\n"
            f"![desktop-share](https://assets.rbl.ms/123/origin.png)Share"
            f"![desktop-like](https://assets.rbl.ms/456/origin.png)Like"
            f"![desktop-comment](https://assets.rbl.ms/789/origin.png)\n\n"
            f"{FILLER}"
        )
        result = converter.clean_markdown(md)
        assert "desktop-share" not in result
        assert "desktop-like" not in result
        assert "desktop-comment" not in result
        assert FILLER in result

    def test_removes_social_share_links(self, converter):
        md = (
            f"# Title\n\n"
            f"[Facebook](https://www.facebook.com/sharer/sharer.php?u=https://example.com)  "
            f"[Twitter](https://twitter.com/intent/tweet?url=https://example.com)  "
            f"[E-mail](mailto:?body=https://example.com)\n\n"
            f"{FILLER}"
        )
        result = converter.clean_markdown(md)
        assert "[Facebook]" not in result
        assert "[Twitter]" not in result
        assert "[E-mail]" not in result
        assert FILLER in result

    def test_reformats_byline_block(self, converter):
        md = (
            f"# Title\n\n"
            f"By\n"
            f"[Joe Sledge](../authors/joe-sledge.html),\n\n"
            f"{FILLER}"
        )
        result = converter.clean_markdown(md)
        assert "By Joe Sledge" in result
        assert "[Joe Sledge]" not in result

    def test_removes_published_date_in_body(self, converter):
        md = f"# Title\n\nPublished: 26/02/2026\n\n-  20:05\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Published: 26/02/2026" not in result
        assert "20:05" not in result
        assert FILLER in result

    def test_removes_standalone_latest_heading(self, converter):
        md = f"# Title\n\n## Latest\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "## Latest" not in result
        assert FILLER in result

    def test_preserves_latest_news_heading(self, converter):
        """Ensure 'Latest News' (in _JUNK_SECTION_RE) is handled, but
        standalone 'Latest' doesn't eat following content."""
        md = f"# Title\n\n{FILLER}\n\n## Latest\n\nThis should remain."
        result = converter.clean_markdown(md)
        assert "This should remain." in result


class TestPitchforkPatterns:
    """Pitchfork-specific junk patterns."""

    def test_removes_no_score_text(self, converter):
        md = f"# Title\n\nNo score yet, be the first to add.\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "No score yet" not in result
        assert FILLER in result

    def test_removes_reader_score(self, converter):
        md = f"# Title\n\nReader Score\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "Reader Score" not in result
        assert FILLER in result

    def test_removes_zero_rating(self, converter):
        md = f"# Title\n\n0.0\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "0.0" not in result
        assert FILLER in result

    def test_removes_most_read_sidebar(self, converter):
        md = (
            f"# Title\n\n{FILLER}\n\n"
            f"Most Read Reviews\n\n"
            f"[Album One](./album-one.md)\n"
            f"[Album Two](./album-two.md)\n\n"
            f"More real content."
        )
        result = converter.clean_markdown(md)
        assert "Most Read Reviews" not in result
        assert "[Album One]" not in result
        assert "More real content." in result


class TestStandardPatterns:
    """Evening Standard-specific junk patterns."""

    def test_removes_relative_timestamp_with_comments(self, converter):
        md = f"# Title\n\n2 minutes ago[COMMENTS](#comments-area)\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "minutes ago" not in result
        assert "COMMENTS" not in result
        assert FILLER in result

    def test_removes_hours_ago_with_comments(self, converter):
        md = f"# Title\n\n10 hours ago[COMMENTS](#comments-area)\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "hours ago" not in result
        assert FILLER in result

    def test_removes_relative_timestamp_without_comments(self, converter):
        md = f"# Title\n\n3 days ago\n\n{FILLER}"
        result = converter.clean_markdown(md)
        assert "days ago" not in result
        assert FILLER in result

    def test_removes_newsletter_embed_image(self, converter):
        md = (
            f"# Title\n\n"
            f"![The Weekender](../images/newsletter_weekender_embed_desktop.png)\n\n"
            f"{FILLER}"
        )
        result = converter.clean_markdown(md)
        assert "newsletter" not in result
        assert "Weekender" not in result
        assert FILLER in result

    def test_removes_west_end_newsletter_embed(self, converter):
        md = (
            f"# Title\n\n"
            f"![WEST END FINAL](../../images/newsletter_we_final_embed_desktop.png)\n\n"
            f"{FILLER}"
        )
        result = converter.clean_markdown(md)
        assert "newsletter" not in result
        assert FILLER in result
