"""Instagram profile scraping mode."""

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from ..config import ScrapeConfig
from ..engine.fetcher import fetch_page_browser, download_asset
from ..engine.instagram_actions import (
    check_login_status,
    wait_for_login,
    dismiss_login_popup,
    scroll_profile_posts,
    extract_post_links,
    extract_profile_data,
    extract_post_data,
    navigate_carousel,
    setup_video_interceptor,
    trigger_video_playback,
)
from ..output.writer import write_file


COOKIE_DIR = os.path.expanduser("~/.scrapepy/instagram_profile")


async def run_instagram_mode(config: ScrapeConfig):
    """Main entry point for Instagram scraping."""
    accounts = _get_accounts(config)
    if not accounts:
        print("Error: No Instagram accounts specified.")
        print("Provide a URL or use --accounts with a file of usernames.")
        return

    os.makedirs(config.output_dir, exist_ok=True)

    total_posts = 0
    for i, username in enumerate(accounts):
        if i > 0:
            print(f"\nWaiting 5s before next account...")
            await asyncio.sleep(5)

        print(f"\n{'='*60}")
        print(f"Scraping @{username} ({i+1}/{len(accounts)})")
        print(f"{'='*60}")

        posts_saved = await _scrape_account(username, config)
        total_posts += posts_saved

    print(f"\n{'='*60}")
    print(f"Instagram scraping complete")
    print(f"Accounts: {len(accounts)}")
    print(f"Total posts saved: {total_posts}")
    print(f"Output: {config.output_dir}")
    print(f"{'='*60}")


