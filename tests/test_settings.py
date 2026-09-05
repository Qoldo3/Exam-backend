"""Tests for the admin payment settings and the gateway safety fallback.

Covers: merchant-id format validation, resolve_payment_config mode/fallback
logic, the /admin/settings API (superadmin only, validation, persistence) and
the student payment lock when the merchant is missing/malformed.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.setting import SystemSetting, set_setting
from app.models.user import User
from app.payments import PLACEHOLDER_MERCHANT, PaymentConfig, merchant_valid, resolve_payment_config
from app.payments.zarinpal import ZarinPalProvider

VALID_MERCHANT = "123e4567-e89b-42d3-a456-426614174000"


# ---------- helpers ----------


async def _clear_settings():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(SystemSetting))
        await db.commit()


async def _resolve() -> PaymentConfig:
    async with AsyncSessionLocal() as db:
        return await resolve_payment_config(db)


def _set(key: str, value: str) -> None:
    async def inner():
        async with AsyncSessionLocal() as db:
            await set_setting(db, key, value)
            await db.commit()

    asyncio.run(inner())


@pytest.fixture(autouse=True)
def _clean():
    asyncio.run(_clear_settings())
    yield
    asyncio.run(_clear_settings())


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _ensure_superadmin() -> None:
    async def inner():
        async with AsyncSessionLocal() as db:
            u = (
                await db.execute(select(User).where(User.national_id == "0012345679"))
            ).scalar_one_or_none()
            if not u:
                db.add(
                    User(full_name="مدیر ارشد", national_id="0012345679",
                         phone="09120000000", role="superadmin")
                )
                await db.commit()

    asyncio.run(inner())


def _register_student(client: TestClient, nid: str, phone: str) -> dict:
    r = client.post("/api/v1/auth/register", json={
        "full_name": "دانش‌آموز تست", "national_id": nid, "phone": phone,
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------- merchant format ----------


def test_merchant_valid_uuid():
    assert merchant_valid(VALID_MERCHANT) is True


def test_merchant_invalid_format():
    assert merchant_valid("") is False
    assert merchant_valid("not-a-merchant") is False


# ---------- resolve_payment_config ----------


def test_resolve_local_default(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_MODE", "local")
    cfg = asyncio.run(_resolve())
    assert cfg.mode == "local"
    assert cfg.provider is None
    assert cfg.payments_admin_only is False


def test_resolve_forces_sandbox_and_admin_only_when_merchant_missing(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_MODE", "zarinpal_live")
    monkeypatch.setattr(settings, "ZARINPAL_MERCHANT", "")
    cfg = asyncio.run(_resolve())
    assert cfg.mode == "zarinpal_sandbox"
    assert cfg.payments_admin_only is True
    assert cfg.merchant_valid is False
    assert cfg.merchant == PLACEHOLDER_MERCHANT
    assert isinstance(cfg.provider, ZarinPalProvider)
    assert cfg.provider.sandbox is True


def test_resolve_forces_sandbox_and_admin_only_when_merchant_invalid(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_MODE", "zarinpal_live")
    monkeypatch.setattr(settings, "ZARINPAL_MERCHANT", "12345")
    cfg = asyncio.run(_resolve())
    assert cfg.mode == "zarinpal_sandbox"
    assert cfg.payments_admin_only is True
    assert cfg.merchant_valid is False
    assert cfg.merchant == "12345"


def test_resolve_live_with_valid_stored_merchant(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_MODE", "zarinpal_live")
    monkeypatch.setattr(settings, "ZARINPAL_MERCHANT", "")
    _set("payment_mode", "zarinpal_live")
    _set("zarinpal_merchant", VALID_MERCHANT)
    cfg = asyncio.run(_resolve())
    assert cfg.mode == "zarinpal_live"
    assert cfg.merchant_valid is True
    assert cfg.payments_admin_only is False
    assert cfg.merchant == VALID_MERCHANT
    assert cfg.provider.sandbox is False


def test_resolve_sandbox_with_valid_merchant(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_MODE", "zarinpal_sandbox")
    monkeypatch.setattr(settings, "ZARINPAL_MERCHANT", "")
    _set("zarinpal_merchant", VALID_MERCHANT)
    cfg = asyncio.run(_resolve())
    assert cfg.mode == "zarinpal_sandbox"
    assert cfg.payments_admin_only is False
    assert cfg.provider.sandbox is True


def test_resolve_env_merchant_fallback(monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_MODE", "zarinpal_live")
    monkeypatch.setattr(settings, "ZARINPAL_MERCHANT", VALID_MERCHANT)
    cfg = asyncio.run(_resolve())
    assert cfg.mode == "zarinpal_live"
    assert cfg.merchant_valid is True
    assert cfg.payments_admin_only is False


# ---------- /admin/settings API ----------


def test_settings_requires_superadmin(client):
    headers = _register_student(client, "0056789122", "09130000007")
    r = client.get("/api/v1/admin/settings", headers=headers)
    assert r.status_code == 403, r.text


def test_put_invalid_merchant_rejected(client):
    _ensure_superadmin()
    login = client.post("/api/v1/auth/login", json={
        "national_id": "0012345679", "phone": "09120000000"})
    assert login.status_code == 200, login.text
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = client.put("/api/v1/admin/settings", json={
        "payment_mode": "zarinpal_live", "zarinpal_merchant": "abc"}, headers=h)
    assert r.status_code == 422, r.text


def test_put_invalid_mode_rejected(client):
    _ensure_superadmin()
    login = client.post("/api/v1/auth/login", json={
        "national_id": "0012345679", "phone": "09120000000"})
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = client.put("/api/v1/admin/settings", json={
        "payment_mode": "hacked", "zarinpal_merchant": ""}, headers=h)
    assert r.status_code == 422, r.text


def test_put_valid_persists_and_reports_status(client):
    _ensure_superadmin()
    login = client.post("/api/v1/auth/login", json={
        "national_id": "0012345679", "phone": "09120000000"})
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = client.put("/api/v1/admin/settings", json={
        "payment_mode": "zarinpal_live", "zarinpal_merchant": VALID_MERCHANT}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["effective_mode"] == "zarinpal_live"
    assert body["merchant_valid"] is True
    assert body["payments_admin_only"] is False
    g = client.get("/api/v1/admin/settings", headers=h).json()
    assert g["zarinpal_merchant"] == VALID_MERCHANT
    assert g["payment_mode"] == "zarinpal_live"


def test_put_broken_state_reports_admin_only(client):
    _ensure_superadmin()
    login = client.post("/api/v1/auth/login", json={
        "national_id": "0012345679", "phone": "09120000000"})
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = client.put("/api/v1/admin/settings", json={
        "payment_mode": "zarinpal_sandbox", "zarinpal_merchant": ""}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["effective_mode"] == "zarinpal_sandbox"
    assert body["payments_admin_only"] is True
    assert body["merchant_valid"] is False


def test_student_payment_blocked_in_broken_state(client):
    """Real end-to-end of the safety rule: unconfigured gateway -> student 403."""
    _ensure_superadmin()
    login = client.post("/api/v1/auth/login", json={
        "national_id": "0012345679", "phone": "09120000000"})
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    # switch to a zarinpal mode with no merchant -> forced sandbox + admin-only
    r = client.put("/api/v1/admin/settings", json={
        "payment_mode": "zarinpal_sandbox", "zarinpal_merchant": ""}, headers=h)
    assert r.status_code == 200, r.text

    headers = _register_student(client, "0081234562", "09130000008")
    r = client.post("/api/v1/payment/start", json={"exam_id": "does-not-matter"}, headers=headers)
    assert r.status_code == 403, r.text
    assert "پیکربندی نشده" in r.json()["detail"]
