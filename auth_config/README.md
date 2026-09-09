# auth_config

Scripts for managing the streamlit-authenticator `config.yaml` used by `src/streamlit_app.py`.

The config (usernames, bcrypt-hashed passwords, cookie settings) is stored as a
YAML blob in **Azure Blob Storage**, container `snapchat-app` (see
`src/config.py`'s `STREAMLIT_AUTH_CONTAINER_NAME` / `STREAMLIT_AUTH_BLOB_NAME`) -
not in git. This is a dedicated container, separate from other apps'
(e.g. expense-claim-generator's `expense-claim-app`), so these scripts never
touch another app's credentials.

## Blob layout

```
snapchat-app/
└── auth/
    ├── config.yaml                        # live config used by the app
    └── backups/
        └── config_<YYYYMMDDTHHMMSS>.yaml  # automatic backup before each update_users.py edit
```

## Prerequisites

- Python deps: `pyyaml`, `bcrypt`, `utils433` (private package - install with
  `pip install git+https://<your-github-token>@github.com/JOGO-FOOTBALL/utils433.git@v0.0.66`),
  plus the app's existing Azure deps (all in `requirements.txt`/`pyproject.toml`).
- Environment variables required by `src/config.py`: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
  `AZURE_CLIENT_SECRET`, `AZURE_VAULT_URL` (typically loaded from `.env`).
- Access to the `DatascienceAzureBlobStorageAccountName` / `...Key` secrets in
  Key Vault, and write access to the `snapchat-app` blob container.

Run all scripts from the repo root:

```bash
python auth_config/<script>.py
```

## Scripts

### `init_config.py` - first-time bootstrap

Use this **once per environment** to create the initial `auth/config.yaml` blob.

- Refuses to run if the live blob already exists (use `update_users.py` instead).
- Prompts for cookie settings; press Enter on the secret key to auto-generate a strong random key.
- Prompts for at least one user (username, name, email, password).

### `update_users.py` - add/remove users, reset passwords

1. Downloads the current config from blob storage into memory.
2. Lets you add user(s), reset a password, or remove a user.
3. Backs up the config as it stood *before* the change to `auth/backups/config_<timestamp>.yaml`.
4. Uploads the updated config to the live blob - the cookie block is left untouched
   unless you explicitly change it, so this never invalidates other users' sessions.

## Typical workflows

**Brand-new environment:**

```bash
python auth_config/init_config.py
```

**Add a new user / reset a password / remove a user:**

```bash
python auth_config/update_users.py
```

## Safety notes

- Never commit `config.yaml` to git - it only ever lives in the `snapchat-app` blob container.
- Rotating the cookie key logs out every user. `update_users.py` never touches
  the cookie block.
- Passwords are bcrypt-hashed before upload. Plaintext passwords never leave
  the local terminal.
- Unlike Key Vault, blob storage has no automatic version history - that's why
  `update_users.py` writes a timestamped backup under `auth/backups/` before
  every change. These accumulate; prune manually when they get unwieldy.
