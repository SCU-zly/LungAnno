"""JWT token creation and verification."""
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from app.config import settings


def create_access_token(user_id: int, username: str, token_version: int = 0) -> tuple[str, datetime]:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_expire_minutes)
    payload = {"sub": str(user_id), "username": username, "exp": expire, "type": "access", "tv": token_version}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expire


def create_refresh_token(user_id: int, username: str, token_version: int = 0) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_expire_days)
    payload = {"sub": str(user_id), "username": username, "exp": expire, "type": "refresh", "tv": token_version}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
