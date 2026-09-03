"""Paste Instagram permalinks, preview them as a Snapchat Story, and publish
them - a UI on top of the same flow as publish_by_permalink.ipynb."""

from pathlib import Path

import streamlit as st

from Snapchat_Repost import post_story
from publish_helpers import render_publisher_page

LOG_PATH = Path("exports") / "streamlit_story_publish_log.json"

username = st.session_state["username"]

render_publisher_page(
    title="Story Publisher - By Url",
    subtitle="Post Instagram content as a Snapchat Story.",
    session_key="story_found",
    log_path=LOG_PATH,
    username=username,
    name_prefix="streamlit_story",
    post_one=lambda access_token, media_id: post_story(access_token, media_id),
)
