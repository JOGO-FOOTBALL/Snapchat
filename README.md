# Snapchat Repost

Reposts 433's Instagram content (Main account) to Snapchat Story and
Spotlight on 433's Public Profile, via the Snapchat Business API. There are
two independent ways content gets published:

1. **Manual, via a Streamlit app** - a human browses/searches Instagram
   content and clicks to publish it.
2. **Automatic, via a scheduled script** - new Instagram Reels are detected
   and pushed to Spotlight with no human review.

## How a repost actually works

Regardless of which flow triggers it, publishing one piece of Instagram
media to Snapchat always goes through the same pipeline
(`src/Snapchat_Repost.py`):

1. **Fetch** the source media from Instagram (Meta Graph API), using either a
   direct post lookup or a permalink search across recent posts.
2. **Letterbox** it onto a 1080x1920 (9:16) canvas - Instagram media is
   rarely already Story-shaped, so instead of cropping (which can cut off
   text/logos), the image/video is placed on a blurred, darkened, scaled
   copy of itself that fills the frame. Video letterboxing is done with
   `ffmpeg`, images with Pillow.
3. **Encrypt** the processed media client-side with AES-256-CBC, as required
   by Snapchat's Create Media endpoint.
4. **Upload** the ciphertext to Snapchat in <=32MB chunks, then finalize it.
5. **Publish** the finalized media as either a Story post or a Spotlight
   post (Spotlight is video-only, 5-60s).

A carousel post on Instagram becomes multiple consecutive Snaps, one per
slide, in the same order as on Instagram.

## The two publishing flows

### 1. Manual - Streamlit app (`src/streamlit_app.py`)

Run with `streamlit run src/streamlit_app.py`. Gated behind a login screen
(see [Authentication](#authentication) below); once logged in, the top nav
gives access to five pages:

| Page | File | What it does |
|---|---|---|
| Posts publisher - Story | `app_pages/posts_publisher.py` | Browses recent Instagram posts (from a Postgres table populated by a separate scraper, see `db.py`) and pushes them to Snapchat **Story**. Carousels offer "push cover slide" or "push all slides". |
| Posts publisher - Spotlight | `app_pages/posts_publisher_spotlight.py` | Same grid, filtered to Reels/videos only, pushes to **Spotlight**. |
| Stories publisher | `app_pages/stories_publisher.py` | Browses 433's own recent Instagram *Stories* (last 24h) from the same Postgres table and pushes selected ones to Snapchat Story. |
| Story Publisher - By Url | `app_pages/story_publisher.py` | Paste one or more Instagram permalinks, preview every slide, pick which to post, publish to Story. Works for any post the Graph API can still find, not just what's in the DB. |
| Spotlight Publisher - By Url | `app_pages/spotlight_publisher.py` | Same permalink-based flow, publishing to Spotlight (video slides only). |

Shared UI/publishing logic for all five pages lives in
`src/publish_helpers.py`. Every publish action is logged to a JSON file
under `src/exports/` (one log file per page) so already-posted items show
"already posted" instead of being offered again.

### 2. Automatic - Reels autopublish (`src/Snapchat_Reels_Autopublish.py`)

A standalone script meant to be triggered every ~10 minutes by an external
scheduler (cron, Windows Task Scheduler, Airflow, a k8s CronJob, ...). Each
run:

- Scans Instagram for Reels posted in the last `--lookback-minutes` (default
  60 - wider than the run interval, since Instagram's Graph API sometimes
  delays returning a Reel's `media_url` by minutes to hours).
- Skips anything already published or given up on, tracked in
  `src/exports/snapchat_reels_autopublish_state.json`.
- Publishes each new, ready Reel straight to Spotlight - no manual review.
- Gives up on a Reel (marks it `given_up`) if it still has no `media_url`
  after `--give-up-after-hours` (default 6).

Supports `--dry-run` to log what would be published without actually
posting. Run via Docker Compose: `docker compose run --rm reels-autopublish`.

## Where the data comes from

- **Instagram source content**: read via the Meta Graph API
  (`config.GRAPH`), authenticated with a long-lived Meta API token. Only the
  "Main" IG account is scanned for new posts (`config.IG_USER_IDS`); a few
  other 433 accounts are listed in `config.ACCOUNT_CHANNEL` for
  labeling/reference only.
- **Posts/Stories browser pages** (`db.py`) read from a Postgres table
  (`socials_analytics.instagram_posts` / `instagram_stories`) that's
  populated by a separate scraper outside this repo - this app only reads
  from it, never writes.

## Project layout

```
src/
  streamlit_app.py            Entry point: login gate + page navigation
  login.py                    streamlit-authenticator login gate
  config.py                   Env vars + Azure Key Vault secret loading
  db.py                       Read-only Postgres access for the browser pages
  Snapchat_Repost.py          Core pipeline: fetch/letterbox/encrypt/upload/publish
  Snapchat_Reels_Autopublish.py   Unattended Reels -> Spotlight scheduler script
  publish_helpers.py          Shared Streamlit UI/publish logic for all pages
  app_pages/                  The five Streamlit pages (see table above)
  exports/                    Publish logs + autopublish state (gitignored)
auth_config/                  CLI scripts to bootstrap/manage app login users
Dockerfile, compose.yaml      Container build + the two runnable services
```

## Setup

1. Copy `.env.example` to `.env` and fill in the Azure Key Vault and
   Snapchat API credentials (see comments in `config.py` for where each one
   comes from - the Snapchat app must be created via Ads Manager's Business
   Dashboard, not the regular Developer Portal).
2. `pip install -r requirements.txt` (or `docker compose build`).
3. First-time only: bootstrap the app's login users with
   `python auth_config/init_config.py` (see `auth_config/README.md`).
4. Run the Streamlit app: `streamlit run src/streamlit_app.py`, or via
   Docker: `docker compose up story-publisher` (served on host port 8502).
5. Run the autopublish pipeline manually with
   `docker compose run --rm reels-autopublish`, or schedule it externally.

## Authentication

The Streamlit app is gated by `login.py` using `streamlit-authenticator`.
Usernames, bcrypt-hashed passwords, and cookie settings are stored as a
single YAML secret in Azure Key Vault (never in git) and managed with the
scripts in `auth_config/`. See `auth_config/README.md` for adding/removing
users or resetting a password.
