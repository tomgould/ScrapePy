# Markdown output contract

This page describes what a ScrapePy crawl writes, for tools that read it: search indexers, RAG pipelines, archivers. It covers the default crawl and `-j`, with or without `--markdown-only`, and feed mode (see Feeds below). Fields and files not listed here may appear in later versions; anything listed here keeps its meaning until the schema version changes.

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
```

The site folder name is the host with `www.` removed and dots replaced by underscores. With `--stay-in-path` the starting path is appended (`docs_example_com_api_v2`).

With `--markdown-only` the Markdown folder contains only `.md` files and the `ScrapedSites` folder contains only state files: `run_summary.json` always, `.scrapepy_cache.json` unless `--no-incremental` is given, `site_outline.json` only when the home page has navigation links or a sitemap lists URLs, and `broken_links.txt` only when some URLs failed. Nothing else is downloaded: images stay as links to the live site.

## File names

A page's path on the site becomes its path under `MarkdownSites/<site>/`, with `.md` in place of `.html`:

| URL path | File |
|----------|------|
| `/about` | `about.md` |
| `/docs/setup.html` | `docs/setup.md` |
| `/news/12345/1/my-story` | `news/my-story-12345-1.md` (numeric segments move into the name) |
| a section page such as `/` or `/news` or `/news/index.html` | `_index.md` or `news/_index.md` |

Pages are classified as sections (listing pages: home, categories, tags) or articles. A section page becomes `_index.md` in a folder named after it. With `--recent` or `--since`, articles found on a section page are written under that section's folder.

One page is one file. When a page's Markdown body is identical to a file already written in the same run (for example `/` and `/index.html`), it is not written again and `run_summary.json` lists it under `duplicates`. When a different page maps to a file another URL already wrote (or that a page seen unchanged owns) in the same run, the first URL keeps the file, the later page is not written (in the default mode its HTML copy is not written either), and `run_summary.json` lists it under `collisions`. Crawl order decides which URL comes first, so a re-run of the same site keeps the same page. Query strings never reach file names, because links are queued without them.

Only pages are crawled. A URL ending in `.json`, `.xml`, `.rss` or `.atom` is never queued, nor is one that a page declares with `<link rel="alternate">` and a type that is not HTML (its JSON, Markdown or feed version); alternates with no type or an HTML type, such as language versions, are followed. Links to `.md` addresses in the body are followed like any other link, but a response is converted only when the server says it is HTML (`text/html` or `application/xhtml+xml`, or no type at all), so a raw Markdown, JSON or XML file is fetched and skipped rather than written as a garbled copy of the page.

Some pages produce no file: error pages, RSS and Atom feeds met during a crawl (a feed given as the start URL is read in feed mode, below), pages with fewer than 50 words of content (recipe pages excepted), and paths containing `/feed`, `/author/`, `/category/`, `/tag/`, `/wp-includes/` or `/wp-json/` relative to the crawl's start. They are still fetched and counted in the summary.

## Frontmatter

Every file starts with YAML frontmatter between `---` lines, then a blank line, then the body. It is written with `yaml.safe_dump`, so `yaml.safe_load` returns exactly the strings and integers below, whatever the page contained. Control characters and lone surrogates are removed from text values. Keys appear in this order, and a key is left out when it has no value; no key is ever `null`.

| Key | Type | Present | Meaning |
|-----|------|---------|---------|
| `title` | string | almost always | The page title: the first heading, else the H1, else `<title>`, with whitespace collapsed |
| `url` | string | always | The absolute address requested for the page (after a redirect it is still the address requested, not the target). Use it as the document ID and the citation link; see Limitations for when two addresses share one file |
| `canonical_url` | string | when the page declares a different canonical on the same site | The page's `<link rel="canonical">`, made absolute. Written only when it differs from `url` and has the same scheme, host (ignoring `www.`) and port |
| `date` | string | when a publication date was found | ISO 8601, for example `2026-02-20` or `2026-02-20T10:30:00+00:00` |
| `date_source` | string | with `date` | Where the date came from, most to least reliable: `META_TAG`, `JSON_LD`, `URL_PATTERN`, `TIME_ELEMENT`, `HTTP_HEADER` (the server's Last-Modified). Feed entries use `FEED` |
| `page_type` | string | always | `article`, `section` or `unknown`; `recipe` for a page with schema.org Recipe data; `feed_entry` for a feed entry |
| `author` | string | feed entries, when the entry or feed names one | The entry's author name |
| `recipe_category` | string | recipe pages, when stated | The first recipe's `recipeCategory`, several joined with `, ` |
| `recipe_cuisine` | string | recipe pages, when stated | The first recipe's `recipeCuisine`, several joined with `, ` |
| `servings` | string | recipe pages, when stated | The first recipe's `recipeYield` as text, always a string (`'4'`, `12 muffins`). When the yield is a list, its first value |
| `total_time` | string | recipe pages, when stated | The first recipe's `totalTime` in words (`1 hour 30 minutes`); the raw value is in the body |
| `keywords` | list of strings | recipe pages, when stated | The first recipe's `keywords`, split on commas, duplicates removed |
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

## Feeds

When the start URL is an RSS 2.0 or Atom feed, ScrapePy reads it as content instead of crawling. A URL is treated as a feed when its path ends in `/feed`, `/rss`, `/atom`, `/feeds`, `rss.xml`, `atom.xml`, `feed.xml`, `index.xml`, `.rss` or `.atom`, or its query says `feed=rss2`, `format=atom` and similar; `--feed` forces it for any URL. A detected URL whose response is not a feed is crawled as a site; with `--feed` it is recorded as a failure instead.

- robots.txt is checked for the feed URL, and the feed is fetched once, after the usual pacing gap. Entry links are not followed and nothing else is downloaded.
- Each entry, in feed order, up to `--max-articles` entries (50 when not given), becomes one Markdown file named from its link like a crawled page. A query string in the link is kept in the name (`/?p=101` is `index-p-101.md`), an entry on another host goes under a folder named after that host (`feeds_example/...`), and when two entries would get the same name, both get a suffix from a short hash of their link (`post-1a2b3c4d.md`), so names do not change when the feed's order does.
- Frontmatter: `title`, `url` (the entry link, made absolute against `xml:base` or the feed URL), `date` (RSS `pubDate` or `dc:date`, Atom `published` else `updated`, as ISO 8601 no more precise than published: a date stays a date, whether written `2026-10-03`, `20261003` or `Sat, 03 Oct 2026`, a time keeps its offset, and a time without one stays without one) with `date_source: FEED`, `page_type: feed_entry`, `author` when present, `word_count`, `domain` (the feed's host), `scraped_at` and `content_hash`. A date that cannot be read is left out.
- A DOCTYPE in the feed, with any entity declarations, is removed before parsing: entities are never expanded, no DTD or other URL is fetched and no local file is read. Entities the feed then uses are shown as written (`&a;`). A feed that has entries none of which can be read is recorded as failed, with `feed.error` set, and the run exits 1.
- Body: `# Title`, the date line, then the entry's HTML converted to Markdown: RSS `content:encoded`, else `description`; Atom `content` (`html`, `xhtml` or `text`), else `summary`. Links and images are absolute.
- An entry with no link (no `link`, no permalink `guid`, no `http` Atom `id`) and a second entry with the same link are not written; both are listed in `run_summary.json`. An entry whose file already holds the same body is left alone and counted as unchanged.

