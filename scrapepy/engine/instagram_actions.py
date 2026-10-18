"""Instagram-specific browser page_action callbacks and JS extraction."""

import asyncio


async def check_login_status(page) -> bool:
    """Check if the browser session is logged into Instagram."""
    return await page.evaluate("""() => {
        const avatar = document.querySelector(
            'img[data-testid="user-avatar"],'
            + 'span[role="img"][aria-label],'
            + 'nav img[alt][src*="profile"],'
            + 'a[href*="/accounts/activity/"]'
        );
        if (avatar) return true;
        const loginBtn = document.querySelector(
            'a[href="/accounts/login/"],'
            + 'button[type="submit"]'
        );
        const bodyText = document.body?.innerText || '';
        if (!loginBtn && bodyText.length > 500) return true;
        return false;
    }""")


async def wait_for_login(page, timeout: int = 180) -> bool:
    """Poll for login completion during manual login. Returns True if logged in."""
    print("  Waiting for manual login (up to 3 minutes)...")
    elapsed = 0
    interval = 3
    while elapsed < timeout:
        logged_in = await check_login_status(page)
        if logged_in:
            print("  Login detected!")
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    print("  Login timeout — continuing without login")
    return False


async def dismiss_login_popup(page):
    """Close 'Log In' / 'Not Now' / cookie popups on public profiles."""
    await page.evaluate("""() => {
        const buttons = document.querySelectorAll('button, div[role="button"]');
        for (const btn of buttons) {
            const text = (btn.textContent || '').trim().toLowerCase();
            if (text === 'not now' || text === 'decline optional cookies'
                || text === 'allow essential and optional cookies'
                || text === 'accept all') {
                btn.click();
                break;
            }
        }
    }""")
    await asyncio.sleep(1)
    await page.evaluate("""() => {
        const buttons = document.querySelectorAll('button, div[role="button"]');
        for (const btn of buttons) {
            const text = (btn.textContent || '').trim().toLowerCase();
            if (text === 'not now' || text === 'dismiss') {
                btn.click();
                break;
            }
        }
    }""")
    await asyncio.sleep(0.5)


async def scroll_profile_posts(page, max_posts: int = 0) -> int:
    """Infinite-scroll the profile grid to load post thumbnails."""
    post_count = await page.evaluate("""(maxPosts) => {
        return new Promise((resolve) => {
            let lastCount = 0;
            let stableRounds = 0;
            const maxStable = 3;

            const timer = setInterval(() => {
                const posts = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                const currentCount = posts.length;

                if (maxPosts > 0 && currentCount >= maxPosts) {
                    clearInterval(timer);
                    resolve(currentCount);
                    return;
                }

                if (currentCount === lastCount) {
                    stableRounds++;
                    if (stableRounds >= maxStable) {
                        clearInterval(timer);
                        resolve(currentCount);
                        return;
                    }
                } else {
                    stableRounds = 0;
                }
                lastCount = currentCount;
                window.scrollBy(0, 800);
            }, 1500);

            setTimeout(() => {
                clearInterval(timer);
                const posts = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                resolve(posts.length);
            }, 120000);
        });
    }""", max_posts)
    return post_count


async def extract_post_links(page) -> list[str]:
    """Get all /p/ and /reel/ URLs from the profile grid."""
    return await page.evaluate("""() => {
        const links = new Set();
        document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]').forEach(a => {
            const href = a.getAttribute('href');
            if (href) {
                const url = href.startsWith('http') ? href
                    : 'https://www.instagram.com' + href;
                links.add(url);
            }
        });
        return Array.from(links);
    }""")


