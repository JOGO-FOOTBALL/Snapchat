"""Paste Instagram permalinks, preview them, and publish the video slides to
Snapchat Spotlight - same flow as test_reel_publish.ipynb's Spotlight step.
Spotlight is video-only, so image slides (and image-only posts) are skipped."""

import streamlit as st

from Snapchat_Repost import SNAPCHAT_PROFILE_ID, post_spotlight
from publish_helpers import render_publisher_page

SPOTLIGHT_LOCALE = "en_US"

username = st.session_state["username"]

render_publisher_page(
    title="Spotlight Publisher - By Url",
    subtitle="Post Instagram posts to Snapchat Spotlight.",
    session_key="spotlight_found",
    source="manual_url",
    destination="spotlight",
    username=username,
    name_prefix="streamlit_spotlight",
    post_one=lambda access_token, media_id, caption: post_spotlight(
        access_token, media_id, locale=SPOTLIGHT_LOCALE, description=caption,
    ),
    video_only=True,
    video_only_notice="{skipped} image slide(s) skipped - Spotlight is video-only.",
)
