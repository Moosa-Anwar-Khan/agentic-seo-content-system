"""Create the first admin or recover the sole admin from a trusted server console."""

import argparse
import secrets

from src.config.settings import Settings
from src.security.auth import AuthService, hash_password
from src.storage.factory import create_storage


def main():
    parser = argparse.ArgumentParser(description="Set up or recover the first administrator")
    parser.add_argument("--email", required=True, help="Administrator email address")
    parser.add_argument("--recover", action="store_true", help="Reset an existing administrator's credentials and MFA")
    args = parser.parse_args()
    settings = Settings()
    service = AuthService(create_storage(settings), settings.auth_encryption_key.get_secret_value())
    password = secrets.token_urlsafe(24)
    if args.recover:
        user = service.store.user("email", args.email.strip().lower())
        if not user or user["role"] != "admin":
            parser.error("Administrator account not found")
        service.store.reset_credentials(user["id"], hash_password(password))
        print("Administrator credentials reset. All existing sessions were revoked.")
    else:
        service.bootstrap_admin(args.email, password)
        print("Sam's administrator account was created.")
    print("One-time temporary password (shown only now): " + password)


if __name__ == "__main__":
    main()
