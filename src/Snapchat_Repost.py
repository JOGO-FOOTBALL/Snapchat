"""
Queue new Instagram posts for manual review, then publish approved ones as
Snapchat Stories on 433's Public Profile via the Snapchat Business API.

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

State (which Instagram posts have been queued/approved/rejected/posted) is
tracked in a local JSON file (exports/snapchat_repost_state.json), not a
shared DB table, so this script stays fully self-contained.

Usage:
    python src/Snapchat_Repost.py queue                  # scan IG accounts for new posts to review
    python src/Snapchat_Repost.py list                    # show posts pending review
    python src/Snapchat_Repost.py approve --post_id ID    # approve one for publishing
    python src/Snapchat_Repost.py reject --post_id ID     # drop one from the queue
    python src/Snapchat_Repost.py publish                 # publish all approved posts to Snapchat
    python src/Snapchat_Repost.py check                    # read-only: flag posts with no postable media right now
"""

import argparse
import base64
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image, ImageEnhance, ImageFilter

from config import (
    ACCOUNT_CHANNEL,
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

STATE_PATH = Path("exports") / "snapchat_repost_state.json"

ACCESS_TOKEN = Secrets.META_API_TOKEN
FIELDS = (
    "id,media_type,caption,permalink,timestamp,media_url,thumbnail_url,"
    "children{media_type,media_url,thumbnail_url}"
)
LIMIT = 50
QUEUE_WINDOW_DAYS = 7

BUSINESS_API_BASE = "https://businessapi.snapchat.com"
MAX_CHUNK_SIZE = 32 * 1024 * 1024
IMAGE_EXT = ".jpg"  # must match fit_image_to_story's output format
VIDEO_EXT = ".mp4"  # must match fit_video_to_story's output format

# Snapchat Stories are full-bleed 9:16. Instagram source media rarely is, so
# source media gets letterboxed onto a blurred, scaled copy of itself instead
# of being center-cropped - avoids cutting off text/logos baked into the
# image (e.g. player-name graphics) at an unpredictable spot.
STORY_WIDTH = 1080
STORY_HEIGHT = 1920

# One session per host for connection reuse across the many calls each command makes.
IG_SESSION = requests.Session()
SNAP_SESSION = requests.Session()


def _full_url(base: str, path_or_url: str) -> str:
    return path_or_url if path_or_url.startswith("http") else f"{base}{path_or_url}"


def _snap_post(path_or_url: str, access_token: str, **kwargs) -> requests.Response:
    return SNAP_SESSION.post(
        _full_url(BUSINESS_API_BASE, path_or_url),
        headers={"Authorization": f"Bearer {access_token}"},
        **kwargs,
    )


# ---------------- local state ----------------
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


# ---------------- Instagram (source) ----------------
def get_ig_json(url_or_path: str, params: dict | None = None, timeout: int = 60) -> dict:
    path = url_or_path if url_or_path.startswith("http") else "/" + url_or_path.lstrip("/")
    r = IG_SESSION.get(
        _full_url(GRAPH, path), params={"access_token": ACCESS_TOKEN, **(params or {})}, timeout=timeout
    )
    r.raise_for_status()
    return r.json()


def fetch_recent_posts(user_id: str, days: int) -> list[dict]:
    """Assumes /media returns posts newest-first, so pagination stops at the
    first post older than the window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items: list[dict] = []
    next_url = f"/{user_id}/media"

    while next_url:
        params = None if next_url.startswith("http") else {"fields": FIELDS, "limit": LIMIT}
        data = get_ig_json(next_url, params)
        for it in data.get("data", []) or []:
            try:
                ts = datetime.fromisoformat(it.get("timestamp", ""))
            except ValueError:
                continue
            if ts < cutoff:
                return items
            items.append(it)
        next_url = data.get("paging", {}).get("next")

    return items


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
    background.save(out, format="JPEG", quality=92)
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
            "-c:v", "libx264", "-profile:v", "main", "-movflags", "+faststart",
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
        json={
            "type": media_type,  # "VIDEO" or "IMAGE"
            "name": name,
            "key": base64.b64encode(key).decode(),
            "iv": base64.b64encode(iv).decode(),
        },
        timeout=60,
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
            data={"action": "ADD", "part_number": str(part_number)},
            files={"file": ("chunk", chunk, "application/octet-stream")},
            timeout=120,
        )
        if not r.ok:
            raise RuntimeError(f"Upload (part {part_number}) failed ({r.status_code}): {r.text}")

    r = _snap_post(
        finalize_path,
        access_token,
        files={"action": (None, "FINALIZE")},  # forces multipart/form-data, matching the ADD calls
        timeout=60,
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
            json={"media_id": media_id},
            timeout=60,
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


# ---------------- commands ----------------
def _by_status(state: dict, *statuses: str) -> dict:
    return {pid: p for pid, p in state.items() if p["status"] in statuses}


def _require_post(state: dict, post_id: str) -> dict:
    if post_id not in state:
        raise SystemExit(f"Unknown post_id: {post_id}")
    return state[post_id]


def _refresh_media_items(post_id: str) -> list[dict]:
    """Re-fetches a post from Instagram right now and re-derives its slides -
    used instead of a cached `media` list so a slide that had no media_url
    earlier (see _slide) is re-verified, not assumed stale-broken forever."""
    return extract_media_items(get_ig_json(f"/{post_id}", {"fields": FIELDS}))


def cmd_queue(days: int) -> None:
    state = load_state()
    added = 0
    skipped = 0
    for uid in IG_USER_IDS:
        channel = ACCOUNT_CHANNEL.get(uid, uid)
        logger.info(f"Checking {channel} for new posts...")
        for post in fetch_recent_posts(uid, days=days):
            post_id = post["id"]
            if post_id in state:
                continue
            media = extract_media_items(post)
            if not media:
                # e.g. a VIDEO/REEL post where Instagram never returned a
                # media_url (see _slide) - nothing postable, so skip it
                # entirely instead of queueing something that can't publish.
                logger.info(f"Skipping {post_id} ({post.get('permalink')}) - no postable media")
                skipped += 1
                continue
            state[post_id] = {
                "ig_user_id": uid,
                "channel": channel,
                "media_type": post.get("media_type"),
                "media": media,
                "permalink": post.get("permalink"),
                "caption": post.get("caption"),
                "status": "pending_review",
                "queued_at": datetime.now(timezone.utc).isoformat(),
            }
            added += 1
    save_state(state)
    logger.info(f"Queued {added} new post(s) for review, skipped {skipped} without postable media")


def cmd_list() -> None:
    state = load_state()
    pending = _by_status(state, "pending_review")
    if not pending:
        logger.info("No posts pending review")
        return
    for pid, p in pending.items():
        media = p.get("media")
        slide_note = f"  ({len(media)} slides)" if media and len(media) > 1 else ""
        print(f"{pid}  [{p['channel']}]  {p['media_type']}{slide_note}  {p['permalink']}")
        if p.get("caption"):
            print(f"      {p['caption'][:100]}")


def cmd_approve(post_id: str) -> None:
    state = load_state()
    post = _require_post(state, post_id)
    post["status"] = "approved"
    post["approved_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    logger.info(f"Approved {post_id}")


def cmd_reject(post_id: str) -> None:
    state = load_state()
    post = _require_post(state, post_id)
    post["status"] = "rejected"
    save_state(state)
    logger.info(f"Rejected {post_id}")


def cmd_check() -> None:
    """Read-only: re-checks every pending/approved post against Instagram right
    now and reports which ones currently have no postable media - lets you spot
    e.g. old queued reels that predate the media_url check in _slide, without
    running publish."""
    state = load_state()
    candidates = _by_status(state, "pending_review", "approved")
    if not candidates:
        logger.info("Nothing pending or approved to check")
        return

    bad = 0
    for pid, p in candidates.items():
        if not _refresh_media_items(pid):
            bad += 1
            print(f"{pid}  [{p['channel']}]  {p['media_type']}  {p['status']}  no postable media  {p['permalink']}")
    logger.info(f"Checked {len(candidates)} post(s), {bad} without postable media")


def cmd_publish() -> None:
    state = load_state()
    approved = _by_status(state, "approved")
    if not approved:
        logger.info("No approved posts to publish")
        return

    access_token = get_access_token()
    for post_id, post in approved.items():
        # Re-check against Instagram right before publishing rather than trusting
        # the media list cached at queue time - a slide that had no media_url
        # back then (see _slide) is re-verified here so nothing with no real
        # video ever gets posted, even if it was queued/approved before that
        # check existed, or before its media_url happened to become available.
        media_items = _refresh_media_items(post_id)
        if not media_items:
            post["status"] = "error"
            post["error"] = "No postable media at publish time (Instagram never returned a media_url)"
            logger.warning(f"Skipping {post_id} - {post['error']}")
            save_state(state)
            continue

        # already-posted slides (from a previous failed attempt) are skipped by
        # url rather than position, so a re-approve + publish after fixing an
        # error doesn't double-post even if Instagram now returns the slides
        # in a different order or count than the earlier attempt saw.
        posted = list(post.get("snapchat_posted") or [])
        posted_urls = {p["url"] for p in posted}

        try:
            logger.info(
                f"Publishing {post_id} ({post['channel']}) to Snapchat "
                f"- {len(media_items)} slide(s)..."
            )
            for idx, item in enumerate(media_items, start=1):
                if item["url"] in posted_urls:
                    continue  # already posted in an earlier attempt

                is_video = item.get("media_type") in ("VIDEO", "REEL")
                raw = download_media(item["url"])
                raw = fit_video_to_story(raw) if is_video else fit_image_to_story(raw)
                ciphertext, key, iv = encrypt_media(raw)

                media = create_media(
                    access_token,
                    "VIDEO" if is_video else "IMAGE",
                    name=f"{post_id}_{idx}{VIDEO_EXT if is_video else IMAGE_EXT}",
                    key=key,
                    iv=iv,
                )
                upload_media(access_token, media["add_path"], media["finalize_path"], ciphertext)
                result = post_story(access_token, media["media_id"])

                posted.append({"url": item["url"], "media_id": media["media_id"], "request_id": result.get("request_id")})
                post["snapchat_posted"] = posted
                save_state(state)
                logger.info(f"  slide {idx}/{len(media_items)} -> request_id={result.get('request_id')}")

            post["status"] = "posted"
            post["posted_at"] = datetime.now(timezone.utc).isoformat()
            logger.info(f"Posted {post_id} ({len(media_items)} slide(s)) to Snapchat")
        except Exception as e:
            post["status"] = "error"
            post["error"] = f"{e} (posted {len(posted)}/{len(media_items)} slide(s) before failing)"
            logger.error(f"Failed to publish {post_id}: {post['error']}")
        finally:
            save_state(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Queue Instagram posts for review and publish approved ones to Snapchat"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_queue = sub.add_parser("queue", help="Scan Instagram accounts for new posts to review")
    p_queue.add_argument(
        "--days", type=int, default=QUEUE_WINDOW_DAYS, help="Look back N days (default: 7)"
    )

    sub.add_parser("list", help="List posts pending review")

    p_approve = sub.add_parser("approve", help="Approve a queued post for publishing")
    p_approve.add_argument("--post_id", required=True)

    p_reject = sub.add_parser("reject", help="Reject a queued post")
    p_reject.add_argument("--post_id", required=True)

    sub.add_parser("publish", help="Publish all approved posts to Snapchat")

    sub.add_parser(
        "check", help="Read-only: report pending/approved posts with no postable media right now"
    )

    args = parser.parse_args()

    if args.command == "queue":
        cmd_queue(days=args.days)
    elif args.command == "list":
        cmd_list()
    elif args.command == "approve":
        cmd_approve(args.post_id)
    elif args.command == "reject":
        cmd_reject(args.post_id)
    elif args.command == "publish":
        cmd_publish()
    elif args.command == "check":
        cmd_check()
