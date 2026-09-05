from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.security import utcnow


class SystemSetting(Base):
    """Small key/value store for admin-managed system settings."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


async def get_setting(db: AsyncSession, key: str) -> str:
    row = await db.get(SystemSetting, key)
    return row.value if row else ""


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(SystemSetting, key)
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))


async def delete_setting(db: AsyncSession, key: str) -> None:
    row = await db.get(SystemSetting, key)
    if row:
        await db.delete(row)
