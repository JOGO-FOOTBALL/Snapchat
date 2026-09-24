"""Shared logic + UI for the Story and Spotlight publisher pages
(app_pages/story_publisher.py, app_pages/spotlight_publisher.py) - permalink
lookup, slide preview/selection, and the publish flow, parameterized by
destination (post_story vs post_spotlight) since everything else is identical.
"""

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import streamlit as st

from config import ACCOUNT_CHANNEL, IG_USER_IDS
from db import get_connection
from Snapchat_Repost import (
    FIELDS,
    get_ig_json,
    find_post_by_permalink,
    extract_media_items,
    download_media,
    fit_image_to_story,
    fit_video_to_story,
    encrypt_media,
    create_media,
    upload_media,
    post_story,
    get_access_token,
)
from db import fetch_instagram_posts_page

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "Logo433.png"

# Default channel for the Posts/Stories publisher pages' channel filter below
# - Main, the same account config.IG_USER_IDS scans automatically.
DEFAULT_CHANNEL_ID = int(IG_USER_IDS[0])


def channel_selector(key: str) -> int:
    """Channel filter for the Posts/Stories publisher pages (browsing
    socials_analytics data, which spans all of 433's IG accounts in
    config.ACCOUNT_CHANNEL - unlike the automated Reels scan, which only
    ever looks at Main). Defaults to Main; returns the chosen IG user id.

    Narrow fixed width + a collapsed label (rather than the default full-width,
    labeled selectbox) so it sits compactly inline next to the subtitle/Refresh
    button instead of on its own full-width row."""
    ids_by_name = {name: int(uid) for uid, name in ACCOUNT_CHANNEL.items()}
    names = list(ids_by_name)
    default_name = ACCOUNT_CHANNEL[str(DEFAULT_CHANNEL_ID)]
    with st.container(width=140):
        chosen = st.selectbox(
            "Channel", names, index=names.index(default_name), key=key,
            label_visibility="collapsed",
        )
    return ids_by_name[chosen]


# ---------------- DB-backed publish log ----------------

class AlreadyPublishedError(Exception):
    """Raised when content has already been posted to the target destination."""


def load_log(destination: str) -> dict:
    """Load publish log from the DB for a destination, across ALL sources.
    Returns a dict keyed by ig_content_id. When the same content has rows
    from multiple sources, the 'posted' row wins (then most recently updated)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (p.ig_content_id)
                       p.ig_content_id, p.status, p.source, p.permalink, p.published_by,
                       p.snapchat_media_id, p.snapchat_request_id, p.posted_at,
                       (SELECT COUNT(*) FROM snapchat.publish_log_snaps s
                        WHERE s.publish_log_id = p.id) AS snap_count
                FROM snapchat.publish_log p
                WHERE p.destination = %s
                ORDER BY p.ig_content_id,
                         CASE WHEN p.status = 'posted' THEN 0 ELSE 1 END,
                         p.updated_at DESC
                """,
                (destination,),
            )
            return {row["ig_content_id"]: dict(row) for row in cur.fetchall()}
    finally:
        conn.close()


