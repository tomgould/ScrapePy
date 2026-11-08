# Markdown output contract

This page describes what a ScrapePy crawl writes, for tools that read it: search indexers, RAG pipelines, archivers. It covers the default crawl and `-j`, with or without `--markdown-only` and `--documents` (see Documents below), single page mode (`-1`, see Single page mode below) and feed mode (see Feeds below). Fields and files not listed here may appear in later versions; anything listed here keeps its meaning until the schema version changes.

## Folders

A crawl of `https://example.com` with `-o OUT` (default: the current directory) writes:

```
OUT/
  MarkdownSites/example_com/     the Markdown, one file per page
  ScrapedSites/example_com/      run state; also the HTML mirror unless --markdown-only
    run_summary.json             what this run did (below)
    .scrapepy_cache.json         incremental cache (internal format), unless --no-incremental
    site_outline.json            navigation and sitemap summary, when any was found
    broken_links.txt             only when some URLs failed
  files/example_com/             with --documents only: the downloaded documents
```

The site folder name is the host with `www.` removed and dots replaced by underscores. With `--stay-in-path` the starting path is appended (`docs_example_com_api_v2`). `--site-name NAME` replaces the whole name, so several runs on one site share one folder; with it, file paths keep the starting path even with `--stay-in-path`, so they are the same whichever run writes them.

With `--markdown-only` the Markdown folder contains only `.md` files and the `ScrapedSites` folder contains only state files: `run_summary.json` always, `.scrapepy_cache.json` unless `--no-incremental` is given, `site_outline.json` only when the home page has navigation links or a sitemap lists URLs, and `broken_links.txt` only when some URLs failed. Nothing else is downloaded: images stay as links to the live site.

## File names

A page's path on the site becomes its path under `MarkdownSites/<site>/`, with `.md` in place of `.html`:

| URL path | File |
|----------|------|
| `/about` | `about.md` |
| `/docs/setup.html` | `docs/setup.md` |
| `/news/12345/1/my-story` | `news/my-story-12345-1.md` (numeric segments move into the name) |
| a section page such as `/` or `/news` or `/news/index.html` | `_index.md` or `news/_index.md` |
| a section page `/news/123/slug` | `news/123/slug/_index.md` (section pages keep every segment as a folder) |

Pages are classified as sections (listing pages: home, categories, tags) or articles. A section page becomes `_index.md` in a folder named after it. With `--recent` or `--since`, articles found on a section page are written under that section's folder.

One page is one file, and a page never replaces another URL's file. When different URLs map to the same file name (the articles `/news/123/long-article-title-slug` and `/news/long-article-title-slug-123` both map to `news/long-article-title-slug-123.md`, or for the HTML copy `/about` and `/about.html`), the first one met gets the plain name and each other one gets the name with `-` and 8 hex characters of a hash of its URL inserted before the extension (`about-1f3a9c0e.md`); its HTML copy in the default mode carries the same suffix, and the other way round: when only the HTML copy's name is taken, the Markdown file is suffixed too even though its plain name is free. A file or folder name longer than 255 bytes is shortened (its stem cut on a character boundary, with a short hash of the full name added) so that it can be written; the shortened name is the same in every run. `run_summary.json` lists those pages under `renamed`. Which URL gets the plain name in a fresh crawl depends on crawl order, but the assignment of files to URLs is stored in the incremental cache (or, with no usable cache, read back from the `url` in the Markdown files) and reused, so after that every URL keeps its file whatever the order. A cache that holds no file assignments makes the next run convert every page again once. Every crawl also reserves the names of Markdown files already in the site folder for the URL in their frontmatter, such as entries written by feed mode, so a crawl and feed mode sharing a folder never take each other's files; a file whose `url` is missing or not an http(s) address is treated as unowned. URLs are compared exactly: a feed entry `/about/` and a crawled page `/about` are different URLs, so whichever was written first keeps `about.md` and the other gets the suffixed name and is listed under `renamed`. When a page's Markdown body is identical to a file written or found unchanged in the same run (for example `/` and `/index.html`), it gets no file and `run_summary.json` lists it under `duplicates`; this is decided again on every run. Use `url` from the frontmatter as the key, never the file name. Query strings never reach file names, because links are queued without them, and neither does a trailing slash: crawled paths are requested without one, so `/about/` and `/about` are the same page in a crawl. With a targeting option, a start URL with a query string (a site search) is fetched as given for its links and produces no file; without one its query is dropped like any other. In single page mode the query stays in `url` but still never reaches the file name (see below).

### Single page mode (`-1`)

