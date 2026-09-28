"""Auth dependencies for FastAPI."""
from fastapi import Depends, HTTPException, Query, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.auth.jwt import decode_token
from app.database import get_db
from app.models.user import User
from app.models.series import Series
from app.models.batch_study import batch_studies
from app.models.batch_user_access import BatchUserAccess

security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


def _user_from_token(raw_token: str, db: Session) -> dict:
    """Validate an access token and build the user dict. Raises 401 on any problem.

    单点登录：payload 的 tv（token_version）必须与 users.token_version 一致，
    否则说明该会话已被更新的登录/登出吊销（同账号互踢）。"""
    payload = decode_token(raw_token)
    if payload is None or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或过期的令牌")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        # signed token without a usable sub claim — treat as invalid, not 500 (CORR-010)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或过期的令牌")
    row = db.query(User.token_version).filter(User.id == user_id).first()
    if row is None or payload.get("tv") != row[0]:
        # 用户不存在，或令牌签发后又有新登录/登出（含无 tv 的历史令牌）
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录态已失效（账号可能在其他设备登录）")
    return {"user_id": user_id, "username": payload.get("username")}


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)) -> dict:
    return _user_from_token(credentials.credentials, db)


def get_viewer(
    credentials: HTTPAuthorizationCredentials | None = Security(security_optional),
    token: str | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """Accept auth via Bearer header or `?token=` query param.

    The query-param path exists for XHR-based image loaders (Cornerstone nifti
    loader) that cannot attach an Authorization header.
    """
    raw = credentials.credentials if credentials else token
    if raw is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或过期的令牌")
    return _user_from_token(raw, db)


def require_admin(user: dict = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """管理员角色校验（users.role == 'admin'），用于回退审核等管理操作。"""
    row = db.query(User).filter(User.id == user["user_id"]).first()
    if not row or row.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def resolve_role(db: Session, user: dict) -> str:
    """一次查库取角色（token 里的 user dict 只有 user_id/username，角色以 users 表为准）。"""
    row = db.query(User.role).filter(User.id == user["user_id"]).first()
    return row[0] if row else "reviewer"


def ensure_batch_access(db: Session, user: dict, batch_id: int) -> None:
    """批次可见性校验（普通函数，端点里显式调用）：admin 直通；
    reviewer 须存在 batch_user_access 授权行，否则 403。"""
    if resolve_role(db, user) == "admin":
        return
    granted = (
        db.query(BatchUserAccess)
        .filter(
            BatchUserAccess.batch_id == batch_id,
            BatchUserAccess.user_id == user["user_id"],
        )
        .first()
    )
    if not granted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该批次")


def ensure_series_access(db: Session, user: dict, series_id: int) -> None:
    """序列可见性校验（普通函数，端点里显式调用）：admin 直通；reviewer 沿
    series→study→batch_studies 链查 batch_user_access 授权行，无则 403；序列不存在 404。"""
    series = db.query(Series.id, Series.study_id).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Series 不存在")
    if resolve_role(db, user) == "admin":
        return
    granted = (
        db.query(BatchUserAccess)
        .join(batch_studies, batch_studies.c.batch_id == BatchUserAccess.batch_id)
        .filter(
            batch_studies.c.study_id == series.study_id,
            BatchUserAccess.user_id == user["user_id"],
        )
        .first()
    )
    if not granted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该序列")
