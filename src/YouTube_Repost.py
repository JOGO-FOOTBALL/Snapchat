"""
Uploads Instagram Reels to 433's YouTube channel as Shorts via the YouTube
Data API v3 (resumable `videos.insert`). Not a script to run directly - it's
imported by publish_helpers.py for the YouTube button on the Spotlight
publisher page.

The video that goes up is the same 1080x1920 letterboxed mp4 Spotlight gets
(fit_video_to_story) - YouTube classifies any vertical/square video of up to
3 minutes as a Short on its own, so the title needs no "#Shorts" tag.

Auth reuses meta-analytics' Google Cloud OAuth client (config.Secrets.
YOUTUBE_OAUTH_CLIENT_ID/SECRET) with a refresh token minted for the
youtube.upload scope (config.YOUTUBE_UPLOAD_REFRESH_TOKEN, see
auth_config/youtube_refresh_token.py). Quota: an upload costs ~1600 of the
project's default 10,000 units/day, so ~6 uploads/day - shared with
meta-analytics' own Data API usage on the same project.
"""

import logging
import time
import unicodedata

import requests

from Snapchat_Repost import redact_tokens
from config import (
    Secrets,
    YOUTUBE_PRIVACY_STATUS,
    YOUTUBE_TOKEN_URL,
    YOUTUBE_UPLOAD_REFRESH_TOKEN,
)

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_WATCH_URL = "https://www.youtube.com/shorts/{video_id}"
YOUTUBE_CATEGORY_SPORTS = "17"

TITLE_MAX = 100
DESCRIPTION_MAX_BYTES = 5000
DEFAULT_TITLE = "433"

# Same reasoning as Snapchat_Repost.SNAP_TIMEOUT: connect fails fast.
YT_TIMEOUT = (10, 60)
# Per upload chunk. requests/urllib3 applies the *connect* timeout to each
# socket write while sending the body (the read timeout only starts once the
# body is sent), so a 10s connect timeout made a whole-file PUT fail with
# "The write operation timed out" whenever the upstream link stalled >10s.
YT_CHUNK_TIMEOUT = (30, 120)
# Must be a multiple of 256 KiB (resumable upload protocol requirement).
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
UPLOAD_MAX_RETRIES = 5


def get_youtube_access_token() -> str:
    if not YOUTUBE_UPLOAD_REFRESH_TOKEN:
        raise RuntimeError(
            "YOUTUBE_UPLOAD_REFRESH_TOKEN is not set - run "
            "`python auth_config/youtube_refresh_token.py` and add it to the environment."
        )
    r = requests.post(
        YOUTUBE_TOKEN_URL,
        data={
            "client_id": Secrets.YOUTUBE_OAUTH_CLIENT_ID,
            "client_secret": Secrets.YOUTUBE_OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": YOUTUBE_UPLOAD_REFRESH_TOKEN,
        },
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"YouTube token refresh failed ({r.status_code}): {r.text}")
    return r.json()["access_token"]


def _clean(text: str) -> str:
    """YouTube rejects titles/descriptions containing '<' or '>'. NFKC folds
    the Unicode "bold"/"italic" letters 433's captions often use (e.g.
    "𝗡𝗘𝗬𝗠𝗔𝗥") back to plain letters, which
    YouTube search otherwise doesn't match; emoji are left as-is."""
    return unicodedata.normalize("NFKC", text).replace("<", "").replace(">", "")