`-1` saves one page through the same pipeline as a crawl, so the folders, file names, frontmatter, incremental cache and `run_summary.json` above apply to it unchanged. The browser requests the URL exactly as given, trailing slash, query and fragment included, so `/docs/` is requested as `/docs/` and keeps that `url`, while its file is named from the path `/docs`. The page's `url` is the address the browser ended on: after a redirect to another page on the same host, that address is `url` and names the file; a redirect to another host keeps the requested address. A query string stays in `url` and the file is named from the path alone, so `/search?q=apples` is `search.md` and `/search?q=pears`, run later into the same folder, is `search-<hash>.md` and is listed under `renamed`. Each address has its own cache entry (`/docs/` and `/docs` are two addresses, with two entries and two files), so running the same page again updates its file or lists it under `unchanged_urls`. `--site-name` works with `-1`; the other targeting options and `--markdown-only` do not.

A site folder holding a `MarkdownSites/<site>/index/_index.md` whose `url` is the site's root but whose text is another page, with a `ScrapedSites/<site>/index.html` beside it, keeps those files: no other page is written into them, but the Markdown file claims the root address, so delete both by hand. When robots.txt blocks the URL, `-1` exits 1 and writes no `run_summary.json`.

Only pages are crawled. A URL ending in `.json`, `.xml`, `.rss` or `.atom` is never queued, nor is one that a page declares with `<link rel="alternate">` and a type that is not HTML (its JSON, Markdown or feed version); alternates with no type or an HTML type, such as language versions, are followed. Links to `.md` addresses in the body are followed like any other link, but a response is converted only when the server says it is HTML (`text/html` or `application/xhtml+xml`, or no type at all), so a raw Markdown, JSON or XML file is fetched and skipped rather than written as a garbled copy of the page; `run_summary.json` lists it under `skipped_non_html`. In the default mode such responses (a `.txt` file or a PDF served at a page address) are not saved in the HTML copy either.

Some pages produce no file, and `run_summary.json` lists each one under `no_markdown` with the reason: error pages, RSS and Atom feeds met during a crawl (a feed given as the start URL is read in feed mode, below), pages with fewer than 50 words of content (recipe pages excepted), and paths containing `/feed`, `/author/`, `/category/`, `/tag/`, `/wp-includes/` or `/wp-json/` relative to the crawl's start. They are still fetched and counted in the summary.

## Frontmatter

Every file starts with YAML frontmatter between `---` lines, then a blank line, then the body. It is written with `yaml.safe_dump`, so `yaml.safe_load` returns exactly the strings and integers below, whatever the page contained. Control characters and lone surrogates are removed from text values. Keys appear in this order, and a key is left out when it has no value; no key is ever `null`.

