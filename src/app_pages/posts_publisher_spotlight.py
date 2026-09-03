"""Browse Instagram reels and push them to Snapchat Spotlight - see
render_posts_grid_page in publish_helpers.py for the shared implementation
also used by posts_publisher.py (the Story variant, which browses all post
types, not just reels). video_only=True both filters the DB query to
VIDEO/REEL and hides any non-video card as a safety net - Spotlight has no
multi-slide concept and is video-only."""

from pathlib import Path

import streamlit as st

from publish_helpers import render_posts_grid_page
from Snapchat_Repost import post_spotlight

LOG_PATH = Path("exports") / "streamlit_posts_spotlight_publish_log.json"
IG_USER_ID = 17841401739313962  # Main - matches config.IG_USER_IDS
NAME_PREFIX = "post_db"
SPOTLIGHT_LOCALE = "en_US"

username = st.session_state["username"]

render_posts_grid_page(
    title="Posts publisher - Spotlight",
    subtitle="Browse Instagram reels and push them to Snapchat Spotlight.",
    log_path=LOG_PATH,
    username=username,
    name_prefix=NAME_PREFIX,
    ig_user_id=IG_USER_ID,
    post_one=lambda access_token, media_id: post_spotlight(access_token, media_id, locale=SPOTLIGHT_LOCALE),
    video_only=True,
)
