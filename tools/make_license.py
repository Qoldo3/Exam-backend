"""Generate the license secret and its LICENSE_HASH for production.

RUN THIS ONLY ON YOUR OWN MACHINE — never ship this to the client.

Usage:
    python tools/make_license.py             # generate a fresh random secret
    python tools/make_license.py "MY KEY"    # print the hash of a key you chose

Keep the secret in a password manager. Give the secret to the client only at
handover; put LICENSE_HASH (never the secret) in the production .env.
"""
from __future__ import annotations

import hashlib
import secrets
import sys

ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*-_=+?"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def main() -> int:
    if len(sys.argv) > 1:
        secret = sys.argv[1]
        print(f"secret       = {secret}")
        print(f"LICENSE_HASH = {hash_key(secret)}")
        print("(hash of the key you provided)")
        return 0

    secret = "".join(secrets.choice(ALPHABET) for _ in range(40))
    print("=== LICENSE SECRET (give THIS to the client at handover) ===")
    print(secret)
    print()
    print("=== LICENSE_HASH (put THIS in the production .env) ===")
    print(hash_key(secret))
    print()
    print("Keep the secret somewhere safe (password manager). Never commit it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
