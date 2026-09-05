from datetime import datetime

import uuid

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.security import utcnow


class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    exam_id: Mapped[str] = mapped_column(String, ForeignKey("exams.id"), nullable=False)
    payment_id: Mapped[str | None] = mapped_column(String, ForeignKey("payments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_q_idx: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|active|completed
    tab_switches: Mapped[int] = mapped_column(Integer, default=0)
    # denormalized counter so fraud reports can filter in SQL instead of scanning the JSON blob
    fraud_count: Mapped[int] = mapped_column(Integer, default=0)
    fraud_events: Mapped[list | None] = mapped_column(JSON, default=list)

    user = relationship("User", back_populates="sessions")
    exam = relationship("Exam", back_populates="sessions")
    payment = relationship("Payment", back_populates="session", foreign_keys=[payment_id])
    answers = relationship("Answer", back_populates="session")


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("session_id", "question_id", name="uq_session_question"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String, ForeignKey("exam_sessions.id"), nullable=False)
    question_id: Mapped[str] = mapped_column(String, ForeignKey("questions.id"), nullable=False)
    chosen_opt: Mapped[str] = mapped_column(String(1), nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session = relationship("ExamSession", back_populates="answers")
    question = relationship("Question", back_populates="answers")
