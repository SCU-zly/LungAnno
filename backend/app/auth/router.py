"""Auth API router."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.auth.dependencies import get_current_user
from app.auth.schemas import UserLogin, TokenResponse, RefreshRequest, ChangePasswordRequest
from app.auth.password import hash_password, verify_password
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

# 注：不设公开注册端点——账号一律由管理员在 POST /admin/users 创建（批次授权模型的前提）


@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 单点登录：每次登录递增会话版本号，该账号此前签发的令牌全部失效（互踢）
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    access_token, expire = create_access_token(user.id, user.username, user.token_version)
    refresh_token = create_refresh_token(user.id, user.username, user.token_version)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_access_expire_minutes * 60,
        role=user.role,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="无效或过期的刷新令牌")
    # 会话版本校验：刷新令牌签发后若账号又有新登录/登出，则拒绝续期（单点登录）
    row = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not row or payload.get("tv") != row.token_version:
        raise HTTPException(status_code=401, detail="登录态已失效（账号可能在其他设备登录）")
    access_token, expire = create_access_token(row.id, row.username, row.token_version)
    new_refresh = create_refresh_token(row.id, row.username, row.token_version)
    return TokenResponse(access_token=access_token, refresh_token=new_refresh, expires_in=settings.jwt_access_expire_minutes * 60)


@router.post("/logout")
def logout(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """服务端登出：递增会话版本号，吊销该用户全部会话（含本设备与其他设备）。"""
    row = db.query(User).filter(User.id == user["user_id"]).first()
    if row:
        row.token_version = (row.token_version or 0) + 1
        db.commit()
    return {"message": "已退出登录"}


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """修改自己的密码（全账号可用）。改密后前端强制重新登录。"""
    row = db.query(User).filter(User.id == user["user_id"]).first()
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(body.old_password, row.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    if body.old_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    row.password_hash = hash_password(body.new_password)
    db.commit()
    return {"message": "密码已修改，请重新登录"}
