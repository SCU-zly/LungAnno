"""Series 数据访问解析（ISS-002 IDOR fix 的存续部分）。

读取授权的实际判定在 auth.dependencies.ensure_series_access（批次级授权）。
本依赖只负责把 series 取出来（404 语义统一）。历史上的"租约互斥锁"已随
多人审核语义下线（2026-08-12）——不再按 reviewer/lease 拦截读取。
"""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.dependencies import get_viewer
from app.models.series import Series


def require_series_access(
    series_id: int,
    db: Session = Depends(get_db),
    _user: dict = Depends(get_viewer),
) -> Series:
    """Resolve the series row (404 if absent); 批次级授权由 ensure_series_access 判定。"""
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Series 不存在")
    return series
