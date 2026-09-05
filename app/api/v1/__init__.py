from fastapi import APIRouter

from app.api.v1 import admin, auth, exams, license, payments, sessions

router = APIRouter(prefix="/api/v1")

router.include_router(license.router)
router.include_router(auth.router)
router.include_router(exams.router)
router.include_router(payments.router)
router.include_router(sessions.router)
router.include_router(admin.router)
