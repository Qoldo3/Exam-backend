"""Unit tests for the ZarinPal v4 gateway provider.

HTTP is mocked (no real network). The response shapes are based on ZarinPal's
documentation (docs.zarinpal.com) and verified against the live API:
- request.json success -> data.authority + code 100, redirect to /pg/StartPay/{authority}
- request.json failure -> errors as a *dict* like {"message", "code", "validations"}
- verify.json success -> data.ref_id + code 100; code 101 = already verified
"""
import asyncio

import httpx
import pytest

from app.payments import merchant_valid, ZarinPalProvider
from app.payments.zarinpal import (
    ZARINPAL_PRODUCTION_BASE,
    ZARINPAL_SANDBOX_BASE,
    ZARINPAL_PRODUCTION_STARTPAY,
    ZARINPAL_SANDBOX_STARTPAY,
    REQUEST_PATH,
    VERIFY_PATH,
    STARTPAY_PATH,
)
from app.core.config import settings

AUTHORITY = "A000000000000000000000000002790795"


class FakeAsyncClient:
    """Duck-typed stand-in for httpx.AsyncClient (context manager with .post)."""

    def __init__(self, response: httpx.Response):
        self._response = response
        self.last_url: str | None = None
        self.last_payload: dict | None = None
        self.raise_error: Exception | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self.last_url = url
        self.last_payload = json
        if self.raise_error:
            raise self.raise_error
        return self._response


def patch_http(monkeypatch, client: FakeAsyncClient):
    class _Factory:
        def __call__(self, *args, **kwargs):
            return client

    monkeypatch.setattr(httpx, "AsyncClient", _Factory())
    return client


def make_provider(monkeypatch, body: dict, sandbox: bool = True, merchant: str = "test-merchant"):
    client = patch_http(monkeypatch, FakeAsyncClient(httpx.Response(200, json=body)))
    provider = ZarinPalProvider(merchant=merchant, sandbox=sandbox)
    return provider, client


# ---------- create_payment ----------


def test_create_payment_success_sandbox(monkeypatch):
    provider, client = make_provider(
        monkeypatch,
        {"data": {"code": 100, "message": "Success", "authority": AUTHORITY}, "errors": []},
        sandbox=True,
    )
    res = asyncio.run(provider.create_payment(amount_rial=100000, description="آزمون ریاضی", mobile="09120000000"))

    assert res.success is True
    assert res.gateway_ref == AUTHORITY
    assert res.redirect_url == f"{ZARINPAL_SANDBOX_STARTPAY}{STARTPAY_PATH}{AUTHORITY}"
    assert client.last_url == f"{ZARINPAL_SANDBOX_BASE}{REQUEST_PATH}"
    # payload matches the documented request body
    assert client.last_payload["merchant_id"] == "test-merchant"
    assert client.last_payload["amount"] == 100000
    assert client.last_payload["callback_url"].endswith("/api/v1/payment/callback")
    assert client.last_payload["metadata"]["mobile"] == "09120000000"


def test_create_payment_success_production(monkeypatch):
    provider, client = make_provider(
        monkeypatch,
        {"data": {"code": 100, "message": "Success", "authority": AUTHORITY}, "errors": []},
        sandbox=False,
    )
    res = asyncio.run(provider.create_payment(100000, "آزمون", None))
    assert res.success is True
    assert res.redirect_url == f"{ZARINPAL_PRODUCTION_STARTPAY}{STARTPAY_PATH}{AUTHORITY}"
    assert client.last_url == f"{ZARINPAL_PRODUCTION_BASE}{REQUEST_PATH}"


def test_create_payment_error_dict_shape(monkeypatch):
    """Live-probe shape: errors is a dict, not a list — must not crash."""
    provider, _ = make_provider(
        monkeypatch,
        {"data": {}, "errors": {"message": "Invalid merchant_id.", "code": -10, "validations": []}},
    )
    res = asyncio.run(provider.create_payment(1000, "آزمون", None))
    assert res.success is False
    assert "Invalid merchant_id." in res.message


