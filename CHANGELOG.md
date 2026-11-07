# Changelog

All notable changes to ScrapePy are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/).

## Unreleased

### Added

- `--markdown-only`: Markdown and run state only, with no HTML mirror and no image or asset downloads. Links and images stay absolute URLs, and every request is a page or reconnaissance request checked against robots.txt and paced.
- `docs/MARKDOWN-OUTPUT.md`, the output contract for tools that read the Markdown.
- `canonical_url` in frontmatter when the page declares a different same-site canonical (`url` stays the fetched address), and `duplicates` in `run_summary.json`.
- Recipe pages: schema.org Recipe data (JSON-LD, or simple microdata) is written as fixed `## Recipe details`, `## Ingredients`, `## Method` and `## Nutrition` sections, with nutrition values kept exactly as published and absent ones marked "not stated". The ingredient and method lists are removed from the page text, which follows under `## Page text`. Frontmatter gains `page_type: recipe`, `recipe_category`, `recipe_cuisine`, `servings`, `total_time` and `keywords`. Page text is removed only when it repeats an ingredient or step exactly, so notes and qualifiers are never lost; step numbers are removed only when they are clearly numbering and published numbers keep their text. Broken or unusual JSON-LD never stops a page converting.
- Feeds: an RSS 2.0 or Atom feed URL (detected from paths such as `/feed` and `/rss.xml`, or forced with `--feed`) is fetched once and each entry becomes a Markdown file with `page_type: feed_entry`, `date_source: FEED` and `author`. `--max-articles` caps the entries (50 by default). `run_summary.json` gains a `feed` object listing written, unchanged, skipped and teaser entries. Entry dates keep the precision they were published with. A DOCTYPE is removed before parsing, so entities are never expanded and nothing is fetched or read. Entry links are not followed. No new dependency.

### Changed

- Frontmatter is written with `yaml.safe_dump`, so values such as `yes`, `null`, `123` or text with colons, quotes and newlines load back unchanged. Strings are now quoted in YAML's usual way (single quotes where needed).
- Markdown is better for indexing: one H1 and no skipped heading levels, the main content found on listing pages without a known content container, headings inside card links kept, truncated link text expanded from its title, button furniture removed while accordion and FAQ questions, option lists and textarea text are kept, code fenced with its language.
- A page whose Markdown matches a file already written in the same run is not written twice.
- Section URLs ending in `index.html` map to the section's own `_index.md`; listing pages no longer get a visible date from the server's Last-Modified header.

### Fixed

- A page with a sidebar or widget container nested inside another one failed to convert (`AttributeError: 'NoneType' object has no attribute 'get'`): containers inside one already removed are now skipped, and the same guard covers H1 and image clean-up.
- `--proxy` is used by every request: page fetches, reconnaissance, robots.txt, sitemap and feed requests and feed mode, not only the browser and asset downloads. It is validated (`http`, `https` and `socks` schemes) and its credentials are masked in console output, logs, errors and `run_summary.json`.
- Accordion labels that contain a furniture word ("Next steps after your application", "Our cookie recipes", "2024") lost their text; furniture is now matched on the whole label, and number-only selects are kept unless named as a quantity.
- A heading above the page's H1, such as a site tagline, became the title. The first H1 is now the title and headings above it are kept as paragraphs.
- Code containing a line of three backticks broke out of its fence, headings in a quote inside a list item were not normalised, and `javascript:`, `data:` and `tel:` links got `.md` appended.
- A page's alternate formats (`<link rel="alternate">` JSON, Markdown or feed versions) and `.json`, `.xml`, `.rss` and `.atom` links were crawled as pages, and responses that are not HTML were converted, so data files overwrote real pages. They are no longer queued or converted.
- A second URL mapping to a file already written in the same run (`/about` and `/about/`) overwrote it. A page now never replaces another URL's file: the later one gets a hash-suffixed name, listed under `renamed` in `run_summary.json`, and the URL-to-file assignment is kept in the cache so every URL keeps its file. Non-HTML responses are no longer saved in the default HTML copy.
- A junk-removal rule deleted every linked H2 or H3 heading, and links with a title attribute got `.md` appended after the title.

## 0.2.0

### Added

- robots.txt is enforced following RFC 9309 (longest match wins, `*` and `$` wildcards in a linear-time matcher, exact product-token groups, up to five http or https redirects, a 10 second single-attempt fetch, and bounded pattern and URL lengths) in the default crawl, `-j`, `-d`, `-1`, `--swagger` and `--audit`, and `Crawl-delay` sets a minimum gap between requests to the site. A 4xx robots.txt allows everything; a 5xx or unreachable one disallows everything. Instagram mode does not check robots.txt.
- `--ignore-robots` turns robots.txt rules and `Crawl-delay` off and prints a warning.
- `--user-agent` sets the User-Agent for page, browser and asset requests and is matched against robots.txt groups.
- `--output-dir` (`-o`) chooses the folder under which `ScrapedSites/` and `MarkdownSites/` are created.
- `run_summary.json` is written after every crawl with counts and lists of new, changed, unchanged, failed and robots-blocked pages. It is replaced with a `running` placeholder when a crawl starts, and an interrupted crawl writes it with `status: interrupted`. The schema is in the README.
- Documented exit codes: 0 success, 1 nothing fetched or every fetch failed, 2 bad arguments, 130 interrupted (Ctrl+C), 143 terminated (SIGTERM). SIGTERM is handled like Ctrl+C: the cache is saved and the summary written.
- `scraped_at` and `content_hash` in every Markdown file's frontmatter.
- `--no-index-pages` skips the generated `_index.md` section listings.
- `--version`, `scrapepy.__version__` and `python -m scrapepy`.
- Package metadata (readme, project URLs, keywords, classifiers), a `dev` extra with pytest, and a GitHub Actions workflow running the tests on Python 3.11, 3.12 and 3.13.

### Changed

- Default concurrency is 4 instead of 10.
- Page requests, prefetches included, now wait on a per-host rate limiter instead of sleeping after each page, so a prefetch burst keeps the `--wait` gap.
- Reconnaissance requests (sitemaps, feeds, homepage) are spaced out instead of sent all at once.
- Frontmatter no longer writes `null` for a missing `date`, `date_source` or `page_type`; missing fields are left out, as the README already said.
- Generated `_index.md` pages write `page_type: section` instead of `SECTION`.
- `--wait` and `-c` reject negative or zero values, and a bad `--since` or `--recent` is reported as an argument error instead of a traceback.
- The Markdown output folder is derived once on the config instead of by replacing `ScrapedSites` in paths.
- The build requires setuptools 77 or later, which the `license` and `license-files` fields need.

## 0.1.0

- First public release.
