from pydantic import BaseModel, Field, field_validator


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    national_id: str = Field(min_length=10, max_length=10)
    phone: str = Field(min_length=11, max_length=11)

    @field_validator("national_id")
    @classmethod
    def _nid(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("کد ملی باید فقط رقم باشد")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if not v.isdigit():
            raise ValueError("شماره موبایل باید فقط رقم باشد")
        if not v.startswith("09"):
            raise ValueError("شماره موبایل نامعتبر است")
        return v


class LoginRequest(BaseModel):
    national_id: str = Field(min_length=10, max_length=10)
    phone: str = Field(min_length=11, max_length=11)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    full_name: str
    national_id: str
    phone: str
    role: str = "user"
    is_admin: bool = False
    is_blocked: bool = False

    model_config = {"from_attributes": True}
