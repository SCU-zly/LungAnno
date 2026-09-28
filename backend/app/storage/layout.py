"""Storage layout management."""
import os
import json
from app.config import settings


def ensure_series_dirs(batch_id: int, study_uid: str, series_uid: str) -> dict:
    base = os.path.join(settings.storage_root, str(batch_id), study_uid, series_uid)
    raw_dir = os.path.join(base, "raw")
    preprocessed_dir = os.path.join(base, "preprocessed")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(preprocessed_dir, exist_ok=True)
    return {"raw_path": raw_dir, "preprocessed_path": preprocessed_dir, "base": base}


def series_dirs_from_raw(raw_path: str) -> dict:
    """由已持久化的 raw_path 重建 series 目录布局（与 ensure_series_dirs 同一约定）。

    Worker 侧只能拿到 DB 里的 raw_path，目录布局知识必须收口在本模块——
    此前 preprocess.py 用 raw_path.replace("raw", "") 推导父目录，路径中任何
    "raw" 子串（如存储根目录名）都会导致输出写到错误位置。
    """
    base = os.path.dirname(os.path.normpath(raw_path))
    return {
        "base": base,
        "raw_path": os.path.normpath(raw_path),
        "preprocessed_path": os.path.join(base, "preprocessed"),
    }


def write_meta_json(base_dir: str, meta: dict) -> str:
    meta_path = os.path.join(base_dir, "meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    return meta_path


def read_meta_json(base_dir: str) -> dict:
    with open(os.path.join(base_dir, "meta.json")) as f:
        return json.load(f)
