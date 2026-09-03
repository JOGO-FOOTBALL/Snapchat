"""Shared logic + UI for the Story and Spotlight publisher pages
(app_pages/story_publisher.py, app_pages/spotlight_publisher.py) - permalink
lookup, slide preview/selection, and the publish flow, parameterized by
destination (post_story vs post_spotlight) since everything else is identical.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import streamlit as st

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


def load_log(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_log(path: Path, log: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2, default=str))


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
    name_prefix: str, post_one: Callable[[str, str], dict],
) -> dict:
    """Uploads + publishes each selected slide in order. `post_one(access_token,
    media_id)` does the destination-specific call (post_story / post_spotlight)
    and returns its response dict."""
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
        result = post_one(access_token, media["media_id"])
        posted.append({"media_id": media["media_id"], "request_id": result.get("request_id")})

    return {
        "status": "posted",
        "post_id": post["id"],
        "permalink": permalink,
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "published_by": username,
        "snaps": posted,
    }


def render_publisher_page(
    *,
    title: str,
    subtitle: str,
    session_key: str,
    log_path: Path,
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

    log = load_log(log_path)

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
                st.success(f"Already posted on {already['posted_at']}{suffix} · {len(already['snaps'])} snap(s)")
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
                        result = publish_slides(permalink, post, selected_slides, username, name_prefix, post_one)
                        log[post["id"]] = result
                        save_log(log_path, log)
                        st.success(f"Posted - {len(result['snaps'])} snap(s)")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Posting failed: {e}")


def publish_db_item(
    item: dict, username: str, name_prefix: str,
    post_one: Callable[[str, str], dict] = post_story,
) -> dict:
    """Uploads + posts one DB-sourced item's own media_url/media_type via
    `post_one` (post_story by default, or post_spotlight - see
    app_pages/posts_publisher_spotlight.py). Returns the log entry to store
    under str(item['id'])."""
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
    result = post_one(access_token, media["media_id"])
    return {
        "status": "posted",
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "published_by": username,
        "media_id": media["media_id"],
        "request_id": result.get("request_id"),
    }


def render_db_browser_page(
    *,
    title: str,
    subtitle: str,
    log_path: Path,
    username: str,
    name_prefix: str,
    fetch_items: Callable[[], list[dict]],
    clear_cache: Callable[[], None],
) -> None:
    """Renders a page that browses many independent DB-backed items at once
    (Stories/Posts publisher) - each with a lightweight thumbnail preview and
    an opt-in checkbox (unchecked by default, unlike render_publisher_page's
    per-slide checkboxes, since dozens of items can be listed here at once).
    `fetch_items()` returns dicts with at least: id, media_type, media_url,
    thumbnail_url_abs, timestamp_utc, and an optional `note` shown under the
    thumbnail (e.g. flagging a carousel's cover-only limitation)."""
    with st.container(horizontal=True, vertical_alignment="center"):
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=48)
        st.title(title)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(subtitle)
        if st.button("Refresh", key=f"{name_prefix}_refresh", icon=":material/refresh:"):
            clear_cache()
            st.rerun()

    items = fetch_items()
    log = load_log(log_path)

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
                    log[item_key] = publish_db_item(item, username, name_prefix)
                    save_log(log_path, log)
                except Exception as e:
                    st.error(f"Failed to post {item_key}: {e}")


def render_posts_grid_page(
    *,
    title: str,
    subtitle: str,
    log_path: Path,
    username: str,
    name_prefix: str,
    ig_user_id: int,
    post_one: Callable[[str, str], dict],
    video_only: bool = False,
    first_batch: int = 15,
    total: int = 100,
) -> None:
    """Renders the Posts publisher grid (see app_pages/posts_publisher.py and
    posts_publisher_spotlight.py, its Story and Spotlight variants) - browses
    socials_analytics.instagram_posts (db.fetch_instagram_posts_page) and
    pushes selected items via `post_one` (post_story or post_spotlight).

    Loads in two batches: the live media_url check in db.py is what makes a
    full fetch slow, so a small first batch renders fast and the rest streams
    in right after instead of blocking the whole grid on it.

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
                # Vertical alignment with "Push all slides" (see the
                # margin-top on this button's key in streamlit_app.py)
                # rather than a spacer element - st.container(height=..)
                # enforces its own minimum height, which threw off precise
                # alignment.
                push_label = "Push Reel" if is_video else "Push"
                if st.button(push_label, key=f"{name_prefix}_push_{item_key}", icon=":material/send:", type="primary"):
                    with st.spinner("Posting..."):
                        try:
                            log[item_key] = publish_db_item(item, username, name_prefix, post_one)
                            save_log(log_path, log)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to post: {e}")
                return

            if st.button("Push cover slide", key=f"{name_prefix}_cover_{item_key}", icon=":material/image:"):
                with st.spinner("Posting cover..."):
                    try:
                        log[item_key] = publish_db_item(item, username, name_prefix, post_one)
                        save_log(log_path, log)
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
                            result = publish_slides(
                                item["permalink"], found["post"], found["slides"], username,
                                f"{name_prefix}_full", post_one,
                            )
                            log[item_key] = result
                            save_log(log_path, log)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to post: {e}")

    with st.container(horizontal=True, vertical_alignment="center"):
        if LOGO_PATH.exists():
            st.image(str(LOGO_PATH), width=48)
        st.title(title)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(subtitle)
        if st.button("Refresh", key=f"{name_prefix}_refresh", icon=":material/refresh:"):
            fetch_instagram_posts_page.clear()
            st.rerun()

    log = load_log(log_path)

    # video_only also filters at the SQL level (not just hiding non-video
    # cards in _render_card) - Spotlight's recent posting mix is often mostly
    # image carousels, so a plain "last N posts" page can come back with
    # almost no reels even though thousands exist further back.
    first_posts = fetch_instagram_posts_page(ig_user_id, 0, first_batch, reels_only=video_only)
    if not first_posts:
        st.info("Nothing found.")

    # One continuous grid, not two separate containers - otherwise, whenever
    # the first batch doesn't end on an exact row boundary, its last
    # (partial) row stays visibly short instead of the second batch flowing
    # up to fill it.
    with st.container(horizontal=True, gap=16):
        for post in first_posts:
            _render_card(post, log)

        if len(first_posts) == first_batch:
            with st.spinner("Loading more..."):
                rest_posts = fetch_instagram_posts_page(
                    ig_user_id, first_batch, total - first_batch, reels_only=video_only,
                )
            for post in rest_posts:
                _render_card(post, log)
