from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.user import ALL_ROLES


# --- exams ---


class QuestionAdminOut(BaseModel):
    """Question as served to admins (includes the correct answer)."""

    id: str
    body: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_opt: str
    order_num: int

    model_config = {"from_attributes": True}


class ExamAdminDetail(BaseModel):
    id: str
    title: str
    description: str
    duration_secs: int
    price: int
    is_active: bool
    questions: list[QuestionAdminOut]


class AdminStats(BaseModel):
    users: int
    exams: int
    verified_payments: int
    total_revenue_rial: int
    sessions: int
    completed_sessions: int
    avg_score_20: float | None = None
    active_sessions: int = 0


class AdminExamOut(BaseModel):
    id: str
    title: str
    description: str
    duration_secs: int
    price: int
    is_active: bool
    question_count: int
    participant_count: int
    payment_count: int


class AdminResultRow(BaseModel):
    session_id: str
    full_name: str
    national_id: str
    phone: str
    status: str  # pending | active | completed
    score_20: float | None = None
    correct_count: int | None = None
    total_count: int
    tab_switches: int
    fraud_count: int
    started_at: str | None = None
    ended_at: str | None = None


class ExamResultsOut(BaseModel):
    exam_title: str
    rows: list[AdminResultRow]


# --- users & accounts ---


class AdminUserOut(BaseModel):
    id: str
    full_name: str
    national_id: str
    phone: str
    role: str
    is_admin: bool = False
    is_blocked: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class PagedUsersOut(BaseModel):
    total: int
    items: list[AdminUserOut]


class AdminUserUpdate(BaseModel):
    full_name: str
    national_id: str
    phone: str


class AdminUserCreate(AdminUserUpdate):
    """Manually create a student account from the admin panel."""



class UserExamAccessOut(BaseModel):
    exam_id: str
    exam_title: str
    granted_at: datetime | None = None


class ExamAccessIn(BaseModel):
    exam_id: str


class UserPaymentItem(BaseModel):
    id: str
    exam_title: str
    amount: int
    status: str
    ref_id: str | None = None
    created_at: datetime | None = None
    verified_at: datetime | None = None


class UserSessionItem(BaseModel):
    id: str
    exam_title: str
    status: str
    score_20: float | None = None
    tab_switches: int = 0
    fraud_count: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None


class UserDetailOut(BaseModel):
    user: AdminUserOut
    payments: list[UserPaymentItem]
    sessions: list[UserSessionItem]
    exam_access: list[UserExamAccessOut]


class RoleUpdateIn(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in ALL_ROLES:
            raise ValueError("نقش نامعتبر است")
        return v


# --- payments ---


class AdminPaymentOut(BaseModel):
    id: str
    user_full_name: str
    user_national_id: str
    exam_title: str
    amount: int
    status: str
    authority: str | None = None
    ref_id: str | None = None
    created_at: datetime | None = None
    verified_at: datetime | None = None


class PagedPaymentsOut(BaseModel):
    total: int
    items: list[AdminPaymentOut]


class RevenueByExam(BaseModel):
    exam_title: str
    count: int
    total_rial: int


class RevenueOut(BaseModel):
    from_date: str | None = None
    to_date: str | None = None
    total_count: int
    total_rial: int
    by_exam: list[RevenueByExam]


# --- proctoring & review ---


class ProctoringRowOut(BaseModel):
    session_id: str
    full_name: str
    national_id: str
    exam_title: str
    remaining_secs: int
    tab_switches: int
    fraud_count: int
    started_at: datetime | None = None


class ReviewQuestionOut(BaseModel):
    order_num: int
    body: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    chosen_opt: str | None = None
    correct_opt: str
    is_correct: bool | None = None
    time_secs: int | None = None
    answered_at: datetime | None = None


class FraudEventOut(BaseModel):
    type: str
    at: str


class SessionReviewOut(BaseModel):
    session_id: str
    full_name: str
    national_id: str
    exam_title: str
    status: str
    score_20: float | None = None
    correct_count: int | None = None
    total_count: int
    tab_switches: int
    # true (uncapped) event counter; fraud_events is a capped timeline for display
    fraud_count: int
    started_at: datetime | None = None
    ended_at: datetime | None = None
    questions: list[ReviewQuestionOut]
    fraud_events: list[FraudEventOut]


# --- reports ---


class DailyPointOut(BaseModel):
    date: str
    value: int


class QuestionAnalyticsOut(BaseModel):
    order_num: int
    body: str
    correct_opt: str
    answered: int
    correct: int
    p_value: float | None = None  # % correct of those who answered
    blank_rate: float | None = None  # % of completed sessions with no answer
    distractors: list[dict]


class FraudRowOut(BaseModel):
    session_id: str
    full_name: str
    national_id: str
    exam_title: str
    status: str
    tab_switches: int
    fraud_count: int
    score_20: float | None = None


class PagedFraudOut(BaseModel):
    total: int
    items: list[FraudRowOut]


# --- system settings (superadmin) ---


class SettingsUpdate(BaseModel):
    # empty string means "fall back to environment variable"
    payment_mode: str = ""
    zarinpal_merchant: str = ""


class SettingsStatusOut(BaseModel):
    payment_mode: str  # stored value ("" = use env)
    effective_mode: str  # local | zarinpal_sandbox | zarinpal_live
    zarinpal_merchant: str  # stored value ("" = use env)
    effective_merchant: str
    merchant_valid: bool
    payments_admin_only: bool


# --- audit ---


class AuditLogOut(BaseModel):
    id: str
    admin_name: str
    action: str
    target_type: str
    target_id: str | None = None
    detail: dict | None = None
    created_at: datetime


class PagedAuditOut(BaseModel):
    total: int
    items: list[AuditLogOut]
