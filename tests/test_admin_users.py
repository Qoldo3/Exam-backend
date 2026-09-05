"""Tests for the admin create-user endpoint and the local-payment dedup rule."""
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

SUPER = {"national_id": "0012345679", "phone": "09120000000"}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _ensure_admin() -> None:
    async def inner():
        async with AsyncSessionLocal() as db:
            u = (
                await db.execute(select(User).where(User.national_id == SUPER["national_id"]))
            ).scalar_one_or_none()
            if not u:
                db.add(
                    User(
                        full_name="مدیر ارشد", national_id=SUPER["national_id"],
                        phone=SUPER["phone"], role="superadmin",
                    )
                )
                await db.commit()

    asyncio.run(inner())


def _add_exam() -> str:
    async def inner():
        async with AsyncSessionLocal() as db:
            e = Exam(
                id=str(uuid4()), title="آزمون تست کاربران", description="",
                duration_secs=600, price=150000, is_active=True,
            )
            db.add(e)
            await db.commit()
            return e.id

    return asyncio.run(inner())


def _token(client: TestClient, body: dict) -> str:
    r = client.post("/api/v1/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, national_id: str, phone: str) -> dict:
    r = client.post("/api/v1/auth/register", json={
        "full_name": "دانش‌آموز تست", "national_id": national_id, "phone": phone,
    })
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _delete_user(national_id: str) -> None:
    async def inner():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(User).where(User.national_id == national_id))
            await db.commit()

    asyncio.run(inner())


def _delete_exam(exam_id: str) -> None:
    async def inner():
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Payment).where(Payment.exam_id == exam_id))
            await db.execute(delete(Exam).where(Exam.id == exam_id))
            await db.commit()

    asyncio.run(inner())


def test_create_user_requires_auth(client):
    body = {"full_name": "کاربر", "national_id": "1043321810", "phone": "09120000031"}
    assert client.post("/api/v1/admin/users", json=body).status_code == 401


def test_create_user_validation(client):
    _ensure_admin()
    token = _token(client, SUPER)
    h = _auth(token)
    assert client.post(
        "/api/v1/admin/users",
        json={"full_name": "کاربر تست", "national_id": "1234567890", "phone": "09120000031"},
        headers=h,
    ).status_code == 400  # bad checksum
    assert client.post(
        "/api/v1/admin/users",
        json={"full_name": "کاربر تست", "national_id": "1043321810", "phone": "12345"},
        headers=h,
    ).status_code == 400  # bad phone


def test_create_user_and_duplicate_conflict(client):
    _ensure_admin()
    token = _token(client, SUPER)
    h = _auth(token)
    body = {"full_name": "کاربر جدید", "national_id": "1043321810", "phone": "09120000031"}
    r = client.post("/api/v1/admin/users", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "user"
    try:
        dup = {"full_name": "کاربر دیگر", "national_id": "1043321810", "phone": "09120000032"}
        assert client.post("/api/v1/admin/users", json=dup, headers=h).status_code == 409
        dup2 = {"full_name": "کاربر دیگر", "national_id": "9600133891", "phone": "09120000031"}
        assert client.post("/api/v1/admin/users", json=dup2, headers=h).status_code == 409
    finally:
        _delete_user("1043321810")


def test_payment_dedup_reuses_verified_payment(client):
    """Two start_payment calls without a completed attempt return the same payment."""
    exam_id = _add_exam()
    nid, phone = "9600133891", "09120000032"
    sh = _register(client, nid, phone)
    try:
        p1 = client.post("/api/v1/payment/start", json={"exam_id": exam_id}, headers=sh).json()["payment_id"]
        p2 = client.post("/api/v1/payment/start", json={"exam_id": exam_id}, headers=sh).json()["payment_id"]
        assert p1 == p2, "local payment should be deduplicated (same payment id)"
    finally:
        _delete_user(nid)
        _delete_exam(exam_id)


def test_payment_allows_new_payment_after_completion(client):
    """A completed attempt must NOT be blocked by the dedup rule (retake path)."""
    exam_id = _add_exam()
    nid, phone = "0265423511", "09120000033"
    sh = _register(client, nid, phone)
    try:
        p1 = client.post("/api/v1/payment/start", json={"exam_id": exam_id}, headers=sh).json()["payment_id"]

        # complete the exam: start, answer nothing, submit (score 0)
        start = client.post("/api/v1/exam/start", json={"exam_id": exam_id}, headers=sh)
        assert start.status_code == 200, start.text
        session_id = start.json()["session_id"]
        sub = client.post(f"/api/v1/exam/session/{session_id}/submit", json={}, headers=sh)
        assert sub.status_code == 200, sub.text

        # dedup must NOT return the old payment — a fresh one is allowed
        p2 = client.post("/api/v1/payment/start", json={"exam_id": exam_id}, headers=sh)
        assert p2.status_code == 200, p2.text
        assert p2.json()["payment_id"] != p1
    finally:
        _delete_user(nid)
        _delete_exam(exam_id)
