"""HTML URL rewriting for offline viewing."""

import os
import re

from ..config import ScrapeConfig
from ..output.writer import url_to_filepath, get_relative_path
from ..processing.urls import is_in_scope


def rewrite_urls(html: str, current_page_path: str, config: ScrapeConfig) -> str:
    """Rewrite internal URLs in HTML to relative paths for offline viewing."""
    current_file = url_to_filepath(current_page_path, config)
    base_url = config.base_url
    output_dir = config.output_dir

    def _is_internal(url: str) -> bool:
        if not url:
            return False
        if url.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return False
        if url.startswith("/"):
            return True
        if url.startswith(base_url):
            return True
        if url.startswith("http://") or url.startswith("https://"):
            return False
        return True

    def _url_to_path(url: str) -> str:
        if url.startswith(base_url):
            url = url[len(base_url):]
        if not url.startswith("/"):
            current_dir = current_page_path.rsplit("/", 1)[0] if "/" in current_page_path else "/"
            import posixpath
            url = posixpath.join(current_dir, url)
        return url

    def _rewrite_href(match):
        prefix = match.group(1)
        url = match.group(2)
        if not _is_internal(url):
            return match.group(0)

        url_path = _url_to_path(url)
        ext = os.path.splitext(url_path.split("?")[0])[1].lower()

        if ext == ".css":
            filename = url_path.split("/")[-1]
            asset_path = os.path.join(output_dir, "css", filename)
            return f'{prefix}href="{get_relative_path(current_file, asset_path)}"'

        if ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx"):
            filename = url_path.split("/")[-1]
            asset_path = os.path.join(output_dir, "media", filename)
            return f'{prefix}href="{get_relative_path(current_file, asset_path)}"'

        if not ext or ext in (".html", ".htm"):
            if config.stay_in_path and not is_in_scope(url_path, config):
                return f'{prefix}href="{base_url}{url_path}"'
            target_file = url_to_filepath(url_path, config)
            return f'{prefix}href="{get_relative_path(current_file, target_file)}"'

        return match.group(0)

    def _rewrite_src(match):
        prefix = match.group(1)
        url = match.group(2)
        if not _is_internal(url):
            return match.group(0)

        url_path = _url_to_path(url)
        ext = os.path.splitext(url_path.split("?")[0])[1].lower()

        if ext == ".js":
            filename = url_path.split("/")[-1]
            asset_path = os.path.join(output_dir, "js", filename)
            return f'{prefix}src="{get_relative_path(current_file, asset_path)}"'

        if ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico"):
            filename = url_path.split("/")[-1].split("?")[0]
            asset_path = os.path.join(output_dir, "images", filename)
            return f'{prefix}src="{get_relative_path(current_file, asset_path)}"'

        if ext in (".mp4", ".mp3", ".wav", ".webm", ".ogg"):
            filename = url_path.split("/")[-1]
            asset_path = os.path.join(output_dir, "media", filename)
            return f'{prefix}src="{get_relative_path(current_file, asset_path)}"'

        return match.group(0)

    # Rewrite href attributes
    html = re.sub(
        r'(<(?:a|link)[^>]*\s)href="([^"]+)"',
        _rewrite_href,
        html,
        flags=re.IGNORECASE,
    )

    # Rewrite src attributes
    html = re.sub(
        r'(<(?:img|script|source|video|audio)[^>]*\s)src="([^"]+)"',
        _rewrite_src,
        html,
        flags=re.IGNORECASE,
    )

    # Rewrite srcset
    def _rewrite_srcset(match):
        srcset = match.group(1)
        parts = []
        for part in srcset.split(","):
            tokens = part.strip().split()
            url = tokens[0] if tokens else ""
            size = tokens[1] if len(tokens) > 1 else ""
            if _is_internal(url):
                filename = url.split("/")[-1].split("?")[0]
                asset_path = os.path.join(output_dir, "images", filename)
                rel = get_relative_path(current_file, asset_path)
                parts.append(f"{rel} {size}".strip())
            else:
                parts.append(part.strip())
        return f'srcset="{", ".join(parts)}"'

    html = re.sub(r'srcset="([^"]+)"', _rewrite_srcset, html, flags=re.IGNORECASE)

    # Rewrite CSS url()
    def _rewrite_css_url(match):
        url = match.group(1)
        if not _is_internal(url):
            return match.group(0)
        ext = os.path.splitext(url)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"):
            filename = url.split("/")[-1].split("?")[0]
            asset_path = os.path.join(output_dir, "images", filename)
            return f"url('{get_relative_path(current_file, asset_path)}')"
        return match.group(0)

    html = re.sub(r"url\(['\"]?([^'\"\)]+)['\"]?\)", _rewrite_css_url, html, flags=re.IGNORECASE)

    # Rewrite absolute URLs to the same domain
    def _rewrite_absolute(match):
        attr = match.group(1)
        url_path = match.group(2)
        ext = os.path.splitext(url_path.split("?")[0])[1].lower()

        if ext == ".css":
            filename = url_path.split("/")[-1]
            asset_path = os.path.join(output_dir, "css", filename)
            return f'{attr}="{get_relative_path(current_file, asset_path)}"'
        if ext == ".js":
            filename = url_path.split("/")[-1]
            asset_path = os.path.join(output_dir, "js", filename)
            return f'{attr}="{get_relative_path(current_file, asset_path)}"'
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico"):
            filename = url_path.split("/")[-1].split("?")[0]
            asset_path = os.path.join(output_dir, "images", filename)
            return f'{attr}="{get_relative_path(current_file, asset_path)}"'
        if not ext or ext in (".html", ".htm"):
            if config.stay_in_path and not is_in_scope(url_path, config):
                return match.group(0)
            target_file = url_to_filepath(url_path, config)
            return f'{attr}="{get_relative_path(current_file, target_file)}"'

        return match.group(0)

    html = re.sub(
        rf'(href|src)="{re.escape(base_url)}(/[^"]*)"',
        _rewrite_absolute,
        html,
        flags=re.IGNORECASE,
    )

    return html
