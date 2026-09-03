"""Read-only access to socials_analytics.instagram_stories (Postgres) for the
Stories publisher page - see app_pages/stories_publisher.py.

Connection secrets come from the same Key Vault as everything else in
config.py (DatasciencePsqlServer*Prod), fetched lazily like
config.get_streamlit_auth_config_yaml() rather than at import time.
"""

from concurrent.futures import ThreadPoolExecutor

import psycopg2
import psycopg2.extras
import requests
import streamlit as st

from config import secret_client

_SECRET_NAMES = {
    "host": "DatasciencePsqlServerUrlProd",
    "port": "DatasciencePsqlServerPortProd",
    "dbname": "DatasciencePsqlServerDatabaseProd",
    "user": "DatasciencePsqlServerUsernameProd",
    "password": "DatasciencePsqlServerPasswordProd",
}


def _get_connection():
    kwargs = {key: secret_client.get_secret(name).value for key, name in _SECRET_NAMES.items()}
    kwargs["port"] = int(kwargs["port"])
    return psycopg2.connect(sslmode="require", cursor_factory=psycopg2.extras.RealDictCursor, **kwargs)


def _media_url_is_alive(url: str) -> bool:
    """Instagram's signed CDN links expire, so a non-null media_url in the DB
    isn't necessarily still fetchable - HEAD (falling back to a streamed GET
    for CDNs that reject HEAD) confirms it actually resolves right now."""
    try:
        r = requests.head(url, timeout=4, allow_redirects=True)
        if r.status_code in (403, 405):
            r = requests.get(url, timeout=4, stream=True)
        return r.ok
    except requests.RequestException:
        return False


def _filter_live_media_url(rows: list[dict]) -> list[dict]:
    """Checks every row's media_url concurrently (not sequentially - with
    hundreds of rows, one-at-a-time checks would make the page unusably
    slow) and drops rows whose link no longer resolves."""
    if not rows:
        return rows
    with ThreadPoolExecutor(max_workers=40) as pool:
        alive = list(pool.map(lambda row: _media_url_is_alive(row["media_url"]), rows))
    return [row for row, ok in zip(rows, alive) if ok]


# Longer than the plain-DB-query TTL these pages used to have - the live
# media_url check (a HEAD request per item, see above) is what makes a cache
# miss slow, so this cuts how often that cost is paid, not just how often the
# DB itself is hit.
@st.cache_data(ttl="5m", show_spinner=False)
def fetch_recent_instagram_stories(ig_user_id: int, lookback_hours: int) -> list[dict]:
    """Newest-first. Skips rows with no media_url (the scraper occasionally
    misses it, same class of gap as the Graph API's own media_url bug)."""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT story_id, media_type, media_url, thumbnail_url_abs, thumbnail_url, permalink, timestamp_utc
                FROM socials_analytics.instagram_stories
                WHERE ig_user_id = %s
                  AND timestamp_utc > (now() AT TIME ZONE 'utc') - (%s || ' hours')::interval
                  AND media_url IS NOT NULL
                ORDER BY timestamp_utc DESC
                """,
                (ig_user_id, lookback_hours),
            )
            return _filter_live_media_url([dict(row) for row in cur.fetchall()])
    finally:
        conn.close()


@st.cache_data(ttl="5m", show_spinner=False)
def fetch_instagram_posts_page(ig_user_id: int, offset: int, limit: int, reels_only: bool = False) -> list[dict]:
    """Newest-first, not time-bounded (unlike Stories) - paginated via
    offset/limit instead, so the page (see app_pages/posts_publisher.py) can
    render an initial small batch fast and stream the rest in afterwards
    rather than blocking on the live media_url check (see below) for every
    row before showing anything. Skips rows with no media_url. Each row is
    one post; for a CAROUSEL_ALBUM, media_url/thumbnail are the cover slide
    only - this table has no per-slide breakdown, unlike the Graph API (see
    extract_media_items in Snapchat_Repost.py, used by the permalink-based
    Story publisher page and by the "push all slides" action on the Posts
    publisher page).

    reels_only filters to VIDEO/REEL at the SQL level instead of fetching a
    generic recent-posts window and filtering client-side - the account's
    recent posting mix is often mostly image carousels, so a plain "last N
    posts" page can come back with almost no reels even though thousands
    exist further back. Used by the Spotlight variant of the Posts publisher
    page (video-only, no other type is ever postable there)."""
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT post_id, media_type, media_product_type, media_url, thumbnail_url_abs, thumbnail_url,
                       permalink, caption, children_count, timestamp_utc
                FROM socials_analytics.instagram_posts
                WHERE ig_user_id = %s
                  {"AND media_type IN ('VIDEO', 'REEL')" if reels_only else ""}
                  AND media_url IS NOT NULL
                ORDER BY timestamp_utc DESC
                LIMIT %s OFFSET %s
                """,
                (ig_user_id, limit, offset),
            )
            return _filter_live_media_url([dict(row) for row in cur.fetchall()])
    finally:
        conn.close()
