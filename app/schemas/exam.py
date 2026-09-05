from pydantic import BaseModel, Field, field_validator


class QuestionIn(BaseModel):
    """Question payload for admin exam create/update."""

    body: str = Field(min_length=1)
    option_a: str = Field(min_length=1)
    option_b: str = Field(min_length=1)
    option_c: str = Field(min_length=1)
    option_d: str = Field(min_length=1)
    correct_opt: str = Field(min_length=1, max_length=1)

    @field_validator("correct_opt")
    @classmethod
    def _opt(cls, v: str) -> str:
        v = v.upper()
        if v not in ("A", "B", "C", "D"):
            raise ValueError("پاسخ صحیح باید A، B، C یا D باشد")
        return v


class ExamUpsert(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = ""
    duration_secs: int = Field(gt=0, le=86400)
    price: int = Field(ge=0)  # Rial
    is_active: bool = True
    # an exam without questions is unusable; reject it at the API layer too
    questions: list[QuestionIn] = Field(min_length=1)


class QuestionOut(BaseModel):
    id: str
    body: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    order_num: int

    model_config = {"from_attributes": True}


class ExamOut(BaseModel):
    id: str
    title: str
    description: str
    duration_secs: int
    price: int
    question_count: int

    model_config = {"from_attributes": True}


class ExamDetailOut(BaseModel):
    id: str
    title: str
    description: str
    duration_secs: int
    price: int
    question_count: int
    # only a preview question; the full question bank is never shipped pre-payment
    sample_question: QuestionOut | None = None

    model_config = {"from_attributes": True}
