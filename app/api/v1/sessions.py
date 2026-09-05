import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.ratelimit import limiter
from app.core.scoring import finalize_session
from app.core.security import utcnow
from app.models.exam import Exam, Question
from app.models.exam_access import ExamAccess
from app.models.payment import Payment
from app.models.session import Answer, ExamSession
from app.models.user import ADMIN_ROLES, User
from app.schemas.exam import QuestionOut
from app.schemas.exam_session import (
    AnswerIn,
    SessionInfoOut,
    StartOut,
    StartRequest,
    SubmitResult,
)

router = APIRouter(tags=["sessions"])

FRAUD_EVENT_TYPES = {
    "tab_switch",
    "context_menu",
    "copy_attempt",
    "cut_attempt",
    "paste_attempt",
    "devtools",
}
MAX_FRAUD_EVENTS = 500


async def _get_owned_session(db: AsyncSession, user_id: str, session_id: str) -> ExamSession:
    result = await db.execute(
        select(ExamSession).where(ExamSession.id == session_id, ExamSession.user_id == user_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="جلسه یافت نشد")
    return session




@router.post("/exam/start", response_model=StartOut)
@limiter.limit(settings.EXAM_START_RATE_LIMIT)
async def start_exam(
    request: Request,
    payload: StartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exam_id = payload.exam_id
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if not exam or not exam.is_active:
        raise HTTPException(status_code=404, detail="آزمون یافت نشد")

    question_count = (
        await db.execute(select(func.count()).select_from(Question).where(Question.exam_id == exam_id))
    ).scalar_one()

    # Serialize concurrent starts for the same (user, exam): two parallel
    # requests could otherwise both see "no active session" and create two
    # attempts. The user-row FOR UPDATE makes the read-check-create block atomic
    # on PostgreSQL. NOTE: SQLite ignores FOR UPDATE (row locks unsupported),
    # so on SQLite the guard relies on the write lock at commit time + the
    # 10/hour rate limit — acceptable for dev, use PostgreSQL for strictness.
    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())

    # access check: admins bypass payment entirely; students need a verified
    # payment OR an admin-granted ExamAccess for this exam
    if current_user.role not in ADMIN_ROLES:
        paid = await db.execute(
            select(Payment).where(
                Payment.exam_id == exam_id,
                Payment.user_id == current_user.id,
                Payment.status == "verified",
            )
        )
        granted = await db.execute(
            select(ExamAccess).where(
                ExamAccess.exam_id == exam_id, ExamAccess.user_id == current_user.id
            )
        )
        if not paid.scalar_one_or_none() and not granted.scalar_one_or_none():
            raise HTTPException(status_code=402, detail="ابتدا باید هزینه آزمون را پرداخت کنید")

    existing = await db.execute(
        select(ExamSession)
        .where(
            ExamSession.exam_id == exam_id,
            ExamSession.user_id == current_user.id,
        )
        .order_by(ExamSession.created_at.desc())
    )
    sessions = existing.scalars().all()

    # resume an in-progress attempt if one exists
    active = next((s for s in sessions if s.status == "active"), None)
    if active:
        remaining = 0
        if active.expires_at:
            remaining = max(0, int((active.expires_at - utcnow()).total_seconds()))
        return StartOut(
            session_id=active.id,
            total_questions=question_count,
            duration_secs=exam.duration_secs,
            remaining_secs=remaining,
        )

    # activate a pending attempt if one exists (started but never began)
    pending = next((s for s in sessions if s.status == "pending"), None)
    if pending:
        now = utcnow()
        pending.status = "active"
        pending.started_at = now
        pending.expires_at = now + _dt.timedelta(seconds=exam.duration_secs)
        await db.commit()
        return StartOut(
            session_id=pending.id,
            total_questions=question_count,
            duration_secs=exam.duration_secs,
            remaining_secs=exam.duration_secs,
        )

    # one attempt per (user, exam) for students: a finished attempt cannot be
    # retaken unless the user holds a fresh admin grant for this exam
    if next((s for s in sessions if s.status == "completed"), None):
        if current_user.role in ADMIN_ROLES:
            pass  # admins may retake freely (testing/oversight)
        else:
            granted = await db.execute(
                select(ExamAccess).where(
                    ExamAccess.exam_id == exam_id, ExamAccess.user_id == current_user.id
                )
            )
            if not granted.scalar_one_or_none():
                raise HTTPException(
                    status_code=409,
                    detail="شما قبلاً در این آزمون شرکت کرده‌اید و امکان شرکت مجدد وجود ندارد",
                )

    new_session = ExamSession(user_id=current_user.id, exam_id=exam.id, status="active")
    now = utcnow()
    new_session.started_at = now
    new_session.expires_at = now + _dt.timedelta(seconds=exam.duration_secs)
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)
    return StartOut(
        session_id=new_session.id,
        total_questions=question_count,
        duration_secs=exam.duration_secs,
        remaining_secs=exam.duration_secs,
    )


async def _load_context(
    db: AsyncSession, session_id: str, user_id: str, for_update: bool = False
) -> ExamSession:
    stmt = select(ExamSession).where(ExamSession.id == session_id, ExamSession.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="جلسه یافت نشد")
    if session.status == "pending":
        raise HTTPException(status_code=409, detail="آزمون هنوز شروع نشده است")
    if session.status == "completed":
        raise HTTPException(status_code=409, detail="آزمون قبلاً پایان یافته است")

    if session.expires_at and utcnow() > session.expires_at:
        questions = (
            await db.execute(select(Question).where(Question.exam_id == session.exam_id))
        ).scalars().all()
        answers = (
            await db.execute(select(Answer).where(Answer.session_id == session.id))
        ).scalars().all()
        correct_map = {q.id: q.correct_opt for q in questions}
        finalize_session(session, correct_map, answers, len(questions))
        await db.commit()
        raise HTTPException(status_code=409, detail="زمان آزمون به پایان رسیده است")

    return session


