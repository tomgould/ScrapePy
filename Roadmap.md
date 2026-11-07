# Roadmap

What ScrapePy does today is described in [README.md](README.md) and, for its output, [docs/MARKDOWN-OUTPUT.md](docs/MARKDOWN-OUTPUT.md). Released and unreleased changes are recorded in [CHANGELOG.md](CHANGELOG.md). This page lists only work that is planned.

## Planned

No timeline or priority is assigned.

### Crawling and output

- `-j`: scroll each page, wait for its content to render (with `--wait` controlling that wait) and extract links in the browser, using the existing `wait_for_js_content()` and `extract_links_browser()`
- Configurable retry count for failed fetches (HTTP page fetches use Scrapling's default of three attempts)
- User-defined CSS selectors for content extraction
- A JSON output option alongside HTML and Markdown
- A progress indicator during a crawl
- The `logging` module in place of `print()`, for log levels and file output

### Instagram

- Stories, which need a login and expire
- Story Highlights saved on a profile
- Full comment threads ("View all X comments"), not only the preview comments
- Engagement history across `--update-meta` runs, appended to a `history.json`
- Export of scraped data to CSV or JSON for analytics tools
- An engagement report comparing metrics across runs
- Shortcuts for a one-off Instagram scrape and for the daily `--update-meta` monitoring run

### Not planned

- Unifying the link-normalisation functions: each serves a different caller (crawl queueing, section discovery, general normalisation) with deliberately different edge cases.
- Sharing parsed HTML between the crawl, the classifier and date extraction: the API changes it would need outweigh the gain.
