from pydantic import BaseModel, Field, field_validator


class PaymentStartRequest(BaseModel):
    exam_id: str


class StartRequest(BaseModel):
    exam_id: str


class PaymentStartOut(BaseModel):
    payment_id: str
    redirect_url: str | None  # None in local mode


class PaymentStatusOut(BaseModel):
    status: str
    ref_id: str | None = None
    exam_id: str | None = None


class AnswerIn(BaseModel):
    question_id: str
    chosen_opt: str = Field(min_length=1, max_length=1)

    @field_validator("chosen_opt")
    @classmethod
    def _opt(cls, v: str) -> str:
        if v.upper() not in ("A", "B", "C", "D"):
            raise ValueError("گزینه باید A، B، C یا D باشد")
        return v.upper()


class AnswerBatchIn(BaseModel):
    answers: list[AnswerIn]


class SubmitResult(BaseModel):
    # score out of 20 (employer requirement) plus percent for the UI ring
    score_20: float
    score_percent: float
    correct_count: int
    total_count: int


class StartOut(BaseModel):
    session_id: str
    total_questions: int
    duration_secs: int
    remaining_secs: int | None = None


class SessionInfoOut(BaseModel):
    exam_id: str
    total_questions: int
    duration_secs: int
    remaining_secs: int | None
    status: str  # pending | active | completed
