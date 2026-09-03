"""
Unattended pipeline: scan Instagram for new Reels and publish each one straight
to Snapchat Spotlight on 433's Public Profile - no manual review step, unlike
the Streamlit app's Posts/Stories publisher pages (streamlit_app.py).

Meant to be invoked by an external scheduler every ~10 minutes (cron, Windows
Task Scheduler, an Airflow DAG, a k8s CronJob, ...) - this script does one pass
and exits, it does not loop internally:

    */10 * * * *  cd /path/to/repo && ./.venv/bin/python src/Snapchat_Reels_Autopublish.py

Each run re-scans a rolling LOOKBACK_MINUTES window (default 60, not just the
10 minutes between runs) and de-dupes against local state, so a Reel is never
posted twice even though it's seen on multiple consecutive runs. The wider
window matters because Instagram's Graph API often doesn't return a Reel's
media_url right away (a known, flaky Meta bug - see _slide() in
Snapchat_Repost.py) - a Reel that isn't postable yet just gets picked up again
on a later run within the window, instead of being skipped forever.

State (which Instagram post_ids have been published/given up on) is tracked in
a local JSON file (exports/snapchat_reels_autopublish_state.json), separate
from Snapchat_Repost.py's own state file since these are two independent
publishing flows.

Usage:
    python src/Snapchat_Reels_Autopublish.py                  # one pass, publish new Reels
    python src/Snapchat_Reels_Autopublish.py --dry-run          # log what would be published, don't post
    python src/Snapchat_Reels_Autopublish.py --lookback-minutes 30 --give-up-after-hours 12
"""

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import ACCOUNT_CHANNEL
from Snapchat_Repost import (
    FIELDS,
    LIMIT,
    IG_USER_IDS,
    SNAPCHAT_PROFILE_ID,
    get_ig_json,
    get_access_token,
    download_media,
    fit_video_to_story,
    encrypt_media,
    create_media,
    upload_media,
    post_spotlight,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATE_PATH = Path("exports") / "snapchat_reels_autopublish_state.json"
DIAG_FIELDS = FIELDS + ",media_product_type"

DEFAULT_LOOKBACK_MINUTES = 60
DEFAULT_GIVE_UP_AFTER_HOURS = 6
SPOTLIGHT_LOCALE = "en_US"

DONE_STATUSES = ("published", "given_up")


# ---------------- local state ----------------
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


# ---------------- Instagram ----------------
def _is_reel(item: dict) -> bool:
    return item.get("media_type") in ("VIDEO", "REEL") or item.get("media_product_type") == "REELS"


def fetch_recent_reels(lookback_minutes: int) -> list[dict]:
    """Newest-first per account; stops paginating once a post falls outside
    the lookback window, same assumption as Snapchat_Repost.fetch_recent_posts."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    reels: list[dict] = []
    for uid in IG_USER_IDS:
        channel = ACCOUNT_CHANNEL.get(uid, uid)
        next_url = f"/{uid}/media"
        while next_url:
            params = None if next_url.startswith("http") else {"fields": DIAG_FIELDS, "limit": LIMIT}
            data = get_ig_json(next_url, params)
            stop = False
            for it in data.get("data", []) or []:
                try:
                    ts = datetime.fromisoformat(it.get("timestamp", ""))
                except ValueError:
                    continue
                if ts < cutoff:
                    stop = True
                    break
                if _is_reel(it):
                    it["_channel"] = channel
                    reels.append(it)
            if stop:
                break
            next_url = data.get("paging", {}).get("next")
    return reels


# ---------------- pipeline ----------------
def _publish_reel(access_token: str, post: dict) -> str:
    """Downloads, letterboxes, encrypts, uploads and posts the Reel to
    Spotlight. Returns the Spotlight media_id."""
    raw = download_media(post["media_url"])
    processed = fit_video_to_story(raw)
    ciphertext, key, iv = encrypt_media(processed)

    media = create_media(access_token, "VIDEO", name=f"auto_{post['id']}.mp4", key=key, iv=iv)
    upload_media(access_token, media["add_path"], media["finalize_path"], ciphertext)
    post_spotlight(access_token, media["media_id"], locale=SPOTLIGHT_LOCALE)
    return media["media_id"]


def run(lookback_minutes: int, give_up_after_hours: int, dry_run: bool) -> None:
    state = load_state()
    now = datetime.now(timezone.utc)

    reels = fetch_recent_reels(lookback_minutes)
    candidates = [r for r in reels if state.get(r["id"], {}).get("status") not in DONE_STATUSES]

    logger.info(
        f"Found {len(reels)} Reel(s) in the last {lookback_minutes} minute(s), "
        f"{len(candidates)} not yet published/given up on"
    )
    if not candidates:
        return

    access_token = None if dry_run else get_access_token()

    for item in candidates:
        post_id = item["id"]
        entry = state.setdefault(
            post_id,
            {"channel": item["_channel"], "permalink": item.get("permalink"), "first_seen_at": now.isoformat()},
        )
        entry["attempts"] = entry.get("attempts", 0) + 1
        entry["last_attempt_at"] = now.isoformat()

        # re-fetch on the post id rather than trusting the list response, so a
        # media_url that only just became available is picked up
        post = get_ig_json(f"/{post_id}", {"fields": DIAG_FIELDS})

        if not post.get("media_url"):
            first_seen = datetime.fromisoformat(entry["first_seen_at"])
            if now - first_seen > timedelta(hours=give_up_after_hours):
                entry["status"] = "given_up"
                entry["error"] = f"No media_url within {give_up_after_hours}h (Meta media_url bug)"
                logger.warning(f"Giving up on {post_id} ({item['_channel']}) - {entry['error']}")
            else:
                entry["status"] = "pending"
                logger.info(f"{post_id} ({item['_channel']}) has no media_url yet, will retry next run")
            save_state(state)
            continue

        if dry_run:
            logger.info(f"[dry-run] would publish {post_id} ({item['_channel']}) to Spotlight - {post.get('permalink')}")
            save_state(state)
            continue

        try:
            logger.info(f"Publishing {post_id} ({item['_channel']}) to Spotlight - {post.get('permalink')}")
            media_id = _publish_reel(access_token, post)
            entry["status"] = "published"
            entry["spotlight_media_id"] = media_id
            entry["published_at"] = now.isoformat()
            entry.pop("error", None)
            logger.info(f"Published {post_id} -> spotlight media_id={media_id}")
        except Exception as e:
            entry["status"] = "pending"
            entry["error"] = str(e)
            logger.error(f"Failed to publish {post_id}: {e}")
        finally:
            save_state(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scan Instagram for new Reels and auto-publish them to Snapchat Spotlight"
    )
    parser.add_argument(
        "--lookback-minutes", type=int, default=DEFAULT_LOOKBACK_MINUTES,
        help=f"How far back to scan Instagram each run (default: {DEFAULT_LOOKBACK_MINUTES})",
    )
    parser.add_argument(
        "--give-up-after-hours", type=int, default=DEFAULT_GIVE_UP_AFTER_HOURS,
        help=f"Stop retrying a Reel with no media_url after this many hours (default: {DEFAULT_GIVE_UP_AFTER_HOURS})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log what would be published without actually posting to Snapchat",
    )
    args = parser.parse_args()
    run(args.lookback_minutes, args.give_up_after_hours, args.dry_run)
