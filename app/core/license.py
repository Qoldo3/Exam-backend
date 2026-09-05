"""Single-client license gate (payment-acknowledgement switch, not DRM).

Model
-----
* The developer picks a long random secret (never shipped).
* Only ``sha256(secret)`` hex lives in production config as ``LICENSE_HASH``.
* The client enters the raw secret once; the entered key is hashed and
  compared in constant time (``secrets.compare_digest``), so the raw key is
  never stored — only a marker containing ``sha256(entered_key)``.
* The marker file lives OUTSIDE the source tree (env-configurable path) so
  redeploys never wipe the activation.

The gate only arms when ``LICENSE_ENABLED`` is true AND ``LICENSE_HASH`` is
non-empty — development and test environments are never locked.
"""
from __future__ import annotations

import hashlib
import os
import secrets

from app.core.config import settings

# backend/ = three levels up from this file (app/core/license.py)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def hash_key(key: str) -> str:
    """Hex sha256 of a license key (used for both compare and the marker)."""
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()


def license_enabled() -> bool:
    return bool(settings.LICENSE_ENABLED and settings.LICENSE_HASH)


def license_file_path() -> str:
    """Absolute marker path (defaults to ``<backend>/data/license.dat``).

    Relative values resolve against the backend package directory — NOT the
    current working directory — so the marker stays in the same place no matter
    where the server is launched from.
    """
    path = (settings.LICENSE_FILE or "").strip()
    if not path:
        path = "data/license.dat"  # config default; never resolve an empty value to a dir
    if os.path.isabs(path):
        return path
    return os.path.join(BACKEND_DIR, path)


def is_activated() -> bool:
    """True when the marker exists and its content matches the configured hash.

    Also returns True when the gate is disabled (dev/test).
    """
    if not license_enabled():
        return True
    try:
        with open(license_file_path(), "r", encoding="utf-8") as f:
            stored = f.read().strip()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(stored) and secrets.compare_digest(stored, settings.LICENSE_HASH)


def activate(key: str) -> bool:
    """Activate the system with the license secret.

    Writes the marker file (content = the configured hash, only when the key
    matches — the marker never stores the raw key or its hash). Returns True
    only on success.
    """
    if not license_enabled():
        return True
    if not secrets.compare_digest(hash_key(key), settings.LICENSE_HASH):
        return False
    path = license_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(settings.LICENSE_HASH)
    except OSError:
        return False
    return True
