"""File writing, directory creation, and path generation."""

import os
import posixpath
import re
from pathlib import Path

from ..config import ScrapeConfig


def create_output_dirs(config: ScrapeConfig):
    """Create the base output directory structure.

    In markdown-only mode the site's ScrapedSites folder holds only run
    state (cache, run_summary.json, site_outline.json), so no asset folders.
    """
    if config.markdown_only:
        os.makedirs(config.output_dir, exist_ok=True)
        os.makedirs(config.markdown_dir, exist_ok=True)
        return
    for subdir in ("", "css", "js", "images", "fonts", "media"):
        os.makedirs(os.path.join(config.output_dir, subdir), exist_ok=True)


def _flatten_numeric_segments(path: str) -> str:
    """Flatten consecutive numeric path segments into the filename.

    Walks segments right-to-left. The rightmost segment is the filename base.
    Any consecutive purely-numeric segments immediately before it are collected
    and appended as suffixes on the filename. The first non-numeric segment
    (and everything before it) stays as directories.

    Examples:
        motogp/news/1090365/1/slug  ->  motogp/news/slug-1090365-1
        f1/news/1005473             ->  f1/news/1005473  (no slug after number)
        about-us                    ->  about-us
        123/456/789                 ->  789-123-456
        a/1/2/3/4/slug              ->  a/slug-1-2-3-4
    """
    segments = path.split("/")
    if len(segments) < 2:
        return path

    filename = segments[-1]
    numeric_parts = []

    # Walk backwards from second-to-last, collecting consecutive numeric segments
    i = len(segments) - 2
    while i >= 0 and re.fullmatch(r"\d+", segments[i]):
        numeric_parts.append(segments[i])
        i -= 1

    if not numeric_parts:
        return path

    # Remaining segments are the directory part
    dir_segments = segments[: i + 1]
    # Append numeric parts in their original (left-to-right) order
    numeric_parts.reverse()
    suffix = "-".join(numeric_parts)
    new_filename = f"{filename}-{suffix}"

    if dir_segments:
        return "/".join(dir_segments) + "/" + new_filename
    return new_filename


def extract_slug(url_path: str) -> str:
    """Extract article slug from URL path, flattening numeric segments.

    Examples:
        /sport/football/my-great-article -> my-great-article
        /news/12345/my-article -> my-article-12345
        /news/12345 -> 12345
        / -> index
        /sport/article.html -> article
    """
    path = url_path.strip("/")
    if not path:
        return "index"

    # Strip query/fragment
    path = path.split("?")[0].split("#")[0]

    # Apply numeric flattening (reuse existing _flatten_numeric_segments)
    flattened = _flatten_numeric_segments(path)

    # Take just the filename (last segment)
    slug = flattened.rsplit("/", 1)[-1] if "/" in flattened else flattened

    # Strip common web extensions
    for ext in (".html", ".htm", ".php", ".asp", ".aspx"):
        if slug.endswith(ext):
            slug = slug[: -len(ext)]
            break

    return slug


def article_relpath_in_section(url_path: str, section_path: str) -> str:
    """Compute the article's relative path under its section.

    Strips the section prefix from the URL path and applies numeric
    flattening to the remaining portion, preserving intermediate
    directories.

    Examples:
        article_relpath_in_section("/f1/news/article-slug", "/f1")
            -> "news/article-slug"
        article_relpath_in_section("/sport/football/great-match", "/sport")
            -> "football/great-match"
        article_relpath_in_section("/news/article-slug", "/news")
            -> "article-slug"
        article_relpath_in_section("/news/12345/1/my-story", "/news")
            -> "my-story-12345-1"
    """
    article = url_path.strip("/")
    section = section_path.strip("/")

    # Strip section prefix to get relative portion
    if section and article.startswith(section + "/"):
        rel = article[len(section) + 1:]
    elif not section:
        # Root section, use full path
        rel = article
    else:
        # Article not under this section, use just the slug to avoid
        # duplicating path segments (e.g., /business/article under
        # section /business/markets would create markets/business/article)
        return extract_slug(url_path)

    if not rel:
        return extract_slug(url_path)

    # Strip common web extensions from the last segment
    parts = rel.split("/")
    last = parts[-1]
    for ext in (".html", ".htm", ".php", ".asp", ".aspx"):
        if last.endswith(ext):
            last = last[: -len(ext)]
            break
    parts[-1] = last

    # Apply numeric flattening to the relative path
    rel = "/".join(parts)
    return _flatten_numeric_segments(rel)


