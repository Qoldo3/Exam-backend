from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.exam import Exam, Question
from app.schemas.exam import ExamDetailOut, ExamOut

router = APIRouter(tags=["exams"])


@router.get("/exams", response_model=list[ExamOut])
async def list_exams(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Exam).where(Exam.is_active == True))  # noqa: E712
    exams = result.scalars().all()
    counts = await db.execute(
        select(Question.exam_id, func.count()).group_by(Question.exam_id)
    )
    count_map = dict(counts.all())
    out = []
    for ex in exams:
        item = ExamOut(
            id=ex.id,
            title=ex.title,
            description=ex.description,
            duration_secs=ex.duration_secs,
            price=ex.price,
            question_count=count_map.get(ex.id, 0),
        )
        out.append(item)
    return out


@router.get("/exams/{exam_id}", response_model=ExamDetailOut)
async def exam_detail(exam_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    ex = result.scalar_one_or_none()
    if not ex or not ex.is_active:
        raise HTTPException(status_code=404, detail="آزمون یافت نشد")

    questions = (
        await db.execute(
            select(Question).where(Question.exam_id == exam_id).order_by(Question.order_num)
        )
    ).scalars().all()
    # Security: never ship the full question bank to the client before payment.
    # Only metadata + a single preview question; full questions are served one-by-one
    # through the authenticated, payment-gated session flow.
    return ExamDetailOut(
        id=ex.id,
        title=ex.title,
        description=ex.description,
        duration_secs=ex.duration_secs,
        price=ex.price,
        question_count=len(questions),
        sample_question=questions[0] if questions else None,
    )
