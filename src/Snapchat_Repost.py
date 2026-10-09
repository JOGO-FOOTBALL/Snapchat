"""
Shared library for reposting Instagram content to Snapchat Stories/Spotlight
on 433's Public Profile via the Snapchat Business API. Not a script to run
directly - it's imported by:
- The Streamlit app (streamlit_app.py, app_pages/, publish_helpers.py) - the
  Story/Spotlight publisher UIs.
- Snapchat_Reels_Autopublish.py - the unattended Reels-to-Spotlight pipeline.

Requires a Snapchat OAuth app created via Ads Manager > Business Dashboard
(NOT the regular Developer Portal - that Client ID does not work with the
Public Profile API), allowlisted by Snap for the Public Profile API, plus
SNAPCHAT_PROFILE_ID for the target Public Profile. SNAPCHAT_CLIENT_ID/SECRET/
REFRESH_TOKEN come from that app's OAuth flow (authorize -> exchange the
code for a refresh token) - if the app came from the Developer Portal
instead, publishing calls below will fail and a new Business Dashboard app
+ OAuth exchange is needed.

A carousel album posts every slide as its own consecutive Snap, in the same
order as on Instagram (extract_media_items). Media is first letterboxed to a 9:16 canvas (fit_image_to_story /
fit_video_to_story) so Instagram source media - which is rarely already
9:16 - fills the Story without an unpredictable center-crop. It's then AES-
256-CBC encrypted client-side before upload, and uploaded in <=32MB chunks.
This script implements that against Snap's documented Create Media /
multipart upload / Post Story flow, but the exact JSON response shape of
Create Media was not independently verified against a live response while
writing this - if `create_media()` KeyErrors, adjust the field lookup there
to match the real payload.
"""

import base64
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image, ImageEnhance, ImageFilter

