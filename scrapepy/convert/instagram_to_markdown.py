"""Convert Instagram post/profile JSON data to Markdown."""

from datetime import datetime


def _format_timestamp(ts: str) -> str:
    """Format an ISO timestamp into a human-readable string."""
    if not ts:
        return ""
    try:
        if "T" in ts:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(ts)
        return dt.strftime("%B %d, %Y at %I:%M %p")
    except (ValueError, TypeError):
        return ts


def post_to_markdown(post_data: dict) -> str:
    """Convert post data dict to a Markdown document."""
    lines = ["# Instagram Post", ""]

    # Date
    timestamp = post_data.get("timestamp", "")
    formatted = _format_timestamp(timestamp)
    if formatted:
        lines.append(f"**Date:** {formatted}")
    else:
        lines.append("**Date:** Unknown")

    # Author
    author = post_data.get("author", "")
    author_full = post_data.get("author_full_name", "")
    author_verified = post_data.get("author_verified", False)
    if author:
        display = f"@{author}" if not author.startswith("@") else author
        if author_full:
            display = f"{author_full} ({display})"
        if author_verified:
            display += " [Verified]"
        lines.append(f"**Author:** {display}")

    # Collaboration
    coauthors = post_data.get("coauthor_producers", [])
    if coauthors:
        names = [f"@{c.get('username', '')}" for c in coauthors if c.get("username")]
        if names:
            lines.append(f"**Co-authors:** {', '.join(names)}")

    if post_data.get("is_paid_partnership"):
        lines.append("**Paid partnership**")

    # Location
    location = post_data.get("location", "")
    if location:
        lines.append(f"**Location:** {location}")

    # Engagement
    likes = post_data.get("likes", "")
    comments = post_data.get("comments_count", "")
    views = post_data.get("video_view_count", "")
    play_count = post_data.get("play_count", "")
    # Use "null" string check since Instagram JSON sometimes returns literal "null"
    if views == "null":
        views = ""
    if play_count == "null":
        play_count = ""
    display_views = views or play_count

    if post_data.get("like_and_view_counts_disabled"):
        lines.append("**Engagement:** Likes and views hidden by author")
    elif likes or comments or display_views:
        parts = []
        if likes:
            parts.append(f"{likes} likes")
        if comments:
            parts.append(f"{comments} comments")
        if display_views:
            parts.append(f"{display_views} views")
        lines.append(f"**Engagement:** {', '.join(parts)}")

    # Media type
    media_type = post_data.get("media_type", "image")
    is_reel = post_data.get("is_reel", False)
    carousel_count = post_data.get("carousel_count", 0)
    if is_reel:
        lines.append("**Type:** Reel")
    elif media_type == "carousel":
        label = f"Carousel ({carousel_count} slides)" if carousel_count else "Carousel"
        lines.append(f"**Type:** {label}")
    elif media_type == "video":
        lines.append("**Type:** Video")

    # Video metadata
    video_duration = post_data.get("video_duration", "")
    video_width = post_data.get("video_width", "")
    video_height = post_data.get("video_height", "")
    has_audio = post_data.get("has_audio", False)
    original_w = post_data.get("original_width", "")
    original_h = post_data.get("original_height", "")
    # Use original dimensions if video dims missing
    w = video_width or original_w
    h = video_height or original_h
    if video_duration or (w and h):
        video_parts = []
        if video_duration:
            video_parts.append(f"Duration: {video_duration}")
        if w and h:
            video_parts.append(f"Resolution: {w}x{h}")
        if has_audio:
            video_parts.append("Audio: Yes")
        num_qualities = post_data.get("number_of_qualities", 0)
        if num_qualities:
            video_parts.append(f"Qualities: {num_qualities}")
        lines.append(f"**Video:** {' | '.join(video_parts)}")

    # Music / Audio info (Reels)
    music_title = post_data.get("music_title", "")
    music_artist = post_data.get("music_artist", "")
    original_sound_author = post_data.get("original_sound_author", "")
    audio_type = post_data.get("audio_type", "")

    if music_title or original_sound_author:
        if music_title and music_artist:
            lines.append(f"**Music:** {music_title} — {music_artist}")
        elif music_title:
            lines.append(f"**Music:** {music_title}")
        elif original_sound_author:
            lines.append(f"**Original audio:** @{original_sound_author}")
    elif audio_type:
        lines.append(f"**Audio type:** {audio_type}")

    # Identifiers
    post_id = post_data.get("post_id", "")
    ig_id = post_data.get("ig_id", "")
    if post_id:
        id_str = post_id
        if ig_id and ig_id != post_id:
            id_str += f" ({ig_id})"
        lines.append(f"**Post ID:** {id_str}")

    # Caption edited indicator
    if post_data.get("caption_is_edited"):
        lines.append("**Caption:** Edited")

    lines.append("")

    # Caption
    caption = post_data.get("caption", "")
    if caption:
        lines.append("## Caption")
        lines.append("")
        lines.append(caption)
        lines.append("")

    # Alt text (AI-generated description)
    alt_text = post_data.get("alt_text", "")
    if alt_text:
        lines.append(f"**Alt text:** {alt_text}")
        lines.append("")

    # Hashtags
    hashtags = post_data.get("hashtags", [])
    if hashtags:
        lines.append(f"**Hashtags:** {' '.join(hashtags)}")

    # Mentions
    mentions = post_data.get("mentions", [])
    if mentions:
        lines.append(f"**Mentions:** {' '.join(mentions)}")

    # Tagged users
    usertags = post_data.get("usertags", [])
    if usertags:
        tagged = [f"@{t.get('username', '')}" for t in usertags if t.get("username")]
        if tagged:
            lines.append(f"**Tagged:** {', '.join(tagged)}")

    if hashtags or mentions or usertags:
        lines.append("")

    # Media
    local_media = post_data.get("local_media", [])
    if local_media:
        lines.append("## Media")
        lines.append("")
        for filename in local_media:
            if any(filename.endswith(ext) for ext in (".mp4", ".mov", ".webm")):
                lines.append(f"[{filename}](media/{filename})")
            else:
                lines.append(f"![{filename}](media/{filename})")
        lines.append("")

    # Comments
    comments_list = post_data.get("comments", [])
    if comments_list:
        lines.append("## Comments")
        lines.append("")
        for c in comments_list:
            username = c.get("username", "")
            text = c.get("text", "")
            c_likes = c.get("likes", 0)
            c_time = c.get("timestamp", "")
            if username and text:
                like_str = f" ({c_likes} likes)" if c_likes else ""
                time_str = ""
                if c_time:
                    fmt = _format_timestamp(c_time)
                    if fmt:
                        time_str = f" — {fmt}"
                lines.append(f"- **@{username}:** {text}{like_str}{time_str}")
        lines.append("")

    # Footer
    shortcode = post_data.get("shortcode", "")
    url = post_data.get("url", "")
    if not url and shortcode:
        url = f"https://www.instagram.com/p/{shortcode}/"
    if url:
        lines.append("---")
        lines.append("")
        lines.append(f"[View on Instagram]({url})")
        lines.append("")

    return "\n".join(lines)


