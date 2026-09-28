"""DICOM preprocessing worker task."""
import asyncio
import logging
import os
import sys

import numpy as np
import pydicom
import SimpleITK as sitk

from app.config import settings
from app.storage.layout import series_dirs_from_raw, write_meta_json

logger = logging.getLogger(__name__)


async def preprocess_series(ctx: dict, series_id: int):
    """Arq task: preprocess a DICOM series into NIfTI + meta.json.

    Arq requires coroutine tasks; the body runs in a worker thread (ISS-011),
    then inference is enqueued on the event loop.
    """
    result = await asyncio.to_thread(_preprocess_sync, series_id)
    if result.get("status") == "ok":
        from app.worker.queue import enqueue_inference
        await enqueue_inference(series_id)
    return result


def _read_slices(dcm_files: list) -> list:
    """Read DICOM slices together with the geometry tags needed for sorting/spacing."""
    slices = []
    for fp in dcm_files:
        try:
            ds = pydicom.dcmread(fp)
            slope = float(getattr(ds, "RescaleSlope", 1))
            intercept = float(getattr(ds, "RescaleIntercept", 0))
            img = ds.pixel_array.astype(np.float32)
            img = slope * img + intercept  # Convert to HU
            if getattr(ds, "PhotometricInterpretation", "") == "MONOCHROME1":
                img = -img  # Invert MONOCHROME1
            ipp = getattr(ds, "ImagePositionPatient", None)
            ps = getattr(ds, "PixelSpacing", None)
            thickness = getattr(ds, "SliceThickness", None)
            slices.append({
                "img": img,
                "position": [float(v) for v in ipp] if ipp is not None else None,
                "instance": getattr(ds, "InstanceNumber", None),
                # DICOM PixelSpacing = [row spacing (y), column spacing (x)]
                "pixel_spacing": [float(v) for v in ps] if ps is not None else None,
                "slice_thickness": float(thickness) if thickness else None,
            })
        except Exception:
            continue
    return slices


def _scan_axis(positions: np.ndarray) -> int:
    """Dominant-variation axis of ImagePositionPatient (robust to scan orientation)."""
    spans = positions.max(axis=0) - positions.min(axis=0)
    return int(np.argmax(spans))


def _sort_slices(slices: list) -> None:
    """Sort slices along the scan axis (in-place).

    Primary key: ImagePositionPatient projected on the scan axis; fallback
    InstanceNumber; last resort the incoming filename order. Sorting by
    filename alone breaks on PACS exports whose names don't follow
    acquisition order.
    """
    if len(slices) < 2:
        return
    if all(s["position"] is not None for s in slices):
        axis = _scan_axis(np.array([s["position"] for s in slices]))
        slices.sort(key=lambda s: s["position"][axis])
    elif all(s["instance"] is not None for s in slices):
        slices.sort(key=lambda s: int(s["instance"]))


def _original_spacing(slices: list) -> tuple:
    """Derive (x, y, z) spacing in mm from DICOM geometry tags."""
    ps = next((s["pixel_spacing"] for s in slices if s["pixel_spacing"]), None)
    row_sp, col_sp = (ps[0], ps[1]) if ps else (1.0, 1.0)

    z_sp = None
    if len(slices) >= 2 and all(s["position"] is not None for s in slices):
        axis = _scan_axis(np.array([s["position"] for s in slices]))
        coords = sorted(s["position"][axis] for s in slices)
        gaps = np.diff(coords)
        gaps = gaps[gaps > 0]  # tolerate duplicate positions
        if len(gaps):
            z_sp = float(np.median(gaps))
    if z_sp is None:
        z_sp = next((s["slice_thickness"] for s in slices if s["slice_thickness"]), 1.0)
    return (col_sp, row_sp, z_sp)  # SimpleITK order: (x, y, z)


