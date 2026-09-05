from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.responses import Response

from app.api.v1 import router as v1_router
from app.core.config import settings
from app.core.database import init_db
from app.core.license import is_activated, license_enabled
from app.core.ratelimit import limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("exam")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.LICENSE_ENABLED and not settings.LICENSE_HASH:
        logger.warning("LICENSE_ENABLED=true but LICENSE_HASH is empty — the license gate is OFF")
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single-client license gate. Only engages when LICENSE_ENABLED and
# LICENSE_HASH are configured (production); dev/test are never locked.
# The activation endpoints themselves must stay reachable, hence the
# allowlist; /health is public infrastructure.
LICENSE_PUBLIC_PATHS = {
    "/api/v1/license/status",
    "/api/v1/license/activate",
}

# Pure-ASGI (not BaseHTTPMiddleware): unlocked traffic passes through without
# touching the response body, so StreamingResponse (admin CSVs) is unaffected.
class LicenseGateMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/health" or path in LICENSE_PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        if not is_activated():
            # Locked: reject EVERYTHING else — the API, the docs (/docs,
            # /openapi.json, /redoc) and any future route. Only the health probe
            # and the license endpoints stay reachable. No CORS headers needed
            # here: the frontend license gate uses the allowlisted
            # /license/status path, and browsers only enforce CORS on reads.
            response = Response(
                status_code=402,
                media_type="application/json",
                content='{"detail": "سامانه هنوز فعال نشده است"}',
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


# Starlette executes the LAST-added middleware outermost, so this runs before
# CORS: a locked 402 is produced first and CORS adds its headers afterwards.
app.add_middleware(LicenseGateMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "تعداد درخواست‌ها بیش از حد مجاز است"})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    msgs = []
    for err in exc.errors():
        loc = " / ".join(str(p) for p in err.get("loc", []) if p not in ("body",))
        msg = err.get("msg", "").replace("Value error, ", "")
        msgs.append(f"{loc}: {msg}" if loc else msg)
    detail = "؛ ".join(msgs) or "داده‌های ورودی نامعتبر است"
    logger.warning("Validation error on %s: %s", request.url.path, detail)
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "خطای داخلی سرور؛ لطفاً بعداً دوباره تلاش کنید"})


app.include_router(v1_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
