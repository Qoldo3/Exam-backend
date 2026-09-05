import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_admin, require_roles
from app.core.ratelimit import limiter
from app.core.scoring import finalize_session
from app.core.security import utcnow, validate_national_id
from app.models.audit import AdminAuditLog
from app.models.exam import Exam, Question
from app.models.exam_access import ExamAccess
from app.models.payment import Payment
from app.models.session import Answer, ExamSession
from app.models.setting import delete_setting, get_setting, set_setting
from app.models.user import (
    EXAM_MANAGER_ROLES,
    FINANCE_ROLES,
    ROLE_SUPERADMIN,
    ROLE_USER,
    User,
)
from app.payments import PAYMENT_MODES, merchant_valid, resolve_payment_config
from app.schemas.admin import (
    AdminExamOut,
    AdminPaymentOut,
    AdminResultRow,
    AdminStats,
    AdminUserCreate,
    AdminUserOut,
    AdminUserUpdate,
    AuditLogOut,
    DailyPointOut,
    ExamAccessIn,
    ExamAdminDetail,
    ExamResultsOut,
    FraudEventOut,
    FraudRowOut,
    PagedAuditOut,
    PagedFraudOut,
    PagedPaymentsOut,
    PagedUsersOut,
    ProctoringRowOut,
    QuestionAdminOut,
    QuestionAnalyticsOut,
    RevenueByExam,
    RevenueOut,
    RoleUpdateIn,
    SessionReviewOut,
    ReviewQuestionOut,
    SettingsStatusOut,
    SettingsUpdate,
    UserDetailOut,
    UserExamAccessOut,
)
from app.schemas.exam import ExamUpsert

router = APIRouter(tags=["admin"])

EXAM_ROLES = tuple(sorted(EXAM_MANAGER_ROLES))
FIN_ROLES = tuple(sorted(FINANCE_ROLES))

# GET endpoints that do real per-request work (CSV generation, polls, reports).
ADMIN_GET_LIMIT = "120/minute"


# ---------- helpers ----------


async def _audit(
    db: AsyncSession, admin: User, action: str, target_type: str, target_id: str | None = None,
    detail: dict | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            admin_id=admin.id, action=action, target_type=target_type,
            target_id=target_id, detail=detail,
        )
    )


async def _users_by_id(db: AsyncSession, ids: list[str]) -> dict[str, User]:
    """Batch-load users in one query (avoids N+1 on admin list endpoints)."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {u.id: u for u in rows}


async def _exams_by_id(db: AsyncSession, ids: list[str]) -> dict[str, Exam]:
    """Batch-load exams in one query (avoids N+1 on admin list endpoints)."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = (await db.execute(select(Exam).where(Exam.id.in_(ids)))).scalars().all()
    return {e.id: e for e in rows}


async def _exam_session_count(db: AsyncSession, exam_id: str) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(ExamSession).where(ExamSession.exam_id == exam_id)
        )
    ).scalar_one()


async def _load_exam(db: AsyncSession, exam_id: str) -> Exam:
    exam = await db.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="آزمون یافت نشد")
    return exam


async def _load_session(db: AsyncSession, session_id: str) -> ExamSession:
    session = await db.get(ExamSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="جلسه یافت نشد")
    return session


async def _replace_questions(db: AsyncSession, exam_id: str, upsert: ExamUpsert) -> None:
    await db.execute(delete(Question).where(Question.exam_id == exam_id))
    for i, q in enumerate(upsert.questions):
        db.add(
            Question(
                exam_id=exam_id,
                body=q.body,
                option_a=q.option_a,
                option_b=q.option_b,
                option_c=q.option_c,
                option_d=q.option_d,
                correct_opt=q.correct_opt,
                order_num=i,
            )
        )


def _csv_safe(value) -> str:
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


async def _results_rows(db: AsyncSession, exam_id: str) -> list[dict]:
    """All participants of an exam with score, correct count and fraud counters.

    correct_count is counted from the actual Answer rows (never reconstructed from
    the rounded score), and all lookups are batched — 4 queries total.
    """
    questions = (
        await db.execute(select(Question.id, Question.correct_opt).where(Question.exam_id == exam_id))
    ).all()
    total = len(questions)
    correct_map = {qid: opt for qid, opt in questions}

    sessions = (
        await db.execute(
            select(ExamSession)
            .where(ExamSession.exam_id == exam_id)
            .order_by(ExamSession.created_at.desc())
        )
    ).scalars().all()

    users = await _users_by_id(db, [s.user_id for s in sessions])

    correct_by_session: dict[str, int] = {}
    session_ids = [s.id for s in sessions]
    if session_ids:
        answers = (
            await db.execute(
                select(Answer.session_id, Answer.question_id, Answer.chosen_opt).where(
                    Answer.session_id.in_(session_ids)
                )
            )
        ).all()
        for sid, qid, chosen in answers:
            if correct_map.get(qid) == chosen:
                correct_by_session[sid] = correct_by_session.get(sid, 0) + 1

    rows = []
    for s in sessions:
        user = users.get(s.user_id)
        if not user:
            continue
        rows.append(
            {
                "session_id": s.id,
                "full_name": user.full_name,
                "national_id": user.national_id,
                "phone": user.phone,
                "status": s.status,
                "score_20": s.score,
                "correct_count": correct_by_session.get(s.id) if s.status == "completed" else None,
                "total_count": total,
                "tab_switches": s.tab_switches,
                "fraud_count": s.fraud_count,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            }
        )
    return rows


