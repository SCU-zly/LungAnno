"""Review service."""
from sqlalchemy.orm import Session

from app.models.series import Series
from app.models.detection import Detection
from app.models.review_result import ReviewResult


def claim_series(db: Session, series_id: int, reviewer_id: int) -> Series | None:
    """进入审核（去锁化，2026-08-12 起）。

    多人审核语义：同一序列允许多人各自审核与提交，认领不再互斥——任意
    review_status 均视为可进入，返回 series；series 不存在返回 None。
    reviewer_id/lease 列保留但不再承担互斥语义（历史字段，留待后续清理）。
    """
    return db.query(Series).filter(Series.id == series_id).first()


def submit_review(db: Session, series_id: int, reviewer_id: int, verdicts: list) -> dict:
    """提交审核（多人审核 + 重审整组替换）。

    - 多人：同一序列允许多人各自提交，互不影响；review_status 任意状态均可提交。
    - 重审：先删除本人在本序列的旧结论，再按本次 payload 重写（他人的行不动）。
    - 未标记候选默认记为 rejected（提交即对所有候选下结论，不留语义空白）。
    """
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        return {"error": "Series not found"}

    det_ids = [row[0] for row in db.query(Detection.id).filter(Detection.series_id == series_id).all()]

    # 重审语义：整组替换本人的旧结论
    db.query(ReviewResult).filter(
        ReviewResult.reviewer_id == reviewer_id,
        ReviewResult.detection_id.in_(det_ids),
    ).delete(synchronize_session=False)

    saved = 0
    counts = {"accepted": 0, "rejected": 0, "uncertain": 0, "defaulted": 0}
    marked_det_ids = set()
    for v in verdicts:
        det = db.query(Detection).filter(Detection.id == v["detection_id"], Detection.series_id == series_id).first()
        if det:
            rr = ReviewResult(detection_id=det.id, reviewer_id=reviewer_id, verdict=v["verdict"])
            db.add(rr)
            marked_det_ids.add(det.id)
            saved += 1
            if v["verdict"] in counts:
                counts[v["verdict"]] += 1

    # 未标记的候选默认记为拒绝
    for det_id in sorted(set(det_ids) - marked_det_ids):
        db.add(ReviewResult(detection_id=det_id, reviewer_id=reviewer_id, verdict="rejected"))
        counts["rejected"] += 1
        counts["defaulted"] += 1
        saved += 1

    series.review_status = "reviewed"
    series.lease_expires_at = None
    db.commit()
    return {"saved": saved, "status": "reviewed", "counts": counts}


def get_my_review(db: Session, series_id: int, reviewer_id: int) -> dict:
    """当前用户在本序列的既有审核结论（重审回显用）。"""
    det_ids = db.query(Detection.id).filter(Detection.series_id == series_id)
    rows = (
        db.query(ReviewResult)
        .filter(ReviewResult.reviewer_id == reviewer_id, ReviewResult.detection_id.in_(det_ids))
        .all()
    )
    return {
        "submitted": len(rows) > 0,
        "verdicts": {str(r.detection_id): r.verdict for r in rows},
    }


def revert_review(db: Session, series_id: int) -> dict | None:
    """管理员回退已提交的审核结果（reviewed → not_reviewed）。

    事务内删除该 series 的全部 review_results（detections 保留），清除
    reviewer/租约，使 series 可被重新认领审核。返回 None 表示 series 不存在。
    """
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        return None
    if series.review_status != "reviewed":
        return {"error": f"当前状态 {series.review_status} 不可回退（仅 reviewed 可回退）"}

    det_ids = db.query(Detection.id).filter(Detection.series_id == series_id)
    deleted = db.query(ReviewResult).filter(ReviewResult.detection_id.in_(det_ids)).delete(synchronize_session=False)
    series.review_status = "not_reviewed"
    series.reviewer_id = None
    series.lease_expires_at = None
    db.commit()
    return {"reverted": True, "deleted_results": deleted, "review_status": series.review_status}