## Limitations

- Button text is kept only for questions and for toggles that open a panel (accordion and FAQ buttons with `aria-expanded` or `aria-controls`). A custom FAQ button with neither and no question mark ("Returns policy"), or a plan button ("Starter plan"), is dropped with its label. Toggles whose label looks like page furniture (share, load more, pagers, slide counters, cookie settings, search, Menu and Close in a few languages) or whose panel is mostly links are dropped too.

- Different addresses can map to the same file: `/about`, `/about.html` and `/about/` all become `about.md`, and `/news/123/slug` and `/news/slug-123` both become `news/slug-123.md`. Only the first URL crawled gets a file; the others are listed under `collisions` in `run_summary.json` and have no document. Key documents on `url` and expect at most one file per name.
- A page that redirects is written under the address requested, so a redirecting address and its target can both appear, and the later one is listed as a duplicate when their bodies match.

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
| `counts` | object of integers | `fetched`, `new`, `changed`, `unchanged`, `failed`, `blocked_by_robots`, `duplicates`, `collisions`, `skipped_old` |
| `new_markdown`, `changed_markdown` | list of strings | Markdown files written for new and changed pages |
| `unchanged_urls` | list of strings | Pages the server or the cache showed had not changed |
| `failed` | list of `{url, reason}` | Fetch errors, HTTP 4xx and 5xx, error pages, and pages whose conversion to Markdown failed (these are not counted as new or changed and are converted again on the next run) |
| `blocked_by_robots` | list of strings | URLs robots.txt disallowed |
| `duplicates` | list of `{url, same_as}` | Pages whose Markdown matched a file already written, and that file |
| `collisions` | list of `{url, file, kept}` | Pages not written because their file (`file`, relative to `output_dir`) already belongs to another URL this run (`kept`). They are not counted as new or changed and are not cached, so every run decides the same way |
| `interrupted`, `interrupted_by` | boolean, string or null | Whether the run stopped early, and the signal (`SIGINT` or `SIGTERM`) |
| `exit_code` | integer or null | The process exit code; null while running |
| `feed` | object | Feed mode only (below) |

In feed mode `counts.fetched` is 1 (the feed), `new_markdown` and `changed_markdown` list the entry files, `unchanged_urls` the entries whose file did not change, and `feed` holds:

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