async def extract_profile_data(page) -> dict:
    """Extract profile metadata from embedded JSON and meta tags."""
    return await page.evaluate("""() => {
        const data = {
            username: '',
            full_name: '',
            bio: '',
            profile_pic_url: '',
            followers: '',
            following: '',
            posts_count: '',
            is_verified: false,
            is_private: false,
            external_url: '',
            category: '',
            user_id: '',
        };

        // ── Strategy 1: Parse embedded JSON from <script type="application/json"> ──
        // Instagram embeds full profile data in ScheduledServerJS payloads
        function deepFind(obj, keys, depth) {
            if (!obj || typeof obj !== 'object' || depth > 12) return null;
            for (const key of keys) {
                if (obj[key] !== undefined) return obj[key];
            }
            for (const val of Object.values(obj)) {
                const found = deepFind(val, keys, depth + 1);
                if (found !== null) return found;
            }
            return null;
        }

        function findUserProfile(obj, depth) {
            // Look for a user object with follower_count (profile-level data)
            if (!obj || typeof obj !== 'object' || depth > 12) return null;
            if (obj.follower_count !== undefined && obj.username) return obj;
            if (obj.edge_followed_by && obj.username) return obj;
            for (const val of Object.values(obj)) {
                const found = findUserProfile(val, depth + 1);
                if (found) return found;
            }
            return null;
        }

        const scripts = document.querySelectorAll('script[type="application/json"]');
        for (const script of scripts) {
            const text = script.textContent;
            if (!text || text.length < 500) continue;
            // Quick check: does this block contain profile-like data?
            if (!text.includes('follower_count') && !text.includes('edge_followed_by')
                && !text.includes('biography') && !text.includes('full_name')) continue;
            try {
                const json = JSON.parse(text);
                const user = findUserProfile(json, 0);
                if (user) {
                    data.username = user.username || '';
                    data.full_name = user.full_name || '';
                    data.bio = user.biography || user.bio || '';
                    data.profile_pic_url = user.profile_pic_url || user.profile_pic_url_hd || '';
                    data.is_verified = !!user.is_verified;
                    data.is_private = !!user.is_private;
                    data.user_id = String(user.pk || user.id || '');
                    data.external_url = user.external_url || '';
                    data.category = user.category_name || user.category || '';

                    // Follower/following counts — different API shapes
                    if (user.follower_count !== undefined) {
                        data.followers = String(user.follower_count);
                    } else if (user.edge_followed_by) {
                        data.followers = String(user.edge_followed_by.count);
                    }
                    if (user.following_count !== undefined) {
                        data.following = String(user.following_count);
                    } else if (user.edge_follow) {
                        data.following = String(user.edge_follow.count);
                    }
                    if (user.media_count !== undefined) {
                        data.posts_count = String(user.media_count);
                    } else if (user.edge_owner_to_timeline_media) {
                        data.posts_count = String(user.edge_owner_to_timeline_media.count);
                    }
                    break;
                }
            } catch (e) {}
        }

        // ── Strategy 2: Meta tag fallback ──
        if (!data.username || !data.full_name) {
            const ogTitle = document.querySelector('meta[property="og:title"]');
            if (ogTitle) {
                const match = ogTitle.content.match(/^(.+?)\\s*\\(@([\\w.]+)\\)/);
                if (match) {
                    if (!data.full_name) data.full_name = match[1].trim();
                    if (!data.username) data.username = match[2];
                }
            }
        }

        if (!data.followers) {
            const ogDesc = document.querySelector('meta[property="og:description"]');
            if (ogDesc) {
                const desc = ogDesc.content;
                const followersMatch = desc.match(/([\d,.]+[KMB]?)\\s*Followers/i);
                const followingMatch = desc.match(/([\d,.]+[KMB]?)\\s*Following/i);
                const postsMatch = desc.match(/([\d,.]+[KMB]?)\\s*Posts/i);
                if (followersMatch) data.followers = followersMatch[1];
                if (followingMatch) data.following = followingMatch[1];
                if (postsMatch) data.posts_count = postsMatch[1];

                if (!data.bio) {
                    const parts = desc.split(' - ');
                    if (parts.length > 1) {
                        data.bio = parts.slice(1).join(' - ').trim().replace(/^"/, '').replace(/"$/, '');
                    }
                }
            }
        }

        if (!data.profile_pic_url) {
            const ogImage = document.querySelector('meta[property="og:image"]');
            if (ogImage) data.profile_pic_url = ogImage.content;
        }

        // ── Strategy 3: DOM fallback for verified badge ──
        if (!data.is_verified) {
            const verified = document.querySelector(
                'span[title="Verified"], svg[aria-label="Verified"], [data-testid="verified-badge"]'
            );
            data.is_verified = !!verified;
        }

        return data;
    }""")


