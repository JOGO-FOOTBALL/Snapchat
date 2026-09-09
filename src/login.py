"""Multi-user login gate for streamlit_app.py, via streamlit-authenticator.

Credentials (usernames, bcrypt-hashed passwords, cookie settings) live in
Azure Blob Storage as a single YAML blob (container STREAMLIT_AUTH_CONTAINER_NAME)
- never in git. Bootstrap/manage them with auth_config/init_config.py and
auth_config/update_users.py (see auth_config/README.md).
"""

from pathlib import Path

import streamlit as st
import streamlit_authenticator as stauth
from utils433.absUtils import absUtils

from config import STREAMLIT_AUTH_BLOB_NAME, STREAMLIT_AUTH_CONTAINER_NAME, Secrets

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "Logo433.png"


@st.cache_data(ttl=300)
def _load_auth_config() -> dict:
    abs_ds = absUtils(Secrets.ABS_STORAGE_ACCOUNT_NAME_DS, Secrets.ABS_STORAGE_ACCOUNT_KEY_DS)
    return abs_ds.load_object(STREAMLIT_AUTH_CONTAINER_NAME, STREAMLIT_AUTH_BLOB_NAME)


def require_login() -> str:
    """Blocks (via st.stop()) until the user is logged in. Returns the username."""
    config = _load_auth_config()
    authenticator = stauth.Authenticate(
        config["credentials"],
        config["cookie"]["name"],
        config["cookie"]["key"],
        config["cookie"]["expiry_days"],
    )

    # Reserve the logo's spot before login() renders the form, but only fill
    # it once login() has actually run - a fresh session_state (e.g. right
    # after this app's container restarts) doesn't yet know a returning
    # user's cookie is valid until login() checks it, so testing
    # authentication_status beforehand would show the logo for one run even
    # when the user turns out to already be logged in.
    logo_slot = st.empty()

    authenticator.login(location="main")

    if not st.session_state.get("authentication_status") and LOGO_PATH.exists():
        with logo_slot.container():
            _, center, _ = st.columns([1, 1, 1])
            with center:
                st.image(str(LOGO_PATH), width=96)

    if st.session_state.get("authentication_status") is False:
        st.error("Username or password is incorrect.")
        st.stop()
    if st.session_state.get("authentication_status") is None:
        st.stop()

    with st.sidebar:
        st.caption(f"Logged in as {st.session_state.get('name')}")
        authenticator.logout("Logout", "sidebar")

    return st.session_state["username"]