def profile_to_markdown(profile_data: dict) -> str:
    """Convert profile data dict to a Markdown summary."""
    username = profile_data.get("username", "unknown")
    full_name = profile_data.get("full_name", "")

    lines = [f"# @{username}", ""]

    if full_name:
        lines.append(f"**Name:** {full_name}")

    if profile_data.get("is_verified"):
        lines.append("**Verified:** Yes")

    if profile_data.get("is_private"):
        lines.append("**Private:** Yes")

    category = profile_data.get("category", "")
    if category:
        lines.append(f"**Category:** {category}")

    user_id = profile_data.get("user_id", "")
    if user_id:
        lines.append(f"**User ID:** {user_id}")

    lines.append("")

    # Stats table
    followers = profile_data.get("followers", "")
    following = profile_data.get("following", "")
    posts_count = profile_data.get("posts_count", "")

    if followers or following or posts_count:
        lines.append("## Stats")
        lines.append("")
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")
        if posts_count:
            lines.append(f"| Posts | {posts_count} |")
        if followers:
            lines.append(f"| Followers | {followers} |")
        if following:
            lines.append(f"| Following | {following} |")
        lines.append("")

    # Bio
    bio = profile_data.get("bio", "")
    if bio:
        lines.append("## Bio")
        lines.append("")
        lines.append(bio)
        lines.append("")

    # External URL
    external_url = profile_data.get("external_url", "")
    if external_url:
        lines.append(f"**Website:** [{external_url}]({external_url})")
        lines.append("")

    # Footer
    url = profile_data.get("url", f"https://www.instagram.com/{username}/")
    lines.append("---")
    lines.append("")
    lines.append(f"[View on Instagram]({url})")
    lines.append("")

    return "\n".join(lines)
