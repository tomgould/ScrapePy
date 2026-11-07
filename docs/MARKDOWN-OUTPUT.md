# Markdown output contract

This page describes what a ScrapePy crawl writes, for tools that read it: search indexers, RAG pipelines, archivers. It covers the default crawl and `-j`, with or without `--markdown-only`. Fields and files not listed here may appear in later versions; anything listed here keeps its meaning until the schema version changes.

## Folders

A crawl of `https://example.com` with `-o OUT` (default: the current directory) writes:

```
OUT/
  MarkdownSites/example_com/     the Markdown, one file per page
  ScrapedSites/example_com/      run state; also the HTML mirror unless --markdown-only
    run_summary.json             what this run did (below)
    .scrapepy_cache.json         incremental cache (internal format)
    site_outline.json            navigation and sitemap summary
    broken_links.txt             only when some URLs failed
```

The site folder name is the host with `www.` removed and dots replaced by underscores. With `--stay-in-path` the starting path is appended (`docs_example_com_api_v2`).

With `--markdown-only` the Markdown folder contains only `.md` files and the `ScrapedSites` folder contains only the four state files above. Nothing else is downloaded: images stay as links to the live site.

## File names

A page's path on the site becomes its path under `MarkdownSites/<site>/`, with `.md` in place of `.html`:

| URL path | File |
|----------|------|
| `/about` | `about.md` |
| `/docs/setup.html` | `docs/setup.md` |
| `/news/12345/1/my-story` | `news/my-story-12345-1.md` (numeric segments move into the name) |
| a section page such as `/` or `/news` or `/news/index.html` | `_index.md` or `news/_index.md` |

Pages are classified as sections (listing pages: home, categories, tags) or articles. A section page becomes `_index.md` in a folder named after it. With `--recent` or `--since`, articles found on a section page are written under that section's folder.

One page is one file. When a page's Markdown body is identical to a file already written in the same run (for example `/` and `/index.html`), it is not written again and `run_summary.json` lists it under `duplicates`. Query strings never reach file names, because links are queued without them.

Some pages produce no file: error pages, RSS and Atom feeds, pages with fewer than 50 words of content, and paths containing `/feed`, `/author/`, `/category/`, `/tag/`, `/wp-includes/` or `/wp-json/` relative to the crawl's start. They are still fetched and counted in the summary.

## Frontmatter

Every file starts with YAML frontmatter between `---` lines, then a blank line, then the body. It is written with `yaml.safe_dump`, so `yaml.safe_load` returns exactly the strings and integers below, whatever the page contained. Control characters and lone surrogates are removed from text values. Keys appear in this order, and a key is left out when it has no value; no key is ever `null`.

