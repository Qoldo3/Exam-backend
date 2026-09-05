from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.ratelimit import limiter
from app.core.security import utcnow
from app.models.exam import Exam
from app.models.payment import Payment
from app.models.session import ExamSession
from app.models.user import ADMIN_ROLES, User
from app.payments import resolve_payment_config
from app.schemas.exam_session import PaymentStartOut, PaymentStartRequest, PaymentStatusOut

router = APIRouter(tags=["payments"])


@router.post("/payment/start", response_model=PaymentStartOut)
@limiter.limit("20/minute")
async def start_payment(
    request: Request,
    payload: PaymentStartRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # global safety gate first: when the gateway is unconfigured, students are
    # locked out of payments entirely (admins may still test the flow)
    config = await resolve_payment_config(db)
    if config.payments_admin_only and current_user.role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=403,
            detail="درگاه پرداخت هنوز پیکربندی نشده است؛ فقط مدیران می‌توانند پرداخت آزمایشی انجام دهند",
        )

    exam_id = payload.exam_id

    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if not exam or not exam.is_active:
        raise HTTPException(status_code=404, detail="آزمون یافت نشد")

    # never mint a second verified payment for the same (user, exam): reusing
    # the existing one keeps revenue reports honest and the student flow intact.
    # Exception: a user who already COMPLETED the exam may retake it with a
    # fresh attempt (admin grant), which requires a new payment — so dedup only
    # applies while no completed attempt exists yet.
    completed = await db.execute(
        select(ExamSession.id).where(
            ExamSession.exam_id == exam_id,
            ExamSession.user_id == current_user.id,
            ExamSession.status == "completed",
        )
    )
    if not completed.scalar_one_or_none():
        existing = await db.execute(
            select(Payment).where(
                Payment.exam_id == exam_id,
                Payment.user_id == current_user.id,
                Payment.status == "verified",
            )
        )
        prior = existing.scalar_one_or_none()
        if prior:
            return PaymentStartOut(payment_id=prior.id, redirect_url=None)

    payment = Payment(user_id=current_user.id, exam_id=exam.id, amount=exam.price)
    db.add(payment)
    await db.flush()

    if config.provider is None:
        # local simulation: mark as verified immediately so flow is testable
        payment.status = "verified"
        payment.ref_id = "LOCAL-SIM"
        payment.verified_at = utcnow()
        await db.commit()
        await db.refresh(payment)
        return PaymentStartOut(payment_id=payment.id, redirect_url=None)

    gateway = await config.provider.create_payment(
        amount_rial=exam.price,
        description=f"ثبت‌نام آزمون «{exam.title}»",
        mobile=current_user.phone,
    )
    if not gateway.success:
        payment.status = "failed"
        await db.commit()
        raise HTTPException(status_code=502, detail=gateway.message or "خطا در ایجاد پرداخت")

    payment.authority = gateway.gateway_ref
    await db.commit()
    return PaymentStartOut(payment_id=payment.id, redirect_url=gateway.redirect_url)


@router.get("/payment/callback")
async def payment_callback(
    Authority: str = "",
    Status: str = "",
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Payment).where(Payment.authority == Authority))
    payment = result.scalar_one_or_none()
    if not payment:
        return RedirectResponse(f"{settings.FRONTEND_URL}/?payment=failed")

    back = f"{settings.FRONTEND_URL}/exam/{payment.exam_id}"

    if Status == "NOK":
        payment.status = "failed"
        await db.commit()
        return RedirectResponse(f"{back}?payment=failed")

    config = await resolve_payment_config(db)
    if config.provider is None:
        return RedirectResponse(f"{back}?payment=failed")

    verify = await config.provider.verify(amount_rial=payment.amount, gateway_ref=payment.authority)
    if verify.success:
        payment.status = "verified"
        payment.ref_id = verify.ref_id
        payment.verified_at = utcnow()
        await db.commit()
        return RedirectResponse(f"{back}?payment=success")
    payment.status = "failed"
    await db.commit()
    return RedirectResponse(f"{back}?payment=failed")


@router.get("/payment/status/{payment_id}", response_model=PaymentStatusOut)
async def payment_status(
    payment_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment or payment.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="پرداخت یافت نشد")
    return PaymentStatusOut(
        status=payment.status,
        ref_id=payment.ref_id,
        exam_id=payment.exam_id,
    )