async def setup_video_interceptor(page) -> list[str]:
    """Set up network request interception to capture video CDN URLs."""
    intercepted_urls = []

    async def _on_response(response):
        url = response.url
        content_type = response.headers.get("content-type", "")
        is_video_url = (
            "video" in content_type
            or url.endswith(".mp4")
            or "/video/" in url
            or ("cdninstagram.com" in url and ("video" in url or ".mp4" in url))
            or ("fbcdn.net" in url and ("video" in url or ".mp4" in url))
        )
        if is_video_url and url not in intercepted_urls:
            intercepted_urls.append(url)

    page.on("response", _on_response)
    return intercepted_urls


async def extract_post_data(page) -> dict:
    """Extract post data from Instagram's embedded JSON payloads.

    Primary strategy: parse <script type="application/json"> tags and search for
    the post object matching the current shortcode. All post data lives in these
    ScheduledServerJS payloads, not in the DOM.
    Fallback: og: meta tags.
    """
    return await page.evaluate("""() => {
        const data = {
            // ── Identifiers ──
            shortcode: '',
            post_id: '',
            ig_id: '',           // full pk_userid format

            // ── Content ──
            caption: '',
            caption_is_edited: false,
            timestamp: '',
            caption_created_at: '',
            alt_text: '',        // AI-generated accessibility caption

            // ── Engagement ──
            likes: '',
            comments_count: '',
            video_view_count: '',
            play_count: '',
            like_and_view_counts_disabled: false,

            // ── Media ──
            media_urls: [],
            media_type: 'image',  // image, video, carousel
            product_type: '',     // feed, clips (reel), igtv
            is_carousel: false,
            is_reel: false,
            carousel_count: 0,
            original_width: '',
            original_height: '',
            display_uri: '',      // primary display thumbnail
            number_of_qualities: 0,

            // ── Video-specific ──
            video_url: '',
            video_duration: '',
            video_width: '',
            video_height: '',
            video_poster: '',
            has_audio: false,
            is_dash_eligible: false,

            // ── Audio / Music (Reels) ──
            audio_type: '',           // original_sounds, licensed_music
            music_title: '',
            music_artist: '',
            music_duration_ms: '',
            original_sound_author: '',

            // ── Location ──
            location: '',
            location_id: '',

            // ── Author ──
            author: '',
            author_full_name: '',
            author_pic: '',
            author_verified: false,
            author_id: '',
            author_is_private: false,

            // ── Collaboration ──
            coauthor_producers: [],
            is_paid_partnership: false,

            // ── Tagged users ──
            usertags: [],

            // ── Parsed from caption ──
            hashtags: [],
            mentions: [],

            // ── Comments ──
            comments: [],
        };

        // Detect reel from URL
        if (window.location.pathname.includes('/reel/')) {
            data.is_reel = true;
        }

        // Extract shortcode from URL
        const urlMatch = window.location.pathname.match(/\\/(?:p|reel)\\/([A-Za-z0-9_-]+)/);
        if (urlMatch) data.shortcode = urlMatch[1];

        // ── Find post object in embedded JSON ──
        function findPostItem(obj, shortcode, depth) {
            if (!obj || typeof obj !== 'object' || depth > 15) return null;
            if (typeof obj === 'object' && !Array.isArray(obj)) {
                if ((obj.code === shortcode || obj.shortcode === shortcode)
                    && (obj.taken_at || obj.media_type !== undefined)) {
                    return obj;
                }
                if (Array.isArray(obj.items)) {
                    for (const item of obj.items) {
                        if (item && (item.code === shortcode || item.shortcode === shortcode)) {
                            return item;
                        }
                    }
                }
            }
            const values = Array.isArray(obj) ? obj : Object.values(obj);
            for (const val of values) {
                const found = findPostItem(val, shortcode, depth + 1);
                if (found) return found;
            }
            return null;
        }

        let postObj = null;
        const scripts = document.querySelectorAll('script[type="application/json"]');
        for (const script of scripts) {
            const text = script.textContent;
            if (!text || text.length < 500) continue;
            if (data.shortcode && !text.includes(data.shortcode)) continue;
            try {
                const json = JSON.parse(text);
                postObj = findPostItem(json, data.shortcode, 0);
                if (postObj) break;
            } catch (e) {}
        }

        if (postObj) {
            // ── Identifiers ──
            data.shortcode = postObj.code || postObj.shortcode || data.shortcode;
            data.post_id = String(postObj.pk || '');
            data.ig_id = postObj.id ? String(postObj.id) : '';

            // ── Timestamps ──
            if (postObj.taken_at) {
                data.timestamp = new Date(postObj.taken_at * 1000).toISOString();
            }
            const captionObj = postObj.caption;
            if (captionObj && typeof captionObj === 'object' && captionObj.created_at) {
                data.caption_created_at = new Date(captionObj.created_at * 1000).toISOString();
            }

            // ── Engagement ──
            if (postObj.like_count !== undefined && postObj.like_count !== null) {
                data.likes = String(postObj.like_count);
            }
            if (postObj.comment_count !== undefined && postObj.comment_count !== null) {
                data.comments_count = String(postObj.comment_count);
            }
            if (postObj.view_count !== undefined && postObj.view_count !== null) {
                data.video_view_count = String(postObj.view_count);
            }
            if (postObj.play_count !== undefined && postObj.play_count !== null) {
                data.play_count = String(postObj.play_count);
            }
            data.like_and_view_counts_disabled = !!postObj.like_and_view_counts_disabled;

            // ── Caption ──
            if (captionObj) {
                if (typeof captionObj === 'string') {
                    data.caption = captionObj;
                } else if (captionObj.text) {
                    data.caption = captionObj.text;
                }
            }
            data.caption_is_edited = !!postObj.caption_is_edited;
            data.alt_text = postObj.accessibility_caption || '';

            // ── Author ──
            const user = postObj.user || postObj.owner || {};
            data.author = user.username || '';
            data.author_full_name = user.full_name || '';
            data.author_pic = user.hd_profile_pic_url_info?.url || user.profile_pic_url || '';
            data.author_verified = !!user.is_verified;
            data.author_id = String(user.pk || user.id || '');
            data.author_is_private = !!user.is_private;

            // ── Location ──
            if (postObj.location && typeof postObj.location === 'object') {
                data.location = postObj.location.name || postObj.location.short_name || '';
                data.location_id = String(postObj.location.pk || postObj.location.id || '');
            }

            // ── Media type: 1=image, 2=video/reel, 8=carousel ──
            const mt = postObj.media_type;
            if (mt === 2 || postObj.product_type === 'clips') {
                data.media_type = 'video';
                data.is_reel = postObj.product_type === 'clips' || data.is_reel;
            } else if (mt === 8) {
                data.media_type = 'carousel';
                data.is_carousel = true;
            } else {
                data.media_type = 'image';
            }
            data.product_type = postObj.product_type || '';

            // ── Original dimensions ──
            if (postObj.original_width) data.original_width = String(postObj.original_width);
            if (postObj.original_height) data.original_height = String(postObj.original_height);
            data.display_uri = postObj.display_uri || '';
            data.number_of_qualities = postObj.number_of_qualities || 0;

            // ── Video metadata ──
            if (postObj.video_versions && postObj.video_versions.length > 0) {
                const best = postObj.video_versions[0];
                data.video_url = best.url || '';
                data.video_width = String(best.width || '');
                data.video_height = String(best.height || '');
                data.media_urls.push({ type: 'video', url: best.url });
            }
            if (postObj.video_duration) {
                data.video_duration = String(Math.round(postObj.video_duration)) + 's';
            }
            data.has_audio = !!postObj.has_audio;
            data.is_dash_eligible = !!postObj.is_dash_eligible;

            // ── Thumbnail / poster from image_versions2 ──
            if (postObj.image_versions2?.candidates?.length > 0) {
                const best = postObj.image_versions2.candidates[0];
                data.video_poster = best.url || '';
                if (data.media_type === 'image') {
                    data.media_urls.push({ type: 'image', url: best.url });
                }
            }
            // Fallback poster from display_uri
            if (!data.video_poster && data.display_uri) {
                data.video_poster = data.display_uri;
            }

            // ── Audio / Music (Reels) ──
            const cm = postObj.clips_metadata;
            if (cm) {
                data.audio_type = cm.audio_type || '';

                if (cm.music_info?.music_asset_info) {
                    const track = cm.music_info.music_asset_info;
                    data.music_title = track.title || '';
                    data.music_artist = track.display_artist || '';
                    if (track.duration_in_ms) data.music_duration_ms = String(track.duration_in_ms);
                }

                if (cm.original_sound_info?.ig_artist) {
                    data.original_sound_author = cm.original_sound_info.ig_artist.username || '';
                }
            }

            // ── Carousel items ──
            if (postObj.carousel_media && Array.isArray(postObj.carousel_media)) {
                data.carousel_count = postObj.carousel_media.length;
                data.is_carousel = true;
                data.media_type = 'carousel';
                data.media_urls = [];
                for (const slide of postObj.carousel_media) {
                    if (slide.video_versions?.length > 0) {
                        data.media_urls.push({
                            type: 'video',
                            url: slide.video_versions[0].url,
                            width: slide.video_versions[0].width,
                            height: slide.video_versions[0].height,
                        });
                    } else if (slide.image_versions2?.candidates?.length > 0) {
                        const img = slide.image_versions2.candidates[0];
                        data.media_urls.push({
                            type: 'image',
                            url: img.url,
                            width: img.width,
                            height: img.height,
                        });
                    }
                }
            }

            // ── Collaboration ──
            data.is_paid_partnership = !!postObj.is_paid_partnership;
            if (postObj.coauthor_producers && Array.isArray(postObj.coauthor_producers)) {
                data.coauthor_producers = postObj.coauthor_producers.map(u => ({
                    username: u.username || '',
                    full_name: u.full_name || '',
                    id: String(u.pk || u.id || ''),
                }));
            }

            // ── Tagged users ──
            if (postObj.usertags?.in) {
                data.usertags = postObj.usertags.in.map(t => ({
                    username: t.user?.username || '',
                    full_name: t.user?.full_name || '',
                    id: String(t.user?.pk || t.user?.id || ''),
                    position: t.position || [],
                }));
            }

            // ── Comments ──
            const commentsList = postObj.comments || postObj.preview_comments || [];
            if (Array.isArray(commentsList)) {
                data.comments = commentsList.slice(0, 20).map(c => ({
                    username: c.user?.username || '',
                    text: c.text || '',
                    likes: c.comment_like_count || 0,
                    timestamp: c.created_at ? new Date(c.created_at * 1000).toISOString() : '',
                    id: String(c.pk || ''),
                }));
            }
        }

        // ── Strategy 2: Meta tag fallback ──
        if (!data.caption || !data.likes) {
            const ogDesc = document.querySelector('meta[property="og:description"]');
            if (ogDesc) {
                const desc = ogDesc.content;
                if (!data.likes) {
                    const m = desc.match(/([\d,.]+[KMB]?)\\s*likes?/i);
                    if (m) data.likes = m[1];
                }
                if (!data.comments_count) {
                    const m = desc.match(/([\d,.]+[KMB]?)\\s*comments?/i);
                    if (m) data.comments_count = m[1];
                }
                if (!data.author) {
                    const m = desc.match(/- ([\\w.]+) on /);
                    if (m) data.author = m[1];
                }
                if (!data.caption) {
                    const m = desc.match(/:\\s*["\u201c](.+)/);
                    if (m) data.caption = m[1].replace(/["\u201d]$/, '').trim();
                }
            }
        }

        if (!data.author_full_name) {
            const ogTitle = document.querySelector('meta[property="og:title"]');
            if (ogTitle) {
                const m = ogTitle.content.match(/^(.+?)\\s*(?:on Instagram|\\(@)/);
                if (m) data.author_full_name = m[1].trim();
            }
        }

        if (!data.video_poster && !data.display_uri) {
            const ogImage = document.querySelector('meta[property="og:image"]');
            if (ogImage) data.video_poster = ogImage.content;
        }

        if (!data.timestamp) {
            const timeEl = document.querySelector('time[datetime]');
            if (timeEl) data.timestamp = timeEl.getAttribute('datetime');
        }

        // ── Parse hashtags and mentions from caption ──
        if (data.caption) {
            const hm = data.caption.match(/#[\\w\\u00C0-\\u024F]+/g);
            if (hm) data.hashtags = [...new Set(hm)];
            const mm = data.caption.match(/@[\\w.]+/g);
            if (mm) data.mentions = [...new Set(mm)];
        }

        return data;
    }""")


