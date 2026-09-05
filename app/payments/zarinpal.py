import httpx

from app.core.config import settings
from app.payments.base import PaymentProvider, PaymentRequestResult, VerifyResult

ZARINPAL_PRODUCTION_BASE = "https://payment.zarinpal.com"
ZARINPAL_SANDBOX_BASE = "https://sandbox.zarinpal.com"
ZARINPAL_PRODUCTION_STARTPAY = "https://www.zarinpal.com"
ZARINPAL_SANDBOX_STARTPAY = "https://sandbox.zarinpal.com"

REQUEST_PATH = "/pg/v4/payment/request.json"
VERIFY_PATH = "/pg/v4/payment/verify.json"
STARTPAY_PATH = "/pg/StartPay/"


def _error_message(body: dict, data: dict) -> str:
    """Extract a human-readable message from a ZarinPal error response.

    ZarinPal v4 returns `errors` as a list in some responses but as a single
    dict ({"message", "code", "validations"}) for request-time failures — the
    real API shape observed when probing with an invalid merchant_id. Handle
    both so a gateway error never crashes with AttributeError.
    """
    raw = body.get("errors")
    if isinstance(raw, dict):
        msg = str(raw.get("message", ""))
        if msg:
            return msg
    elif isinstance(raw, list):
        msgs = [e.get("message", "") for e in raw if isinstance(e, dict)]
        if msgs:
            return "؛ ".join(msgs)
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return ""


class ZarinPalProvider(PaymentProvider):
    """ZarinPal v4 REST integration (request -> StartPay -> verify).

    Amounts are in Rial, as required by ZarinPal. Sandbox mode accepts any
    36-char merchant id and uses sandbox.zarinpal.com endpoints.
    """

    def __init__(self, merchant: str | None = None, sandbox: bool | None = None):
        self.merchant = merchant or settings.ZARINPAL_MERCHANT
        if sandbox is None:
            sandbox = settings.PAYMENT_MODE == "zarinpal_sandbox"
        self.sandbox = sandbox

        api_base = ZARINPAL_SANDBOX_BASE if sandbox else ZARINPAL_PRODUCTION_BASE
        startpay_base = ZARINPAL_SANDBOX_STARTPAY if sandbox else ZARINPAL_PRODUCTION_STARTPAY
        self.request_url = api_base + REQUEST_PATH
        self.verify_url = api_base + VERIFY_PATH
        self.startpay_url = startpay_base + STARTPAY_PATH

    async def create_payment(
        self, amount_rial: int, description: str, mobile: str | None
    ) -> PaymentRequestResult:
        callback = f"{settings.PAYMENT_CALLBACK_BASE_URL}/api/v1/payment/callback"
        payload = {
            "merchant_id": self.merchant,
            "amount": amount_rial,
            "description": description,
            "callback_url": callback,
            "metadata": {"mobile": mobile} if mobile else {},
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(self.request_url, json=payload)
                body = resp.json()
        except (httpx.HTTPError, ValueError):
            return PaymentRequestResult(success=False, message="خطا در اتصال به درگاه پرداخت")

        data = body.get("data") or {}
        code = data.get("code")
        authority = data.get("authority")
        if code == 100 and authority:
            return PaymentRequestResult(
                success=True,
                gateway_ref=authority,
                redirect_url=f"{self.startpay_url}{authority}",
            )
        message = _error_message(body, data)
        return PaymentRequestResult(
            success=False,
            message=message or (f"کد خطا: {code}" if code is not None else "خطا در پرداخت"),
        )

    async def verify(self, amount_rial: int, gateway_ref: str) -> VerifyResult:
        payload = {
            "merchant_id": self.merchant,
            "amount": amount_rial,
            "authority": gateway_ref,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(self.verify_url, json=payload)
                body = resp.json()
        except (httpx.HTTPError, ValueError):
            return VerifyResult(success=False, message="خطا در بررسی پرداخت")

        data = body.get("data") or {}
        code = data.get("code")
        if code == 100:
            return VerifyResult(success=True, ref_id=str(data.get("ref_id")), message="پرداخت تأیید شد")
        if code == 101:
            return VerifyResult(
                success=True,
                already_verified=True,
                ref_id=str(data.get("ref_id")) if data.get("ref_id") else None,
                message="این پرداخت قبلاً تأیید شده است",
            )
        message = _error_message(body, data)
        return VerifyResult(
            success=False,
            message=message or (f"کد خطا: {code}" if code is not None else "خطا در پرداخت"),
        )
