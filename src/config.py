import os

from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient

from dotenv import load_dotenv

load_dotenv(override=True)

client_id = os.environ["AZURE_CLIENT_ID"]
tenant_id = os.environ["AZURE_TENANT_ID"]
client_secret = os.environ["AZURE_CLIENT_SECRET"]
vault_url = os.environ["AZURE_VAULT_URL"]

credentials = ClientSecretCredential(
    client_id=client_id, client_secret=client_secret, tenant_id=tenant_id
)
secret_client = SecretClient(vault_url=vault_url, credential=credentials)


class Secrets:
    # Meta Graph API token, reused to read Instagram posts as the source
    # content for the Snapchat repost queue.
    META_API_TOKEN = secret_client.get_secret("SocialsAnalyticsMetaApiToken").value

    # 433 ds database dev
    POSTGRES_USERNAME_DS_DEV = secret_client.get_secret(
        "DatasciencePsqlServerUsernameDev"
    ).value
    POSTGRES_PASSWORD_DS_DEV = secret_client.get_secret(
        "DatasciencePsqlServerPasswordDev"
    ).value
    POSTGRES_SERVER_DS_DEV = secret_client.get_secret(
        "DatasciencePsqlServerUrlDev"
    ).value
    POSTGRES_PORT_DS_DEV = secret_client.get_secret(
        "DatasciencePsqlServerPortDev"
    ).value
    POSTGRES_DATABASE_DS_DEV = secret_client.get_secret(
        "DatasciencePsqlServerDatabaseDev"
    ).value

    # 433 ds database Prod
    POSTGRES_USERNAME_DS_PROD = secret_client.get_secret(
        "DatasciencePsqlServerUsernameProd"
    ).value
    POSTGRES_PASSWORD_DS_PROD = secret_client.get_secret(
        "DatasciencePsqlServerPasswordProd"
    ).value
    POSTGRES_SERVER_DS_PROD = secret_client.get_secret(
        "DatasciencePsqlServerUrlProd"
    ).value
    POSTGRES_PORT_DS_PROD = secret_client.get_secret(
        "DatasciencePsqlServerPortProd"
    ).value
    POSTGRES_DATABASE_DS_PROD = secret_client.get_secret(
        "DatasciencePsqlServerDatabaseProd"
    ).value

    # Azure Blob Storage - DS (used to store the streamlit-authenticator
    # config, see STREAMLIT_AUTH_CONTAINER_NAME below)
    ABS_STORAGE_ACCOUNT_NAME_DS = secret_client.get_secret(
        "DatascienceAzureBlobStorageAccountName"
    ).value
    ABS_STORAGE_ACCOUNT_KEY_DS = secret_client.get_secret(
        "DatascienceAzureBlobStorageAccountKey"
    ).value

    # YouTube OAuth client - the same (Internal Workspace) Google Cloud app
    # meta-analytics uses for YouTube Analytics. Its refresh token there only
    # has the yt-analytics.readonly scope, so uploads use their own refresh
    # token (YOUTUBE_UPLOAD_REFRESH_TOKEN below).
    YOUTUBE_OAUTH_CLIENT_ID = secret_client.get_secret(
        "SocialsAnalyticsYoutubeOauthClientId"
    ).value
    YOUTUBE_OAUTH_CLIENT_SECRET = secret_client.get_secret(
        "SocialsAnalyticsYoutubeOauthClientSecret"
    ).value


# Owned Instagram accounts (kept for reference / channel labels)
ACCOUNT_CHANNEL = {
    "17841401193572991": "NL",
    "17841401739313962": "Main",
    "17841400565524817": "WomenFC",
    "17841401435554534": "E-sports",
}
# Only Main gets scanned for new posts to queue for Snapchat (the automated
# Reels-to-Spotlight pipeline, Snapchat_Reels_Autopublish.py)
IG_USER_IDS = ("17841401739313962",)

# All 433 IG accounts, for URL-based post lookup (find_post_by_permalink) and
# the Posts/Stories publisher pages' channel filter - unlike IG_USER_IDS
# above, not limited to Main.
ALL_IG_USER_IDS = tuple(int(uid) for uid in ACCOUNT_CHANNEL)

API_VER = os.getenv("IG_API_VER", "v25.0")
GRAPH = f"https://graph.facebook.com/{API_VER}"

# Snapchat (Public Profile / Content Management API, OAuth). Requires a
# Snapchat OAuth app created via Ads Manager > Business Dashboard (NOT the
# regular Developer Portal), allowlisted by Snap for the Public Profile API.
SNAPCHAT_CLIENT_ID = os.environ["SNAPCHAT_CLIENT_ID"]
SNAPCHAT_CLIENT_SECRET = os.environ["SNAPCHAT_CLIENT_SECRET"]
SNAPCHAT_REDIRECT_URI = os.environ["SNAPCHAT_REDIRECT_URI"]
SNAPCHAT_OAUTH_SCOPES = "snapchat-marketing-api snapchat-profile-api"
SNAPCHAT_REFRESH_TOKEN = os.environ["SNAPCHAT_REFRESH_TOKEN"]
SNAPCHAT_PROFILE_ID = os.environ["SNAPCHAT_PROFILE_ID"]
SNAPCHAT_TOKEN_URL = "https://accounts.snapchat.com/login/oauth2/access_token"
# Public link to 433's profile (snap_user_name of SNAPCHAT_PROFILE_ID, from
# GET /v1/public_profiles/{id}). The publish responses we store carry no
# public per-Snap link, so the "on Snapchat" checks link here instead.
SNAPCHAT_PROFILE_URL = "https://www.snapchat.com/add/official_433"

# YouTube Shorts (Data API v3 upload, OAuth). Refresh token for 433's channel
# with the youtube.upload scope - get one with
# `python auth_config/youtube_refresh_token.py`. Optional: without it the app
# still starts, only the YouTube buttons fail. Until the Google Cloud project
# passes YouTube's API audit, uploads are forced to private regardless of
# YOUTUBE_PRIVACY_STATUS.
YOUTUBE_UPLOAD_REFRESH_TOKEN = os.getenv("YOUTUBE_UPLOAD_REFRESH_TOKEN")
YOUTUBE_UPLOAD_SCOPES = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")

# streamlit-authenticator config (usernames, bcrypt-hashed passwords, cookie
# settings) for streamlit_app.py, stored as a single YAML blob in Azure Blob
# Storage rather than in git - see login.py and auth_config/. Own container
# ("snapchat-app") so this never touches other apps' auth blobs (e.g.
# expense-claim-generator's "expense-claim-app" container).
STREAMLIT_AUTH_CONTAINER_NAME = "snapchat-app"
STREAMLIT_AUTH_BLOB_NAME = "auth/config.yaml"
