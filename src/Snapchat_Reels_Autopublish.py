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
the snapchat.publish_log Postgres table (source='auto_reels'), shared with the
Streamlit publisher pages to prevent cross-flow duplicates.

Usage:
    python src/Snapchat_Reels_Autopublish.py                  # one pass, publish new Reels
    python src/Snapchat_Reels_Autopublish.py --dry-run          # log what would be published, don't post
    python src/Snapchat_Reels_Autopublish.py --lookback-minutes 30 --give-up-after-hours 12
"""

import argparse
import logging
from datetime import datetime, timedelta, timezone

from config import ACCOUNT_CHANNEL
from db import get_connection
from publish_helpers import AlreadyPublishedError, publish_lock
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

DIAG_FIELDS = FIELDS + ",media_product_type"

DEFAULT_LOOKBACK_MINUTES = 60
DEFAULT_GIVE_UP_AFTER_HOURS = 6
SPOTLIGHT_LOCALE = "en_US"

SOURCE = "auto_reels"
DESTINATION = "spotlight"
DONE_STATUSES = ("posted", "given_up")


# ---------------- DB state ----------------
def load_state() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ig_content_id, status, permalink, channel, published_by,
                       snapchat_media_id, posted_at, first_seen_at, attempts,
                       last_attempt_at, error
                FROM snapchat.publish_log
                WHERE source = %s AND destination = %s
                """,
                (SOURCE, DESTINATION),
            )
            return {row["ig_content_id"]: dict(row) for row in cur.fetchall()}
    finally:
        conn.close()


def save_entry(ig_content_id: str, *, status: str, permalink: str | None = None,
               channel: str | None = None, snapchat_media_id: str | None = None,
               posted_at: datetime | None = None, first_seen_at: datetime | None = None,
               attempts: int = 0, last_attempt_at: datetime | None = None,
               error: str | None = None) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO snapchat.publish_log
                    (ig_content_id, source, destination, status, permalink, channel,
                     snapchat_media_id, posted_at, first_seen_at, attempts,
                     last_attempt_at, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ig_content_id, destination, source) DO UPDATE SET
                    status = EXCLUDED.status,
                    snapchat_media_id = COALESCE(EXCLUDED.snapchat_media_id, snapchat.publish_log.snapchat_media_id),
                    posted_at = COALESCE(EXCLUDED.posted_at, snapchat.publish_log.posted_at),
                    first_seen_at = COALESCE(EXCLUDED.first_seen_at, snapchat.publish_log.first_seen_at),
                    attempts = EXCLUDED.attempts,
                    last_attempt_at = EXCLUDED.last_attempt_at,
                    error = EXCLUDED.error,
                    updated_at = now()
                """,
                (ig_content_id, SOURCE, DESTINATION, status, permalink, channel,
                 snapchat_media_id, posted_at, first_seen_at, attempts,
                 last_attempt_at, error),
            )
            conn.commit()
    finally:
        conn.close()


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
    post_spotlight(access_token, media["media_id"], locale=SPOTLIGHT_LOCALE, description=post.get("caption"))
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
        existing = state.get(post_id, {})
        attempts = existing.get("attempts", 0) + 1
        first_seen = existing.get("first_seen_at") or now

        post = get_ig_json(f"/{post_id}", {"fields": DIAG_FIELDS})

        if not post.get("media_url"):
            if now - first_seen > timedelta(hours=give_up_after_hours):
                error_msg = f"No media_url within {give_up_after_hours}h (Meta media_url bug)"
                logger.warning(f"Giving up on {post_id} ({item['_channel']}) - {error_msg}")
                save_entry(post_id, status="given_up", permalink=item.get("permalink"),
                           channel=item["_channel"], first_seen_at=first_seen,
                           attempts=attempts, last_attempt_at=now, error=error_msg)
            else:
                logger.info(f"{post_id} ({item['_channel']}) has no media_url yet, will retry next run")
                save_entry(post_id, status="pending", permalink=item.get("permalink"),
                           channel=item["_channel"], first_seen_at=first_seen,
                           attempts=attempts, last_attempt_at=now)
            continue

        if dry_run:
            logger.info(f"[dry-run] would publish {post_id} ({item['_channel']}) to Spotlight - {post.get('permalink')}")
            continue

        try:
            with publish_lock(post_id, DESTINATION):
                logger.info(f"Publishing {post_id} ({item['_channel']}) to Spotlight - {post.get('permalink')}")
                media_id = _publish_reel(access_token, post)
                logger.info(f"Published {post_id} -> spotlight media_id={media_id}")
                save_entry(post_id, status="posted", permalink=item.get("permalink"),
                           channel=item["_channel"], snapchat_media_id=media_id,
                           posted_at=now, first_seen_at=first_seen,
                           attempts=attempts, last_attempt_at=now)
        except AlreadyPublishedError as e:
            logger.info(f"Skipping {post_id} ({item['_channel']}): {e}")
        except Exception as e:
            logger.error(f"Failed to publish {post_id}: {e}")
            save_entry(post_id, status="pending", permalink=item.get("permalink"),
                       channel=item["_channel"], first_seen_at=first_seen,
                       attempts=attempts, last_attempt_at=now, error=str(e))


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