| Key | Type | Present | Meaning |
|-----|------|---------|---------|
| `title` | string | almost always | The text of the body's H1 (see Body): the page's own H1 when it is the title, else `<title>` (without the site's name when that name is a separated part at either end and an H1 holding it was dropped as the site's name), else the first heading; whitespace collapsed |
| `url` | string | always | The absolute address requested for the page (after a redirect it is still the address requested, not the target). Use it as the document ID and the citation link; see Known limits for when two addresses share one file |
| `canonical_url` | string | when the page declares a different canonical on the same site | The page's `<link rel="canonical">`, made absolute. Written only when it differs from `url` and has the same scheme, host (ignoring `www.`) and port |
| `date` | string | when a publication date was found | ISO 8601, for example `2026-02-20` or `2026-02-20T10:30:00+00:00` |
| `date_source` | string | with `date` | Where the date came from, most to least reliable: `META_TAG`, `JSON_LD`, `URL_PATTERN`, `TIME_ELEMENT`, `HTTP_HEADER` (the server's Last-Modified). Feed entries use `FEED`; documents use `DOCUMENT_METADATA` (the creation date stored in the file, else its modification date) or `HTTP_HEADER` |
| `page_type` | string | always | `article`, `section` or `unknown`; `recipe` for a page with schema.org Recipe data; `feed_entry` for a feed entry; `document` for a converted document |
| `author` | string | feed entries, when the entry or feed names one | The entry's author name |
| `recipe_category` | string | recipe pages, when stated | The first recipe's `recipeCategory`, several joined with `, ` |
| `recipe_cuisine` | string | recipe pages, when stated | The first recipe's `recipeCuisine`, several joined with `, ` |
| `servings` | string | recipe pages, when stated | The first recipe's `recipeYield` as text, always a string (`'4'`, `12 muffins`). When the yield is a list, its first value |
| `total_time` | string | recipe pages, when stated | The first recipe's `totalTime` in words (`1 hour 30 minutes`); the raw value is in the body |
| `keywords` | list of strings | recipe pages, when stated | The first recipe's `keywords`, split on commas, duplicates removed |
| `word_count` | integer | on pages and documents | Words in the body, ignoring Markdown syntax |
| `description` | string | when the page has one | Its meta description or `og:description` |
| `domain` | string | always | Host of the crawled site, with port if any |
| `articles` | integer | generated `_index.md` only | Number of articles listed |
| `sub_sections` | integer | generated `_index.md` only, when there are some | Number of sub-sections listed |
| `file_type` | string | documents only | `pdf`, `docx`, `xlsx` or `csv` |
| `pages` | integer | PDF and XLSX documents | The PDF's page count (all pages, even past the page cap), or the workbook's sheet count |
| `linked_from` | string | documents found on a page | The absolute URL of the first page in the run that linked the document; left out for a document given as a start URL |
| `scraped_at` | string | always | When the file was written, UTC, ISO 8601 to the second |
| `content_hash` | string | always | `sha256:` plus the hex sha256 of the body (everything after the blank line that follows the closing `---`) |

`content_hash` changes only when the body does, so an indexer can skip files whose hash it has already stored. `scraped_at` changes on every write.

`canonical_url` is a hint, not an identity. Pages often point their canonical at another page (every page of a paginated list at page 1, print or filtered views at the main view, near copies at one of them), so keying documents on it would let one page replace another in an index. Use it to group or rank such pages, or to prefer the canonical address when showing a link, but keep `url` as the key.

## Body

- The body has one H1 (`# Title`), and every other heading is H2 or deeper. The title is chosen like this:
  - The page's first H1 is the title when it is the first heading, or when the headings above it look like a kicker or site tagline: at most two, each of at most ten words, with at most 30 words of other text above the H1. Those headings are kept as plain paragraphs before the H1, so the body can start with their text.
  - Otherwise (for example real sections above a late "Newsletter" H1) that H1 is an ordinary heading, and the title is the page's `<title>`, or its first heading when it has no `<title>`.
  - An H1 in the site's chrome never counts: in a `<nav>`, `<footer>` or `<aside>`, a `<header>` that is not inside `<article>` or `<main>`, a `banner`, `navigation`, `contentinfo` or `complementary` role, or an element whose class or id is, as a whole token, `site-header`, `masthead`, `navbar`, `logo`, `branding`, `top-bar` or similar (so `has-navbar` or `docs-top-bar` do not count). An H1 inside `<main>` or `<article>` always counts. Nor does an H1 that links to the home page, or that equals one part of `<title>` (split at `|`, `-`, en or em dash, `:`, `·` or `»`) when that part is the site's name by `og:site_name`, `application-name`, a schema.org WebSite or publisher name, or the host name (`acme` for `www.acme.co.uk`); that part is then dropped from the title. Without such a signal the H1 is kept. An H1 in an article's own `<header>` counts.
  - With no brand signal at all (no `og:site_name`, `application-name` or schema.org name, no link to the home page, and a host name that differs), an unlinked brand H1 such as "Acme Corp" on a page titled "Contact us | Acme Corp" is used as the title (see Known limits).
- Heading levels never skip and keep the page's outline. Headings that were siblings on the page stay siblings (three H4 cards under the title are all H2), a deeper heading becomes a child one level down, and a shallower one closes the deeper headings before it. Levels stop at H6. This applies to headings that start a list item (`- ## Card title`), headings in blockquotes (`> ## Quoted`) and any mix of the two (`- > ## Quoted`), but not to `#` lines inside code blocks, which are left alone.
- Article pages with a date have a line `**20 Feb 2026, 10:30**` after the title. Section pages do not.
- Navigation, headers, footers, sidebars, cookie and newsletter boxes, share buttons and form furniture (inputs, "Submit", "Menu", "Close" and similar buttons) are removed. Accordion and FAQ questions on buttons are kept (as headings when the button sits in a heading), a `<select>` becomes a list of its options unless it is a long picker, a sort, filter or language switcher, or a quantity picker, and `<textarea>` text is kept. See Known limits below. Removal is heuristic; some boilerplate can remain on unusual layouts.
- Tables are GitHub-style Markdown tables. Code blocks are fenced with three backticks (one more than the longest run of backticks inside, when the code itself contains a fence) and carry the language when the HTML names one (`language-python`, `lang-js`, `data-lang`).
- Lists are `-` bullets or numbered lists. Images are `![alt](url)`.
- With `--markdown-only`, every link and image is an absolute URL on the live site. In the default mode, links to other crawled pages are rewritten to relative `.md` paths on a best-effort basis (links with a scheme, such as `mailto:`, `javascript:` or `data:`, are left as written) and images point at copies in the Markdown folder's `images/`.

## Recipe pages

A page that carries schema.org Recipe data, as JSON-LD (`<script type="application/ld+json">`, including `@graph` arrays and several blocks) or as microdata (`itemtype="https://schema.org/Recipe"`, used only when there is no JSON-LD recipe), gets `page_type: recipe` and this body:

```
# Page title

**14 Mar 2026**                 (the date line, as on other pages)

## Recipe details

The recipe's description.

- Name: Rainbow Oat Bites
- Servings: 16 bites            (every recipeYield value, joined with "; ")
- Prep time: 20 minutes (PT20M)
- Cook time: 0 minutes (PT0M)
- Total time: 1 hour 20 minutes (PT1H20M)
- Author: Pat Example
- Category: Snack
- Cuisine: British
- Diet: Vegetarian, Gluten free
- Keywords: oats, no bake, lunchbox
- Published: 2026-03-14

## Ingredients

- 150 g rolled oats
- 3 tbsp smooth peanut butter

## Method

1. Tip the oats into a large bowl.
2. Stir in the peanut butter.

### For the topping              (one per HowToSection, numbered from 1)

1. Drizzle with honey.

## Nutrition

Serving size: 1 bite

- Calories: 95 kcal
- Fat: 4.1 g
- Saturated fat: 0.8 g
- Unsaturated fat: not stated
- Trans fat: not stated
- Carbohydrate: 12 g
- Sugar: 5.2 g
- Fibre: 1.3 g
- Protein: 2.6 g
- Sodium: 25 mg
- Cholesterol: not stated

## Page text

The rest of the page (introduction, tips, notes), with its headings one level down.
```

- The four headings are always present, in this order. A recipe with no ingredients or no method says `Not stated.` under that heading, and one with no nutrition data says `Not stated.` under `## Nutrition`. When some nutrition is given, all eleven lines are written and the missing ones say `not stated`. `Serving size` says `not stated` when `servingSize` is absent.
- Lines under `## Recipe details` appear only when the field is present. Times show the words and then the raw ISO 8601 value; a time that is not ISO 8601 (`30 mins`) is shown as written. Diets are named from their schema.org value (`https://schema.org/GlutenFreeDiet` is `Gluten free`).
- Each ingredient is one bullet, with the text as published: HTML entities decoded, tags removed and runs of whitespace collapsed, nothing else changed. A list given as one string is split on line breaks; nested lists are flattened, and an object item is written as its `text` or `name`, else as `key: value; key: value`.
- Method steps come from HowToStep (its `text`, else its `name`), HowToSection (a `###` heading with its steps, nested sections flattened into it; an unnamed one is called `Part N`), HowToTip (a step starting `Tip: `), plain strings, a string holding an HTML list, or a single string, which is split on line breaks, and within one line only at explicit numbering that counts up from 1 (`1. ... 2. ...`, `Step 1 ... Step 2`). A line is never split at sentence ends, because abbreviations (`approx.`, `tsp.`) and decimals look like them. Numbering the page wrote into a step is removed only when it is clearly numbering: `Step N`, or `N. ` or `N) ` where N is the step's position or every step in the list is numbered in order. A quantity such as `2 - 3 drops`, `3 cups`, `1.5 cups` or `2) 1/2 cup` is never removed.
- Nutrition values keep the number and unit exactly as published (`95 kcal`, `12.50`, `20.0g`, `1e21`). JSON numbers keep their original text, so `12.50` is not shortened to `12.5`. Nothing is converted, added up or estimated. A bare number stays a bare number, because the unit was not given. A value given as a list shows every item, joined with ` / `.
- Several recipes on one page: one `## <recipe name>` section each, in page order, with the headings above one level deeper (`### Recipe details`, `### Ingredients`, ...). `## Page text` follows the last recipe. Frontmatter fields come from the first recipe.
- No duplication, without losing facts: before the page text is converted, a list item or paragraph is removed only when its text equals an ingredient or a step. The comparison is exact, case included, except that runs of whitespace (including non-breaking, thin and zero-width spaces) count as one space, one leading bullet (a bullet sign or a hyphen followed by a space, but not an asterisk or star) is ignored, and so is a full stop or colon ending the item. For steps only, numbering is ignored too: `Step N`, or `N. ` or `N) ` when N is that step's position. So `~200 g sugar`, `1 t sugar`, `*2 eggs`, `* 2 eggs` or `2) eggs` on the page stay when the data says `200 g sugar`, `1 T sugar`, `2 eggs` or `eggs`, and different ways of writing the same amount (`½` and `1/2`, `200g` and `200 g`, an en dash and a hyphen, `2,5` and `2.5`) are left as duplicate lines. Each ingredient or step removes at most one element, the first that matches, and nothing inside a comment section (an element whose id or class mentions comment, respond or reply) is removed. Text that adds anything, such as `(optional)`, `Never ...` or `(160C fan)`, stays, so a fact can appear twice but is never lost. Microdata recipe properties and the JSON-LD are removed too, and so are label headings (`Ingredients`, `Method`, `Instructions`, `Nutrition` and similar) left with nothing under them. Introductions, tips and notes stay. When nothing is left, `## Page text` is not written. Times, servings and nutrition text from a recipe card can still appear in the page text.
- An ItemList of recipes (a listing page) is not a recipe page and is converted as usual. A JSON-LD block that cannot be read, even after allowing comments, trailing commas and raw line breaks in strings, is skipped and the page is converted as usual. Recipe pages are written even when they have fewer than 50 words. RDFa is not read. A JSON-LD Recipe that describes something other than the page (a related recipe, say) cannot be told apart and is shown as the page's recipe.

## Documents

With `--documents` (a comma list of `pdf`, `docx`, `xlsx`, `csv`), links on the crawl's own site (same scheme, host and port) to those types are downloaded and converted. They pass the same scope, `--include`/`--exclude` (on the path), `--max-depth`, `--max-pages` and (with `--obey-robots`) robots.txt rules as pages, and a document URL in `--urls-file` is fetched as a document. `.doc` and `.xls` links are recorded as skipped, unfetched, when `docx` or `xlsx` is listed. Without `--documents` none of this happens and nothing below appears.

- Files: the original is saved byte for byte at its URL path under `files/<site>/` (outside `MarkdownSites`, so it is never indexed twice), and the Markdown is written under `MarkdownSites/<site>/` as the URL path plus `.md`: `/reports/2025/annual.pdf` gives `files/<site>/reports/2025/annual.pdf` and `MarkdownSites/<site>/reports/2025/annual.pdf.md`. Percent-escapes stay as written, characters file systems refuse (`<>:"|?*\` and control characters) become `_`, and the file name rule above applies: a document never takes another URL's file (page or document) and gets the hash-suffixed name instead, listed under `renamed`. The site folder name is the same as for `ScrapedSites` and `MarkdownSites`. A write through a symbolic link at or below `files/` or `MarkdownSites/` (so a link at `files/<site>` too), or outside these folders, is refused and recorded as failed. The original of a document that fails to convert stays in `files/<site>/`.
- Frontmatter: `title` (the title stored in the document, else for DOCX its first Title or heading paragraph, else the decoded file name such as `annual.pdf`), `url`, `canonical_url` (from a `Link: <...>; rel="canonical"` response header, under the same rule as pages), `date` and `date_source` (see Frontmatter), `page_type: document`, `word_count`, `domain`, `file_type`, `pages` (PDF and XLSX), `linked_from`, `scraped_at` and `content_hash`.
- Body: `# Title`, then:
  - PDF: the text of each page under `## Page N` (pages with no text are left out), with ligatures spelled out, a word hyphenated across a line end joined with its hyphen kept (`well-known`, and also `main-tenance`, since nothing tells them apart), and line breaks kept. No OCR: a PDF with no text layer gets no Markdown file (its original is saved) and is listed as skipped with `no text layer`.
  - DOCX: headings as `##` and deeper (Heading 1 is `##`), paragraphs, bulleted and numbered lists, and tables.
  - XLSX: one `## <sheet name>` and table per sheet that has data, hidden sheets included (and counted in `pages`), the first non-empty row as the header. Formulas show the value last saved in the file; nothing is calculated and macros are ignored.
  - CSV: one table, the first row as the header. The delimiter (comma, semicolon, tab or `|`) is detected; text is read as UTF-8, UTF-16 with a byte order mark, or else Windows-1252.
  - Tables are GitHub-style, with `|` in a cell escaped and line breaks in a cell turned into spaces. Empty rows are dropped.
  - Caps, each ending the text with an italic note when reached: 2,000 data rows and 50 columns per table, 100,000 rows read per table (empty ones included) (`_Rows after the first 2,000 are not shown._`), 2,000 PDF pages, and 2,000,000 characters per document. Control characters are removed, and a line of document text that starts with `#` or `>` is escaped so it never becomes a heading or a quote.
- Failures: an encrypted file (unless it opens with an empty password), a corrupt file, a file whose content does not match its type (an HTML login page served at a `.pdf` address, say), a file larger than `--max-document-mb` (25 MB by default), a DOCX or XLSX file that would unpack to more than 128 MB or holds an XML part over 16 MB (XLSX sheets, which are read row by row, excepted), a PDF stream that unpacks past pypdf's limit (pdfminer.six, which has none, is then not tried), a conversion that runs longer than 120 seconds or needs more than 1 GB (each runs in its own process, killed at the limit; the reason says which limit, a crash, or an exit without a result), a download that takes longer than 300 seconds in all, an HTTP error, or a redirect to another site or, with `--obey-robots`, to a path robots.txt disallows. Each is listed with its reason; none stops the crawl.
- Incremental runs: the cache keeps each document's ETag, Last-Modified and sha256. A document answered 304, or downloaded with the same sha256, is listed in `unchanged_urls` and its files are left alone. A changed document is converted again and listed in `changed_markdown`.

## Feeds

When the start URL is an RSS 2.0 or Atom feed, ScrapePy reads it as content instead of crawling. A URL is treated as a feed when its path ends in `/feed`, `/rss`, `/atom`, `/feeds`, `rss.xml`, `atom.xml`, `feed.xml`, `index.xml`, `.rss` or `.atom`, or its query says `feed=rss2`, `format=atom` and similar; `--feed` forces it for any URL. A detected URL whose response is not a feed is crawled as a site; with `--feed` it is recorded as a failure instead.

- The feed is fetched once. Entry links are not followed and nothing else is downloaded. robots.txt is not requested unless `--obey-robots` is given: then it is checked for the feed URL first, and the feed is fetched after the usual pacing gap.
- Each entry, in feed order, up to `--max-articles` entries (50 when not given), becomes one Markdown file named from its link like a crawled page. A query string in the link is kept in the name (`/?p=101` is `index-p-101.md`), an entry on another host goes under a folder named after that host (`feeds_example/...`), and when two entries would get the same name, both get a suffix from a short hash of their link (`post-1a2b3c4d.md`), so names do not change when the feed's order does. As in a crawl, no entry replaces another URL's file: an entry keeps the file it already has from an earlier run (read back from the files' `url`), and a name another URL already holds gets the same link-hash suffix. Every entry whose file name carries a suffix is listed under `renamed`.
- Frontmatter: `title`, `url` (the entry link, made absolute against `xml:base` or the feed URL), `date` (RSS `pubDate` or `dc:date`, Atom `published` else `updated`, as ISO 8601 no more precise than published: a date stays a date, whether written `2026-10-03`, `20261003` or `Sat, 03 Oct 2026`, a time keeps its offset, and a time without one stays without one) with `date_source: FEED`, `page_type: feed_entry`, `author` when present, `word_count`, `domain` (the feed's host), `scraped_at` and `content_hash`. A date that cannot be read is left out.
- A DOCTYPE in the feed, with any entity declarations, is removed before parsing: entities are never expanded, no DTD or other URL is fetched and no local file is read. Entities the feed then uses are shown as written (`&a;`). A feed that has entries none of which can be read is recorded as failed, with `feed.error` set, and the run exits 1.
- Body: `# Title`, the date line, then the entry's HTML converted to Markdown: RSS `content:encoded`, else `description`; Atom `content` (`html`, `xhtml` or `text`), else `summary`. Links and images are absolute.
- An entry with no link (no `link`, no permalink `guid`, no `http` Atom `id`) and a second entry with the same link are not written; both are listed in `run_summary.json`. An entry whose file already holds the same body is left alone and counted as unchanged.

## Known limits

- Button text is kept only for questions and for toggles that open a panel (accordion and FAQ buttons with `aria-expanded` or `aria-controls`). A custom FAQ button with neither and no question mark ("Returns policy"), or a plan button ("Starter plan"), is dropped with its label. Toggles whose whole label is page furniture ("Share on Facebook", "Load more", "Next page", "Slide 1 of 3", "Cookie settings", "Search", Menu and Close in a few languages) are dropped too, as are bare numbers in a pager or navigation container and toggles whose panel is mostly links with no sentence of its own. Labels that only contain such words ("Next steps after your application", "Our cookie recipes", "2024") are kept. A select of only numbers is dropped only when its name, id or class says it is a quantity.

- Different addresses can map to the same file name; the later ones get a hash-suffixed name (see File names), so file names are not a stable key across a deleted output folder. Key documents on `url`.
- In a crawl, a page that redirects is written under the address requested, so a redirecting address and its target can both appear, and the later one is listed as a duplicate when their bodies match. `-1` records the address it ended on instead (same host only).
- An unlinked brand H1 with no brand signal (see Body) becomes the title. Key documents on `url` and use the headings as the page's section tree rather than rely on `title` alone.
- A page too short to convert (under 50 words, recipe pages excepted) is counted as new or changed in `run_summary.json` but has no file and is not in `new_markdown` or `changed_markdown`.
- After an interrupted run, pages that were not reached yet appear in no list.
- With `--stay-in-path`, no `--site-name` and no `--recent` or `--since`, article files have the starting path removed from their path while section `_index.md` files keep it: crawling `/docs/` with `-s`, the article `/docs/how-to-bake-sourdough-bread` is `how-to-bake-sourdough-bread.md`, while `/docs/how-to-bake-bread` and `/docs/kitchen`, which are classified as sections, are `docs/how-to-bake-bread/_index.md` and `docs/kitchen/_index.md`. With `--recent` or `--since`, articles found on a section are written under that section's folder and keep the starting path (`docs/how-to-bake-sourdough-bread.md`).
- Crawled paths are requested without a trailing slash, so a server that answers only `/docs/` must redirect `/docs` to it.
- In recipe pages, the same amount written two ways (`200g` and `200 g`, `½` and `1/2`) leaves a duplicate line in `## Page text` (see Recipe pages).
- `-j` returns the page once the browser has loaded it and Scrapling's Cloudflare check has finished (a few seconds), so JavaScript-rendered content that appears later can be missed. The page is not scrolled, links are found in the HTML by regular expression, and `--wait` does not lengthen the wait: it is only the gap between page requests.
- With `--documents`, documents on another host than the crawl's (a CDN, a file-sharing host) are not fetched. A document is written under the address requested, like a page that redirects.
- `-j`'s argument parsing and settings are covered by the automated tests. Its crawl is covered only by an opt-in test (`SCRAPEPY_BROWSER_TESTS=1`, which starts a browser) that checks it goes through `--proxy`; `-j` with `--stay-in-path` is not tested.

## Generated `_index.md` listing pages

When `--recent` or `--since` groups articles under sections, each section also gets an `_index.md` that lists its articles (title, link to the article file, date, description, thumbnail) and sub-sections, with `page_type: section` and the `articles` and `sub_sections` counts. These pages hold no content of their own, so for search indexing either skip files named `_index.md` whose frontmatter has `articles`, or crawl with `--no-index-pages` to stop them being written. A section page's own content, when it has enough, is written to the same `_index.md` path when listings are off.

## run_summary.json

Written to `ScrapedSites/<site>/run_summary.json` at the start of a crawl or a `-1` run (with `status: running`) and replaced, atomically, when it ends. Paths in it are relative to the output folder (`-o`).

| Key | Type | Meaning |
|-----|------|---------|
| `schema_version` | integer | `1`. Goes up only when a key is renamed or removed |
| `status` | string | `running`, `complete` or `interrupted` |
| `start_url` | string | The URL given on the command line |
| `started_at`, `ended_at` | string, string or null | UTC ISO 8601; `ended_at` is null while running |
| `output_dir` | string | Absolute path of the output folder |
| `html_dir`, `markdown_dir` | string | Site folders, relative to `output_dir`; `markdown_dir` is null with `--no-markdown` |
| `options` | object | The settings used. Credentials are never included. `obey_robots` is true only when `--obey-robots` was given; `ignore_robots` is its opposite: true when robots.txt rules and `Crawl-delay` were not applied, which is the default |
| `counts` | object of integers | `fetched`, `new`, `changed`, `unchanged`, `failed`, `blocked_by_robots`, `duplicates`, `renamed`, `skipped_non_html`, `skipped_old`, `no_markdown`, `needs_render`, `encoding_fallbacks` (exact even when a list is truncated) |
| `new_markdown`, `changed_markdown` | list of strings | Markdown files written for new and changed pages |
| `unchanged_urls` | list of strings | Pages the server or the cache showed had not changed |
| `failed` | list of `{url, reason}` | Fetch errors, HTTP 4xx and 5xx, error pages, and pages whose conversion to Markdown failed (these are not counted as new or changed and are converted again on the next run) |
| `blocked_by_robots` | list of strings | URLs robots.txt disallowed. Empty unless `--obey-robots` was given |
| `duplicates` | list of `{url, same_as}` | Pages whose Markdown matched a file already written, and that file |
| `skipped_non_html` | list of `{url, content_type}` | Responses fetched but not converted because the server said they are not HTML (a raw Markdown, JSON or plain-text file), with the media type it gave, and binary data served as a page (a body that needed a fallback encoding and holds NUL bytes) |
| `no_markdown` | list of `{url, reason}` | Every page fetched that gave no Markdown file, with the reason: `converter failure`, `duplicate`, `empty` (no main content found), `error page`, `feed or XML`, `non-HTML`, `not a recipe` (`--only-recipes`), `skipped path` (`/category/`, `/tag/`, `/author/` and feed paths) and `too short` (under 50 words). A page can also appear in `failed`, `duplicates` or `skipped_non_html`. A page that failed to fetch or answered 400 or above is only in `failed`. A page found unchanged is listed again with the reason stored when it was converted. Empty with `--no-markdown` |
| `no_markdown_reasons` | object of integers | `no_markdown` counted by reason |
| `needs_render` | list of `{url, bytes}` | Pages fetched over HTTP that came back nearly empty and look built by JavaScript (scripts and under 200 characters of visible text, or a `<noscript>` asking for JavaScript and under 500), with the size of the HTML in bytes. Fetching them again with `-j` may give their content. Never listed in the browser modes |
| `encoding_fallbacks` | list of `{url, declared, used}` | Pages whose body did not decode with the encoding the response declared (the Content-Type charset, else UTF-8), with the encoding that worked. A UTF-16 or UTF-32 byte order mark comes first (`utf-16`, `utf-32`); then the `<meta>` charset (read in the first 4,096 bytes) and UTF-8; then a body that is mostly valid UTF-8 is read as UTF-8 with its few bad bytes replaced and any other as Windows-1252 (`cp1252`), and last UTF-8 with undecodable bytes replaced (`utf-8 (undecodable bytes replaced)`). A Content-Type charset Python does not know makes the page be requested a second time with a plain HTTP client, so its raw body can be decoded |
| `truncated` | list of strings | The names of the `no_markdown`, `needs_render` and `encoding_fallbacks` lists that hold more than 200 entries. Each list keeps its first 200 entries; its count in `counts` stays exact |
| `renamed` | list of `{url, file, natural_file}` | Pages written to a hash-suffixed name because another URL has their natural file name, with the file used and the name they would have had (both relative to `output_dir`). Listed in every run where the page is written or unchanged |
| `interrupted`, `interrupted_by` | boolean, string or null | Whether the run stopped early, and the signal (`SIGINT` or `SIGTERM`) |
| `exit_code` | integer or null | The process exit code; null while running |
| `feed` | object | Feed mode only (below) |
| `documents` | object | With `--documents` only (below) |
| `targeting` | object | Only when `--include`, `--exclude`, `--max-depth`, `--max-pages`, `--urls-file`, `--only-recipes`, `--site-name` or `--follow-next` is given (below) |

`options` lists those options only when they are set; `allow_sitemap_hosts` (a list of hosts), `refuse_private_redirects` (`true`) and `solve_cloudflare` (`false`) likewise appear only when `--allow-sitemap-host`, `--refuse-private-redirects` or `--no-cloudflare-solve` is given. A redirect refused by `--refuse-private-redirects` is listed in `failed` with a reason starting `RedirectRefused: `. With `--documents` it also holds `documents` (the list of types) and `max_document_mb`. Document requests are counted in `counts.fetched`, documents converted in `new_markdown` or `changed_markdown` (and `counts.new` or `counts.changed`), unchanged documents in `unchanged_urls`, and failed documents in `failed`, as for pages. `documents` holds:

| Key | Type | Meaning |
|-----|------|---------|
| `types` | list of strings | The types given to `--documents` |
| `files_dir` | string | Where the originals are saved, relative to `output_dir` (`files/<site>`) |
| `found` | integer | Distinct document URLs the crawl reached after its scope and targeting rules, including those robots.txt blocked (with `--obey-robots`) |
| `fetched` | integer | Document requests made, including those that ended in an error or a refused redirect (a redirect followed counts once) |
| `converted`, `unchanged` | integer | Documents whose Markdown was written, and documents found unchanged |
| `skipped` | list of `{url, reason}` | Documents not converted that are not failures: `old binary format (.doc), not converted` (or `.xls`), `blocked by robots.txt` (with `--obey-robots`), `no text layer` (a PDF), `no text` (a DOCX) or `no data` (an XLSX or CSV with no cells) |
| `failed` | list of `{url, reason}` | Documents that could not be fetched, saved or converted (see Documents), also listed in the top-level `failed` |

`targeting` holds:

| Key | Type | Meaning |
|-----|------|---------|
| `skipped_by_include`, `skipped_by_exclude`, `skipped_by_depth` | integer | Distinct discovered paths never fetched in the run because they matched no `--include` (and were outside the `-s` scope), matched an `--exclude`, or were deeper than `--max-depth`. A path skipped once and fetched later by a shorter route is not counted |
| `skipped_not_recipe` | integer | With `--only-recipes`, pages fetched but not written because they have no schema.org Recipe data |
| `not_recipe_sample` | list of strings | Up to 20 of those page URLs |
| `stopped_by_limit` | boolean | Whether `--max-pages` ended the run with pages still queued |


In feed mode `counts.fetched` is 1 (the feed), `new_markdown` and `changed_markdown` list the entry files, `unchanged_urls` the entries whose file did not change, `renamed` the entries with a suffixed file name, and `feed` holds:

| Key | Type | Meaning |
|-----|------|---------|
| `feed_url` | string | The feed fetched |
| `format`, `title` | string or null | `rss` or `atom`, and the feed's title; null when no feed was read |
| `entries_in_feed` | integer | Entries in the feed |
| `entries_written`, `entries_unchanged` | integer | Entry files written, and entries whose file already held the same body |
| `entries_over_cap` | integer | Entries after the first `--max-articles` (default 50), not written |
| `entries_skipped` | list of `{title, url?, reason}` | Entries not written: `no link` or `duplicate link` |
| `error` | string | Only when the feed has entries but none could be read, or none could be written (`no entry could be written (no link)`); the run then exits 1 |
| `teaser_entries`, `teaser_urls` | integer, list of strings | Entries whose body looks like an excerpt only: no body, fewer than 100 words, or ending in an ellipsis or "Read more". Crawl these links yourself if you need the full text |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | At least one page was fetched without failing (unchanged pages count) |
| 1 | Nothing was fetched, or every fetch failed (including, with `--obey-robots`, robots.txt blocking the start URL) |
| 2 | Bad arguments (including `--obey-robots` with `--ignore-robots`) |
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
