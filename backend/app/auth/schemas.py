"""Auth Pydantic schemas."""
from pydantic import BaseModel, Field


class UserLogin(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str | None = None  # 仅 login 响应携带（前端据此后置展示管理员功能）


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)
