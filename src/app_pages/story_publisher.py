"""Paste Instagram permalinks, preview them as a Snapchat Story, and publish
them - a UI on top of the same flow as publish_by_permalink.ipynb."""

import streamlit as st

from Snapchat_Repost import post_story
from publish_helpers import render_publisher_page

username = st.session_state["username"]

render_publisher_page(
    title="Story Publisher - By Url",
    subtitle="Post Instagram content as a Snapchat Story.",
    session_key="story_found",
    source="manual_url",
    destination="story",
    username=username,
    name_prefix="streamlit_story",
    post_one=lambda access_token, media_id, caption: post_story(access_token, media_id),
)
