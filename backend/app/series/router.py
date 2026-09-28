"""Series API router."""
import json
import os
import struct
import subprocess
import sys
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from app.config import settings
from app.database import get_db
from app.auth.dependencies import get_current_user, get_viewer, ensure_batch_access, ensure_series_access
from app.models.series import Series
from app.models.detection import Detection
from app.models.batch import Batch
from app.models.batch_study import batch_studies
from app.models.study import Study
from app.models.patient_clinical import PatientClinical
from app.series.schemas import (
    SeriesStatusResponse,
    BatchDetailResponse,
    SeriesListItem,
    DetectionItem,
)
from app.pipeline.orchestrator import get_pipeline_status
from app.series.dependencies import require_series_access

router = APIRouter(prefix="/series", tags=["series"])

_HTJ2K_MAGIC = b"CTJ2K001"


def _patient_display_name(db: Session, study: Study | None) -> str:
    """患者展示名：patient_clinical 中文名（按去前导零住院号关联）→ DICOM 拼音 → 未知。"""
    if study and study.patient_id:
        clinical = (
            db.query(PatientClinical)
            .filter(PatientClinical.patient_id == study.patient_id.lstrip("0"))
            .first()
        )
        if clinical and clinical.name:
            return clinical.name
    if study and study.patient_name:
        return study.patient_name
    return "未知"


def _htj2k_bundle_path(series: Series) -> str | None:
    """返回该 series 的 .htj2k bundle 路径（含存储根包含校验）；无 volume 返回 None。"""
    if not series.preprocessed_path or not os.path.isfile(series.preprocessed_path):
        return None
    storage_root = os.path.realpath(settings.storage_root)
    volume_path = os.path.realpath(series.preprocessed_path)
    if not volume_path.startswith(storage_root + os.sep):
        return None
    return volume_path[:-7] + ".htj2k" if volume_path.endswith(".nii.gz") else volume_path + ".htj2k"


def _ensure_htj2k_bundle(series: Series) -> str:
    """确保 bundle 存在（缺失时现场编码，一次性 ~2-5s），返回路径。"""
    bundle_path = _htj2k_bundle_path(series)
    if bundle_path is None:
        raise HTTPException(status_code=404, detail="Series 尚未完成预处理")
    if not os.path.isfile(bundle_path):
        encoder = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "..", "deploy", "htj2k", "encode_volume.py",
        )
        encoder = os.path.normpath(encoder)
        try:
            subprocess.run(
                [sys.executable, encoder, series.preprocessed_path, "-q", str(settings.htj2k_qstep)],
                check=True, capture_output=True, timeout=600,
            )
        except Exception:
            raise HTTPException(status_code=503, detail="HTJ2K 编码失败，请稍后重试")
    return bundle_path


def _read_htj2k_header(bundle_path: str) -> tuple[dict, int]:
    """读 bundle 头，返回 (header dict, 数据区起点偏移)。"""
    with open(bundle_path, "rb") as f:
        magic = f.read(8)
        if magic != _HTJ2K_MAGIC:
            raise HTTPException(status_code=500, detail="HTJ2K bundle 损坏")
        header_len = struct.unpack("<I", f.read(4))[0]
        header = json.loads(f.read(header_len))
    return header, 12 + header_len


@router.get("/{series_id}/detections", response_model=List[DetectionItem])
def list_detections(
    series: Series = Depends(require_series_access),
    db: Session = Depends(get_db),
    user: dict = Depends(get_viewer),
):
    ensure_series_access(db, user, series.id)
    series_id = series.id
    # 候选按 score 只回前 K 个（DETECTION_TOP_K 可配；0 = 全部；DB 保留全量，读时过滤）
    query = (
        db.query(Detection)
        .filter(Detection.series_id == series_id)
        .order_by(Detection.score.desc())
    )
    if settings.detection_top_k > 0:
        query = query.limit(settings.detection_top_k)
    dets = query.all()
    return [
        DetectionItem(
            id=d.id, score=d.score, slice_index=d.slice_index,
            box_x=d.box_x, box_y=d.box_y, box_z=d.box_z,
            box_w=d.box_w, box_h=d.box_h, box_d=d.box_d,
            voxel_x=d.voxel_x, voxel_y=d.voxel_y, voxel_z=d.voxel_z,
            voxel_w=d.voxel_w, voxel_h=d.voxel_h, voxel_d=d.voxel_d,
        )
        for d in dets
    ]