def _get_accounts(config: ScrapeConfig) -> list[str]:
    """Build list of usernames from CLI url and/or accounts file."""
    accounts = []

    # Read accounts file
    if config.accounts_file:
        try:
            with open(config.accounts_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    accounts.append(_normalize_username(line))
        except FileNotFoundError:
            print(f"Error: Accounts file not found: {config.accounts_file}")

    # Add URL if provided
    if config.url:
        username = _normalize_username(config.url)
        if username and username not in accounts:
            accounts.append(username)

    return accounts


def _normalize_username(value: str) -> str:
    """Extract username from URL, @username, or bare username."""
    value = value.strip()

    # Full Instagram URL
    if "instagram.com" in value:
        parsed = urlparse(value)
        path = parsed.path.strip("/")
        # Remove trailing segments like /reels, /tagged, etc.
        parts = path.split("/")
        if parts:
            return parts[0]

    # @username
    if value.startswith("@"):
        return value[1:]

    return value


async def _scrape_account(username: str, config: ScrapeConfig) -> int:
    """Scrape a single Instagram account. Returns number of posts saved."""
    account_dir = os.path.join(config.output_dir, username)
    os.makedirs(account_dir, exist_ok=True)

    profile_url = f"https://www.instagram.com/{username}/"

    # Collect data via page_action callback
    profile_data = {}
    post_links = []

    async def profile_page_action(page):
        nonlocal profile_data, post_links

        # Handle login if requested
        if config.ig_login:
            logged_in = await check_login_status(page)
            if not logged_in:
                print("  Not logged in, navigating to login page...")
                await page.goto("https://www.instagram.com/accounts/login/")
                await asyncio.sleep(3)
                await dismiss_login_popup(page)
                logged_in = await wait_for_login(page)
                if logged_in:
                    # Navigate back to the profile
                    await page.goto(profile_url)
                    await asyncio.sleep(3)

        # Dismiss popups (cookie banners, login prompts)
        await dismiss_login_popup(page)
        await asyncio.sleep(2)

        # Check if profile exists / is accessible
        page_text = await page.evaluate("() => document.body?.innerText || ''")
        if "Sorry, this page isn't available" in page_text:
            print(f"  Profile @{username} not found or not accessible")
            return page

        if "This account is private" in page_text:
            print(f"  Profile @{username} is private")
            logged_in = await check_login_status(page)
            if not logged_in:
                print("  Use --ig-login to log in and access private profiles")
                return page

        # Extract profile data
        print("  Extracting profile data...")
        profile_data.update(await extract_profile_data(page))
        if not profile_data.get("username"):
            profile_data["username"] = username

        # Scroll to load posts
        print("  Scrolling to load posts...")
        found = await scroll_profile_posts(page, config.ig_max_posts)
        print(f"  Found {found} posts in grid")

        # Extract post links
        post_links.extend(await extract_post_links(page))
        print(f"  Extracted {len(post_links)} post URLs")

        return page

    # Fetch profile page
    print(f"\n  Fetching profile: {profile_url}")
    user_data_dir = COOKIE_DIR if config.ig_login else None

    try:
        response = await fetch_page_browser(
            profile_url, config,
            page_action=profile_page_action,
            user_data_dir=user_data_dir,
        )
    except Exception as e:
        print(f"  Error fetching profile @{username}: {e}")
        return 0

    if not profile_data:
        print(f"  Could not extract profile data for @{username}")
        return 0

    # Save profile data
    profile_data["url"] = profile_url
    profile_data["scraped_posts_count"] = len(post_links)

    profile_json_path = os.path.join(account_dir, "profile.json")
    write_file(profile_json_path, json.dumps(profile_data, indent=2, ensure_ascii=False))
    print(f"  Saved: profile.json")

    if config.markdown:
        from ..convert.instagram_to_markdown import profile_to_markdown
        profile_md = profile_to_markdown(profile_data)
        write_file(os.path.join(account_dir, "profile.md"), profile_md)
        print(f"  Saved: profile.md")

    # Apply max_posts limit
    if config.ig_max_posts > 0:
        post_links = post_links[:config.ig_max_posts]

    if not post_links:
        print(f"  No posts found for @{username}")
        return 0

    # Incremental mode: skip posts already scraped (disabled by --update-meta
    # since the whole point of --update-meta is to revisit existing posts)
    if config.incremental and not config.ig_update_meta:
        existing_shortcodes = _get_existing_shortcodes(account_dir)
        before = len(post_links)
        post_links = [
            url for url in post_links
            if _shortcode_from_url(url) not in existing_shortcodes
        ]
        skipped = before - len(post_links)
        if skipped:
            print(f"  Incremental: skipping {skipped} already-scraped posts")
        if not post_links:
            print(f"  All posts already scraped for @{username}")
            return 0

    print(f"\n  Scraping {len(post_links)} posts...")

    # Scrape individual posts
    posts_saved = 0
    for j, post_url in enumerate(post_links):
        # Rate limiting: deterministic jitter based on URL hash
        delay = 2.0 + (int(hashlib.sha256(post_url.encode()).hexdigest()[:8], 16) % 3000) / 1000
        if j > 0:
            await asyncio.sleep(delay)

        print(f"\n  [{j+1}/{len(post_links)}] {post_url}")
        saved = await _scrape_post(post_url, username, account_dir, config)
        if saved:
            posts_saved += 1

    print(f"\n  Completed @{username}: {posts_saved}/{len(post_links)} posts saved")
    return posts_saved


async def _scrape_post(
    post_url: str, username: str, account_dir: str, config: ScrapeConfig,
) -> bool:
    """Scrape a single Instagram post. Returns True if saved successfully."""
    post_data = {}
    carousel_media = []
    raw_html = ""
    intercepted_video_urls = []

    async def post_page_action(page):
        nonlocal post_data, carousel_media, raw_html, intercepted_video_urls

        # Set up network interception to capture video CDN URLs
        intercepted_video_urls = await setup_video_interceptor(page)

        await dismiss_login_popup(page)
        await asyncio.sleep(2)

        # Extract post data
        post_data.update(await extract_post_data(page))
        if not post_data.get("author"):
            post_data["author"] = username

        # For video/reel posts, trigger playback to force CDN request
        is_video = (
            post_data.get("media_type") == "video"
            or post_data.get("is_reel")
            or "/reel/" in post_url
        )
        if is_video:
            print("    Video post, triggering playback to capture CDN URL...")
            await trigger_video_playback(page)
            await asyncio.sleep(2)

        # Handle carousel posts
        if post_data.get("is_carousel"):
            print("    Carousel post, navigating slides...")
            carousel_media.extend(await navigate_carousel(page, intercepted_video_urls))
        elif is_video and not carousel_media:
            # For non-carousel video: trigger playback again and wait
            await trigger_video_playback(page)
            await asyncio.sleep(2)

        # Capture raw HTML
        raw_html = await page.evaluate("() => document.documentElement.outerHTML")

        return page

    user_data_dir = COOKIE_DIR if config.ig_login else None

    try:
        await fetch_page_browser(
            post_url, config,
            page_action=post_page_action,
            user_data_dir=user_data_dir,
        )
    except Exception as e:
        print(f"    Error: {e}")
        return False

    if not post_data.get("shortcode"):
        # Try extracting shortcode from URL
        m = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", post_url)
        if m:
            post_data["shortcode"] = m.group(1)
        else:
            print("    Could not extract post data")
            return False

    # Build post directory name
    date_prefix = ""
    if post_data.get("timestamp"):
        try:
            from datetime import datetime
            ts = post_data["timestamp"]
            # Handle ISO format
            if "T" in ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(ts)
            date_prefix = dt.strftime("%Y-%m-%d") + "_"
        except (ValueError, TypeError):
            pass

    post_dir_name = f"{date_prefix}{post_data['shortcode']}"
    post_dir = os.path.join(account_dir, post_dir_name)
    media_dir = os.path.join(post_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    # Save raw HTML
    if raw_html:
        write_file(os.path.join(post_dir, "post.html"), raw_html)

    # Collect all media URLs
    all_media = []
    if carousel_media:
        all_media = carousel_media
    elif post_data.get("media_urls"):
        all_media = list(post_data["media_urls"])

    # Merge intercepted video CDN URLs (most reliable source for videos)
    existing_urls = {m.get("url") for m in all_media}
    for vid_url in intercepted_video_urls:
        if vid_url not in existing_urls:
            all_media.append({"type": "video", "url": vid_url})
            existing_urls.add(vid_url)

    # If we have a video poster/thumbnail and no image yet, add it
    poster = post_data.get("video_poster", "")
    if poster and poster not in existing_urls:
        all_media.append({"type": "image", "url": poster})

    # Filter out blob: URLs (can't download)
    all_media = [m for m in all_media if not m.get("url", "").startswith("blob:")]

    # Check for existing media on disk (for --update-meta)
    existing_media = _get_existing_media(media_dir)

    if config.ig_update_meta and existing_media:
        # Reuse existing media files, skip downloads
        downloaded_files = existing_media
        print(f"    Reusing {len(existing_media)} existing media files")
    else:
        # Download media
        downloaded_files = []
        for k, media_item in enumerate(all_media):
            media_url = media_item.get("url", "")
            media_type = media_item.get("type", "image")

            if not media_url:
                continue

            # Determine filename
            ext = _guess_extension(media_url, media_type)
            filename = f"{media_type}_{k+1}{ext}"
            filepath = os.path.join(media_dir, filename)

            # Rate limit between media downloads
            if k > 0:
                await asyncio.sleep(0.5)

            try:
                data, status, _ = await download_asset(media_url, config)
                if data:
                    with open(filepath, "wb") as f:
                        f.write(data)
                    downloaded_files.append(filename)
                    print(f"    Downloaded: {filename}")
                else:
                    print(f"    Failed to download: {filename} (status {status})")
            except Exception as e:
                print(f"    Failed to download: {filename} ({e})")

    # Update post_data with local file references
    post_data["local_media"] = downloaded_files
    post_data["url"] = post_url
    post_data["post_dir"] = post_dir_name

    # Save post.json
    write_file(
        os.path.join(post_dir, "post.json"),
        json.dumps(post_data, indent=2, ensure_ascii=False),
    )
    print(f"    Saved: {post_dir_name}/post.json")

    # Save post.md if markdown requested
    if config.markdown:
        from ..convert.instagram_to_markdown import post_to_markdown
        post_md = post_to_markdown(post_data)
        write_file(os.path.join(post_dir, "post.md"), post_md)
        print(f"    Saved: {post_dir_name}/post.md")

    return True


def _guess_extension(url: str, media_type: str) -> str:
    """Guess file extension from URL or media type."""
    # Try to extract from URL path
    parsed = urlparse(url)
    path = parsed.path
    _, ext = os.path.splitext(path)
    if ext and ext.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm"):
        return ext.lower()

    # Default by type
    if media_type == "video":
        return ".mp4"
    return ".jpg"


def _get_existing_media(media_dir: str) -> list[str]:
    """Return sorted list of media filenames already on disk."""
    if not os.path.isdir(media_dir):
        return []
    media_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm"}
    files = [
        f.name for f in os.scandir(media_dir)
        if f.is_file() and os.path.splitext(f.name)[1].lower() in media_exts
    ]
    return sorted(files)


def _shortcode_from_url(url: str) -> str:
    """Extract shortcode from a post/reel URL."""
    m = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else ""


def _get_existing_shortcodes(account_dir: str) -> set[str]:
    """Scan account directory for already-scraped post shortcodes.

    Post directories are named ``YYYY-MM-DD_SHORTCODE`` or just ``SHORTCODE``.
    A post is considered scraped if its directory contains a ``post.json`` file.
    """
    shortcodes: set[str] = set()
    if not os.path.isdir(account_dir):
        return shortcodes
    for entry in os.scandir(account_dir):
        if not entry.is_dir():
            continue
        # Check that post.json exists (directory might be partial/failed)
        if not os.path.isfile(os.path.join(entry.path, "post.json")):
            continue
        # Extract shortcode: after last underscore if date-prefixed, else full name
        name = entry.name
        parts = name.split("_", 1)
        if len(parts) == 2 and re.match(r"\d{4}-\d{2}-\d{2}$", parts[0]):
            shortcodes.add(parts[1])
        else:
            shortcodes.add(name)
    return shortcodes