def save_log_entry(
    ig_content_id: str, source: str, destination: str, *,
    status: str = "posted",
    permalink: str | None = None,
    channel: str | None = None,
    published_by: str | None = None,
    snapchat_media_id: str | None = None,
    snapchat_request_id: str | None = None,
    posted_at: datetime | None = None,
    error: str | None = None,
    snaps: list[dict] | None = None,
) -> None:
    """Upsert one publish log entry (and optional child snap rows)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO snapchat.publish_log
                    (ig_content_id, source, destination, status, permalink, channel,
                     published_by, snapchat_media_id, snapchat_request_id, posted_at, error, attempts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
                ON CONFLICT (ig_content_id, destination, source) DO UPDATE SET
                    status = EXCLUDED.status,
                    published_by = COALESCE(EXCLUDED.published_by, snapchat.publish_log.published_by),
                    snapchat_media_id = COALESCE(EXCLUDED.snapchat_media_id, snapchat.publish_log.snapchat_media_id),
                    snapchat_request_id = COALESCE(EXCLUDED.snapchat_request_id, snapchat.publish_log.snapchat_request_id),
                    posted_at = COALESCE(EXCLUDED.posted_at, snapchat.publish_log.posted_at),
                    error = EXCLUDED.error,
                    updated_at = now()
                RETURNING id
                """,
                (ig_content_id, source, destination, status, permalink, channel,
                 published_by, snapchat_media_id, snapchat_request_id, posted_at, error),
            )
            log_id = cur.fetchone()["id"]

            if snaps:
                cur.execute(
                    "DELETE FROM snapchat.publish_log_snaps WHERE publish_log_id = %s",
                    (log_id,),
                )
                for idx, snap in enumerate(snaps, start=1):
                    cur.execute(
                        """
                        INSERT INTO snapchat.publish_log_snaps
                            (publish_log_id, snap_index, snapchat_media_id, snapchat_request_id)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (log_id, idx, snap["media_id"], snap.get("request_id")),
                    )
            conn.commit()
    finally:
        conn.close()


# ---------------- Publish lock (dedup + concurrency) ----------------

def _publish_lock_key(ig_content_id: str, destination: str) -> int:
    """Deterministic signed int64 from (ig_content_id, destination) for pg_advisory_lock."""
    h = hashlib.md5(f"{ig_content_id}:{destination}".encode()).digest()
    return int.from_bytes(h[:8], byteorder="big", signed=True)


@contextmanager
def publish_lock(ig_content_id: str, destination: str):
    """Acquire a Postgres advisory lock on (content, destination) and verify
    the content hasn't already been posted (by ANY source). Prevents both
    concurrent races and cross-flow duplicates.

    Raises AlreadyPublishedError if the lock can't be acquired or the content
    is already posted."""
    conn = get_connection()
    key = _publish_lock_key(ig_content_id, destination)
    locked = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            locked = cur.fetchone()["pg_try_advisory_lock"]
            if not locked:
                raise AlreadyPublishedError(
                    f"Another process is already publishing this content to {destination}"
                )
            cur.execute(
                """
                SELECT source FROM snapchat.publish_log
                WHERE ig_content_id = %s AND destination = %s AND status = 'posted'
                LIMIT 1
                """,
                (ig_content_id, destination),
            )
            existing = cur.fetchone()
            if existing:
                raise AlreadyPublishedError(
                    f"Already posted to {destination} (via {existing['source']})"
                )
        yield
    finally:
        if locked:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
            except Exception:
                pass
        conn.close()


# ---------------- Helpers ----------------

def _is_video(item: dict) -> bool:
    return item["media_type"] in ("VIDEO", "REEL")


@st.cache_data(ttl="30m", show_spinner=False)
def process_slide(url: str, media_type: str) -> tuple[bytes, bool]:
    is_video = media_type in ("VIDEO", "REEL")
    raw = download_media(url)
    processed = fit_video_to_story(raw) if is_video else fit_image_to_story(raw)
    return processed, is_video


def lookup(permalink: str) -> dict:
    try:
        found = find_post_by_permalink(permalink)
        post = get_ig_json(f"/{found['id']}", {"fields": FIELDS})
        slides = extract_media_items(post)
        return {"post": post, "slides": slides, "error": None}
    except Exception as e:
        return {"post": None, "slides": [], "error": str(e)}


def publish_slides(
    permalink: str, post: dict, slides: list[dict], username: str,
    name_prefix: str, post_one: Callable[[str, str, str | None], dict],
    source: str, destination: str,
) -> dict:
    """Uploads + publishes each selected slide in order. `post_one(access_token,
    media_id, caption)` does the destination-specific call (post_story /
    post_spotlight) and returns its response dict. `caption` is the source
    Instagram post's caption - post_story ignores it (Post Story has no
    caption field), post_spotlight forwards it as `description` when it fits
    Snap's length limit. Saves the result to the DB.

    Acquires an advisory lock and checks for cross-source duplicates before
    calling the Snapchat API. Raises AlreadyPublishedError if already posted."""
    with publish_lock(post["id"], destination):
        access_token = get_access_token()
        posted = []
        for idx, item in enumerate(slides, start=1):
            is_video = _is_video(item)
            processed, _ = process_slide(item["url"], item["media_type"])
            ciphertext, key, iv = encrypt_media(processed)

            ext = "mp4" if is_video else "jpg"
            media = create_media(
                access_token, "VIDEO" if is_video else "IMAGE",
                name=f"{name_prefix}_{post['id']}_{idx}.{ext}", key=key, iv=iv,
            )
            upload_media(access_token, media["add_path"], media["finalize_path"], ciphertext)
            result = post_one(access_token, media["media_id"], post.get("caption"))
            posted.append({"media_id": media["media_id"], "request_id": result.get("request_id")})

        now = datetime.now(timezone.utc)
        save_log_entry(
            post["id"], source, destination,
            status="posted",
            permalink=permalink,
            published_by=username,
            posted_at=now,
            snaps=posted,
        )

    return {
        "status": "posted",
        "post_id": post["id"],
        "permalink": permalink,
        "posted_at": now.isoformat(),
        "published_by": username,
        "snaps": posted,
    }


def render_publisher_page(
    *,
    title: str,
    subtitle: str,
    session_key: str,
    source: str,
    destination: str,
    username: str,
    name_prefix: str,
    post_one: Callable[[str, str], dict],
    video_only: bool = False,
    video_only_notice: str = "",
) -> None:
    """Renders one full publisher page: permalink form, per-post preview with
    per-slide checkboxes (checked by default), and a publish button. `post_one`
    is the destination-specific single-slide publish call."""
    st.session_state.setdefault(session_key, {})
    found = st.session_state[session_key]

    with st.container(horizontal=True, vertical_alignment="center"):
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=48)
        st.title(title)
    st.caption(subtitle)

    with st.form(f"{session_key}_form", border=False):
        permalinks_text = st.text_area(
            "Permalinks",
            placeholder="https://www.instagram.com/reel/...\nhttps://www.instagram.com/p/...",
            height=120,
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("Search", icon=":material/search:")

    if submitted:
        permalinks = [line.strip() for line in permalinks_text.splitlines() if line.strip()]
        with st.spinner(f"Looking up {len(permalinks)} permalink(s) on Instagram..."):
            for permalink in permalinks:
                found[permalink] = lookup(permalink)

    log = load_log(destination)

    for permalink, entry in found.items():
        with st.container(border=True):
            st.markdown(f"[{permalink}]({permalink})")

            if entry["error"]:
                st.error(entry["error"])
                if st.button("Retry", key=f"{session_key}_retry_{permalink}", icon=":material/refresh:"):
                    found[permalink] = lookup(permalink)
                    st.rerun()
                continue

            post, all_slides = entry["post"], entry["slides"]
            st.caption(f"{post.get('media_type')} · {(post.get('caption') or '')[:150]}")

            already = log.get(post["id"])
            if already and already.get("status") == "posted":
                by = already.get("published_by")
                suffix = f" by {by}" if by else ""
                st.success(f"Already posted on {already['posted_at']}{suffix} · {already['snap_count']} snap(s)")
                continue

            if not all_slides:
                st.warning(
                    "Instagram's API isn't returning a direct video/photo link (`media_url`) for "
                    "this post yet, even though the post itself is live. This mostly happens with "
                    "videos/reels, shortly after they're posted - it's a known limitation on "
                    "Instagram/Meta's side, not something this tool can work around. It usually "
                    "resolves on its own within a few minutes to a few hours; try the button below "
                    "again then."
                )
                if st.button("Retry", key=f"{session_key}_retry_{permalink}", icon=":material/refresh:"):
                    found[permalink] = lookup(permalink)
                    st.rerun()
                continue

            slides = [s for s in all_slides if _is_video(s)] if video_only else all_slides
            skipped = len(all_slides) - len(slides)
            if skipped and video_only_notice:
                st.caption(video_only_notice.format(skipped=skipped))

            if not slides:
                st.warning("This post has no video content to publish here.")
                continue

            selected_slides = []
            with st.container(horizontal=True):
                for i, s in enumerate(slides, start=1):
                    processed, is_video = process_slide(s["url"], s["media_type"])
                    with st.container(width=170):
                        if is_video:
                            st.video(processed)
                        else:
                            st.image(processed)
                        checked = st.checkbox(
                            f"post slide {i}/{len(slides)}", value=True,
                            key=f"{session_key}_slide_sel_{post['id']}_{i}",
                        )
                        if checked:
                            selected_slides.append(s)

            if not selected_slides:
                st.caption("No slides selected - check at least 1 slide to post.")

            if st.button(
                f"Post ({len(selected_slides)}/{len(slides)} slide(s))",
                key=f"{session_key}_publish_{permalink}", icon=":material/send:", type="primary",
                disabled=not selected_slides,
            ):
                with st.spinner("Posting to Snapchat..."):
                    try:
                        result = publish_slides(
                            permalink, post, selected_slides, username,
                            name_prefix, post_one, source, destination,
                        )
                        st.success(f"Posted - {len(result['snaps'])} snap(s)")
                        st.rerun()
                    except AlreadyPublishedError as e:
                        st.info(str(e))
                        st.rerun()
                    except Exception as e:
                        st.error(f"Posting failed: {e}")


def publish_db_item(
    item: dict, username: str, name_prefix: str,
    source: str, destination: str,
    post_one: Callable[[str, str, str | None], dict] = lambda access_token, media_id, caption: post_story(access_token, media_id),
) -> dict:
    """Uploads + posts one DB-sourced item's own media_url/media_type via
    `post_one` (post_story by default, or post_spotlight) - see publish_slides
    for what `caption` means. Acquires an advisory lock and checks for
    cross-source duplicates before calling the Snapchat API. Saves the result
    to the DB. Raises AlreadyPublishedError if already posted."""
    content_id = str(item["id"])
    with publish_lock(content_id, destination):
        access_token = get_access_token()
        is_video = _is_video(item)
        processed, _ = process_slide(item["media_url"], item["media_type"])
        ciphertext, key, iv = encrypt_media(processed)

        ext = "mp4" if is_video else "jpg"
        media = create_media(
            access_token, "VIDEO" if is_video else "IMAGE",
            name=f"{name_prefix}_{item['id']}.{ext}", key=key, iv=iv,
        )
        upload_media(access_token, media["add_path"], media["finalize_path"], ciphertext)
        result = post_one(access_token, media["media_id"], item.get("caption"))

        now = datetime.now(timezone.utc)
        save_log_entry(
            content_id, source, destination,
            status="posted",
            permalink=item.get("permalink"),
            published_by=username,
            snapchat_media_id=media["media_id"],
            snapchat_request_id=result.get("request_id"),
            posted_at=now,
        )

    return {
        "status": "posted",
        "posted_at": now.isoformat(),
        "published_by": username,
        "media_id": media["media_id"],
        "request_id": result.get("request_id"),
    }


def render_db_browser_page(
    *,
    title: str,
    subtitle: str,
    source: str,
    destination: str,
    username: str,
    name_prefix: str,
    fetch_items: Callable[[int], list[dict]],
    clear_cache: Callable[[], None],
) -> None:
    """Renders a page that browses many independent DB-backed items at once
    (Stories/Posts publisher) - each with a lightweight thumbnail preview and
    an opt-in checkbox (unchecked by default, unlike render_publisher_page's
    per-slide checkboxes, since dozens of items can be listed here at once).
    `fetch_items(ig_user_id)` returns dicts with at least: id, media_type,
    media_url, thumbnail_url_abs, timestamp_utc, and an optional `note` shown
    under the thumbnail (e.g. flagging a carousel's cover-only limitation)."""
    with st.container(horizontal=True, vertical_alignment="center"):
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=48)
        st.title(title)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(subtitle)
        ig_user_id = channel_selector(key=f"{name_prefix}_channel")
        if st.button("Refresh", key=f"{name_prefix}_refresh", icon=":material/refresh:"):
            clear_cache()
            st.rerun()

    items = fetch_items(ig_user_id)
    log = load_log(destination)

    if not items:
        st.info("Nothing found.")

    selected = []
    with st.container(horizontal=True, gap=16):
        for item in items:
            item_key = str(item["id"])
            already = log.get(item_key)
            with st.container(width=170, border=True, key=f"db_card_{name_prefix}_{item_key}"):
                is_video = _is_video(item)
                # media_url is only image-safe for non-video items (for video
                # it's the actual .mp4); fall back to it only when the two
                # pre-generated thumbnail columns are both empty.
                thumb = item.get("thumbnail_url_abs") or item.get("thumbnail_url")
                if not thumb and not is_video:
                    thumb = item.get("media_url")
                with st.container(key=f"db_card_thumb_{name_prefix}_{item_key}"):
                    if thumb:
                        st.image(thumb)
                    else:
                        st.caption("(no preview)")
                st.caption(f"{'video' if is_video else 'image'} · {item['timestamp_utc'].strftime('%b %d, %H:%M')}")
                if item.get("note"):
                    st.caption(item["note"])
                if already and already.get("status") == "posted":
                    st.caption(":material/check_circle: already posted")
                else:
                    checked = st.checkbox("Push", value=False, key=f"{name_prefix}_sel_{item_key}")
                    if checked:
                        selected.append(item)

    if st.button(
        f"Post selected to Snapchat Story ({len(selected)})",
        key=f"{name_prefix}_publish", icon=":material/send:", type="primary",
        disabled=not selected,
    ):
        with st.spinner(f"Posting {len(selected)} item(s) to Snapchat..."):
            for item in selected:
                item_key = str(item["id"])
                try:
                    publish_db_item(item, username, name_prefix, source, destination)
                except AlreadyPublishedError:
                    st.info(f"{item_key} was already posted")
                except Exception as e:
                    st.error(f"Failed to post {item_key}: {e}")


