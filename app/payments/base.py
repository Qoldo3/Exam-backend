from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PaymentRequestResult:
    success: bool
    gateway_ref: str | None = None  # authority / id of the payment at the gateway
    redirect_url: str | None = None
    message: str = ""


@dataclass
class VerifyResult:
    success: bool
    already_verified: bool = False
    ref_id: str | None = None
    message: str = ""


class PaymentProvider(ABC):
    """Abstraction over an Iranian bank payment gateway.

    Swap the implementation (ZarinPal today, Behpardakht/Sadad later for the
    registered institute) without touching the rest of the app.
    """

    @abstractmethod
    async def create_payment(
        self, amount_rial: int, description: str, mobile: str | None
    ) -> PaymentRequestResult:
        raise NotImplementedError

    @abstractmethod
    async def verify(self, amount_rial: int, gateway_ref: str) -> VerifyResult:
        raise NotImplementedError
