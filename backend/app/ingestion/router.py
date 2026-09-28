"""Ingestion API router."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, nullslast, select
from starlette.concurrency import run_in_threadpool
from app.config import settings
from app.database import get_db
from app.auth.dependencies import get_current_user, resolve_role, ensure_batch_access
from app.models.batch import Batch
from app.models.series import Series
from app.models.study import Study
from app.models.detection import Detection
from app.models.batch_study import batch_studies
from app.models.patient_clinical import PatientClinical
from app.models.batch_user_access import BatchUserAccess
from app.ingestion.schemas import IngestRequest, IngestResponse
from app.ingestion.service import ingest_batch
from app.worker.queue import enqueue_preprocess

router = APIRouter(prefix="/batches", tags=["ingestion"])


@router.get("")
def list_batches(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    query = db.query(Batch)
    # reviewer 只见授权批次（batch_user_access 有行）；admin 全部
    if resolve_role(db, user) != "admin":
        query = query.join(BatchUserAccess, BatchUserAccess.batch_id == Batch.id).filter(
            BatchUserAccess.user_id == user["user_id"]
        )
    batches = query.order_by(Batch.created_at.desc()).all()
    if not batches:
        return []
    # 两条 GROUP BY 覆盖全部批次：处理状态分布 + 审核完成数（无逐 batch 循环查询）
    proc_rows = (
        db.query(batch_studies.c.batch_id, Series.processing_status, func.count(Series.id))
        .join(Series, Series.study_id == batch_studies.c.study_id)
        .group_by(batch_studies.c.batch_id, Series.processing_status)
        .all()
    )
    reviewed_rows = (
        db.query(batch_studies.c.batch_id, func.count(Series.id))
        .join(Series, Series.study_id == batch_studies.c.study_id)
        .filter(Series.review_status == "reviewed")
        .group_by(batch_studies.c.batch_id)
        .all()
    )
    proc_map: dict = {}
    for batch_id, status, count in proc_rows:
        proc_map.setdefault(batch_id, {})[status] = count
    reviewed_map = dict(reviewed_rows)
    result = []
    for b in batches:
        processing = proc_map.get(b.id, {})
        result.append({
            "batch_id": b.id,
            "batch_name": b.name,
            "series_total": sum(processing.values()),
            "reviewed_count": reviewed_map.get(b.id, 0),
            "processing": processing,
        })
    return result


@router.post("/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest, db: Session = Depends(get_db), _user: dict = Depends(get_current_user)):
    try:
        result = await run_in_threadpool(ingest_batch, db, request.directory_path, request.batch_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 管线接线（ISS-003）：新 series + 可重跑的存量 series 自动入队预处理 → 推理
    enqueue_failed = []
    for series_id in result["to_enqueue"]:
        try:
            await enqueue_preprocess(series_id)
        except Exception:
            enqueue_failed.append(series_id)
    if enqueue_failed:
        raise HTTPException(
            status_code=503,
            detail=f"批次已摄入但任务队列不可用，series {enqueue_failed} 未入队，请稍后重试",
        )

    response = {k: v for k, v in result.items() if k in ("batch_id", "batch_name", "study_count", "series_count", "dicom_count")}
    return IngestResponse(**response)


@router.get("/{batch_id}/patients")
def list_patients(batch_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    """批次内患者列表（含中文名与序列/审核统计），按姓名排序。"""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    ensure_batch_access(db, user, batch_id)

    study_ids = select(batch_studies.c.study_id).where(batch_studies.c.batch_id == batch_id)
    rows = (
        db.query(Study.patient_id, Study.patient_name, Series.review_status)
        .join(Series, Series.study_id == Study.id)
        .filter(Study.id.in_(study_ids))
        .all()
    )
    # 单查询 + Python 侧按 patient_id 聚合（无 N+1）
    agg: dict = {}
    for patient_id, patient_name, review_status in rows:
        if patient_id is None:
            continue
        entry = agg.setdefault(patient_id, {"patient_name": "", "series_total": 0, "reviewed_count": 0})
        if patient_name:
            entry["patient_name"] = patient_name
        entry["series_total"] += 1
        if review_status == "reviewed":
            entry["reviewed_count"] += 1

    # 中文名来自 patient_clinical（ltrim(studies.patient_id,'0') 关联，等价于去前导零后的 display_id）
    display_map = {pid: pid.lstrip("0") for pid in agg}
    clinical_names: dict = {}
    if agg:
        clinical_names = {
            c.patient_id: c.name
            for c in db.query(PatientClinical)
            .filter(PatientClinical.patient_id.in_(set(display_map.values())))
            .all()
            if c.name
        }

    result = []
    for pid, entry in agg.items():
        display_id = display_map[pid]
        result.append({
            "patient_id": pid,  # DB 原样（可能含前导零）
            "display_id": display_id,
            # 中文名优先：patient_clinical → DICOM PatientName（拼音兜底）→ 未知
            "name": clinical_names.get(display_id) or entry["patient_name"] or "未知",
            "series_total": entry["series_total"],
            "reviewed_count": entry["reviewed_count"],
        })
    result.sort(key=lambda r: r["name"])
    return result


@router.get("/{batch_id}/patients/{patient_id}/series")
def list_patient_series(
    batch_id: int,
    patient_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """批次内某患者的序列列表，按 study_date 倒序。patient_id 原样精确匹配 studies.patient_id。"""
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    ensure_batch_access(db, user, batch_id)

    study_ids = select(batch_studies.c.study_id).where(batch_studies.c.batch_id == batch_id)
    rows = (
        db.query(Series, Study.study_date)
        .join(Study, Series.study_id == Study.id)
        .filter(Study.id.in_(study_ids), Study.patient_id == patient_id)
        .order_by(nullslast(Study.study_date.desc()))
        .all()
    )

    det_counts = (
        dict(
            db.query(Detection.series_id, func.count(Detection.id))
            .filter(Detection.series_id.in_([s.id for s, _ in rows]))
            .group_by(Detection.series_id)
            .all()
        )
        if rows
        else {}
    )

    return [
        {
            "series_id": s.id,
            "series_uid": s.series_uid,
            "study_date": study_date or "",
            "series_description": s.series_description or "",
            "slice_thickness": s.slice_thickness,
            "processing_status": s.processing_status,
            "review_status": s.review_status,
            # 与批次详情/审核页同一口径：top_k>0 时截取，0 = 真实全量
            "detection_count": (
                min(det_counts.get(s.id, 0), settings.detection_top_k)
                if settings.detection_top_k > 0
                else det_counts.get(s.id, 0)
            ),
        }
        for s, study_date in rows
    ]