def test_create_payment_error_list_shape(monkeypatch):
    provider, _ = make_provider(
        monkeypatch,
        {"data": {"code": -1}, "errors": [{"code": -1, "message": "شماره کارت نامعتبر"}]},
    )
    res = asyncio.run(provider.create_payment(1000, "آزمون", None))
    assert res.success is False
    assert "شماره کارت نامعتبر" in res.message


def test_create_payment_no_authority(monkeypatch):
    provider, _ = make_provider(monkeypatch, {"data": {"code": 100}, "errors": []})
    res = asyncio.run(provider.create_payment(1000, "آزمون", None))
    assert res.success is False


def test_create_payment_network_error(monkeypatch):
    provider, client = make_provider(monkeypatch, {"data": {}}, sandbox=True)
    client.raise_error = httpx.ConnectError("connection refused")
    res = asyncio.run(provider.create_payment(1000, "آزمون", None))
    assert res.success is False
    assert "خطا در اتصال" in res.message


def test_create_payment_non_json_response(monkeypatch):
    client = patch_http(monkeypatch, FakeAsyncClient(httpx.Response(200, text="<html>gateway down</html>")))
    provider = ZarinPalProvider(merchant="m", sandbox=True)
    res = asyncio.run(provider.create_payment(1000, "آزمون", None))
    assert res.success is False


# ---------- verify ----------


def test_verify_success_code_100(monkeypatch):
    provider, client = make_provider(
        monkeypatch,
        {"data": {"code": 100, "message": "Verified", "ref_id": 123456789}, "errors": []},
    )
    res = asyncio.run(provider.verify(amount_rial=100000, gateway_ref=AUTHORITY))
    assert res.success is True
    assert res.ref_id == "123456789"
    assert res.already_verified is False
    assert client.last_url == f"{ZARINPAL_SANDBOX_BASE}{VERIFY_PATH}"
    assert client.last_payload["authority"] == AUTHORITY
    assert client.last_payload["amount"] == 100000


def test_verify_already_verified_code_101(monkeypatch):
    provider, _ = make_provider(
        monkeypatch,
        {"data": {"code": 101, "message": "Already verified", "ref_id": 987654321}, "errors": []},
    )
    res = asyncio.run(provider.verify(100000, AUTHORITY))
    assert res.success is True
    assert res.already_verified is True
    assert res.ref_id == "987654321"


def test_verify_failed_code(monkeypatch):
    provider, _ = make_provider(
        monkeypatch,
        {"data": {"code": -21, "message": "تراکنش ناموفق بود"}, "errors": []},
    )
    res = asyncio.run(provider.verify(100000, AUTHORITY))
    assert res.success is False
    assert "تراکنش ناموفق بود" in res.message


def test_verify_error_dict(monkeypatch):
    provider, _ = make_provider(
        monkeypatch,
        {"data": {}, "errors": {"message": "Invalid authority.", "code": -11, "validations": []}},
    )
    res = asyncio.run(provider.verify(100000, AUTHORITY))
    assert res.success is False
    assert "Invalid authority." in res.message


def test_verify_network_error(monkeypatch):
    provider, client = make_provider(monkeypatch, {"data": {}})
    client.raise_error = httpx.TimeoutException("timed out")
    res = asyncio.run(provider.verify(100000, AUTHORITY))
    assert res.success is False


# ---------- merchant id validation ----------

VALID_UUID = "123e4567-e89b-42d3-a456-426614174000"


def test_merchant_valid_accepts_uuid():
    assert merchant_valid(VALID_UUID) is True


def test_merchant_valid_rejects_empty():
    assert merchant_valid("") is False


def test_merchant_valid_rejects_bad_format():
    assert merchant_valid("nope") is False
    assert merchant_valid("123e4567e89b42d3a456426614174000") is False  # 32 chars, no dashes
    assert merchant_valid("123e4567-e89b-42d3-a456-42661417400Z") is False


def test_provider_constructed_with_merchant_and_sandbox():
    """The provider keeps its configured merchant id and sandbox flag."""
    provider = ZarinPalProvider(merchant=VALID_UUID, sandbox=True)
    assert provider.sandbox is True
    assert provider.merchant == VALID_UUID
