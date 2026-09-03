"""
Entry point for the Snapchat publisher app - login gate + navigation between
the Story and Spotlight publisher pages (app_pages/).

Run with: streamlit run src/streamlit_app.py
"""

from pathlib import Path

import streamlit as st

from login import require_login

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "Logo433.png"

st.set_page_config(
    page_title="Snapchat publisher",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else ":material/send:",
    layout="wide",
)

# 433 house style (matches Music_tool): primaryColor #E5FF00 in
# .streamlit/config.toml is too light for Streamlit's default white primary-
# button text, so force black there; headings get the brand lime color too.
st.markdown(
    """
    <style>
    button[kind="primary"], button[kind="primaryFormSubmit"] {
        color: #000000 !important;
        font-weight: 700 !important;
    }
    h1, h2, h3 {
        color: #E5FF00 !important;
    }
    /* Checked checkboxes fill with the lime primary color. The checkmark is
    an unfilled <polyline> (a line icon, not a filled shape) - coloring the
    whole svg's fill also fills the implied closed area under the open
    polyline (a blobby triangle), and killing stroke removes the only thing
    that actually draws it. Only recolor the stroke, and force fill off. */
    .stCheckbox svg polyline {
        stroke: #000000 !important;
        fill: none !important;
    }
    /* Top nav in brand lime so inactive items stand out against the dark
    background. Excludes the active/selected page (aria-current="page") -
    that one already gets a lime pill background from the theme's own
    primaryColor, so forcing lime text on it too made it unreadable
    (lime-on-lime); its original (dark) text color reads fine there. */
    .stTopNavLink:not([aria-current="page"]),
    .stTopNavLink:not([aria-current="page"]) * {
        color: #E5FF00 !important;
    }
    /* Equal-size thumbnail cards in the DB browser grids (Stories/Posts
    publisher) - source images have mixed aspect ratios, so crop to a fixed
    box instead of letting each card take its natural image height. */
    [class*="st-key-db_card_thumb_"] {
        height: 170px !important;
        flex: 0 0 170px !important;
        align-self: flex-start !important;
        overflow: hidden;
    }
    [class*="st-key-db_card_thumb_"] img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
    /* Same fixed-size treatment for the caption block above the buttons (see
    the card-height comment below for why height + flex-basis are both
    needed). */
    [class*="st-key-db_card_caption_"] {
        height: 24px !important;
        flex: 0 0 24px !important;
        align-self: flex-start !important;
        overflow: hidden;
        width: 100% !important;
        text-align: center !important;
    }
    [class*="st-key-db_card_caption_"] * {
        width: 100% !important;
        text-align: center !important;
    }
    /* Posts publisher's per-card action button(s): "Push cover slide" +
    "Push all slides" (carousels), or a single "Push"/"Push Reel" otherwise.
    Same size/centering for all of them; push_ additionally gets a
    margin-top - not a spacer element, st.container(height=..) enforces its
    own minimum height and threw off precise alignment - so a lone button
    lines up with "Push all slides" instead of floating at the cover-slide
    row. */
    [class*="st-key-post_db_cover_"],
    [class*="st-key-post_db_all_"],
    [class*="st-key-post_db_push_"] {
        width: 100% !important;
    }
    [class*="st-key-post_db_push_"] {
        margin-top: 54px !important;
        flex: 0 0 auto !important;
        align-self: flex-start !important;
    }
    [class*="st-key-post_db_cover_"] button,
    [class*="st-key-post_db_all_"] button,
    [class*="st-key-post_db_push_"] button {
        min-height: 38px !important;
        width: 100% !important;
        box-sizing: border-box !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-align: center !important;
        font-size: 0.72rem !important;
        white-space: nowrap !important;
    }
    /* Pin every card in a DB browser grid (Stories/Posts publisher) to the
    same fixed height, so small content differences (a carousel note present
    on some cards, a shorter/longer caption line, sub-pixel rendering) never
    leave one card's border a few px shorter than its neighbors. Generous
    values with no overflow clipping - a rare oversized card just extends
    slightly past the line instead of hiding a button.
    align-self: flex-start opts each card out of its row's flex cross-axis
    "stretch" (which re-equalizes siblings to that row's own tallest natural
    card). flex/flex-basis is the other half: this card is itself a flex
    column item, and Streamlit sets its own inline flex-basis for auto-sizing
    - flex-basis wins over the height property for a column item's main-axis
    size whenever it isn't 'auto', so height alone silently loses out to
    Streamlit's inline value even with !important on a different property. */
    [class*="st-key-db_card_post_db_"] {
        height: 348px !important;
        flex: 0 0 348px !important;
        align-self: flex-start !important;
    }
    [class*="st-key-db_card_story_db_"] {
        height: 290px !important;
        flex: 0 0 290px !important;
        align-self: flex-start !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.session_state["username"] = require_login()

page = st.navigation(
    [
        st.Page("app_pages/posts_publisher.py", title="Posts publisher - Story", icon=":material/grid_view:"),
        st.Page("app_pages/posts_publisher_spotlight.py", title="Posts publisher - Spotlight", icon=":material/bolt:"),
        st.Page("app_pages/stories_publisher.py", title="Stories publisher", icon=":material/database:"),
        st.Page("app_pages/story_publisher.py", title="Story Publisher - By Url", icon=":material/history:"),
        st.Page("app_pages/spotlight_publisher.py", title="Spotlight Publisher - By Url", icon=":material/bolt:"),
    ],
    position="top",
)
page.run()