@router.get("/{series_id}/volume")
def get_volume(
    request: Request,
    series: Series = Depends(require_series_access),
    db: Session = Depends(get_db),
    user: dict = Depends(get_viewer),
    format: str = "auto",
):
    """Serve the preprocessed NIfTI volume for Cornerstone3D rendering.

    Detections are stored in this volume's voxel grid, so rendering it guarantees
    exact bbox overlay alignment (world mm = voxel * spacing, identity affine).

    传输优化：客户端接受 gzip 时直接把 .nii.gz 以 Content-Encoding: gzip 发出，
    浏览器原生流式 gunzip（替代前端 JS 软解压，大体积下省去秒级主线程阻塞）；
    不接受 gzip 时优先发同名未压缩 .nii；都没有则维持原始 .nii.gz 行为
    （前端 loader 内 JS 解压兜底）。
    format=htj2k 时供 HTJ2K bundle（体积再小 3-9 倍，前端 wasm 解码；无产物则 404 由前端回退）。
    """
    ensure_series_access(db, user, series.id)
    if not series.preprocessed_path or not os.path.isfile(series.preprocessed_path):
        raise HTTPException(status_code=404, detail="Series 尚未完成预处理，无 volume 可渲染")
    # Path containment guard: preprocessed_path is derived from untrusted DICOM UIDs (SEC-003)
    storage_root = os.path.realpath(settings.storage_root)
    volume_path = os.path.realpath(series.preprocessed_path)
    if not volume_path.startswith(storage_root + os.sep):
        raise HTTPException(status_code=404, detail="Volume 路径无效")

    if format == "htj2k":
        bundle_path = volume_path[:-7] + ".htj2k" if volume_path.endswith(".nii.gz") else volume_path + ".htj2k"
        if os.path.isfile(bundle_path):
            return FileResponse(bundle_path, media_type="application/octet-stream")
        raise HTTPException(status_code=404, detail="无 HTJ2K 产物")

    accepts_gzip = "gzip" in request.headers.get("accept-encoding", "")
    if accepts_gzip and volume_path.endswith(".gz"):
        return FileResponse(
            volume_path,
            media_type="application/octet-stream",
            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
        )
    raw_path = volume_path[:-3] if volume_path.endswith(".gz") else volume_path
    if os.path.isfile(raw_path):
        return FileResponse(raw_path, media_type="application/octet-stream")
    return FileResponse(volume_path, media_type="application/gzip", filename=os.path.basename(volume_path))


@router.get("/{series_id}/volume/info")
def get_volume_info(
    series: Series = Depends(require_series_access),
    db: Session = Depends(get_db),
    user: dict = Depends(get_viewer),
):
    """按需取片模式的体数据信息（HTJ2K bundle 头：shape/spacing/voi/count）。"""
    ensure_series_access(db, user, series.id)
    bundle_path = _ensure_htj2k_bundle(series)
    header, _ = _read_htj2k_header(bundle_path)
    return header


@router.get("/{series_id}/slice")
def get_slice(
    series: Series = Depends(require_series_access),
    db: Session = Depends(get_db),
    user: dict = Depends(get_viewer),
    z: int = 0,
):
    """按需取片：返回第 z 层的 HTJ2K 码流（j2c bytes，~30-200KB，前端 wasm 解码）。"""
    ensure_series_access(db, user, series.id)
    bundle_path = _ensure_htj2k_bundle(series)
    header, data_start = _read_htj2k_header(bundle_path)
    count = header["count"]
    if z < 0 or z >= count:
        raise HTTPException(status_code=400, detail=f"z 越界（0..{count - 1}）")
    offset = data_start + header["offsets"][z]
    size = header["sizes"][z]
    with open(bundle_path, "rb") as f:
        f.seek(offset)
        payload = f.read(size)
    return Response(content=payload, media_type="application/octet-stream")


@router.get("/{series_id}/info")
def get_series_info(
    series: Series = Depends(require_series_access),
    db: Session = Depends(get_db),
    user: dict = Depends(get_viewer),
):
    """序列基本信息（含患者标识与 DICOM 层厚/描述，供列表与审核页头部展示）。"""
    ensure_series_access(db, user, series.id)
    study = db.query(Study).filter(Study.id == series.study_id).first()
    # 审核页「返回列表」需要定位所属批次：取该 study 关联的第一个批次（无则 null）
    batch_id = None
    if study:
        row = db.execute(
            select(batch_studies.c.batch_id).where(batch_studies.c.study_id == study.id).limit(1)
        ).first()
        batch_id = row[0] if row else None
    return {
        "series_id": series.id,
        "series_uid": series.series_uid,
        "patient_id": study.patient_id if study else None,
        "patient_name": _patient_display_name(db, study),
        "study_date": (study.study_date or "") if study else "",
        "slice_thickness": series.slice_thickness,
        "series_description": series.series_description or "",
        "processing_status": series.processing_status,
        "review_status": series.review_status,
        "batch_id": batch_id,
    }