| Key | Type | Present | Meaning |
|-----|------|---------|---------|
| `title` | string | almost always | The page title: the first heading, else the H1, else `<title>`, with whitespace collapsed |
| `url` | string | always | The absolute URL the page was fetched from. Unique per file, so use it as the document ID and the citation link |
| `canonical_url` | string | when the page declares a different canonical on the same site | The page's `<link rel="canonical">`, made absolute. Written only when it differs from `url` and has the same scheme, host (ignoring `www.`) and port |
| `date` | string | when a publication date was found | ISO 8601, for example `2026-02-20` or `2026-02-20T10:30:00+00:00` |
| `date_source` | string | with `date` | Where the date came from, most to least reliable: `META_TAG`, `JSON_LD`, `URL_PATTERN`, `TIME_ELEMENT`, `HTTP_HEADER` (the server's Last-Modified) |
| `page_type` | string | always | `article`, `section` or `unknown` |
| `word_count` | integer | on pages | Words in the body, ignoring Markdown syntax |
| `description` | string | when the page has one | Its meta description or `og:description` |
| `domain` | string | always | Host of the crawled site, with port if any |
| `articles` | integer | generated `_index.md` only | Number of articles listed |
| `sub_sections` | integer | generated `_index.md` only, when there are some | Number of sub-sections listed |
| `scraped_at` | string | always | When the file was written, UTC, ISO 8601 to the second |
| `content_hash` | string | always | `sha256:` plus the hex sha256 of the body (everything after the blank line that follows the closing `---`) |

`content_hash` changes only when the body does, so an indexer can skip files whose hash it has already stored. `scraped_at` changes on every write.

`canonical_url` is a hint, not an identity. Pages often point their canonical at another page (every page of a paginated list at page 1, print or filtered views at the main view, near copies at one of them), so keying documents on it would let one page replace another in an index. Use it to group or rank such pages, or to prefer the canonical address when showing a link, but keep `url` as the key.

## Body

- The first line is `# Title`. It is the only H1: the page's first heading becomes it whatever its level, and every later heading is H2 or deeper.
- Heading levels never skip and keep the page's outline. Headings that were siblings on the page stay siblings (three H4 cards under the title are all H2), a deeper heading becomes a child one level down, and a shallower one closes the deeper headings before it. Levels stop at H6. This applies to headings that start a list item (`- ## Card title`) and headings in blockquotes (`> ## Quoted`), but not to `#` lines inside code blocks, which are left alone.
- Article pages with a date have a line `**20 Feb 2026, 10:30**` after the title. Section pages do not.
- Navigation, headers, footers, sidebars, cookie and newsletter boxes, share buttons and form furniture (inputs, "Submit", "Menu", "Close" and similar buttons) are removed. Accordion and FAQ questions on buttons are kept (as headings when the button sits in a heading), a `<select>` becomes a list of its options unless it is a long picker, a sort, filter or language switcher, or only numbers, and `<textarea>` text is kept. See Limitations below. Removal is heuristic; some boilerplate can remain on unusual layouts.
- Tables are GitHub-style Markdown tables. Code blocks are fenced with three backticks and carry the language when the HTML names one (`language-python`, `lang-js`, `data-lang`).
- Lists are `-` bullets or numbered lists. Images are `![alt](url)`.
- With `--markdown-only`, every link and image is an absolute URL on the live site. In the default mode, links to other crawled pages are rewritten to relative `.md` paths on a best-effort basis and images point at copies in the Markdown folder's `images/`.

## Limitations

- Button text is kept only for questions and for toggles that open a panel (accordion and FAQ buttons with `aria-expanded` or `aria-controls`). A custom FAQ button with neither and no question mark ("Returns policy"), or a plan button ("Starter plan"), is dropped with its label. Toggles whose label looks like page furniture (share, load more, pagers, slide counters, cookie settings, search, Menu and Close in a few languages) or whose panel is mostly links are dropped too.

## Generated `_index.md` listing pages

When `--recent` or `--since` groups articles under sections, each section also gets an `_index.md` that lists its articles (title, link to the article file, date, description, thumbnail) and sub-sections, with `page_type: section` and the `articles` and `sub_sections` counts. These pages hold no content of their own, so for search indexing either skip files named `_index.md` whose frontmatter has `articles`, or crawl with `--no-index-pages` to stop them being written. A section page's own content, when it has enough, is written to the same `_index.md` path when listings are off.

## run_summary.json

Written to `ScrapedSites/<site>/run_summary.json` at the start of a crawl (with `status: running`) and replaced, atomically, when it ends. Paths in it are relative to the output folder (`-o`).

| Key | Type | Meaning |
|-----|------|---------|
| `schema_version` | integer | `1`. Goes up only when a key is renamed or removed |
| `status` | string | `running`, `complete` or `interrupted` |
| `start_url` | string | The URL given on the command line |
| `started_at`, `ended_at` | string, string or null | UTC ISO 8601; `ended_at` is null while running |
| `output_dir` | string | Absolute path of the output folder |
| `html_dir`, `markdown_dir` | string | Site folders, relative to `output_dir`; `markdown_dir` is null with `--no-markdown` |
| `options` | object | The settings used. Credentials are never included |
| `counts` | object of integers | `fetched`, `new`, `changed`, `unchanged`, `failed`, `blocked_by_robots`, `duplicates`, `skipped_old` |
| `new_markdown`, `changed_markdown` | list of strings | Markdown files written for new and changed pages |
| `unchanged_urls` | list of strings | Pages the server or the cache showed had not changed |
| `failed` | list of `{url, reason}` | Fetch errors, HTTP 4xx and 5xx, error pages, and pages whose conversion to Markdown failed (these are not counted as new or changed and are converted again on the next run) |
| `blocked_by_robots` | list of strings | URLs robots.txt disallowed |
| `duplicates` | list of `{url, same_as}` | Pages whose Markdown matched a file already written, and that file |
| `interrupted`, `interrupted_by` | boolean, string or null | Whether the run stopped early, and the signal (`SIGINT` or `SIGTERM`) |
| `exit_code` | integer or null | The process exit code; null while running |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | At least one page was fetched without failing (unchanged pages count) |
| 1 | Nothing was fetched, or every fetch failed (including robots.txt blocking the start URL) |
| 2 | Bad arguments |
| 130 | Interrupted with Ctrl+C (SIGINT) |
| 143 | Stopped with SIGTERM |

## Feeding a search engine

1. Crawl with `--markdown-only --no-index-pages -o OUT`, adding `--user-agent` to identify yourself.
2. Read `OUT/ScrapedSites/<site>/run_summary.json`. Go on only if `status` is `complete` and `exit_code` is 0.
3. On the first run, index every `*.md` under `OUT/<markdown_dir>`. On later runs, index the files in `new_markdown` and `changed_markdown`; files for `unchanged_urls` are untouched.
4. For each file, split off the frontmatter, load it with `yaml.safe_load`, and index the body. Use `url` as the document ID and the citation link (never `canonical_url`, which several pages can share), `title` as the title, and `content_hash` to skip a document you already hold.
5. Pages that disappear from the site are not deleted from `MarkdownSites`. Remove documents from your index yourself when their `url` stops appearing in crawls.

```python
import pathlib, yaml

def read(path):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    end = text.index("\n---\n", 3)        # the closing --- line
    front = yaml.safe_load(text[4:end]) or {}
    body = text[end + 5:].removeprefix("\n")
    return front, body
```
