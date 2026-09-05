from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.ratelimit import limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    validate_national_id,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, TokenPair, UserOut

router = APIRouter(tags=["auth"])


def _issue_tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token({"sub": user.id}, version=user.token_version),
        refresh_token=create_refresh_token({"sub": user.id}, version=user.token_version),
    )


@router.post("/auth/register", response_model=TokenPair)
@limiter.limit(settings.REGISTER_RATE_LIMIT)
async def register(request: Request, payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if not validate_national_id(payload.national_id):
        raise HTTPException(status_code=400, detail="کد ملی معتبر نیست")

    existing = await db.execute(
        select(User).where(
            (User.national_id == payload.national_id) | (User.phone == payload.phone)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="کاربری با این کد ملی یا شماره موبایل قبلاً ثبت شده است")

    user = User(full_name=payload.full_name, national_id=payload.national_id, phone=payload.phone)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _issue_tokens(user)


@router.post("/auth/login", response_model=TokenPair)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(
            (User.national_id == payload.national_id) & (User.phone == payload.phone)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="کد ملی یا شماره موبایل نادرست است")
    if user.is_blocked:
        raise HTTPException(
            status_code=403, detail="حساب شما مسدود شده است؛ برای رفع مسدودی با پشتیبانی تماس بگیرید"
        )
    return _issue_tokens(user)


@router.post("/auth/refresh", response_model=TokenPair)
@limiter.limit("30/minute")
async def refresh(request: Request, payload: RefreshRequest, db: AsyncSession = Depends(get_db)):
    data = decode_token(payload.refresh_token)
    if not data or data.get("type") != "refresh" or not data.get("sub"):
        raise HTTPException(status_code=401, detail="توکن تازه‌سازی نامعتبر است")
    user = await db.get(User, data["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="کاربر یافت نشد؛ لطفاً دوباره وارد شوید")
    if user.is_blocked:
        raise HTTPException(
            status_code=403, detail="حساب شما مسدود شده است؛ برای رفع مسدودی با پشتیبانی تماس بگیرید"
        )
    if data.get("ver", 0) != user.token_version:
        raise HTTPException(status_code=401, detail="نشست شما منقضی شده است؛ لطفاً دوباره وارد شوید")
    return _issue_tokens(user)


@router.get("/auth/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/auth/logout")
async def logout(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Server-side logout: bump token_version to invalidate every outstanding token."""
    current_user.token_version += 1
    await db.commit()
    return {"ok": True}
