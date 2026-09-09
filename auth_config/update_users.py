"""Add users, reset a password, or remove a user in the streamlit-authenticator
config.yaml stored in Azure Blob Storage (container STREAMLIT_AUTH_CONTAINER_NAME,
see src/config.py / login.py).

Every run backs up the current config to auth/backups/ before uploading the
change, so prior versions stay recoverable even though blob storage (unlike
Key Vault) has no automatic version history.

Run from the repo root: python auth_config/update_users.py
"""

import sys
from datetime import datetime
from getpass import getpass
from pathlib import Path

import bcrypt
from utils433.absUtils import absUtils

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))
from config import STREAMLIT_AUTH_BLOB_NAME, STREAMLIT_AUTH_CONTAINER_NAME, Secrets

BACKUP_PREFIX = "auth/backups/config"


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def prompt_new_users(existing_usernames: dict) -> dict:
    new_users = {}
    while True:
        username = input("\nUsername (leave blank to finish): ").strip()
        if not username:
            break
        if username in existing_usernames or username in new_users:
            print(f"User '{username}' already exists. Pick another username.")
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

        new_users[username] = {"name": name, "email": email, "password": hash_password(pwd)}
        print(f"Queued new user: {username}")
    return new_users


def prompt_reset_password(usernames: dict) -> tuple[str, str] | None:
    print(f"\nExisting users: {', '.join(usernames) or '(none)'}")
    username = input("Username to reset password for (leave blank to cancel): ").strip()
    if not username:
        return None
    if username not in usernames:
        print(f"User '{username}' not found.")
        return None

    while True:
        pwd = getpass(f"New password for {username}: ")
        if not pwd:
            print("Password cannot be empty.")
            continue
        if getpass("Confirm new password: ") != pwd:
            print("Passwords did not match. Try again.")
            continue
        break

    return username, hash_password(pwd)


def prompt_remove_user(usernames: dict) -> str | None:
    print(f"\nExisting users: {', '.join(usernames) or '(none)'}")
    username = input("Username to remove (leave blank to cancel): ").strip()
    if not username:
        return None
    if username not in usernames:
        print(f"User '{username}' not found.")
        return None
    return username


def main():
    abs_ds = absUtils(Secrets.ABS_STORAGE_ACCOUNT_NAME_DS, Secrets.ABS_STORAGE_ACCOUNT_KEY_DS)

    print(f"Downloading '{STREAMLIT_AUTH_BLOB_NAME}' from '{STREAMLIT_AUTH_CONTAINER_NAME}'...")
    config = abs_ds.load_object(STREAMLIT_AUTH_CONTAINER_NAME, STREAMLIT_AUTH_BLOB_NAME)

    credentials = config.setdefault("credentials", {})
    usernames = credentials.setdefault("usernames", {})
    print(f"Current users ({len(usernames)}): {', '.join(usernames) or '(none)'}")

    print("\nWhat do you want to do?")
    print("  1. Add new user(s)")
    print("  2. Reset a password")
    print("  3. Remove a user")
    choice = input("Choice [1/2/3]: ").strip()

    if choice == "1":
        new_users = prompt_new_users(usernames)
        if not new_users:
            print("No new users added. Nothing to upload.")
            return
        print(f"\nAbout to add {len(new_users)} user(s): {', '.join(new_users)}")
        if input("Proceed with upload? [y/N]: ").strip().lower() != "y":
            print("Aborted. No changes uploaded.")
            return
        apply_change = lambda: usernames.update(new_users)

    elif choice == "2":
        result = prompt_reset_password(usernames)
        if not result:
            print("Nothing to upload.")
            return
        username, new_hash = result
        print(f"\nAbout to reset password for '{username}'.")
        if input("Proceed with upload? [y/N]: ").strip().lower() != "y":
            print("Aborted. No changes uploaded.")
            return
        apply_change = lambda: usernames[username].__setitem__("password", new_hash)

    elif choice == "3":
        username = prompt_remove_user(usernames)
        if not username:
            print("Nothing to upload.")
            return
        print(f"\nAbout to remove user '{username}'.")
        if input("Proceed with upload? [y/N]: ").strip().lower() != "y":
            print("Aborted. No changes uploaded.")
            return
        apply_change = lambda: usernames.pop(username)

    else:
        print("Invalid choice. Exiting.")
        return

    # Back up the config as it currently stands in blob storage *before*
    # mutating it in memory, otherwise the "backup" would already contain
    # the change it's meant to be a fallback from.
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_blob = f"{BACKUP_PREFIX}_{timestamp}.yaml"
    print(f"Backing up current config to '{backup_blob}'...")
    abs_ds.upload_object(
        data=config,
        container_name=STREAMLIT_AUTH_CONTAINER_NAME,
        blob_name=backup_blob,
    )

    apply_change()

    print(f"Uploading updated config to '{STREAMLIT_AUTH_BLOB_NAME}'...")
    abs_ds.upload_object(
        data=config,
        container_name=STREAMLIT_AUTH_CONTAINER_NAME,
        blob_name=STREAMLIT_AUTH_BLOB_NAME,
    )
    print("Done.")


if __name__ == "__main__":
    main()
