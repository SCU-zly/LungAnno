"""Ingestion service - orchestrates DICOM scanning and registration."""
import os
import shutil
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.batch import Batch
from app.models.study import Study
from app.models.series import Series
from app.models.batch_study import batch_studies
from app.ingestion.dicom_scanner import scan_dicom_directory
from app.storage.layout import ensure_series_dirs

# 已存在序列允许重置重跑的状态：error（上次处理失败）/ registered（入队失败残留）；
# 其余状态（preprocessing/inferring/preprocessed/detected）不重复入队，避免与在途任务竞争
_REQUEUEABLE_STATUS = ("error", "registered")


def create_batch(db: Session, name: str, description: str = None) -> Batch:
    """创建批次（仅 flush，提交由随后的 ingest_into_batch 统一完成）。"""
    batch = Batch(name=name, description=description)
    db.add(batch)
    db.flush()
    return batch


def ingest_into_batch(
    db: Session,
    batch: Batch,
    directory_path: str,
    slice_thickness_max: float = None,
    min_instances: int = 50,
    only_series_uids: set = None,
    link_mode: str = "copy",
) -> dict:
    """扫描目录并把序列注册进既有批次。

    过滤：slice_thickness_max 提供时启用层厚/实例数过滤；only_series_uids 提供时
    只注册清单内 series_uid（此时层厚过滤不再适用——清单即权威筛选结果），
    其余记入返回值的 skipped_series（含原因）。已存在序列（按 series_uid）
    处于 error/registered 状态时重置为 registered 并放入 to_enqueue 以便重跑。
    link_mode="symlink" 时 raw 目录内用符号链接替代实体拷贝（跨盘点名场景省空间）。
    """
    scan_result = scan_dicom_directory(directory_path)

    study_count = 0
    series_count = 0
    series_ids = []
    to_enqueue = []
    skipped_series = []

    for study_uid, study_data in scan_result["studies"].items():
        # 先按过滤条件筛出本次要注册的序列，全被过滤的 study 不进批次
        eligible = []
        for series_uid, sdata in study_data["series"].items():
            if only_series_uids is not None:
                # 清单模式：非清单序列静默略过；清单序列不再做层厚过滤（清单即权威筛选结果）
                if series_uid not in only_series_uids:
                    continue
            else:
                reason = _skip_reason(sdata, slice_thickness_max, min_instances)
                if reason:
                    skipped_series.append({"series_uid": series_uid, "reason": reason})
                    continue
            eligible.append((series_uid, sdata))
        if not eligible:
            continue

        study = db.query(Study).filter(Study.study_uid == study_uid).first()
        if not study:
            study = Study(
                study_uid=study_uid,
                patient_id=study_data["patient_id"],
                patient_name=study_data["patient_name"] or None,
                study_date=study_data["study_date"] or None,
            )
            db.add(study)
            db.flush()
            study_count += 1
        else:
            # 既有 study 用最新扫描值补全（空值不覆盖已有数据）
            if study_data["patient_name"]:
                study.patient_name = study_data["patient_name"]
            if study_data["study_date"]:
                study.study_date = study_data["study_date"]

        # 幂等关联（PG 兼容：避免 SQLite 方言 INSERT OR IGNORE）
        linked = db.execute(
            select(batch_studies).where(
                batch_studies.c.batch_id == batch.id,
                batch_studies.c.study_id == study.id,
            )
        ).first()
        if not linked:
            db.execute(batch_studies.insert().values(batch_id=batch.id, study_id=study.id))

        for series_uid, sdata in eligible:
            existing = db.query(Series).filter(Series.series_uid == series_uid).first()
            if existing:
                if existing.processing_status in _REQUEUEABLE_STATUS:
                    existing.processing_status = "registered"
                    to_enqueue.append(existing.id)
                continue

            dirs = ensure_series_dirs(batch.id, study_uid, series_uid)
            for fpath in sdata["files"]:
                if link_mode == "symlink":
                    dest = os.path.join(dirs["raw_path"], os.path.basename(fpath))
                    if not os.path.lexists(dest):
                        os.symlink(os.path.abspath(fpath), dest)
                else:
                    shutil.copy2(fpath, dirs["raw_path"])

            series = Series(
                series_uid=series_uid,
                study_id=study.id,
                raw_path=dirs["raw_path"],
                slice_thickness=sdata["slice_thickness"],
                series_description=sdata["description"] or None,
                processing_status="registered",
            )
            db.add(series)
            db.flush()
            series_ids.append(series.id)
            to_enqueue.append(series.id)
            series_count += 1

    db.commit()
    return {
        "series_ids": series_ids,
        "to_enqueue": to_enqueue,
        "skipped_series": skipped_series,
        "study_count": study_count,
        "series_count": series_count,
        "dicom_count": scan_result["dicom_count"],
    }


def _skip_reason(sdata: dict, slice_thickness_max: float, min_instances: int) -> str | None:
    """返回该序列被跳过的原因；不需要跳过（或未启用过滤）返回 None。"""
    if slice_thickness_max is None:
        return None
    thickness = sdata["slice_thickness"]
    if thickness is None:
        return "层厚缺失"
    if thickness > slice_thickness_max:
        return f"层厚 {thickness}mm > {slice_thickness_max}mm"
    if sdata["count"] < min_instances:
        return f"实例数 {sdata['count']} < {min_instances}"
    return None


def ingest_batch(db: Session, directory_path: str, batch_name: str) -> dict:
    """建批次 + 摄入（保持原签名兼容，默认不做层厚/实例数过滤）。"""
    batch = create_batch(db, batch_name)
    result = ingest_into_batch(db, batch, directory_path)
    result["batch_id"] = batch.id
    result["batch_name"] = batch_name
    return result
