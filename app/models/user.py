from datetime import datetime

import uuid

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.security import utcnow

ROLE_USER = "user"
ROLE_SUPERADMIN = "superadmin"
ROLE_EXAM_MANAGER = "exam_manager"
ROLE_FINANCE = "finance"

ALL_ROLES = {ROLE_USER, ROLE_SUPERADMIN, ROLE_EXAM_MANAGER, ROLE_FINANCE}
ADMIN_ROLES = {ROLE_SUPERADMIN, ROLE_EXAM_MANAGER, ROLE_FINANCE}
EXAM_MANAGER_ROLES = {ROLE_SUPERADMIN, ROLE_EXAM_MANAGER}
FINANCE_ROLES = {ROLE_SUPERADMIN, ROLE_FINANCE}


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    national_id: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(11), unique=True, nullable=False)
    # bumped on logout to invalidate every outstanding JWT (access + refresh)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(20), default=ROLE_USER, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sessions = relationship("ExamSession", back_populates="user")
    payments = relationship("Payment", back_populates="user")

    @property
    def is_admin(self) -> bool:
        """Any non-student role counts as an admin (backward-compatible with earlier code)."""
        return self.role in ADMIN_ROLES