def render_posts_grid_page(
    *,
    title: str,
    subtitle: str,
    source: str,
    destination: str,
    username: str,
    name_prefix: str,
    post_one: Callable[[str, str], dict],
    video_only: bool = False,
    first_batch: int = 15,
    total: int | None = 100,
    lookback_days: int | None = None,
) -> None:
    """Renders the Posts publisher grid (see app_pages/posts_publisher.py and
    posts_publisher_spotlight.py, its Story and Spotlight variants) - browses
    socials_analytics.instagram_posts (db.fetch_instagram_posts_page) for the
    channel picked via channel_selector (defaults to Main) and pushes
    selected items via `post_one` (post_story or post_spotlight).

    Loads in two batches: the live media_url check in db.py is what makes a
    full fetch slow, so a small first batch renders fast and the rest streams
    in right after instead of blocking the whole grid on it. total=None
    fetches every remaining row past first_batch instead of capping at a
    fixed page size - slower, but nothing with a live media_url is hidden
    just for sitting further back than the cap. Pair total=None with
    lookback_days (see db.fetch_instagram_posts_page) to bound the SQL window
    itself - otherwise "no cap" on an account with years of history means
    thousands of live media_url checks per load.

    Carousels get two actions (push the cover only vs. fetch+push every
    slide via the Graph API, see publish_slides) when video_only is False.
    When True (Spotlight - video-only, no multi-slide concept), non-video
    posts are skipped with a note instead - CAROUSEL_ALBUM is its own
    media_type distinct from VIDEO/REEL, so this also naturally excludes
    carousels without a separate flag for it."""

    def _render_card(post: dict, log: dict) -> None:
        item_key = str(post["post_id"])
        item = {**post, "id": post["post_id"]}
        is_video = _is_video(item)
        is_carousel = item["media_type"] == "CAROUSEL_ALBUM"
        already = log.get(item_key)

        # Checked before rendering anything (not after showing the preview,
        # then a "not supported" note) - Spotlight only wants reels in the
        # grid at all, not a card explaining why it's skipped.
        if video_only and not is_video:
            return

        with st.container(width=170, border=True, key=f"db_card_{name_prefix}_{item_key}"):
            thumb = item.get("thumbnail_url_abs") or item.get("thumbnail_url")
            if not thumb and not is_video:
                thumb = item.get("media_url")
            with st.container(key=f"db_card_thumb_{name_prefix}_{item_key}"):
                if thumb:
                    st.image(thumb)
                else:
                    st.caption("(no preview)")
            with st.container(key=f"db_card_caption_{name_prefix}_{item_key}"):
                # Always shown as the /p/ form - Instagram treats /p/<code>/
                # and /reel/<code>/ as interchangeable for the same content
                # (see _normalize_permalink in Snapchat_Repost.py).
                permalink = (item.get("permalink") or "").replace("/reel/", "/p/")
                st.caption(f"[Link]({permalink})")

            if already and already.get("status") == "posted":
                st.caption(":material/check_circle: already posted")
                return

            if not is_carousel:
                push_label = "Push Reel" if is_video else "Push"
                if st.button(push_label, key=f"{name_prefix}_push_{item_key}", icon=":material/send:", type="primary"):
                    with st.spinner("Posting..."):
                        try:
                            publish_db_item(item, username, name_prefix, source, destination, post_one)
                            st.rerun()
                        except AlreadyPublishedError as e:
                            st.info(str(e))
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to post: {e}")
                return

            if st.button("Push cover slide", key=f"{name_prefix}_cover_{item_key}", icon=":material/image:"):
                with st.spinner("Posting cover..."):
                    try:
                        publish_db_item(item, username, name_prefix, source, destination, post_one)
                        st.rerun()
                    except AlreadyPublishedError as e:
                        st.info(str(e))
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to post: {e}")

            if st.button(
                "Push all slides", key=f"{name_prefix}_all_{item_key}",
                icon=":material/burst_mode:", type="primary",
            ):
                with st.spinner("Fetching all slides..."):
                    found = lookup(item["permalink"])
                if found["error"]:
                    st.error(found["error"])
                elif not found["slides"]:
                    st.warning("No postable media (media_url missing).")
                else:
                    with st.spinner(f"Posting {len(found['slides'])} slide(s)..."):
                        try:
                            publish_slides(
                                item["permalink"], found["post"], found["slides"], username,
                                f"{name_prefix}_full", post_one, source, destination,
                            )
                            st.rerun()
                        except AlreadyPublishedError as e:
                            st.info(str(e))
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to post: {e}")

    with st.container(horizontal=True, vertical_alignment="center"):
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=48)
        st.title(title)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(subtitle)
        ig_user_id = channel_selector(key=f"{name_prefix}_channel")
        if st.button("Refresh", key=f"{name_prefix}_refresh", icon=":material/refresh:"):
            fetch_instagram_posts_page.clear()
            st.rerun()

    log = load_log(destination)

    # video_only also filters at the SQL level (not just hiding non-video
    # cards in _render_card) - Spotlight's recent posting mix is often mostly
    # image carousels, so a plain "last N posts" page can come back with
    # almost no reels even though thousands exist further back.
    first_posts = fetch_instagram_posts_page(
        ig_user_id, 0, first_batch, reels_only=video_only, lookback_days=lookback_days,
    )
    if not first_posts:
        st.info("Nothing found.")

    # One continuous grid, not two separate containers - otherwise, whenever
    # the first batch doesn't end on an exact row boundary, its last
    # (partial) row stays visually short instead of the second batch flowing
    # up to fill it.
    with st.container(horizontal=True, gap=16):
        for post in first_posts:
            _render_card(post, log)

        # Always fetches the rest, regardless of how many of the first batch
        # came back live - len(first_posts) is post-live-filter, so any dead
        # link in that small first batch (the common case) would silently
        # drop it below first_batch and skip the second fetch entirely, even
        # though plenty more (live) rows exist past offset first_batch.
        if total is None or total > first_batch:
            with st.spinner("Loading more..."):
                rest_limit = None if total is None else total - first_batch
                rest_posts = fetch_instagram_posts_page(
                    ig_user_id, first_batch, rest_limit, reels_only=video_only, lookback_days=lookback_days,
                )
            for post in rest_posts:
                _render_card(post, log)