@router.get("/exam/session/{session_id}/info", response_model=SessionInfoOut)
async def session_info(
    session_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Server-authoritative session state so the client timer always resyncs, even after refresh/new tab."""
    session = await _get_owned_session(db, current_user.id, session_id)
    exam = await db.get(Exam, session.exam_id)
    total = (
        await db.execute(select(func.count()).select_from(Question).where(Question.exam_id == session.exam_id))
    ).scalar_one()
    duration = exam.duration_secs if exam else 0

    if session.status == "completed":
        return SessionInfoOut(
            exam_id=session.exam_id, total_questions=total, duration_secs=duration,
            remaining_secs=0, status="completed",
        )

    if session.status == "active" and session.expires_at and utcnow() > session.expires_at:
        questions = (
            await db.execute(select(Question).where(Question.exam_id == session.exam_id))
        ).scalars().all()
        answers = (
            await db.execute(select(Answer).where(Answer.session_id == session.id))
        ).scalars().all()
        correct_map = {q.id: q.correct_opt for q in questions}
        finalize_session(session, correct_map, answers, len(questions))
        await db.commit()
        return SessionInfoOut(
            exam_id=session.exam_id, total_questions=total, duration_secs=duration,
            remaining_secs=0, status="completed",
        )

    if session.status == "active":
        remaining = (
            max(0, int((session.expires_at - utcnow()).total_seconds()))
            if session.expires_at
            else duration
        )
        return SessionInfoOut(
            exam_id=session.exam_id, total_questions=total, duration_secs=duration,
            remaining_secs=remaining, status="active",
        )

    return SessionInfoOut(
        exam_id=session.exam_id, total_questions=total, duration_secs=duration,
        remaining_secs=None, status="pending",
    )


@router.get("/exam/session/{session_id}/question", response_model=QuestionOut | None)
async def get_question(
    session_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _load_context(db, session_id, current_user.id)
    question = (
        await db.execute(
            select(Question)
            .where(Question.exam_id == session.exam_id)
            .order_by(Question.order_num)
            .offset(session.current_q_idx)
            .limit(1)
        )
    ).scalar_one_or_none()
    return question


@router.post("/exam/session/{session_id}/answer", response_model=dict)
async def save_answer(
    session_id: str,
    payload: AnswerIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # row lock guards the read-modify-write on current_q_idx under concurrent requests
    session = await _load_context(db, session_id, current_user.id, for_update=True)

    current = (
        await db.execute(
            select(Question)
            .where(Question.exam_id == session.exam_id)
            .order_by(Question.order_num)
            .offset(session.current_q_idx)
            .limit(1)
        )
    ).scalar_one_or_none()
    # enforce strict sequential answering: only the question at the current index is acceptable
    if current is None or current.id != payload.question_id:
        raise HTTPException(status_code=400, detail="این سؤال در مرحله فعلی قابل پاسخ‌دهی نیست")

    db.add(
        Answer(
            session_id=session_id,
            question_id=payload.question_id,
            chosen_opt=payload.chosen_opt,
        )
    )
    session.current_q_idx += 1
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="این سؤال قبلاً پاسخ داده شده است")
    return {"ok": True}


@router.post("/exam/session/{session_id}/submit", response_model=SubmitResult)
async def submit_exam(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _load_context(db, session_id, current_user.id)
    questions = (
        await db.execute(select(Question).where(Question.exam_id == session.exam_id))
    ).scalars().all()
    answers = (
        await db.execute(select(Answer).where(Answer.session_id == session.id))
    ).scalars().all()
    correct_map = {q.id: q.correct_opt for q in questions}
    correct, percent = finalize_session(session, correct_map, answers, len(questions))
    await db.commit()
    return SubmitResult(
        score_20=session.score,
        score_percent=percent,
        correct_count=correct,
        total_count=len(questions),
    )


@router.get("/exam/session/{session_id}/result", response_model=SubmitResult)
async def session_result(
    session_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    session = await _get_owned_session(db, current_user.id, session_id)
    if session.status != "completed":
        raise HTTPException(status_code=409, detail="آزمون هنوز به پایان نرسیده است")

    questions = (
        await db.execute(select(Question).where(Question.exam_id == session.exam_id))
    ).scalars().all()
    answers = (
        await db.execute(select(Answer).where(Answer.session_id == session.id))
    ).scalars().all()
    correct_map = {q.id: q.correct_opt for q in questions}
    correct = sum(1 for a in answers if correct_map.get(a.question_id) == a.chosen_opt)
    percent = (correct / len(questions) * 100) if questions else 0.0
    return SubmitResult(
        score_20=session.score,
        score_percent=percent,
        correct_count=correct,
        total_count=len(questions),
    )


@router.post("/exam/session/{session_id}/fraud")
@limiter.limit(settings.FRAUD_RATE_LIMIT)
async def report_fraud(
    request: Request,
    session_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await _load_context(db, session_id, current_user.id)
    event_type = payload.get("type", "tab_switch")
    if event_type not in FRAUD_EVENT_TYPES:
        event_type = "unknown"
    if event_type == "tab_switch":
        session.tab_switches += 1
    # fraud_count is the total of ALL suspicious events, including tab switches;
    # tab_switches is the specialized sub-counter for the tab-switch subtype.
    session.fraud_count += 1
    events = list(session.fraud_events or [])
    if len(events) < MAX_FRAUD_EVENTS:
        events.append({"type": event_type, "at": utcnow().isoformat()})
    session.fraud_events = events
    await db.commit()
    return {"ok": True}
