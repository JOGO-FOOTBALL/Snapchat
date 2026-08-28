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


# Owned Instagram accounts (kept for reference / channel labels)
ACCOUNT_CHANNEL = {
    "17841401193572991": "NL",
    "17841401739313962": "Main",
    "17841400565524817": "WomenFC",
    "17841401435554534": "E-sports",
}
# Only Main gets scanned for new posts to queue for Snapchat
IG_USER_IDS = ("17841401739313962",)

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
