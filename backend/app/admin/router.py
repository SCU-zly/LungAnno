"""Admin API router - 用户管理与批次访问授权（全部端点 require_admin）。"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.dependencies import require_admin
from app.auth.password import hash_password
from app.models.user import User
from app.models.batch import Batch
from app.models.batch_user_access import BatchUserAccess
from app.models.review_result import ReviewResult
from app.models.series import Series

router = APIRouter(prefix="/admin", tags=["admin"])

# 角色白名单（与 users.role 取值一致）
_ALLOWED_ROLES = ("admin", "reviewer")


class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str = "reviewer"


class AccessUpdateRequest(BaseModel):
    user_ids: List[int]


@router.get("/users")
def list_users(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    users = db.query(User).order_by(User.id).all()
    return [
        {"id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at}
        for u in users
    ]


@router.post("/users", status_code=201)
def create_user(
    body: UserCreateRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    if body.role not in _ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail=f"非法角色 {body.role!r}（仅允许 admin/reviewer）")
    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username, "role": user.role}


@router.get("/batches/{batch_id}/access")
def get_batch_access(batch_id: int, db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    user_ids = [
        row.user_id
        for row in db.query(BatchUserAccess).filter(BatchUserAccess.batch_id == batch_id).all()
    ]
    return {"user_ids": sorted(user_ids)}


@router.put("/batches/{batch_id}/access")
def put_batch_access(
    batch_id: int,
    body: AccessUpdateRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    """整体替换批次授权集（先清后插，单事务）。"""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    user_ids = sorted(set(body.user_ids))
    if user_ids:
        found = {u.id for u in db.query(User).filter(User.id.in_(user_ids)).all()}
        invalid = [uid for uid in user_ids if uid not in found]
        if invalid:
            raise HTTPException(status_code=400, detail=f"用户不存在: {invalid}")

    db.query(BatchUserAccess).filter(BatchUserAccess.batch_id == batch_id).delete(synchronize_session=False)
    for uid in user_ids:
        db.add(BatchUserAccess(batch_id=batch_id, user_id=uid))
    db.commit()
    return {"user_ids": user_ids}


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    body: ResetPasswordRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
):
    """管理员重置任意用户密码（用户忘密时的唯一途径——无公开注册/找回）。"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.password_hash = hash_password(body.password)
    db.commit()
    return {"message": "已重置"}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
):
    """删除用户。保护规则：不能删自己（防管理员自锁）；已提交审核记录的用户
    不可删（保留审核留痕）。删除时清理其批次授权与序列认领锁。"""
    if user_id == admin["user_id"]:
        raise HTTPException(status_code=400, detail="不能删除当前登录的管理员账号")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    review_count = db.query(ReviewResult).filter(ReviewResult.reviewer_id == user_id).count()
    if review_count > 0:
        raise HTTPException(status_code=400, detail=f"该用户已提交 {review_count} 条审核记录，为保留审核留痕不可删除")
    db.query(BatchUserAccess).filter(BatchUserAccess.user_id == user_id).delete(synchronize_session=False)
    db.query(Series).filter(Series.reviewer_id == user_id).update(
        {Series.reviewer_id: None, Series.lease_expires_at: None}, synchronize_session=False
    )
    db.delete(user)
    db.commit()
    return {"message": "已删除"}
