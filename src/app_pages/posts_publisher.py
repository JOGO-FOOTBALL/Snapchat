"""Browse Instagram posts pulled from socials_analytics.instagram_posts
(Postgres, see db.py) and push them to Snapchat Story - see
render_posts_grid_page in publish_helpers.py for the shared implementation
also used by posts_publisher_spotlight.py."""

from pathlib import Path

import streamlit as st

from publish_helpers import render_posts_grid_page
from Snapchat_Repost import post_story

LOG_PATH = Path("exports") / "streamlit_posts_publish_log.json"
IG_USER_ID = 17841401739313962  # Main - matches config.IG_USER_IDS
NAME_PREFIX = "post_db"

username = st.session_state["username"]

render_posts_grid_page(
    title="Posts publisher - Story",
    subtitle="Browse Instagram posts and push them to Snapchat Story.",
    log_path=LOG_PATH,
    username=username,
    name_prefix=NAME_PREFIX,
    ig_user_id=IG_USER_ID,
    post_one=lambda access_token, media_id: post_story(access_token, media_id),
)
