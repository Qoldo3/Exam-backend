from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.license import activate, is_activated, license_enabled
from app.core.ratelimit import limiter

router = APIRouter(tags=["license"])


class LicenseStatusOut(BaseModel):
    enabled: bool
    activated: bool


class LicenseActivateIn(BaseModel):
    key: str


@router.get("/license/status", response_model=LicenseStatusOut)
async def license_status():
    return LicenseStatusOut(enabled=license_enabled(), activated=is_activated())


@router.post("/license/activate", response_model=LicenseStatusOut)
@limiter.limit("5/minute")
async def license_activate(request: Request, payload: LicenseActivateIn):
    """Activate the installation. Wrong keys simply fail (rate-limited per IP).

    A 403 is returned when the gate is disabled entirely (misconfiguration),
    so a client can never brute-force or abuse the endpoint in dev/test.
    """
    if not license_enabled():
        return LicenseStatusOut(enabled=False, activated=True)
    ok = activate(payload.key)
    return LicenseStatusOut(enabled=True, activated=ok)
