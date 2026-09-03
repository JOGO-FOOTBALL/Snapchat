"""Paste Instagram permalinks, preview them, and publish the video slides to
Snapchat Spotlight - same flow as test_reel_publish.ipynb's Spotlight step.
Spotlight is video-only, so image slides (and image-only posts) are skipped."""

from pathlib import Path

import streamlit as st

from Snapchat_Repost import SNAPCHAT_PROFILE_ID, post_spotlight
from publish_helpers import render_publisher_page

LOG_PATH = Path("exports") / "streamlit_spotlight_publish_log.json"
SPOTLIGHT_LOCALE = "en_US"

username = st.session_state["username"]

render_publisher_page(
    title="Spotlight Publisher - By Url",
    subtitle="Post Instagram posts to Snapchat Spotlight.",
    session_key="spotlight_found",
    log_path=LOG_PATH,
    username=username,
    name_prefix="streamlit_spotlight",
    post_one=lambda access_token, media_id: post_spotlight(access_token, media_id, locale=SPOTLIGHT_LOCALE),
    video_only=True,
    video_only_notice="{skipped} image slide(s) skipped - Spotlight is video-only.",
)
