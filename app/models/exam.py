import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    duration_secs: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[int] = mapped_column(Integer, nullable=False)  # Rial
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    questions = relationship("Question", back_populates="exam", order_by="Question.order_num")
    sessions = relationship("ExamSession", back_populates="exam")
    payments = relationship("Payment", back_populates="exam")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    exam_id: Mapped[str] = mapped_column(String, ForeignKey("exams.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(String(500))
    option_b: Mapped[str] = mapped_column(String(500))
    option_c: Mapped[str] = mapped_column(String(500))
    option_d: Mapped[str] = mapped_column(String(500))
    correct_opt: Mapped[str] = mapped_column(String(1), nullable=False)  # NEVER sent to client
    order_num: Mapped[int] = mapped_column(Integer, nullable=False)

    exam = relationship("Exam", back_populates="questions")
    answers = relationship("Answer", back_populates="question")