from config import (
    ALL_IG_USER_IDS,
    GRAPH,
    IG_USER_IDS,
    Secrets,
    SNAPCHAT_CLIENT_ID,
    SNAPCHAT_CLIENT_SECRET,
    SNAPCHAT_PROFILE_ID,
    SNAPCHAT_REFRESH_TOKEN,
    SNAPCHAT_TOKEN_URL,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ACCESS_TOKEN = Secrets.META_API_TOKEN
FIELDS = (
    "id,media_type,caption,permalink,timestamp,media_url,thumbnail_url,"
    "children{media_type,media_url,thumbnail_url}"
)
LIMIT = 50

BUSINESS_API_BASE = "https://businessapi.snapchat.com"
# Snap's cap is exactly 32MiB (33554432 bytes), but each chunk goes out inside
# a multipart/form-data body (boundary + headers), which adds a little on top
# of the raw chunk - a chunk sized at exactly the cap was getting rejected
# ("Media size is too large"). Leave headroom for that framing overhead.
MAX_CHUNK_SIZE = 31 * 1024 * 1024

# (connect, read) timeouts in seconds for Business API calls. Snap regularly
# takes over a minute to answer FINALIZE and the Post Story/Spotlight calls
# (it's processing the media server-side), so a flat 60s read timeout was
# failing posts that would have gone through. Connect stays short so a real
# outage still fails fast.
SNAP_TIMEOUT = (10, 180)
SNAP_UPLOAD_TIMEOUT = (10, 300)

# A timeout on the publish call itself is deliberately NOT retried: Snap may
# have accepted the post and only the response got lost, so a blind retry
# risks a duplicate Snap. The error tells the user to check first instead.
PUBLISH_TIMEOUT_STEP = (
    "Post {destination} - it may still have gone live, check Snapchat before posting again"
)

# Snapchat Stories are full-bleed 9:16. Instagram source media rarely is, so
# source media gets letterboxed onto a blurred, scaled copy of itself instead
# of being center-cropped - avoids cutting off text/logos baked into the
# image (e.g. player-name graphics) at an unpredictable spot.
STORY_WIDTH = 1080
STORY_HEIGHT = 1920

# One session per host for connection reuse across the many calls each caller makes.
IG_SESSION = requests.Session()
SNAP_SESSION = requests.Session()


def _full_url(base: str, path_or_url: str) -> str:
    return path_or_url if path_or_url.startswith("http") else f"{base}{path_or_url}"


def _snap_post(path_or_url: str, access_token: str, step: str, **kwargs) -> requests.Response:
    """`step` names the call in the error if Snap doesn't answer in time, so a
    timeout says which part of the publish flow it hit."""
    try:
        return SNAP_SESSION.post(
            _full_url(BUSINESS_API_BASE, path_or_url),
            headers={"Authorization": f"Bearer {access_token}"},
            **kwargs,
        )
    except requests.Timeout as e:
        raise RuntimeError(f"Snapchat didn't respond in time during {step}: {e}") from e


# ---------------- Instagram (source) ----------------
def get_ig_json(url_or_path: str, params: dict | None = None, timeout: int = 60) -> dict:
    path = url_or_path if url_or_path.startswith("http") else "/" + url_or_path.lstrip("/")
    r = IG_SESSION.get(
        _full_url(GRAPH, path), params={"access_token": ACCESS_TOKEN, **(params or {})}, timeout=timeout
    )
    r.raise_for_status()
    return r.json()


def _slide(media_type: str | None, media_url: str | None, thumbnail_url: str | None) -> dict | None:
    """Instagram's Graph API sometimes omits media_url for VIDEO/REEL items -
    a known, intermittent Meta bug (see https://developers.facebook.com/community/threads/298461861532746/),
    not something a retry or a different field selection fixes. When that
    happens there's no real video to repost, so this slide is skipped rather
    than substituting the static thumbnail for it."""
    if media_type in ("VIDEO", "REEL"):
        if not media_url:
            return None
        return {"media_type": media_type, "url": media_url}
    url = media_url or thumbnail_url
    if not url:
        return None
    return {"media_type": media_type, "url": url}


def extract_media_items(post: dict) -> list[dict]:
    """One post -> one or more Snapchat slides. Carousels post every child as
    its own consecutive Snap; everything else is a single slide."""
    if post.get("media_type") == "CAROUSEL_ALBUM":
        children = (post.get("children") or {}).get("data") or []
        items = [
            s for c in children
            if (s := _slide(c.get("media_type"), c.get("media_url"), c.get("thumbnail_url")))
        ]
        if items:
            return items
    slide = _slide(post.get("media_type"), post.get("media_url"), post.get("thumbnail_url"))
    return [slide] if slide else []


_SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([^/?#]+)")


def _normalize_permalink(permalink: str) -> str:
    """Matches on the shortcode alone (ignoring the /p/, /reel/ or /tv/ prefix,
    query string, and trailing slash). Instagram treats /p/<code>/ and
    /reel/<code>/ as interchangeable URLs for the same content - the Graph
    API's own permalink field always uses /reel/ for Reels, but a URL copied
    from the app/website is often the /p/ form instead - so comparing full
    paths would miss a real match."""
    match = _SHORTCODE_RE.search(permalink)
    if match:
        return match.group(1)
    return permalink.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def _post_id_from_db(shortcode: str) -> str | None:
    """Post id for a shortcode from socials_analytics.instagram_posts (filled
    by the scraper for all of 433's accounts), or None if it isn't there (yet).
    Imported lazily so this library doesn't pull in db/streamlit at import."""
    from db import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT post_id FROM socials_analytics.instagram_posts "
                "WHERE strpos(permalink, %s) > 0 LIMIT 1",
                (f"/{shortcode}/",),
            )
            row = cur.fetchone()
            return str(row["post_id"]) if row else None
    finally:
        conn.close()


def find_post_by_permalink(permalink: str, max_pages: int = 20) -> dict:
    """Instagram's Graph API has no lookup-by-permalink endpoint, so the post
    id is first looked up in the scraper's DB table (~1s, no age limit).
    Only if it isn't there - e.g. posted minutes ago, before the scraper's
    next run - does this page through each of 433's IG accounts' recent media
    until it finds a matching permalink: all accounts (config.ALL_IG_USER_IDS),
    so the URL-based publisher pages work regardless of which channel a
    permalink belongs to, Main first since that's where nearly every
    permalink comes from (otherwise a Main post waits behind ~1000 NL posts)."""
    target = _normalize_permalink(permalink)
    try:
        post_id = _post_id_from_db(target)
    except Exception as e:
        logger.warning(f"DB permalink lookup failed, scanning Instagram instead: {e}")
        post_id = None
    if post_id:
        return get_ig_json(f"/{post_id}", {"fields": FIELDS})

    main_first = sorted(ALL_IG_USER_IDS, key=lambda uid: str(uid) not in IG_USER_IDS)
    for uid in main_first:
        next_url = f"/{uid}/media"
        for _ in range(max_pages):
            if not next_url:
                break
            params = None if next_url.startswith("http") else {"fields": FIELDS, "limit": LIMIT}
            data = get_ig_json(next_url, params)
            for it in data.get("data", []) or []:
                if _normalize_permalink(it.get("permalink") or "") == target:
                    return it
            next_url = data.get("paging", {}).get("next")
    raise ValueError(f"Post not found (or older than {max_pages} pages): {permalink}")


# ---------------- Snapchat auth ----------------
def get_access_token() -> str:
    r = requests.post(
        SNAPCHAT_TOKEN_URL,
        data={
            "client_id": SNAPCHAT_CLIENT_ID,
            "client_secret": SNAPCHAT_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": SNAPCHAT_REFRESH_TOKEN,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"Snapchat token refresh failed: {data}")
    if data.get("refresh_token") and data["refresh_token"] != SNAPCHAT_REFRESH_TOKEN:
        logger.warning(
            "Snapchat issued a new refresh_token - update SNAPCHAT_REFRESH_TOKEN "
            "in .env or a future run may fail."
        )
    return data["access_token"]


# ---------------- Snapchat media upload + publish ----------------
def encrypt_media(data: bytes) -> tuple[bytes, bytes, bytes]:
    """AES-256-CBC encrypt, as required by Snapchat's Create Media endpoint."""
    key = os.urandom(32)
    iv = os.urandom(16)
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return ciphertext, key, iv


def download_media(url: str) -> bytes:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def _scaled(img: Image.Image, width: int, height: int, fit: callable) -> Image.Image:
    scale = fit(width / img.width, height / img.height)
    return img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)


def _resize_cover(img: Image.Image, width: int, height: int) -> Image.Image:
    resized = _scaled(img, width, height, max)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _resize_contain(img: Image.Image, width: int, height: int) -> Image.Image:
    return _scaled(img, width, height, min)


def fit_image_to_story(raw: bytes) -> bytes:
    """Letterbox `raw` onto a blurred, scaled copy of itself so it fills the
    STORY_WIDTH x STORY_HEIGHT canvas without cropping any original content."""
    img = Image.open(io.BytesIO(raw)).convert("RGB")

    background = _resize_cover(img, STORY_WIDTH, STORY_HEIGHT).filter(ImageFilter.GaussianBlur(40))
    background = ImageEnhance.Brightness(background).enhance(0.6)

    foreground = _resize_contain(img, STORY_WIDTH, STORY_HEIGHT)
    background.paste(
        foreground,
        ((STORY_WIDTH - foreground.width) // 2, (STORY_HEIGHT - foreground.height) // 2),
    )

    out = io.BytesIO()
    background.save(out, format="JPEG", quality=95)
    return out.getvalue()


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def fit_video_to_story(raw: bytes) -> bytes:
    """Same letterbox treatment as fit_image_to_story, applied to video via ffmpeg."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in"
        dst = Path(tmp) / "out.mp4"
        src.write_bytes(raw)

        filter_complex = (
            f"[0:v]scale={STORY_WIDTH}:{STORY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={STORY_WIDTH}:{STORY_HEIGHT},gblur=sigma=30,eq=brightness=-0.15[bg];"
            f"[0:v]scale={STORY_WIDTH}:{STORY_HEIGHT}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
        )
        cmd = [
            _ffmpeg_bin(), "-y", "-i", str(src),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-profile:v", "main", "-preset", "slow", "-crf", "18",
            "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k",
            str(dst),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr.decode(errors='replace')[-2000:]}")
        return dst.read_bytes()


def create_media(access_token: str, media_type: str, name: str, key: bytes, iv: bytes) -> dict:
    r = _snap_post(
        f"/v1/public_profiles/{SNAPCHAT_PROFILE_ID}/media",
        access_token,
        "Create Media",
        json={
            "type": media_type,  # "VIDEO" or "IMAGE"
            "name": name,
            "key": base64.b64encode(key).decode(),
            "iv": base64.b64encode(iv).decode(),
        },
        timeout=SNAP_TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(f"Create Media failed ({r.status_code}): {r.text}")
    body = r.json()
    # Confirmed response envelope: flat, not nested like GET /public_profiles/{id}:
    # {"request_status": ..., "media_id": ..., "add_path": ..., "finalize_path": ...}
    if body.get("request_status") != "SUCCESS" or "media_id" not in body:
        raise RuntimeError(f"Create Media failed: {body}")
    return body


def upload_media(access_token: str, add_path: str, finalize_path: str, ciphertext: bytes) -> None:
    chunks = [ciphertext[i : i + MAX_CHUNK_SIZE] for i in range(0, len(ciphertext), MAX_CHUNK_SIZE)]

    for part_number, chunk in enumerate(chunks, start=1):
        r = _snap_post(
            add_path,
            access_token,
            f"upload (part {part_number})",
            data={"action": "ADD", "part_number": str(part_number)},
            files={"file": ("chunk", chunk, "application/octet-stream")},
            timeout=SNAP_UPLOAD_TIMEOUT,
        )
        if not r.ok:
            raise RuntimeError(f"Upload (part {part_number}) failed ({r.status_code}): {r.text}")

    r = _snap_post(
        finalize_path,
        access_token,
        "Finalize",
        files={"action": (None, "FINALIZE")},  # forces multipart/form-data, matching the ADD calls
        timeout=SNAP_TIMEOUT,
    )
    if not r.ok:
        raise RuntimeError(f"Finalize failed ({r.status_code}): {r.text}")


def post_story(access_token: str, media_id: str) -> dict:
    """Media stays in MEDIA_PROCESSING (server-side transcoding) for a bit
    after FINALIZE, so retry with backoff before giving up."""
    delay = 5.0
    for attempt in range(1, 7):
        r = _snap_post(
            f"/v1/public_profiles/{SNAPCHAT_PROFILE_ID}/stories",
            access_token,
            PUBLISH_TIMEOUT_STEP.format(destination="Story"),
            json={"media_id": media_id},
            timeout=SNAP_TIMEOUT,
        )
        if r.ok:
            return r.json()

        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if body.get("error_code") == "MEDIA_PROCESSING" and attempt < 6:
            logger.info(f"Media still processing, retrying in {delay:.0f}s...")
            time.sleep(delay)
            delay = min(delay * 1.8, 30)
            continue

        raise RuntimeError(f"Post Story failed ({r.status_code}): {r.text}")


SPOTLIGHT_DESCRIPTION_MAX = 160
# Instagram handle rules: letters, digits, '.' and '_'. A trailing '.' is
# sentence punctuation ("thanks @433."), not part of the handle.
_MENTION_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9._]+)")
# 433's own handles (lowercase) - it's 433's own profile posting, so tagging
# them adds nothing.
_SKIP_MENTIONS = {"@433", "@433womenfc", "@433nl"}


def _spotlight_description(caption: str | None) -> str | None:
    """The full caption if it fits Spotlight's limit; otherwise just its
    @mentions (deduped, in order, minus _SKIP_MENTIONS, as many as fit), or
    None if there are none."""
    if not caption:
        return None
    if len(caption) <= SPOTLIGHT_DESCRIPTION_MAX:
        return caption

    mentions = []
    for handle in _MENTION_RE.findall(caption):
        mention = "@" + handle.rstrip(".")
        if mention != "@" and mention.lower() not in _SKIP_MENTIONS and mention not in mentions:
            mentions.append(mention)

    text = ""
    for mention in mentions:
        candidate = f"{text} {mention}" if text else mention
        if len(candidate) > SPOTLIGHT_DESCRIPTION_MAX:
            break
        text = candidate
    return text or None


def post_spotlight(
    access_token: str,
    media_id: str,
    locale: str = "en_US",
    description: str | None = None,
    skip_save_to_profile: bool = False,
) -> dict:
    """Spotlight is video-only (5-60s mp4) and, unlike Post Story, requires a
    `locale`. Media stays in MEDIA_PROCESSING for a bit after FINALIZE, so
    retry with backoff before giving up, same as post_story.

    Snap's documented limit for `description` is 160 characters. A caption
    over that limit isn't truncated - a mid-sentence cutoff reads worse on a
    branded account than no caption at all - but its @mentions are still
    posted on their own, so the credit to the source stays (see
    _spotlight_description)."""
    body = {"media_id": media_id, "locale": locale, "skip_save_to_profile": skip_save_to_profile}
    if text := _spotlight_description(description):
        body["description"] = text

    delay = 5.0
    for attempt in range(1, 7):
        r = _snap_post(
            f"/v1/public_profiles/{SNAPCHAT_PROFILE_ID}/spotlights",
            access_token,
            PUBLISH_TIMEOUT_STEP.format(destination="Spotlight"),
            json=body,
            timeout=SNAP_TIMEOUT,
        )
        if r.ok:
            return r.json()

        resp_body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if resp_body.get("error_code") == "MEDIA_PROCESSING" and attempt < 6:
            logger.info(f"Media still processing, retrying in {delay:.0f}s...")
            time.sleep(delay)
            delay = min(delay * 1.8, 30)
            continue

        raise RuntimeError(f"Post Spotlight failed ({r.status_code}): {r.text}")