async def trigger_video_playback(page):
    """Click play on video posts to force CDN request for network interceptor."""
    await page.evaluate("""async () => {
        const playBtns = document.querySelectorAll(
            'button[aria-label="Play"],'
            + 'button[aria-label="Toggle audio"],'
            + 'div[role="button"][aria-label*="Play"],'
            + '[aria-label="Control"]'
        );
        for (const btn of playBtns) {
            try { btn.click(); } catch (e) {}
        }
        const videos = document.querySelectorAll('article video, main video, [role="presentation"] video');
        for (const vid of videos) {
            try { if (vid.paused) vid.play(); } catch (e) {}
        }
        await new Promise(r => setTimeout(r, 2000));
        for (const vid of document.querySelectorAll('video')) {
            try { vid.pause(); } catch (e) {}
        }
    }""")


async def navigate_carousel(page, intercepted_video_urls: list[str] | None = None) -> list[dict]:
    """Click through carousel arrows collecting all media URLs."""
    media = await page.evaluate("""async () => {
        const allMedia = [];
        const seen = new Set();

        function collectCurrentMedia() {
            document.querySelectorAll('article img[src*="cdninstagram"], article img[src*="fbcdn"]').forEach(img => {
                if ((img.width > 100 || img.naturalWidth > 100 || !img.width)
                    && !img.src.startsWith('blob:') && !seen.has(img.src)) {
                    seen.add(img.src);
                    allMedia.push({ type: 'image', url: img.src });
                }
            });
            document.querySelectorAll('article video source, article video').forEach(vid => {
                const src = vid.src || vid.querySelector?.('source')?.src;
                if (src && !src.startsWith('blob:') && !seen.has(src)) {
                    seen.add(src);
                    allMedia.push({ type: 'video', url: src });
                }
            });
            // Trigger video playback for interceptor
            document.querySelectorAll('article video').forEach(vid => {
                try { if (vid.paused) vid.play(); } catch (e) {}
            });
        }

        collectCurrentMedia();

        let maxClicks = 20;
        while (maxClicks-- > 0) {
            const nextBtn = document.querySelector(
                'button[aria-label="Next"],'
                + 'button[aria-label="Go to next image"],'
                + 'button[class*="next"]'
            );
            if (!nextBtn || nextBtn.disabled) break;
            nextBtn.click();
            await new Promise(r => setTimeout(r, 1200));
            collectCurrentMedia();
        }

        document.querySelectorAll('video').forEach(v => { try { v.pause(); } catch(e) {} });
        return allMedia;
    }""")

    if intercepted_video_urls:
        dom_urls = {m["url"] for m in media}
        for url in intercepted_video_urls:
            if url not in dom_urls:
                media.append({"type": "video", "url": url})

    return media