# ---------- dashboard ----------


@router.get("/admin/stats", response_model=AdminStats)
async def admin_stats(admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    exams = (await db.execute(select(func.count()).select_from(Exam))).scalar_one()
    verified = (
        await db.execute(select(func.count()).select_from(Payment).where(Payment.status == "verified"))
    ).scalar_one()
    revenue = (
        await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "verified")
        )
    ).scalar_one()
    sessions = (await db.execute(select(func.count()).select_from(ExamSession))).scalar_one()
    completed = (
        await db.execute(
            select(func.count()).select_from(ExamSession).where(ExamSession.status == "completed")
        )
    ).scalar_one()
    active = (
        await db.execute(
            select(func.count()).select_from(ExamSession).where(ExamSession.status == "active")
        )
    ).scalar_one()
    avg_score = (
        await db.execute(
            select(func.avg(ExamSession.score)).where(
                ExamSession.status == "completed", ExamSession.score.is_not(None)
            )
        )
    ).scalar_one()
    return AdminStats(
        users=users,
        exams=exams,
        verified_payments=verified,
        total_revenue_rial=int(revenue or 0),
        sessions=sessions,
        completed_sessions=completed,
        active_sessions=active,
        avg_score_20=round(float(avg_score), 2) if avg_score is not None else None,
    )


# ---------- exams (exam_manager+) ----------


@router.get("/admin/exams", response_model=list[AdminExamOut])
async def admin_exams(admin: User = Depends(require_roles(*EXAM_ROLES)), db: AsyncSession = Depends(get_db)):
    exams = (await db.execute(select(Exam).order_by(Exam.title))).scalars().all()
    exam_ids = [e.id for e in exams]
    if not exam_ids:
        return []
    # three grouped queries instead of 3N per-exam queries
    q_counts = dict(
        (
            await db.execute(
                select(Question.exam_id, func.count())
                .where(Question.exam_id.in_(exam_ids))
                .group_by(Question.exam_id)
            )
        ).all()
    )
    part_counts = dict(
        (
            await db.execute(
                select(ExamSession.exam_id, func.count(func.distinct(ExamSession.user_id)))
                .where(ExamSession.exam_id.in_(exam_ids))
                .group_by(ExamSession.exam_id)
            )
        ).all()
    )
    paid_counts = dict(
        (
            await db.execute(
                select(Payment.exam_id, func.count())
                .where(Payment.exam_id.in_(exam_ids), Payment.status == "verified")
                .group_by(Payment.exam_id)
            )
        ).all()
    )
    return [
        AdminExamOut(
            id=ex.id, title=ex.title, description=ex.description,
            duration_secs=ex.duration_secs, price=ex.price, is_active=ex.is_active,
            question_count=q_counts.get(ex.id, 0),
            participant_count=part_counts.get(ex.id, 0),
            payment_count=paid_counts.get(ex.id, 0),
        )
        for ex in exams
    ]


@router.get("/admin/exams/{exam_id}", response_model=ExamAdminDetail)
async def admin_exam_detail(
    exam_id: str, admin: User = Depends(require_roles(*EXAM_ROLES)), db: AsyncSession = Depends(get_db)
):
    exam = await _load_exam(db, exam_id)
    questions = (
        await db.execute(
            select(Question).where(Question.exam_id == exam_id).order_by(Question.order_num)
        )
    ).scalars().all()
    return ExamAdminDetail(
        id=exam.id, title=exam.title, description=exam.description,
        duration_secs=exam.duration_secs, price=exam.price, is_active=exam.is_active,
        questions=[QuestionAdminOut.model_validate(q) for q in questions],
    )


