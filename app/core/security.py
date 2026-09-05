from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from app.core.config import settings


def utcnow() -> datetime:
    """Naive UTC now, safe to store in SQLite/PG DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_access_token(data: dict, version: int = 0) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access", "ver": version})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict, version: int = 0) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh", "ver": version})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def validate_national_id(nid: str) -> bool:
    """Validate Iranian national ID with the official checksum algorithm."""
    if not nid.isdigit() or len(nid) != 10:
        return False
    if len(set(nid)) == 1:
        return False
    digits = [int(d) for d in nid]
    check = digits[9]
    total = sum(digits[i] * (10 - i) for i in range(9))
    remainder = total % 11
    return (remainder < 2 and check == remainder) or (remainder >= 2 and check == 11 - remainder)
