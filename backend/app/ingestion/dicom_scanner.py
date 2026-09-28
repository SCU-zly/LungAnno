"""DICOM directory scanner."""
import os
import re
import pydicom
from collections import defaultdict

# DICOM UI (Unique Identifier) 字符集白名单：仅数字与点，最长 64（ISS-007）
_UI_PATTERN = re.compile(r"^[0-9.]{1,64}$")


def validate_uid(value: str, label: str) -> str:
    """Reject UIDs outside the DICOM UI charset — they end up in filesystem paths."""
    if not _UI_PATTERN.match(value):
        raise ValueError(f"非法 {label}: {value!r}（UID 仅允许数字与点，最长 64 字符）")
    return value


def _series_entry() -> dict:
    """序列级聚合结构：文件清单 + 厚度/描述/实例数（供摄入过滤与登记）。"""
    return {"files": [], "slice_thickness": None, "description": "", "count": 0}


def scan_dicom_directory(directory_path: str) -> dict:
    """Scan a directory recursively for DICOM files, group by StudyInstanceUID/SeriesInstanceUID.

    series 值为 dict：{"files": [...], "slice_thickness": float|None,
    "description": str, "count": int}；study 层附带 study_date / patient_name。
    """
    if not os.path.isdir(directory_path):
        raise ValueError(f"目录不存在或不可访问: {directory_path}")

    studies = defaultdict(
        lambda: {
            "patient_id": None,
            "patient_name": "",
            "study_date": "",
            "series": defaultdict(_series_entry),
        }
    )
    dicom_count = 0

    for root, _, files in os.walk(directory_path):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                ds = pydicom.dcmread(fpath, stop_before_pixels=True)
                study_uid = validate_uid(str(ds.StudyInstanceUID), "StudyInstanceUID")
                series_uid = validate_uid(str(ds.SeriesInstanceUID), "SeriesInstanceUID")
                patient_id = str(getattr(ds, "PatientID", "unknown"))[:128]

                study_entry = studies[study_uid]
                study_entry["patient_id"] = patient_id
                # PatientName 原样保留（多为拼音，作中文名的展示兜底）；StudyDate 为 YYYYMMDD 字符串
                study_entry["patient_name"] = str(getattr(ds, "PatientName", "") or "")[:128]
                study_entry["study_date"] = str(getattr(ds, "StudyDate", "") or "")[:8]

                series_entry = study_entry["series"][series_uid]
                series_entry["files"].append(fpath)
                series_entry["count"] += 1
                if series_entry["slice_thickness"] is None:
                    # SliceThickness 缺失或为 0 一律记 None（无法判定薄层，过滤时按跳过处理）
                    try:
                        thickness = float(getattr(ds, "SliceThickness", 0) or 0)
                    except (TypeError, ValueError):
                        thickness = 0.0
                    series_entry["slice_thickness"] = thickness if thickness > 0 else None
                if not series_entry["description"]:
                    series_entry["description"] = str(getattr(ds, "SeriesDescription", "") or "")[:128]
                dicom_count += 1
            except ValueError:
                raise  # UID 白名单违规 → 显式拒绝整批摄入
            except Exception:
                continue

    if dicom_count == 0:
        raise ValueError(f"目录中未找到有效DICOM文件: {directory_path}")

    return {"studies": dict(studies), "dicom_count": dicom_count}