@router.get("/{series_id}/patient-metadata")
def get_patient_metadata(
    series: Series = Depends(require_series_access),
    db: Session = Depends(get_db),
    user: dict = Depends(get_viewer),
):
    """患者临床元数据（patient_clinical 按去前导零住院号关联）；查不到返回空结构，不报错。"""
    ensure_series_access(db, user, series.id)
    empty = {"patient_id": None, "name": None, "fields": {}}
    study = db.query(Study).filter(Study.id == series.study_id).first()
    if not study or not study.patient_id:
        return empty
    clinical = (
        db.query(PatientClinical)
        .filter(PatientClinical.patient_id == study.patient_id.lstrip("0"))
        .first()
    )
    if not clinical:
        return empty
    return {
        "patient_id": study.patient_id,  # DB 原样（与 /info、患者列表口径一致）
        "name": clinical.name,
        "fields": clinical.payload or {},
    }


@router.get("/{series_id}/status", response_model=SeriesStatusResponse)
def series_status(
    series: Series = Depends(require_series_access),
    db: Session = Depends(get_db),
):
    status = get_pipeline_status(series.id)
    if not status:
        raise HTTPException(status_code=404, detail="Series不存在")
    return SeriesStatusResponse(**status)


@router.post("/{series_id}/retry")
async def retry_processing(
    series: Series = Depends(require_series_access),
    db: Session = Depends(get_db),
):
    """重新入队处理流水线——processing_status 状态机的恢复路径。

    允许重试的状态：registered（入队失败残留）、error、以及卡死的
    preprocessing/inferring（worker 崩溃或 job_timeout 后无自愈）。
    任务幂等（预处理重写产物、推理单事务 delete+insert），重复入队安全。
    """
    RETRYABLE = {"registered", "error", "preprocessing", "inferring"}
    if series.processing_status not in RETRYABLE:
        raise HTTPException(status_code=409, detail=f"当前状态 {series.processing_status} 无需重试")

    from app.worker.queue import enqueue_inference, enqueue_preprocess

    # 预处理产物齐全则从推理阶段重试，否则从头（预处理）重试
    has_artifacts = bool(
        series.preprocessed_path
        and series.meta_path
        and os.path.isfile(series.preprocessed_path)
        and os.path.isfile(series.meta_path)
    )
    try:
        if has_artifacts:
            await enqueue_inference(series.id)
            series.processing_status = "preprocessed"  # 等待推理任务接管
        else:
            await enqueue_preprocess(series.id)
            series.processing_status = "registered"  # 等待预处理任务接管
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail="任务队列不可用，请稍后重试")
    db.commit()
    return {"message": "已重新入队", "series_id": series.id, "processing_status": series.processing_status}


@router.get("/batches/{batch_id}", response_model=BatchDetailResponse)
def batch_detail(batch_id: int, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    ensure_batch_access(db, user, batch_id)

    study_ids = db.query(batch_studies.c.study_id).filter(batch_studies.c.batch_id == batch_id).subquery()
    series_rows = db.query(Series).filter(Series.study_id.in_(study_ids)).all()

    # 单条 GROUP BY 查询消除 N+1（ISS-008）
    det_counts = dict(
        db.query(Detection.series_id, func.count(Detection.id))
        .filter(Detection.series_id.in_([s.id for s in series_rows]))
        .group_by(Detection.series_id)
        .all()
    )

    series_list = [
        SeriesListItem(
            series_id=s.id,
            series_uid=s.series_uid,
            review_status=s.review_status,
            processing_status=s.processing_status,
            # 与审核页所见一致：候选按 score 只展示前 K 个（0 = 真实全量；DB 保留全量）
            detection_count=(
                min(det_counts.get(s.id, 0), settings.detection_top_k)
                if settings.detection_top_k > 0
                else det_counts.get(s.id, 0)
            ),
        )
        for s in series_rows
    ]

    return BatchDetailResponse(batch_id=batch.id, batch_name=batch.name, series_list=series_list)
