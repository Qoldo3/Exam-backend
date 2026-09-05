"""License gate tests.

The gate middleware reads the settings at request time, so we monkeypatch
``app.core.config.settings`` fields directly. All marker files are written to
a temp dir; the real dev ``data/license.dat`` is never touched.
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.license import hash_key, license_file_path
from app.main import app

SECRET = "x9Kz2!vQ7#mP4@dR8$cW3&nB6*eT5(ghJ1"
HASH = hash_key(SECRET)


@pytest.fixture()
def locked(monkeypatch, tmp_path):
    """Arm the gate with a known secret and an isolated marker file."""
    marker = tmp_path / "license.dat"
    monkeypatch.setattr(settings, "LICENSE_ENABLED", True)
    monkeypatch.setattr(settings, "LICENSE_HASH", HASH)
    monkeypatch.setattr(settings, "LICENSE_FILE", str(marker))
    return {"marker": marker, "secret": SECRET, "hash": HASH}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_gate_disabled_by_default(client, monkeypatch):
    """Explicitly disabled license config → the gate never engages."""
    monkeypatch.setattr(settings, "LICENSE_ENABLED", False)
    monkeypatch.setattr(settings, "LICENSE_HASH", "")
    assert client.get("/api/v1/license/status").json() == {
        "enabled": False,
        "activated": True,
    }
    # normal endpoint reachable while disabled
    assert client.get("/api/v1/exams").status_code == 200


def test_locked_blocks_everything_except_license_and_health(client, locked):
    assert client.get("/api/v1/license/status").json() == {
        "enabled": True,
        "activated": False,
    }
    assert client.get("/api/v1/exams").status_code == 402
    assert client.post(
        "/api/v1/auth/login",
        json={"national_id": "0012345679", "phone": "09120000000"},
    ).status_code == 402
    assert client.get("/api/v1/admin/stats").status_code == 402
    # the activation endpoint itself must stay reachable
    assert client.post("/api/v1/license/activate", json={"key": "wrong-key"}).status_code == 200
    # health is public infrastructure
    assert client.get("/health").status_code == 200


def test_locked_blocks_docs_and_openapi_too(client, locked):
    """Non-API surface (/docs, /openapi.json, /redoc) must also be hidden while locked."""
    assert client.get("/docs").status_code == 402
    assert client.get("/openapi.json").status_code == 402
    assert client.get("/redoc").status_code == 402


def test_license_file_relative_resolves_against_backend_dir(monkeypatch):
    """A relative LICENSE_FILE must anchor to the backend dir, not the CWD."""
    monkeypatch.setattr(settings, "LICENSE_FILE", "data/license.dat")
    path = license_file_path()
    assert os.path.isabs(path)
    assert path.replace("\\", "/").endswith("backend/data/license.dat")


def test_wrong_key_stays_locked(client, locked):
    r = client.post("/api/v1/license/activate", json={"key": "wrong-key"})
    assert r.json() == {"enabled": True, "activated": False}
    assert client.get("/api/v1/exams").status_code == 402


def test_correct_key_unlocks_and_persists(client, locked):
    r = client.post("/api/v1/license/activate", json={"key": SECRET})
    assert r.json() == {"enabled": True, "activated": True}
    assert client.get("/api/v1/license/status").json() == {
        "enabled": True,
        "activated": True,
    }
    # normal endpoints work again
    assert client.get("/api/v1/exams").status_code == 200
    # marker file exists and contains the hash (never the raw key)
    content = locked["marker"].read_text(encoding="utf-8").strip()
    assert content == HASH
    assert SECRET not in content


def test_activation_survives_marker_recheck(client, locked):
    """Once activated, a fresh check (new client) still sees it unlocked."""
    client.post("/api/v1/license/activate", json={"key": SECRET})
    with TestClient(app) as c2:
        assert c2.get("/api/v1/license/status").json()["activated"] is True


def test_deleting_marker_relocks(client, locked):
    client.post("/api/v1/license/activate", json={"key": SECRET})
    os.remove(locked["marker"])
    assert client.get("/api/v1/exams").status_code == 402


def test_no_marker_means_locked(client, locked):
    """Fresh install with the gate armed and no marker file → everything is locked."""
    assert not locked["marker"].exists()
    assert client.get("/api/v1/exams").status_code == 402