def _preprocess_sync(series_id: int):
    """Synchronous preprocessing body (executed in a thread by preprocess_series)."""
    from app.database import SessionLocal
    from app.models.series import Series

    db = SessionLocal()
    series = None
    try:
        series = db.query(Series).filter(Series.id == series_id).first()
        if not series:
            return {"status": "error", "reason": f"Series {series_id} not found"}

        series.processing_status = "preprocessing"
        db.commit()

        # Read DICOM files
        dcm_dir = series.raw_path
        dcm_files = sorted([os.path.join(dcm_dir, f) for f in os.listdir(dcm_dir) if f.endswith(".dcm")])
        if not dcm_files:
            # Try without extension filter
            dcm_files = sorted([os.path.join(dcm_dir, f) for f in os.listdir(dcm_dir)])

        slices = _read_slices(dcm_files)
        if not slices:
            series.processing_status = "error"
            db.commit()
            return {"status": "error", "reason": "No valid DICOM slices"}

        # 体积检测需要足够切片数——scout 定位像/协议截图等小序列直接拦截：
        # 不浪费推理算力，也不让垃圾候选污染审核队列（真实批量数据必含此类 series）
        if len(slices) < 8:
            series.processing_status = "error"
            db.commit()
            return {"status": "error", "reason": f"仅 {len(slices)} 张有效切片（<8），非体积 CT 序列，跳过检测"}

        # Real DICOM geometry: order slices by position and derive spacing from
        # PixelSpacing / ImagePositionPatient (was: filename order + (1,1,1)).
        _sort_slices(slices)
        original_spacing = _original_spacing(slices)

        volume = np.stack([s["img"] for s in slices], axis=0)
        original_shape = volume.shape  # numpy order: (z, y, x)

        # Resample to target spacing. SimpleITK size/spacing are (x, y, z) while
        # numpy shape is (z, y, x) — derive new_size from sitk's own size, never
        # from volume.shape, otherwise the x/z sampling factors get swapped and
        # the volume comes out distorted.
        sitk_vol = sitk.GetImageFromArray(volume.astype(np.float32))
        sitk_vol.SetSpacing(original_spacing)
        target_sp = settings.target_spacing
        in_size = sitk_vol.GetSize()
        new_size = [max(1, int(round(in_size[i] * original_spacing[i] / target_sp[i]))) for i in range(3)]
        resampled = sitk.Resample(sitk_vol, new_size, sitk.Transform(), sitk.sitkLinear, sitk_vol.GetOrigin(), target_sp, sitk_vol.GetDirection(), 0.0, sitk.sitkFloat32)
        preprocessed = sitk.GetArrayFromImage(resampled).astype(np.float32)

        # Apply lungmask if enabled
        if settings.use_lungmask:
            try:
                from lungmask import LMInferer
                inferer = LMInferer()
                # lungmask expects shape (1, D, H, W) in range [0, 255]
                lm_input = np.clip((preprocessed + 1024) / 1324 * 255, 0, 255).astype(np.uint8)
                lung_mask = inferer.apply(lm_input[np.newaxis, ...])[0]
                preprocessed[lung_mask == 0] = -1024  # Air outside lungs
            except Exception:
                pass  # lungmask is optional

        # Write NIfTI（目录布局推导收口到 storage.layout）
        dirs = series_dirs_from_raw(series.raw_path)
        preprocessed_dir = dirs["preprocessed_path"]
        os.makedirs(preprocessed_dir, exist_ok=True)
        nifti_path = os.path.join(preprocessed_dir, f"{series.series_uid}.nii.gz")
        out_img = sitk.GetImageFromArray(preprocessed)
        out_img.SetSpacing(target_sp)
        # int16 写盘：HU 值域天然适配；体积较 float32 减半，且 gzip 压缩率
        # 远好于 float32 尾数噪声（公网带宽敏感）。推理 LoadImaged 兼容 int16。
        out_img = sitk.Cast(out_img, sitk.sitkInt16)
        sitk.WriteImage(out_img, nifti_path)
        # 同时写未压缩 .nii：volume 端点对非 gzip 客户端直发（gzip 客户端走
        # Content-Encoding 传输编码直接发 .nii.gz，浏览器原生解压）。DB 中
        # preprocessed_path 仍指向 .nii.gz，.nii 仅按同名约定派生。
        sitk.WriteImage(out_img, nifti_path[:-3])

        # HTJ2K bundle（查看器传输专用，~3-9x 压缩）：失败不阻塞主流程（无产物
        # 时前端自动回退 nifti 路径）。推理仍读无损 .nii.gz。
        htj2k_ok = False
        if settings.htj2k_enabled:
            try:
                import subprocess as _sp
                _sp.run(
                    [sys.executable,
                     os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "deploy", "htj2k", "encode_volume.py"),
                     nifti_path, "-q", str(settings.htj2k_qstep)],
                    check=True, capture_output=True, timeout=600,
                )
                htj2k_ok = True
            except Exception:
                logger.warning("htj2k encode failed for series %s（忽略，前端将回退 nifti）", series_id)

        # 未压缩 .nii 仅作 volume 端点对非 gzip 客户端的回退；htj2k 已就绪时
        # 查看器用不到它。keep_plain_nii=false 时删除以省空间（~120M/例），
        # htj2k 失败时无论开关都保留作回退
        if not settings.keep_plain_nii and htj2k_ok:
            try:
                os.remove(nifti_path[:-3])
            except OSError:
                logger.warning("plain .nii remove failed for series %s（忽略）", series_id)

        # Write meta.json
        meta = {
            "spacing": list(target_sp),
            "original_spacing": list(original_spacing),
            "original_shape": list(original_shape),
            "preprocessed_shape": list(preprocessed.shape),
            "affine": "identity",  # 输出 grid 约定：origin=0、identity direction（与 world_to_voxel 一致）
            "hu_window": [-1024, 300],
        }
        meta_path = write_meta_json(dirs["base"], meta)

        series.preprocessed_path = nifti_path
        series.meta_path = meta_path
        series.processing_status = "preprocessed"
        db.commit()

        # 推理入队由 async 包装层 preprocess_series 完成（避免在线程里建 event loop）
        return {"status": "ok", "series_id": series_id, "nifti_path": nifti_path}
    except Exception as e:
        # 与 inference 同一模式（CORR-011/BP-013）：先 rollback 再做错误登记，
        # 绝不让登记动作本身掩盖根因（series 可能未绑定、事务可能已损坏）
        logger.exception("preprocess failed for series %s", series_id)
        if series:
            try:
                db.rollback()
                series.processing_status = "error"
                db.commit()
            except Exception:
                logger.exception("failed to persist error status for series %s", series_id)
        return {"status": "error", "reason": str(e)}
    finally:
        db.close()
