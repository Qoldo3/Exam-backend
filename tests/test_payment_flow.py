"""Router-level tests for the payment flow with a mocked gateway.

The gateway provider is replaced by a fake, so no real HTTP happens and the
tests exercise the wiring: payment row creation, authority storage, status
transitions and the callback redirects.
"""
import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.exam import Exam
from app.models.payment import Payment
from app.models.user import User
from app.payments import PaymentConfig
from app.payments.base import PaymentRequestResult, VerifyResult

FRONTEND = "http://localhost:5173"


class FakeProvider:
    """Stand-in gateway; results are configured per test."""

    def __init__(self, create: PaymentRequestResult | None = None, verify: VerifyResult | None = None):
        self.create_result = create or PaymentRequestResult(
            success=True,
            gateway_ref="AUTH-FAKE-1",
            redirect_url="https://sandbox.zarinpal.com/pg/StartPay/AUTH-FAKE-1",
        )
        self.verify_result = verify or VerifyResult(success=True, ref_id="REF-FAKE-1")
        self.create_calls = 0
        self.verify_calls = 0

    async def create_payment(self, amount_rial: int, description: str, mobile: str | None):
        self.create_calls += 1
        return self.create_result

    async def verify(self, amount_rial: int, gateway_ref: str):
        self.verify_calls += 1
        return self.verify_result


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _patch_config(monkeypatch, provider=None, admin_only=False, mode="zarinpal_sandbox"):
    """Replace the gateway resolver with a fixed config (no real HTTP)."""

    async def fake_resolve(db):
        return PaymentConfig(
            mode=mode, merchant="fake-merchant", merchant_valid=True,
            payments_admin_only=admin_only, provider=provider,
        )

    monkeypatch.setattr("app.api.v1.payments.resolve_payment_config", fake_resolve)


def _ensure_admin() -> None:
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


def _add_exam() -> str:
    async def inner():
        async with AsyncSessionLocal() as db:
            e = Exam(
                id=str(uuid4()), title="آزمون تست درگاه", description="",
                duration_secs=600, price=150000, is_active=True,
            )
            db.add(e)
            await db.commit()
            return e.id

    return asyncio.run(inner())


