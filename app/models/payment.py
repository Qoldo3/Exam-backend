from datetime import datetime

import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.security import utcnow


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    exam_id: Mapped[str] = mapped_column(String, ForeignKey("exams.id"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # Rial
    authority: Mapped[str | None] = mapped_column(String(200), nullable=True)  # ZarinPal authority
    ref_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|verified|failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="payments")
    exam = relationship("Exam", back_populates="payments")
    session = relationship("ExamSession", back_populates="payment", foreign_keys="ExamSession.payment_id")