def short_title(caption: str | None) -> str:
    """First non-empty caption line, within YouTube's 100-char
    title limit. Unlike Spotlight's description, a title is required, so a
    too-long first line is cut at a word boundary (with an ellipsis) rather
    than dropped."""
    first_line = next((line.strip() for line in (caption or "").splitlines() if line.strip()), "")
    title = _clean(first_line) or DEFAULT_TITLE

    if len(title) > TITLE_MAX:
        cut = title[: TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-") or title[: TITLE_MAX - 1]
        title = cut + "…"
    return title


def short_description(caption: str | None) -> str:
    """Full caption, trimmed to YouTube's 5000-byte description limit."""
    text = _clean(caption or "")
    encoded = text.encode("utf-8")
    if len(encoded) <= DESCRIPTION_MAX_BYTES:
        return text
    return encoded[:DESCRIPTION_MAX_BYTES].decode("utf-8", errors="ignore")


def _next_offset(r: requests.Response) -> int:
    """First byte YouTube still needs, from a 308's Range header
    ("bytes=0-<last received>"); no header means nothing was stored yet."""
    received = r.headers.get("Range")
    return int(received.rsplit("-", 1)[1]) + 1 if received else 0


def _upload_status(upload_url: str, access_token: str, total: int) -> tuple[int, dict | None]:
    """Asks YouTube how far the upload got. Returns (next byte offset, the
    video resource if the upload had in fact already completed, else None)."""
    r = requests.put(
        upload_url,
        headers={"Authorization": f"Bearer {access_token}", "Content-Range": f"bytes */{total}"},
        timeout=YT_TIMEOUT,
    )
    if r.status_code in (200, 201):
        return total, r.json()
    if r.status_code == 308:
        return _next_offset(r), None
    raise RuntimeError(f"YouTube upload status check failed ({r.status_code}): {r.text}")


def _upload_chunks(upload_url: str, access_token: str, video: bytes) -> dict:
    """Sends the video in chunks, resuming from wherever YouTube says it got
    to after a dropped connection, timeout or 5xx (up to UPLOAD_MAX_RETRIES
    in a row). Safe against duplicates: if the last chunk did arrive and only
    the response got lost, the status check returns the finished video
    instead of starting over. Returns the video resource."""
    total = len(video)
    offset = failures = 0
    while True:
        try:
            if failures:
                offset, done = _upload_status(upload_url, access_token, total)
                if done is not None:
                    return done
            end = min(offset + UPLOAD_CHUNK_SIZE, total)
            r = requests.put(
                upload_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes {offset}-{end - 1}/{total}",
                },
                data=video[offset:end],
                timeout=YT_CHUNK_TIMEOUT,
            )
            if r.status_code in (200, 201):
                return r.json()
            if r.status_code == 308:
                offset, failures = _next_offset(r), 0
                continue
            if r.status_code < 500:
                raise RuntimeError(f"YouTube upload failed ({r.status_code}): {r.text}")
            error = f"{r.status_code}: {r.text[:200]}"
        except (requests.ConnectionError, requests.Timeout) as e:
            error = redact_tokens(str(e))

        failures += 1
        if failures > UPLOAD_MAX_RETRIES:
            raise RuntimeError(
                f"YouTube upload kept failing after {UPLOAD_MAX_RETRIES} retries ({error}). "
                f"It most likely didn't go live - check YouTube Studio if unsure, then try again."
            )
        logger.warning(f"YouTube upload interrupted ({error}), resuming ({failures}/{UPLOAD_MAX_RETRIES})...")
        time.sleep(min(2 ** failures, 30))


def upload_short(access_token: str, video: bytes, caption: str | None) -> str:
    """Resumable upload: one call to open the upload session with the
    metadata, then the bytes in UPLOAD_CHUNK_SIZE chunks (_upload_chunks).
    Returns the YouTube video id."""
    metadata = {
        "snippet": {
            "title": short_title(caption),
            "description": short_description(caption),
            "categoryId": YOUTUBE_CATEGORY_SPORTS,
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }
    r = requests.post(
        YOUTUBE_UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(len(video)),
        },
        json=metadata,
        timeout=YT_TIMEOUT,
    )
    if not r.ok:
        if "youtubeSignupRequired" in r.text:
            raise RuntimeError(
                "YouTube upload init failed: the refresh token belongs to a Google account "
                "without a YouTube channel - re-run auth_config/youtube_refresh_token.py and "
                "pick the 433 channel when Google asks which account/channel to use."
            )
        raise RuntimeError(f"YouTube upload init failed ({r.status_code}): {r.text}")
    body = _upload_chunks(r.headers["Location"], access_token, video)
    status = body.get("status", {})
    if status.get("privacyStatus") != YOUTUBE_PRIVACY_STATUS:
        logger.warning(
            f"YouTube set video {body['id']} to {status.get('privacyStatus')!r} instead of "
            f"{YOUTUBE_PRIVACY_STATUS!r} - expected until the API project passes YouTube's audit."
        )
    return body["id"]
