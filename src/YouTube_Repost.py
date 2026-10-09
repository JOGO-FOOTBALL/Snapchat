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
import unicodedata

import requests

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

# Same reasoning as Snapchat_Repost.SNAP_TIMEOUT: connect fails fast, the
# upload itself can take a while for a large video.
YT_TIMEOUT = (10, 60)
YT_UPLOAD_TIMEOUT = (10, 600)


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


def upload_short(access_token: str, video: bytes, caption: str | None) -> str:
    """Resumable upload: one call to open the upload session with the
    metadata, one PUT with the bytes. Returns the YouTube video id.

    A timeout on the PUT is deliberately not retried, same as Snapchat's
    publish call: YouTube may have received the whole video and only the
    response got lost, so a blind retry risks a duplicate Short."""
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
    upload_url = r.headers["Location"]

    try:
        r = requests.put(
            upload_url,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "video/mp4"},
            data=video,
            timeout=YT_UPLOAD_TIMEOUT,
        )
    except requests.Timeout as e:
        raise RuntimeError(
            f"YouTube didn't respond in time during upload - it may still have gone live, "
            f"check YouTube Studio before posting again: {e}"
        ) from e
    if not r.ok:
        raise RuntimeError(f"YouTube upload failed ({r.status_code}): {r.text}")

    body = r.json()
    status = body.get("status", {})
    if status.get("privacyStatus") != YOUTUBE_PRIVACY_STATUS:
        logger.warning(
            f"YouTube set video {body['id']} to {status.get('privacyStatus')!r} instead of "
            f"{YOUTUBE_PRIVACY_STATUS!r} - expected until the API project passes YouTube's audit."
        )
    return body["id"]