def _register(client: TestClient, national_id: str, phone: str) -> dict:
    r = client.post("/api/v1/auth/register", json={
        "full_name": "دانش‌آموز تست درگاه", "national_id": national_id, "phone": phone,
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _get_payment(payment_id: str) -> dict:
    async def inner():
        async with AsyncSessionLocal() as db:
            p = await db.get(Payment, payment_id)
            return {"status": p.status, "authority": p.authority, "ref_id": p.ref_id}

    return asyncio.run(inner())


def _insert_pending_payment(national_id: str, exam_id: str, authority: str) -> str:
    """Create a pending payment row; resolves the user id inside its own session
    so no asyncio.run is ever nested."""
    async def inner():
        async with AsyncSessionLocal() as db:
            uid = (
                await db.execute(select(User.id).where(User.national_id == national_id))
            ).scalar_one()
            p = Payment(
                user_id=uid, exam_id=exam_id, amount=150000,
                authority=authority, status="pending",
            )
            db.add(p)
            await db.commit()
            await db.refresh(p)
            return p.id

    return asyncio.run(inner())


def _cleanup(exam_id: str, national_id: str, payment_ids: list[str]) -> None:
    async def inner():
        async with AsyncSessionLocal() as db:
            if payment_ids:
                await db.execute(delete(Payment).where(Payment.id.in_(payment_ids)))
            if exam_id:
                await db.execute(delete(Exam).where(Exam.id == exam_id))
            await db.execute(delete(User).where(User.national_id == national_id))
            await db.commit()

    asyncio.run(inner())


def test_start_payment_creates_authority(client, monkeypatch):
    fake = FakeProvider()
    _patch_config(monkeypatch, provider=fake)
    exam_id = _add_exam()
    nid = "0067891233"
    headers = _register(client, nid, "09130000001")
    try:
        r = client.post("/api/v1/payment/start", json={"exam_id": exam_id}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["redirect_url"] == "https://sandbox.zarinpal.com/pg/StartPay/AUTH-FAKE-1"
        assert fake.create_calls == 1
        p = _get_payment(body["payment_id"])
        assert p["status"] == "pending"
        assert p["authority"] == "AUTH-FAKE-1"
        pid = body["payment_id"]
    finally:
        _cleanup(exam_id, nid, [pid] if "pid" in locals() else [])


def test_start_payment_gateway_failure_marks_failed(client, monkeypatch):
    fake = FakeProvider(create=PaymentRequestResult(success=False, message="Invalid merchant_id."))
    _patch_config(monkeypatch, provider=fake)
    exam_id = _add_exam()
    nid = "0081234562"
    headers = _register(client, nid, "09130000002")
    payment_id = None
    try:
        r = client.post("/api/v1/payment/start", json={"exam_id": exam_id}, headers=headers)
        assert r.status_code == 502, r.text
        assert "Invalid merchant_id." in r.json()["detail"]
        # the created payment row must be marked failed, not left pending
        async def find():
            async with AsyncSessionLocal() as db:
                uid = (
                    await db.execute(select(User.id).where(User.national_id == nid))
                ).scalar_one()
                p = (
                    await db.execute(select(Payment).where(Payment.user_id == uid))
                ).scalars().all()
                return [{"status": x.status, "id": x.id} for x in p]

        rows = asyncio.run(find())
        assert rows and all(row["status"] == "failed" for row in rows)
        payment_id = rows[0]["id"]
    finally:
        _cleanup(exam_id, nid, [payment_id] if payment_id else [])


def test_callback_nok_marks_failed(client, monkeypatch):
    exam_id = _add_exam()
    nid = "0098765434"
    headers = _register(client, nid, "09130000003")
    payment_id = None
    try:
        payment_id = _insert_pending_payment(nid, exam_id, "AUTH-NOK")
        r = client.get(
            "/api/v1/payment/callback", params={"Authority": "AUTH-NOK", "Status": "NOK"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        assert "?payment=failed" in r.headers["location"]
        assert _get_payment(payment_id)["status"] == "failed"
    finally:
        _cleanup(exam_id, nid, [payment_id] if payment_id else [])


def test_callback_verify_success(client, monkeypatch):
    fake = FakeProvider(verify=VerifyResult(success=True, ref_id="REF-123"))
    _patch_config(monkeypatch, provider=fake)
    exam_id = _add_exam()
    nid = "0011223340"
    headers = _register(client, nid, "09130000004")
    payment_id = None
    try:
        payment_id = _insert_pending_payment(nid, exam_id, "AUTH-OK")
        r = client.get(
            "/api/v1/payment/callback", params={"Authority": "AUTH-OK", "Status": "OK"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        assert r.headers["location"] == f"{FRONTEND}/exam/{exam_id}?payment=success"
        p = _get_payment(payment_id)
        assert p["status"] == "verified"
        assert p["ref_id"] == "REF-123"
        assert fake.verify_calls == 1
    finally:
        _cleanup(exam_id, nid, [payment_id] if payment_id else [])


def test_callback_verify_failure(client, monkeypatch):
    fake = FakeProvider(verify=VerifyResult(success=False, message="تراکنش ناموفق بود"))
    _patch_config(monkeypatch, provider=fake)
    exam_id = _add_exam()
    nid = "0056789122"
    headers = _register(client, nid, "09130000005")
    payment_id = None
    try:
        payment_id = _insert_pending_payment(nid, exam_id, "AUTH-FAIL")
        r = client.get(
            "/api/v1/payment/callback", params={"Authority": "AUTH-FAIL", "Status": "OK"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        assert "?payment=failed" in r.headers["location"]
        assert _get_payment(payment_id)["status"] == "failed"
    finally:
        _cleanup(exam_id, nid, [payment_id] if payment_id else [])


def test_callback_unknown_authority(client):
    r = client.get(
        "/api/v1/payment/callback", params={"Authority": "NO-SUCH", "Status": "OK"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 307)
    assert r.headers["location"] == f"{FRONTEND}/?payment=failed"


def test_start_payment_requires_auth(client, monkeypatch):
    fake = FakeProvider()
    _patch_config(monkeypatch, provider=fake)
    exam_id = _add_exam()
    try:
        r = client.post("/api/v1/payment/start", json={"exam_id": exam_id})
        assert r.status_code == 401, r.text
    finally:
        _cleanup(exam_id, "", [])


def test_admin_only_lock_blocks_students_but_allows_admins(client, monkeypatch):
    _ensure_admin()
    fake = FakeProvider()
    _patch_config(monkeypatch, provider=fake, admin_only=True)
    exam_id = _add_exam()
    nid = "0077891236"
    headers = _register(client, nid, "09130000006")
    admin_login = client.post("/api/v1/auth/login", json={
        "national_id": "0012345679", "phone": "09120000000"})
    assert admin_login.status_code == 200, admin_login.text
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    try:
        r = client.post("/api/v1/payment/start", json={"exam_id": exam_id}, headers=headers)
        assert r.status_code == 403, r.text
        r2 = client.post("/api/v1/payment/start", json={"exam_id": exam_id}, headers=admin_headers)
        assert r2.status_code == 200, r2.text
    finally:
        _cleanup(exam_id, nid, [])