@router.post("/admin/exams", response_model=ExamAdminDetail, status_code=201)
@limiter.limit("60/minute")
async def create_exam(
    request: Request,
    payload: ExamUpsert,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    exam = Exam(
        title=payload.title, description=payload.description,
        duration_secs=payload.duration_secs, price=payload.price, is_active=payload.is_active,
    )
    db.add(exam)
    await db.flush()
    await _replace_questions(db, exam.id, payload)
    await _audit(db, admin, "exam_create", "exam", exam.id, {"title": payload.title})
    await db.commit()
    await db.refresh(exam)
    questions = (
        await db.execute(
            select(Question).where(Question.exam_id == exam.id).order_by(Question.order_num)
        )
    ).scalars().all()
    return ExamAdminDetail(
        id=exam.id, title=exam.title, description=exam.description,
        duration_secs=exam.duration_secs, price=exam.price, is_active=exam.is_active,
        questions=[QuestionAdminOut.model_validate(q) for q in questions],
    )


@router.put("/admin/exams/{exam_id}", response_model=ExamAdminDetail)
@limiter.limit("60/minute")
async def update_exam(
    request: Request,
    exam_id: str,
    payload: ExamUpsert,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    exam = await _load_exam(db, exam_id)
    if await _exam_session_count(db, exam_id):
        raise HTTPException(
            status_code=409,
            detail="این آزمون شرکت‌کننده دارد و امکان ویرایش سوالات وجود ندارد؛ در صورت نیاز آن را غیرفعال کنید",
        )
    exam.title = payload.title
    exam.description = payload.description
    exam.duration_secs = payload.duration_secs
    exam.price = payload.price
    exam.is_active = payload.is_active
    await _replace_questions(db, exam_id, payload)
    await _audit(db, admin, "exam_update", "exam", exam_id, {"title": payload.title})
    await db.commit()
    await db.refresh(exam)
    questions = (
        await db.execute(
            select(Question).where(Question.exam_id == exam_id).order_by(Question.order_num)
        )
    ).scalars().all()
    return ExamAdminDetail(
        id=exam.id, title=exam.title, description=exam.description,
        duration_secs=exam.duration_secs, price=exam.price, is_active=exam.is_active,
        questions=[QuestionAdminOut.model_validate(q) for q in questions],
    )


@router.post("/admin/exams/{exam_id}/toggle")
@limiter.limit("60/minute")
async def toggle_exam(
    request: Request,
    exam_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    exam = await _load_exam(db, exam_id)
    exam.is_active = not exam.is_active
    await _audit(db, admin, "exam_toggle", "exam", exam_id, {"is_active": exam.is_active})
    await db.commit()
    return {"is_active": exam.is_active}


@router.delete("/admin/exams/{exam_id}")
@limiter.limit("60/minute")
async def delete_exam(
    request: Request,
    exam_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    exam = await _load_exam(db, exam_id)
    if await _exam_session_count(db, exam_id):
        raise HTTPException(
            status_code=409, detail="این آزمون شرکت‌کننده دارد؛ به جای حذف، آن را غیرفعال کنید"
        )
    session_ids = (
        await db.execute(select(ExamSession.id).where(ExamSession.exam_id == exam_id))
    ).scalars().all()
    if session_ids:
        await db.execute(delete(Answer).where(Answer.session_id.in_(session_ids)))
        await db.execute(delete(ExamSession).where(ExamSession.exam_id == exam_id))
    await db.execute(delete(Payment).where(Payment.exam_id == exam_id))
    await db.execute(delete(Question).where(Question.exam_id == exam_id))
    await db.execute(delete(Exam).where(Exam.id == exam_id))
    await _audit(db, admin, "exam_delete", "exam", exam_id, {"title": exam.title})
    await db.commit()
    return {"ok": True}


@router.get("/admin/exams/{exam_id}/results", response_model=ExamResultsOut)
async def exam_results(
    exam_id: str, admin: User = Depends(require_roles(*EXAM_ROLES)), db: AsyncSession = Depends(get_db)
):
    exam = await _load_exam(db, exam_id)
    rows = await _results_rows(db, exam_id)
    return ExamResultsOut(exam_title=exam.title, rows=[AdminResultRow(**r) for r in rows])


@router.get("/admin/exams/{exam_id}/results.csv")
@limiter.limit(ADMIN_GET_LIMIT)
async def exam_results_csv(
    request: Request,
    exam_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    exam = await _load_exam(db, exam_id)
    rows = await _results_rows(db, exam_id)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["نام", "کد ملی", "موبایل", "وضعیت", "نمره (از ۲۰)", "پاسخ صحیح", "تعداد سؤال", "خروج از تب", "رویداد تقلب", "شروع", "پایان"]
    )
    status_fa = {"pending": "شروع نشده", "active": "در حال برگزاری", "completed": "پایان‌یافته"}
    for r in rows:
        writer.writerow(
            [
                _csv_safe(r["full_name"]),
                _csv_safe(r["national_id"]),
                _csv_safe(r["phone"]),
                _csv_safe(status_fa.get(r["status"], r["status"])),
                r["score_20"] if r["score_20"] is not None else "",
                r["correct_count"] if r["correct_count"] is not None else "",
                r["total_count"],
                r["tab_switches"],
                r["fraud_count"],
                r["started_at"] or "",
                r["ended_at"] or "",
            ]
        )
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="results-{exam.id}.csv"'},
    )


# ---------- users (view: all admins; block: exam_manager+) ----------


@router.get("/admin/users", response_model=PagedUsersOut)
async def admin_users(
    q: str = "",
    role: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            (User.full_name.like(like)) | (User.national_id.like(like)) | (User.phone.like(like))
        )
    if role:
        stmt = stmt.where(User.role == role)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    users = (
        await db.execute(
            stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return PagedUsersOut(total=total, items=[AdminUserOut.model_validate(u) for u in users])


@router.post("/admin/users", response_model=AdminUserOut, status_code=201)
@limiter.limit("60/minute")
async def create_user(
    request: Request,
    payload: AdminUserCreate,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Manually add a student account (name / national id / phone)."""
    if len(payload.full_name.strip()) < 2:
        raise HTTPException(status_code=400, detail="نام و نام خانوادگی معتبر نیست")
    if not validate_national_id(payload.national_id):
        raise HTTPException(status_code=400, detail="کد ملی معتبر نیست")
    if not payload.phone.startswith("09") or len(payload.phone) != 11 or not payload.phone.isdigit():
        raise HTTPException(status_code=400, detail="شماره موبایل معتبر نیست")
    existing = await db.execute(
        select(User).where(
            (User.national_id == payload.national_id) | (User.phone == payload.phone)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="کاربری با این کد ملی یا شماره موبایل قبلاً ثبت شده است")
    user = User(
        full_name=payload.full_name.strip(),
        national_id=payload.national_id,
        phone=payload.phone,
    )
    db.add(user)
    await db.flush()
    await _audit(
        db, admin, "user_create", "user", user.id,
        {"full_name": user.full_name, "national_id": user.national_id, "phone": user.phone},
    )
    await db.commit()
    await db.refresh(user)
    return AdminUserOut.model_validate(user)


@router.get("/admin/users/{user_id}", response_model=UserDetailOut)
async def admin_user_detail(
    user_id: str, admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")

    payments = (
        await db.execute(
            select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc())
        )
    ).scalars().all()
    sessions = (
        await db.execute(
            select(ExamSession).where(ExamSession.user_id == user_id).order_by(ExamSession.created_at.desc())
        )
    ).scalars().all()
    grants = (
        await db.execute(
            select(ExamAccess).where(ExamAccess.user_id == user_id).order_by(ExamAccess.created_at.desc())
        )
    ).scalars().all()
    exams = await _exams_by_id(
        db, [p.exam_id for p in payments] + [s.exam_id for s in sessions] + [g.exam_id for g in grants]
    )

    payment_items = [
        {
            "id": p.id,
            "exam_title": exams[p.exam_id].title if p.exam_id in exams else "—",
            "amount": p.amount,
            "status": p.status,
            "ref_id": p.ref_id,
            "created_at": p.created_at,
            "verified_at": p.verified_at,
        }
        for p in payments
    ]
    session_items = [
        {
            "id": s.id,
            "exam_title": exams[s.exam_id].title if s.exam_id in exams else "—",
            "status": s.status,
            "score_20": s.score,
            "tab_switches": s.tab_switches,
            "fraud_count": s.fraud_count,
            "started_at": s.started_at,
            "ended_at": s.ended_at,
        }
        for s in sessions
    ]

    return UserDetailOut(
        user=AdminUserOut.model_validate(user),
        payments=payment_items,
        sessions=session_items,
        exam_access=[
            UserExamAccessOut(
                exam_id=g.exam_id,
                exam_title=exams[g.exam_id].title if g.exam_id in exams else "—",
                granted_at=g.created_at,
            )
            for g in grants
        ],
    )


@router.post("/admin/users/{user_id}/block")
@limiter.limit("60/minute")
async def block_user(
    request: Request,
    user_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if user.is_admin and admin.role != ROLE_SUPERADMIN:
        raise HTTPException(status_code=403, detail="فقط مدیر ارشد می‌تواند حساب‌های مدیریتی را مسدود کند")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="نمی‌توانید حساب خودتان را مسدود کنید")
    user.is_blocked = True
    await _audit(db, admin, "user_block", "user", user_id, {"role": user.role})
    await db.commit()
    return {"is_blocked": True}


@router.post("/admin/users/{user_id}/unblock")
@limiter.limit("60/minute")
async def unblock_user(
    request: Request,
    user_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if user.is_admin and admin.role != ROLE_SUPERADMIN:
        raise HTTPException(status_code=403, detail="فقط مدیر ارشد می‌تواند حساب‌های مدیریتی را مدیریت کند")
    user.is_blocked = False
    await _audit(db, admin, "user_unblock", "user", user_id, {})
    await db.commit()
    return {"is_blocked": False}


@router.put("/admin/users/{user_id}", response_model=AdminUserOut)
@limiter.limit("60/minute")
async def update_user(
    request: Request,
    user_id: str,
    payload: AdminUserUpdate,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Edit a user's profile (name / national id / phone)."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if user.is_admin and admin.role != ROLE_SUPERADMIN:
        raise HTTPException(status_code=403, detail="فقط مدیر ارشد می‌تواند حساب‌های مدیریتی را ویرایش کند")
    if len(payload.full_name.strip()) < 2:
        raise HTTPException(status_code=400, detail="نام و نام خانوادگی معتبر نیست")
    if not validate_national_id(payload.national_id):
        raise HTTPException(status_code=400, detail="کد ملی معتبر نیست")
    if not payload.phone.startswith("09") or len(payload.phone) != 11 or not payload.phone.isdigit():
        raise HTTPException(status_code=400, detail="شماره موبایل معتبر نیست")
    clash = await db.execute(
        select(User).where(
            User.id != user_id,
            (User.national_id == payload.national_id) | (User.phone == payload.phone),
        )
    )
    if clash.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="کد ملی یا شماره موبایل توسط کاربر دیگری استفاده شده است")
    old = {"full_name": user.full_name, "national_id": user.national_id, "phone": user.phone}
    user.full_name = payload.full_name.strip()
    user.national_id = payload.national_id
    user.phone = payload.phone
    await _audit(db, admin, "user_edit", "user", user_id, {"old": old})
    await db.commit()
    await db.refresh(user)
    return AdminUserOut.model_validate(user)


@router.post("/admin/users/{user_id}/exam-access")
@limiter.limit("60/minute")
async def grant_exam_access(
    request: Request,
    user_id: str,
    payload: ExamAccessIn,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Grant a student access to an exam without payment (till revoked)."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="حساب‌های مدیریتی نیازی به دسترسی آزمون ندارند")
    exam = await db.get(Exam, payload.exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="آزمون یافت نشد")
    existing = await db.execute(
        select(ExamAccess).where(
            ExamAccess.user_id == user_id, ExamAccess.exam_id == payload.exam_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="این کاربر قبلاً به این آزمون دسترسی دارد")
    db.add(ExamAccess(user_id=user_id, exam_id=payload.exam_id, granted_by=admin.id))
    await _audit(db, admin, "exam_access_grant", "user", user_id, {"exam_id": payload.exam_id})
    await db.commit()
    return {"ok": True}


@router.delete("/admin/users/{user_id}/exam-access/{exam_id}")
@limiter.limit("60/minute")
async def revoke_exam_access(
    request: Request,
    user_id: str,
    exam_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        select(ExamAccess).where(ExamAccess.user_id == user_id, ExamAccess.exam_id == exam_id)
    )
    access = row.scalar_one_or_none()
    if not access:
        raise HTTPException(status_code=404, detail="دسترسی یافت نشد")
    await db.delete(access)
    await _audit(db, admin, "exam_access_revoke", "user", user_id, {"exam_id": exam_id})
    await db.commit()
    return {"ok": True}


# ---------- accounts (superadmin) ----------


@router.get("/admin/accounts", response_model=list[AdminUserOut])
async def admin_accounts(admin: User = Depends(require_roles(ROLE_SUPERADMIN)), db: AsyncSession = Depends(get_db)):
    users = (
        await db.execute(select(User).where(User.role != ROLE_USER).order_by(User.created_at.desc()))
    ).scalars().all()
    return [AdminUserOut.model_validate(u) for u in users]


@router.post("/admin/accounts/{user_id}/role", response_model=AdminUserOut)
@limiter.limit("60/minute")
async def set_account_role(
    request: Request,
    user_id: str,
    payload: RoleUpdateIn,
    admin: User = Depends(require_roles(ROLE_SUPERADMIN)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    if payload.role == ROLE_USER and user.id == admin.id:
        raise HTTPException(status_code=400, detail="نمی‌توانید نقش خودتان را حذف کنید")
    if user.role == ROLE_SUPERADMIN and payload.role != ROLE_SUPERADMIN:
        superadmin_count = (
            await db.execute(
                select(func.count()).select_from(User).where(User.role == ROLE_SUPERADMIN)
            )
        ).scalar_one()
        if superadmin_count <= 1:
            raise HTTPException(status_code=400, detail="آخرین مدیر ارشد را نمی‌توان تغییر داد")
    old_role = user.role
    user.role = payload.role
    await _audit(
        db, admin, "role_change", "user", user_id, {"from": old_role, "to": payload.role}
    )
    await db.commit()
    await db.refresh(user)
    return AdminUserOut.model_validate(user)


# ---------- payments (finance + superadmin) ----------


@router.get("/admin/payments", response_model=PagedPaymentsOut)
async def admin_payments(
    status: str = "",
    exam_id: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_roles(*FIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Payment)
    if status:
        stmt = stmt.where(Payment.status == status)
    if exam_id:
        stmt = stmt.where(Payment.exam_id == exam_id)
    if start_date:
        stmt = stmt.where(Payment.created_at >= start_date)
    if end_date:
        stmt = stmt.where(Payment.created_at <= end_date + timedelta(days=1))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    payments = (
        await db.execute(stmt.order_by(Payment.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()
    users = await _users_by_id(db, [p.user_id for p in payments])
    exams = await _exams_by_id(db, [p.exam_id for p in payments])
    items = [
        AdminPaymentOut(
            id=p.id,
            user_full_name=users[p.user_id].full_name if p.user_id in users else "—",
            user_national_id=users[p.user_id].national_id if p.user_id in users else "—",
            exam_title=exams[p.exam_id].title if p.exam_id in exams else "—",
            amount=p.amount,
            status=p.status,
            authority=p.authority,
            ref_id=p.ref_id,
            created_at=p.created_at,
            verified_at=p.verified_at,
        )
        for p in payments
    ]
    return PagedPaymentsOut(total=total, items=items)


@router.post("/admin/payments/{payment_id}/verify")
@limiter.limit("60/minute")
async def verify_payment(
    request: Request,
    payment_id: str,
    admin: User = Depends(require_roles(*FIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="پرداخت یافت نشد")
    if payment.status == "verified":
        return {"status": "verified"}
    if payment.status == "failed":
        raise HTTPException(status_code=409, detail="پرداخت ناموفق قابل تأیید نیست")
    # the payment must actually become verified: revenue stats and exam start
    # both filter on status == "verified"
    payment.status = "verified"
    payment.ref_id = payment.ref_id or "MANUAL"
    payment.verified_at = utcnow()
    await _audit(db, admin, "payment_verify", "payment", payment_id, {"amount": payment.amount})
    await db.commit()
    return {"status": "verified"}


@router.post("/admin/payments/{payment_id}/cancel")
@limiter.limit("60/minute")
async def cancel_payment(
    request: Request,
    payment_id: str,
    admin: User = Depends(require_roles(*FIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    payment = await db.get(Payment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="پرداخت یافت نشد")
    if payment.status == "verified":
        raise HTTPException(status_code=409, detail="پرداخت تأییدشده قابل لغو نیست")
    payment.status = "failed"
    await _audit(db, admin, "payment_cancel", "payment", payment_id, {})
    await db.commit()
    return {"status": "failed"}


@router.get("/admin/payments/revenue", response_model=RevenueOut)
@limiter.limit(ADMIN_GET_LIMIT)
async def revenue_report(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    admin: User = Depends(require_roles(*FIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Payment).where(Payment.status == "verified")
    if start_date:
        stmt = stmt.where(Payment.verified_at >= start_date)
    if end_date:
        stmt = stmt.where(Payment.verified_at <= end_date + timedelta(days=1))
    payments = (await db.execute(stmt)).scalars().all()
    exams = await _exams_by_id(db, [p.exam_id for p in payments])

    by_exam: dict[str, dict] = {}
    total = 0
    for p in payments:
        title = exams[p.exam_id].title if p.exam_id in exams else "—"
        bucket = by_exam.setdefault(title, {"count": 0, "total": 0})
        bucket["count"] += 1
        bucket["total"] += p.amount
        total += p.amount

    return RevenueOut(
        from_date=start_date.isoformat() if start_date else None,
        to_date=end_date.isoformat() if end_date else None,
        total_count=len(payments),
        total_rial=total,
        by_exam=[RevenueByExam(exam_title=t, count=v["count"], total_rial=v["total"]) for t, v in by_exam.items()],
    )


@router.get("/admin/payments/revenue.csv")
@limiter.limit(ADMIN_GET_LIMIT)
async def revenue_csv(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    admin: User = Depends(require_roles(*FIN_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    report = await revenue_report(request, start_date, end_date, admin, db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["آزمون", "تعداد", "درآمد (ریال)"])
    for row in report.by_exam:
        writer.writerow([_csv_safe(row.exam_title), row.count, row.total_rial])
    writer.writerow(["جمع کل", report.total_count, report.total_rial])
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="revenue.csv"'},
    )


# ---------- session review (exam_manager+) ----------


@router.get("/admin/sessions/{session_id}/review", response_model=SessionReviewOut)
@limiter.limit(ADMIN_GET_LIMIT)
async def session_review(
    request: Request,
    session_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    session = await _load_session(db, session_id)
    user = await db.get(User, session.user_id)
    exam = await db.get(Exam, session.exam_id)

    questions = (
        await db.execute(
            select(Question).where(Question.exam_id == session.exam_id).order_by(Question.order_num)
        )
    ).scalars().all()
    answers = (
        await db.execute(
            select(Answer).where(Answer.session_id == session.id).order_by(Answer.answered_at)
        )
    ).scalars().all()
    answer_map = {a.question_id: a for a in answers}

    prev_time = session.started_at
    question_items = []
    for q in questions:
        a = answer_map.get(q.id)
        time_secs = None
        if a and prev_time:
            time_secs = max(0, int((a.answered_at - prev_time).total_seconds()))
            prev_time = a.answered_at
        question_items.append(
            ReviewQuestionOut(
                order_num=q.order_num,
                body=q.body,
                option_a=q.option_a,
                option_b=q.option_b,
                option_c=q.option_c,
                option_d=q.option_d,
                chosen_opt=a.chosen_opt if a else None,
                correct_opt=q.correct_opt,
                is_correct=(a.chosen_opt == q.correct_opt) if a else None,
                time_secs=time_secs,
                answered_at=a.answered_at if a else None,
            )
        )

    total = len(questions)
    # counted from the actual answers, never reconstructed from the rounded score
    correct = sum(
        1 for q in questions
        if answer_map.get(q.id) and answer_map[q.id].chosen_opt == q.correct_opt
    ) if session.status == "completed" else None

    return SessionReviewOut(
        session_id=session.id,
        full_name=user.full_name if user else "—",
        national_id=user.national_id if user else "—",
        exam_title=exam.title if exam else "—",
        status=session.status,
        score_20=session.score,
        correct_count=correct,
        total_count=total,
        tab_switches=session.tab_switches,
        fraud_count=session.fraud_count,
        started_at=session.started_at,
        ended_at=session.ended_at,
        questions=question_items,
        fraud_events=[FraudEventOut(**e) for e in (session.fraud_events or [])],
    )


# ---------- live proctoring (exam_manager+) ----------


@router.get("/admin/proctoring", response_model=list[ProctoringRowOut])
@limiter.limit(ADMIN_GET_LIMIT)
async def proctoring(
    request: Request,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    sessions = (
        await db.execute(
            select(ExamSession).where(ExamSession.status == "active").order_by(ExamSession.started_at)
        )
    ).scalars().all()
    users = await _users_by_id(db, [s.user_id for s in sessions])
    exams = await _exams_by_id(db, [s.exam_id for s in sessions])
    now = utcnow()
    out = []
    for s in sessions:
        remaining = 0
        if s.expires_at:
            remaining = max(0, int((s.expires_at - now).total_seconds()))
        out.append(
            ProctoringRowOut(
                session_id=s.id,
                full_name=users[s.user_id].full_name if s.user_id in users else "—",
                national_id=users[s.user_id].national_id if s.user_id in users else "—",
                exam_title=exams[s.exam_id].title if s.exam_id in exams else "—",
                remaining_secs=remaining,
                tab_switches=s.tab_switches,
                fraud_count=s.fraud_count,
                started_at=s.started_at,
            )
        )
    return out


@router.post("/admin/sessions/{session_id}/terminate")
@limiter.limit("60/minute")
async def terminate_session(
    request: Request,
    session_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Force-finalize a session with the answers recorded so far."""
    session = await _load_session(db, session_id)
    if session.status == "completed":
        return {"status": "completed", "already": True}
    questions = (
        await db.execute(select(Question).where(Question.exam_id == session.exam_id))
    ).scalars().all()
    answers = (
        await db.execute(select(Answer).where(Answer.session_id == session.id))
    ).scalars().all()
    correct_map = {q.id: q.correct_opt for q in questions}
    finalize_session(session, correct_map, answers, len(questions))
    await _audit(db, admin, "session_terminate", "session", session_id, {})
    await db.commit()
    return {"status": "completed", "already": False}


# ---------- reports (exam_manager+) ----------


@router.get("/admin/reports/daily", response_model=list[DailyPointOut])
@limiter.limit(ADMIN_GET_LIMIT)
async def daily_report(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    metric: str = "attempts",  # attempts | revenue
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    if metric not in ("attempts", "revenue"):
        raise HTTPException(status_code=422, detail="metric باید attempts یا revenue باشد")
    if metric == "revenue":
        stmt = select(func.date(Payment.verified_at), func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "verified"
        )
    else:
        # "attempts" = sessions that actually started (excludes never-started pending rows)
        stmt = select(func.date(ExamSession.created_at), func.count()).where(
            ExamSession.status.in_(("active", "completed"))
        )
    if start_date:
        col = Payment.verified_at if metric == "revenue" else ExamSession.created_at
        stmt = stmt.where(col >= start_date)
    if end_date:
        col = Payment.verified_at if metric == "revenue" else ExamSession.created_at
        stmt = stmt.where(col <= end_date + timedelta(days=1))
    group_col = func.date(Payment.verified_at) if metric == "revenue" else func.date(ExamSession.created_at)
    rows = (await db.execute(stmt.group_by(group_col))).all()
    return [DailyPointOut(date=str(d or ""), value=int(v or 0)) for d, v in rows]


@router.get("/admin/reports/question-analytics", response_model=list[QuestionAnalyticsOut])
@limiter.limit(ADMIN_GET_LIMIT)
async def question_analytics(
    request: Request,
    exam_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    exam = await _load_exam(db, exam_id)
    questions = (
        await db.execute(
            select(Question).where(Question.exam_id == exam_id).order_by(Question.order_num)
        )
    ).scalars().all()
    completed = (
        await db.execute(
            select(func.count()).select_from(ExamSession).where(
                ExamSession.exam_id == exam_id, ExamSession.status == "completed"
            )
        )
    ).scalar_one()

    # single query for every answer of this exam instead of one per question
    answer_rows = (
        await db.execute(
            select(Answer.question_id, Answer.chosen_opt)
            .join(Question, Question.id == Answer.question_id)
            .where(Question.exam_id == exam_id)
        )
    ).all()
    by_question: dict[str, list[str]] = {}
    for qid, chosen in answer_rows:
        by_question.setdefault(qid, []).append(chosen)

    out = []
    for q in questions:
        answered_rows = by_question.get(q.id, [])
        answered = len(answered_rows)
        correct = sum(1 for o in answered_rows if o == q.correct_opt)
        blank = max(0, completed - answered)
        distractors = [
            {"opt": opt, "count": sum(1 for o in answered_rows if o == opt)}
            for opt in ("A", "B", "C", "D")
        ]
        out.append(
            QuestionAnalyticsOut(
                order_num=q.order_num,
                body=q.body,
                correct_opt=q.correct_opt,
                answered=answered,
                correct=correct,
                p_value=round(correct / answered * 100, 1) if answered else None,
                blank_rate=round(blank / completed * 100, 1) if completed else None,
                distractors=distractors,
            )
        )
    return out


@router.get("/admin/reports/fraud", response_model=PagedFraudOut)
@limiter.limit(ADMIN_GET_LIMIT)
async def fraud_report(
    request: Request,
    exam_id: str = "",
    min_switches: int = Query(0, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    # threshold is clamped to >= 1 so the default never matches every session
    # (tab_switches >= 0 is trivially true for the whole table)
    threshold = max(min_switches, 1)
    stmt = select(ExamSession).where(
        (ExamSession.tab_switches >= threshold) | (ExamSession.fraud_count > 0)
    )
    if exam_id:
        stmt = stmt.where(ExamSession.exam_id == exam_id)
    stmt = stmt.order_by((ExamSession.tab_switches + ExamSession.fraud_count).desc())
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    sessions = (
        await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()
    users = await _users_by_id(db, [s.user_id for s in sessions])
    exams = await _exams_by_id(db, [s.exam_id for s in sessions])

    rows = [
        FraudRowOut(
            session_id=s.id,
            full_name=users[s.user_id].full_name if s.user_id in users else "—",
            national_id=users[s.user_id].national_id if s.user_id in users else "—",
            exam_title=exams[s.exam_id].title if s.exam_id in exams else "—",
            status=s.status,
            tab_switches=s.tab_switches,
            fraud_count=s.fraud_count,
            score_20=s.score,
        )
        for s in sessions
    ]
    return PagedFraudOut(total=total, items=rows)


@router.get("/admin/reports/daily.csv")
@limiter.limit(ADMIN_GET_LIMIT)
async def daily_csv(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    metric: str = "attempts",
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    points = await daily_report(request, start_date, end_date, metric, admin, db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["تاریخ", "درآمد (ریال)" if metric == "revenue" else "تعداد"])
    for p in points:
        writer.writerow([p.date, p.value])
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="daily-report.csv"'},
    )


@router.get("/admin/reports/question-analytics.csv")
@limiter.limit(ADMIN_GET_LIMIT)
async def question_analytics_csv(
    request: Request,
    exam_id: str,
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    rows = await question_analytics(request, exam_id, admin, db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["سؤال", "متن", "پاسخ صحیح", "پاسخ‌داده", "درست", "ضریب دشواری (P)", "بدون پاسخ (%)"])
    for q in rows:
        writer.writerow(
            [_csv_safe(q.order_num + 1), _csv_safe(q.body), q.correct_opt, q.answered, q.correct, q.p_value or "", q.blank_rate or ""]
        )
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="question-analytics.csv"'},
    )


@router.get("/admin/reports/fraud.csv")
@limiter.limit(ADMIN_GET_LIMIT)
async def fraud_csv(
    request: Request,
    exam_id: str = "",
    min_switches: int = Query(0, ge=0),
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    report = await fraud_report(request, exam_id, min_switches, 1, 100000, admin, db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["نام", "کد ملی", "آزمون", "وضعیت", "خروج از تب", "رویداد تقلب", "نمره (از ۲۰)"])
    status_fa = {"pending": "شروع نشده", "active": "در حال برگزاری", "completed": "پایان‌یافته"}
    for r in report.items:
        writer.writerow(
            [
                _csv_safe(r.full_name),
                _csv_safe(r.national_id),
                _csv_safe(r.exam_title),
                _csv_safe(status_fa.get(r.status, r.status)),
                r.tab_switches,
                r.fraud_count,
                r.score_20 if r.score_20 is not None else "",
            ]
        )
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="fraud-report.csv"'},
    )


# ---------- system settings (superadmin) ----------


@router.get("/admin/settings", response_model=SettingsStatusOut)
async def system_settings_get(
    admin: User = Depends(require_roles(ROLE_SUPERADMIN)), db: AsyncSession = Depends(get_db)
):
    stored_mode = await get_setting(db, "payment_mode")
    stored_merchant = await get_setting(db, "zarinpal_merchant")
    config = await resolve_payment_config(db)
    return SettingsStatusOut(
        payment_mode=stored_mode,
        effective_mode=config.mode,
        zarinpal_merchant=stored_merchant,
        effective_merchant=config.merchant,
        merchant_valid=config.merchant_valid,
        payments_admin_only=config.payments_admin_only,
    )


@router.put("/admin/settings", response_model=SettingsStatusOut)
@limiter.limit("30/minute")
async def system_settings_update(
    request: Request,
    payload: SettingsUpdate,
    admin: User = Depends(require_roles(ROLE_SUPERADMIN)),
    db: AsyncSession = Depends(get_db),
):
    if payload.payment_mode not in ("", *PAYMENT_MODES):
        raise HTTPException(status_code=422, detail="حالت پرداخت نامعتبر است")
    merchant = (payload.zarinpal_merchant or "").strip()
    if merchant and not merchant_valid(merchant):
        raise HTTPException(
            status_code=422,
            detail="شناسه درگاه (Merchant ID) باید یک UUID معتبر ۳۶ کاراکتری باشد",
        )
    if payload.payment_mode:
        await set_setting(db, "payment_mode", payload.payment_mode)
    else:
        await delete_setting(db, "payment_mode")
    if merchant:
        await set_setting(db, "zarinpal_merchant", merchant)
    else:
        await delete_setting(db, "zarinpal_merchant")
    await _audit(
        db, admin, "settings_update", "settings", None,
        {"payment_mode": payload.payment_mode or "env", "merchant_set": bool(merchant)},
    )
    await db.commit()

    stored_mode = await get_setting(db, "payment_mode")
    stored_merchant = await get_setting(db, "zarinpal_merchant")
    config = await resolve_payment_config(db)
    return SettingsStatusOut(
        payment_mode=stored_mode,
        effective_mode=config.mode,
        zarinpal_merchant=stored_merchant,
        effective_merchant=config.merchant,
        merchant_valid=config.merchant_valid,
        payments_admin_only=config.payments_admin_only,
    )


# ---------- audit log (exam_manager+) ----------


@router.get("/admin/audit", response_model=PagedAuditOut)
@limiter.limit(ADMIN_GET_LIMIT)
async def audit_log(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_roles(*EXAM_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count()).select_from(AdminAuditLog))).scalar_one()
    logs = (
        await db.execute(
            select(AdminAuditLog)
            .order_by(AdminAuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    users = await _users_by_id(db, [log.admin_id for log in logs])
    items = [
        AuditLogOut(
            id=log.id,
            admin_name=users[log.admin_id].full_name if log.admin_id in users else "—",
            action=log.action,
            target_type=log.target_type,
            target_id=log.target_id,
            detail=log.detail,
            created_at=log.created_at,
        )
        for log in logs
    ]
    return PagedAuditOut(total=total, items=items)
