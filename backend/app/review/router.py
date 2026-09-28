"""Review API router."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth.dependencies import get_current_user, require_admin, ensure_series_access
from app.review.schemas import SubmitReviewRequest
from app.review.service import claim_series, submit_review, revert_review, get_my_review

router = APIRouter(prefix="/series", tags=["review"])


@router.post("/{series_id}/claim")
def claim(series_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """进入审核（多人审核语义，不再互斥认领）：任意状态均可进入。"""
    ensure_series_access(db, user, series_id)
    series = claim_series(db, series_id, user["user_id"])
    if series is None:
        raise HTTPException(status_code=404, detail="Series 不存在")
    return {"message": "进入审核", "series_id": series_id, "review_status": series.review_status}


@router.get("/{series_id}/my-review")
def my_review(series_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """当前用户在本序列的既有审核结论（重审回显）。"""
    ensure_series_access(db, user, series_id)
    return get_my_review(db, series_id, user["user_id"])


@router.post("/{series_id}/submit-review")
def submit(series_id: int, body: SubmitReviewRequest, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    ensure_series_access(db, user, series_id)
    result = submit_review(db, series_id, user["user_id"], [v.model_dump() for v in body.verdicts])
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/{series_id}/revert-review")
def revert(series_id: int, db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    """管理员回退已提交的审核结果（仅 reviewed 状态可回退）。"""
    ensure_series_access(db, _admin, series_id)
    result = revert_review(db, series_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Series 不存在")
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result
