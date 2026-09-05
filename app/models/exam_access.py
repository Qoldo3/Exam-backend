from datetime import datetime

import uuid

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.security import utcnow


class ExamAccess(Base):
    """An admin-granted right to take an exam without paying.

    Works like a payment for access purposes: start_exam accepts a user who has
    either a verified payment OR an ExamAccess row for that exam. Reusable for
    the lifetime of the grant (a completed attempt can be retaken by removing
    the completed-session guard only via this grant path, see start_exam).
    """

    __tablename__ = "exam_access"
    __table_args__ = (UniqueConstraint("user_id", "exam_id", name="uq_user_exam_access"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    exam_id: Mapped[str] = mapped_column(String, ForeignKey("exams.id"), nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