def article_filepath_in_section(
    url_path: str,
    section_path: str,
    config: ScrapeConfig,
    ext: str = ".md",
) -> str:
    """Map an article URL to a filepath under its section directory.

    Used for MarkdownSites output. The article is placed under the section's
    directory, preserving intermediate sub-paths between the section and the
    article slug.

    Args:
        url_path: Article URL path (e.g., /sport/football/my-article)
        section_path: Section URL path (e.g., /sport)
        config: ScrapeConfig (uses output_dir)
        ext: File extension (default .md)

    Returns:
        Full filepath like: MarkdownSites/example.com/sport/football/my-article.md
    """
    relpath = article_relpath_in_section(url_path, section_path)

    # Section path becomes directory structure
    section_dir = section_path.strip("/")

    # Build: <markdown_output_dir>/<section_dir>/<relpath><ext>
    md_output = config.markdown_dir
    if section_dir:
        return os.path.join(md_output, section_dir, relpath + ext)
    return os.path.join(md_output, relpath + ext)


def section_index_filepath(url_path: str, config: ScrapeConfig) -> str:
    """Map a section URL path to a _index.md filepath in MarkdownSites.

    Section pages produce a _index.md file inside a directory named after the
    section, rather than a named .md file in the parent directory.

    Examples:
        /business/markets -> MarkdownSites/example.com/business/markets/_index.md
        /news             -> MarkdownSites/example.com/news/_index.md
        /                 -> MarkdownSites/example.com/_index.md
    """
    section_dir = url_path.strip("/")
    # Strip common web extensions from last segment (e.g., page.html -> page)
    for ext in (".html", ".htm", ".php", ".asp", ".aspx"):
        if section_dir.endswith(ext):
            section_dir = section_dir[: -len(ext)]
            break
    # /news/index.html is the /news section: news/_index.md, not news/index/_index.md
    if section_dir == "index":
        section_dir = ""
    elif section_dir.endswith("/index"):
        section_dir = section_dir[: -len("/index")]
    md_output = config.markdown_dir
    if section_dir:
        return os.path.join(md_output, section_dir, "_index.md")
    return os.path.join(md_output, "_index.md")


def url_to_filepath(url_path: str, config: ScrapeConfig, hash_fragment: str = "") -> str:
    """Convert a URL path to a local file path.

    Numeric-only path segments adjacent to the filename are flattened into
    the filename as suffixes, e.g. /news/12345/1/slug -> news/slug-12345-1.html
    """
    clean = url_path.split("?")[0].split("#")[0]

    # Strip starting path prefix when stay-in-path is active
    # With --site-name several runs share one folder, so paths stay whole
    if config.stay_in_path and config.starting_path and not config.site_name:
        if clean.startswith(config.starting_path):
            clean = clean[len(config.starting_path):]

    if clean in ("/", ""):
        if hash_fragment:
            hash_file = re.sub(r'^#[^=]*=?', '', hash_fragment)
            hash_file = hash_file.replace('.htm', '.html') if hash_file.endswith('.htm') else hash_file
            if not hash_file.endswith('.html'):
                hash_file += '.html'
            # Sanitize: strip path separators from hash-derived filename
            hash_file = hash_file.replace("/", "_").replace("\\", "_")
            return os.path.join(config.output_dir, hash_file)
        return os.path.join(config.output_dir, "index.html")

    clean = clean.lstrip("/").rstrip("/")

    # Flatten numeric segments before adding extension
    has_ext = bool(os.path.splitext(clean)[1])
    if not has_ext:
        clean = _flatten_numeric_segments(clean)
        clean += ".html"
    else:
        # For files with extensions, split off extension, flatten, re-add
        base, ext = os.path.splitext(clean)
        clean = _flatten_numeric_segments(base) + ext

    result = os.path.normpath(os.path.join(config.output_dir, clean))

    # Path traversal protection: ensure result stays within output_dir
    output_dir_resolved = os.path.normpath(config.output_dir)
    if not result.startswith(output_dir_resolved + os.sep) and result != output_dir_resolved:
        # Malicious path escaped output_dir, fall back to flat filename
        safe_name = clean.replace("/", "_").replace("\\", "_")
        result = os.path.join(config.output_dir, safe_name)

    return result


def get_relative_path(from_file: str, to_file: str) -> str:
    """Get relative path from one file to another."""
    from_dir = os.path.dirname(from_file)
    relative = os.path.relpath(to_file, from_dir)
    relative = relative.replace("\\", "/")
    if not relative.startswith("../") and not relative.startswith("./"):
        relative = "./" + relative
    return relative


def ensure_parent_dir(filepath: str):
    """Ensure the parent directory of a file exists."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)


def write_file(filepath: str, content: str | bytes):
    """Write content to a file, creating parent directories as needed."""
    ensure_parent_dir(filepath)
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
    with open(filepath, mode, **kwargs) as f:
        f.write(content)
