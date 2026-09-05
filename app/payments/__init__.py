import re
from dataclasses import dataclass

from app.core.config import settings
from app.models.setting import get_setting
from app.payments.base import PaymentProvider, PaymentRequestResult, VerifyResult
from app.payments.zarinpal import ZarinPalProvider

# ZarinPal Merchant ID: a 36-char UUID issued in the merchant panel
# (پنل زرین‌پال ← درگاه پرداخت ← شناسه درگاه / Merchant ID).
MERCHANT_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# sandbox accepts any 36-char merchant id; use a placeholder when none is set
PLACEHOLDER_MERCHANT = "00000000-0000-0000-0000-000000000000"

PAYMENT_MODES = ("local", "zarinpal_sandbox", "zarinpal_live")


def merchant_valid(merchant: str) -> bool:
    return bool(MERCHANT_RE.match(merchant or ""))


@dataclass
class PaymentConfig:
    """Resolved gateway configuration for the current request."""

    mode: str  # local | zarinpal_sandbox | zarinpal_live
    merchant: str  # effective merchant id (may be the sandbox placeholder)
    merchant_valid: bool
    payments_admin_only: bool
    provider: PaymentProvider | None = None


async def resolve_payment_config(db) -> PaymentConfig:
    """Resolve the effective gateway config from DB settings (admin panel),
    falling back to environment variables when a setting is not stored.

    Safety rule: when a Zarinpal mode is active but the merchant id is missing
    or malformed, the system forces the sandbox gateway and locks payments to
    admins only — students can never obtain a (fake/verified) payment while the
    gateway is not properly configured.
    """
    stored_mode = (await get_setting(db, "payment_mode")).strip()
    mode = stored_mode or settings.PAYMENT_MODE

    stored_merchant = (await get_setting(db, "zarinpal_merchant")).strip()
    merchant = stored_merchant or (settings.ZARINPAL_MERCHANT or "").strip()
    valid = merchant_valid(merchant)

    if mode == "local":
        # explicit development simulation — payments are not real
        return PaymentConfig(
            mode="local",
            merchant=merchant,
            merchant_valid=valid,
            payments_admin_only=False,
            provider=None,
        )

    if not valid:
        # unconfigured or broken merchant -> forced sandbox + admin-only payments
        effective = merchant or PLACEHOLDER_MERCHANT
        return PaymentConfig(
            mode="zarinpal_sandbox",
            merchant=effective,
            merchant_valid=False,
            payments_admin_only=True,
            provider=ZarinPalProvider(merchant=effective, sandbox=True),
        )

    return PaymentConfig(
        mode=mode,
        merchant=merchant,
        merchant_valid=True,
        payments_admin_only=False,
        provider=ZarinPalProvider(merchant=merchant, sandbox=(mode == "zarinpal_sandbox")),
    )


__all__ = [
    "PaymentProvider",
    "PaymentRequestResult",
    "VerifyResult",
    "ZarinPalProvider",
    "PaymentConfig",
    "merchant_valid",
    "resolve_payment_config",
    "MERCHANT_RE",
    "PLACEHOLDER_MERCHANT",
    "PAYMENT_MODES",
]
