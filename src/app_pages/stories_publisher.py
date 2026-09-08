"""Browse recent Instagram Stories pulled from socials_analytics.instagram_stories
(Postgres, see db.py) and push selected ones to Snapchat Story."""

import streamlit as st

from publish_helpers import render_db_browser_page
from db import fetch_recent_instagram_stories

LOOKBACK_HOURS = 24

username = st.session_state["username"]

render_db_browser_page(
    title="Stories publisher",
    subtitle=f"Browse Instagram Stories from the last {LOOKBACK_HOURS}h and push selected ones to Snapchat Story.",
    source="manual_db_stories",
    destination="story",
    username=username,
    name_prefix="story_db",
    fetch_items=lambda ig_user_id: [
        {**s, "id": s["story_id"]} for s in fetch_recent_instagram_stories(ig_user_id, LOOKBACK_HOURS)
    ],
    clear_cache=fetch_recent_instagram_stories.clear,
)
