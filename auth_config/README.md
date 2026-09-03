# auth_config

Scripts for managing the streamlit-authenticator config used by `src/streamlit_app.py`.

The config (usernames, bcrypt-hashed passwords, cookie settings) is stored as a
single YAML secret in Azure Key Vault - `SnapchatStreamlitAuthConfig` (see
`src/config.py`'s `STREAMLIT_AUTH_CONFIG_SECRET_NAME`) - not in git. Key Vault
keeps every previous version of a secret automatically, so there's no separate
backup step: prior versions stay recoverable from the vault's version history.

## Prerequisites

- Python deps: `pyyaml`, `bcrypt`, plus the app's existing Azure deps (all in `requirements.txt`).
- Environment variables required by `src/config.py`: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
  `AZURE_CLIENT_SECRET`, `AZURE_VAULT_URL` (typically loaded from `.env`).
- Access to write secrets in the target Key Vault.

Run all scripts from the repo root:

```bash
python auth_config/<script>.py
```

## Scripts

### `init_config.py` - first-time bootstrap

Use this **once per environment** to create the initial config secret.

- Refuses to run if the secret already exists (use `update_users.py` instead).
- Prompts for cookie settings; press Enter on the secret key to auto-generate a strong random key.
- Prompts for at least one user (username, name, email, password).

### `update_users.py` - add/remove users, reset passwords

1. Downloads the current config from Key Vault into memory.
2. Lets you add user(s), reset a password, or remove a user.
3. Uploads the updated config on confirmation - the cookie block is left untouched
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

- Never commit the config to git - it only ever lives in Key Vault.
- Rotating the cookie key logs out every user. `update_users.py` never touches
  the cookie block.
- Passwords are bcrypt-hashed before upload. Plaintext passwords never leave
  the local terminal.
