"""Browse recent Instagram Stories pulled from socials_analytics.instagram_stories
(Postgres, see db.py) and push selected ones to Snapchat Story."""

from pathlib import Path

import streamlit as st

from publish_helpers import render_db_browser_page
from db import fetch_recent_instagram_stories

LOG_PATH = Path("exports") / "streamlit_stories_publish_log.json"
IG_USER_ID = 17841401739313962  # Main - matches config.IG_USER_IDS
LOOKBACK_HOURS = 24

username = st.session_state["username"]

render_db_browser_page(
    title="Stories publisher",
    subtitle=f"Browse Instagram Stories from the last {LOOKBACK_HOURS}h and push selected ones to Snapchat Story.",
    log_path=LOG_PATH,
    username=username,
    name_prefix="story_db",
    fetch_items=lambda: [
        {**s, "id": s["story_id"]} for s in fetch_recent_instagram_stories(IG_USER_ID, LOOKBACK_HOURS)
    ],
    clear_cache=fetch_recent_instagram_stories.clear,
)
