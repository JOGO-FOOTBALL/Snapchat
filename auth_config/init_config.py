"""Bootstrap the initial streamlit-authenticator config as a single Azure Key
Vault secret (see config.STREAMLIT_AUTH_CONFIG_SECRET_NAME / login.py).

Use this once per environment. Refuses to run if the secret already exists -
use update_users.py to add users or reset a password afterwards.

Run from the repo root: python auth_config/init_config.py
"""

import secrets as std_secrets
import sys
from getpass import getpass
from pathlib import Path

import bcrypt
import yaml
from azure.core.exceptions import ResourceNotFoundError

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))
from config import STREAMLIT_AUTH_CONFIG_SECRET_NAME, secret_client


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def prompt_cookie() -> dict:
    name = input("Cookie name (default 'snapchat_story_publisher_auth'): ").strip() or "snapchat_story_publisher_auth"
    key_input = input(
        "Cookie secret key (press Enter to auto-generate a secure random key): "
    ).strip()
    key = key_input or std_secrets.token_urlsafe(48)
    if not key_input:
        print("Generated a secure random cookie key.")

    while True:
        days_str = input("Cookie expiry in days (default 1): ").strip() or "1"
        try:
            days = int(days_str)
            if days <= 0:
                print("Must be a positive integer.")
                continue
            break
        except ValueError:
            print("Invalid number. Try again.")

    return {"name": name, "key": key, "expiry_days": days}


def prompt_users() -> dict:
    users = {}
    while True:
        username = input("\nUsername (leave blank when done): ").strip()
        if not username:
            if not users:
                print("At least one user is required.")
                continue
            break
        if username in users:
            print(f"User '{username}' already added.")
            continue

        name = input(f"Full name for {username}: ").strip() or username
        email = input(f"Email for {username}: ").strip() or f"{username}@by433.com"

        while True:
            pwd = getpass(f"Password for {username}: ")
            if not pwd:
                print("Password cannot be empty.")
                continue
            if getpass("Confirm password: ") != pwd:
                print("Passwords did not match. Try again.")
                continue
            break

        users[username] = {"name": name, "email": email, "password": hash_password(pwd)}
        print(f"Added user: {username}")
    return users


def main():
    print(f"Checking that secret '{STREAMLIT_AUTH_CONFIG_SECRET_NAME}' does not already exist...")
    try:
        secret_client.get_secret(STREAMLIT_AUTH_CONFIG_SECRET_NAME)
    except ResourceNotFoundError:
        print("Secret not found - proceeding with bootstrap.")
    else:
        print(f"Refusing to run: secret '{STREAMLIT_AUTH_CONFIG_SECRET_NAME}' already exists.")
        print("Use update_users.py to modify the existing config.")
        sys.exit(1)

    print("\n--- Configure cookie settings ---")
    cookie = prompt_cookie()

    print("\n--- Add initial user(s) ---")
    users = prompt_users()

    config = {"credentials": {"usernames": users}, "cookie": cookie}

    print(f"\nAbout to create '{STREAMLIT_AUTH_CONFIG_SECRET_NAME}' with {len(users)} user(s): {', '.join(users)}")
    if input("Proceed with upload? [y/N]: ").strip().lower() != "y":
        print("Aborted.")
        return

    secret_client.set_secret(STREAMLIT_AUTH_CONFIG_SECRET_NAME, yaml.safe_dump(config))
    print("Done.")


if __name__ == "__main__":
    main()
