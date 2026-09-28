"""MONAI bundle inference worker task."""
import os
import json
import math
import asyncio
import logging
import subprocess
import threading

import numpy as np
import torch

from app.config import settings

logger = logging.getLogger(__name__)

# MONAI model zoo bundle: RetinaNet 3D trained on LUNA16
BUNDLE_NAME = "lung_nodule_ct_detection"
BUNDLE_VERSION = "0.1.1"
NODULE_LABEL = "nodule"

# Bundle inference.json detector_ops: high-sensitivity box selection (more false
# positives is acceptable — doctors review and reject false candidates).
# Values are the bundle's official detector_ops (inference.json), NOT settings:
# the LUNA16-trained RetinaNet expects score_thresh=0.02 / nms_thresh=0.22 /
# detections_per_img=300. settings.model_score_thresh (0.01) remains as a
# final post-filter below.
_BOX_SELECTOR_PARAMS = {
    "score_thresh": 0.02,
    "topk_candidates_per_level": 1000,
    "nms_thresh": 0.22,
    "detections_per_img": 300,
}
# Sliding window for volumes larger than the ROI (see use_inferer below).
# device is intentionally NOT set here — it is bound to _device at load time so
# the windowed forward runs on the same device as the network (CORR-002).
_SLIDING_WINDOW = {"roi_size": (512, 512, 208), "overlap": 0.25, "sw_batch_size": 1, "mode": "constant"}

# Process-wide lazy-loaded bundle components (Arq workers process many series)
_detector = None
_preprocessing = None
_postprocessing = None
_device = None
_load_lock = threading.Lock()


def _resolve_bundle_dir() -> str:
    """Return the MONAI bundle directory, raising a clear error when not installed."""
    bundle_dir = os.path.join(os.path.expanduser(settings.model_bundle_root), BUNDLE_NAME)
    if not os.path.isdir(bundle_dir):
        raise FileNotFoundError(
            f"MONAI bundle '{BUNDLE_NAME}' not found at '{bundle_dir}'. "
            f"Install it with: python -c \"from monai.bundle import download; "
            f"download(name='{BUNDLE_NAME}', version='{BUNDLE_VERSION}')\""
        )
    return bundle_dir


def _build_dlcsd_detector(device: torch.device):
    """构造 DLCSD-mD 检测器（Duke 临床数据训练的 MONAI RetinaNet）。

    权重为 TorchScript 归档（Zenodo 14967976，CC BY-NC 4.0 仅限科研）；架构与
    LUNA16 bundle 完全一致（DLCSD 团队按 MONAI 教程训练：resnet50(3D)+FPN
    returned_layers=[1,2]，num_anchors=3，anchors [[6,8,4],[8,6,5],[10,10,6]]），
    已验证 state_dict 0 missing / 0 unexpected，故取其 state_dict 灌入等价网络，
    避免直接部署 TorchScript 模块带来的不可审计面。
    """
    from monai.networks.nets.resnet import resnet50
    from monai.apps.detection.networks.retinanet_network import resnet_fpn_feature_extractor, RetinaNet
    from monai.apps.detection.utils.anchor_utils import AnchorGeneratorWithAnchorShape
    from monai.apps.detection.networks.retinanet_detector import RetinaNetDetector

    backbone = resnet50(spatial_dims=3, n_input_channels=1, conv1_t_stride=[2, 2, 1], conv1_t_size=[7, 7, 7])
    feature_extractor = resnet_fpn_feature_extractor(backbone, 3, False, [1, 2], None)
    network = RetinaNet(
        spatial_dims=3, num_classes=1, num_anchors=3,
        feature_extractor=feature_extractor, size_divisible=[16, 16, 8],
    )
    weights_path = os.path.expanduser(settings.dlcsd_model_path)
    if not os.path.isfile(weights_path):
        raise FileNotFoundError(f"DLCSD-mD 权重缺失: {weights_path}")
    scripted = torch.jit.load(weights_path, map_location="cpu")
    network.load_state_dict(scripted.state_dict())
    network.to(device)
    anchor_generator = AnchorGeneratorWithAnchorShape(
        feature_map_scales=[1, 2, 4],
        base_anchor_shapes=[[6, 8, 4], [8, 6, 5], [10, 10, 6]],
    )
    return RetinaNetDetector(network=network, anchor_generator=anchor_generator, debug=False)


def _build_dlcsd_preprocessing():
    """DLCSD-mD 训练预处理：重采样 0.703125×0.703125×1.25 → HU clip [-1000,500]
    → 逐体积 z-score 归一化（与 LUNA16 bundle 的 [-1024,300]+[0,1] 缩放不同）。
    存储卷已 clip 到 [-1024,300]，实际等效输入 [-1000,300]（结节 HU 区间不受影响）。"""
    from monai.transforms import (
        Compose, LoadImaged, EnsureChannelFirstd, Orientationd,
        Spacingd, ScaleIntensityRanged, NormalizeIntensityd, EnsureTyped,
    )
    return Compose([
        LoadImaged(keys=["image"]),
        EnsureChannelFirstd(keys=["image"]),
        Orientationd(keys=["image"], axcodes="RAS"),
        Spacingd(keys=["image"], pixdim=list(settings.target_spacing)),
        ScaleIntensityRanged(keys=["image"], a_min=-1000.0, a_max=500.0, b_min=-1000.0, b_max=500.0, clip=True),
        NormalizeIntensityd(keys=["image"]),
        EnsureTyped(keys=["image"]),
    ])


def _load_bundle_components():
    """Lazy-load bundle preprocessing/detector/postprocessing once per worker process."""
    global _detector, _preprocessing, _postprocessing, _device
    if _detector is not None:
        return

    with _load_lock:  # guard the lazy init against concurrent first tasks (BP-009)
        if _detector is not None:
            return

        from monai.bundle import ConfigParser

        bundle_dir = _resolve_bundle_dir()

        inference_json = os.path.join(bundle_dir, "configs", "inference.json")
        config = ConfigParser()
        config.read_config(inference_json)
        config.bundle_root = bundle_dir

        # MONAI >= 1.1 dropped EnsureChannelFirstd(meta_key_postfix=...) — strip it from the
        # bundle's preprocessing pipeline (MetaTensor keeps meta regardless).
        with open(inference_json) as f:
            raw_config = json.load(f)
        for transform in raw_config.get("preprocessing", {}).get("transforms", []):
            if transform.get("_target_") == "EnsureChannelFirstd":
                transform.pop("meta_key_postfix", None)
        config.update({"preprocessing": raw_config["preprocessing"]})

        # 后处理（box 裁剪到图像 + 世界坐标 cccwhd 换算）是几何簿记，与模型无关，
        # 两个变体共用 bundle 的 postprocessing
        _postprocessing = config.get_parsed_content("postprocessing", instantiate=True)

        # Device resolution: configured GPU first (shared servers: point
        # model_cuda_device at an idle card); fall back to CPU on OOM.
        devices = (
            [torch.device(settings.model_cuda_device), torch.device("cpu")]
            if torch.cuda.is_available()
            else [torch.device("cpu")]
        )

        if settings.model_variant == "dlcsd":
            _preprocessing = _build_dlcsd_preprocessing()
            for attempt_device in devices:
                try:
                    if attempt_device.type == "cuda":
                        torch.cuda.empty_cache()
                    detector = _build_dlcsd_detector(attempt_device)
                    _device = attempt_device
                    break
                except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
                    if attempt_device.type == "cpu":
                        raise
                    logger.warning("GPU OOM loading DLCSD-mD (%s), falling back to CPU.", e)
        else:
            _preprocessing = config.get_parsed_content("preprocessing", instantiate=True)
            ckpt_path = os.path.join(bundle_dir, "models", "model.pt")
            if not os.path.isfile(ckpt_path):
                raise FileNotFoundError(f"MONAI bundle weights missing: {ckpt_path}")
            for attempt_device in devices:
                config["device"] = attempt_device
                try:
                    torch.cuda.empty_cache() if attempt_device.type == "cuda" else None
                    detector = config.get_parsed_content("detector")
                    # The bundle's model.pt is a bare state_dict — weights_only is safe (SEC-006)
                    checkpoint = torch.load(ckpt_path, map_location=attempt_device, weights_only=True)
                    state_dict = checkpoint.get("model", checkpoint)
                    detector.network.load_state_dict(state_dict)
                    _device = attempt_device
                    break
                except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
                    if attempt_device.type == "cpu":
                        raise
                    logger.warning(
                        "GPU OOM loading MONAI bundle (%s), falling back to CPU.", e
                    )

        detector.eval()
        detector.set_target_keys(box_key="box", label_key="label")
        detector.set_box_selector_parameters(**_BOX_SELECTOR_PARAMS)
        detector.set_sliding_window_inferer(device=_device, **_SLIDING_WINDOW)

        _detector = detector
        logger.info("detector loaded (variant=%s, device=%s)", settings.model_variant, _device)


async def run_inference(ctx: dict, series_id: int):
    """Arq task: run MONAI lung nodule detection on a preprocessed series.

    Arq requires coroutine tasks; the CPU/GPU-heavy body runs in a worker
    thread so it never blocks the event loop (ISS-011).
    """
    return await asyncio.to_thread(_run_inference_sync, series_id)


def _run_inference_sync(series_id: int):
    """Synchronous inference body (executed in a thread by run_inference)."""
    if settings.model_variant == "deeplung":
        return _run_deeplung_sync(series_id)
    from app.database import SessionLocal
    from app.models.series import Series
    from app.models.detection import Detection
    from app.models.review_result import ReviewResult
    from app.worker.coord_utils import world_to_voxel

    db = SessionLocal()
    series = None
    try:
        series = db.query(Series).filter(Series.id == series_id).first()
        if not series or not series.preprocessed_path:
            return {"status": "error", "reason": "Series not ready for inference"}

        series.processing_status = "inferring"
        db.commit()

        _load_bundle_components()

        # Bundle preprocessing: load NIfTI → RAS → target spacing → HU clip [-1024, 300] → scale [0, 1]
        sample = _preprocessing({"image": series.preprocessed_path})
        image = sample["image"]

        # RetinaNetDetector returns per-sample dict {box, label, label_scores} (world-coord
        # boxes, mm) — keys are set via set_target_keys in _load_bundle_components.
        # Mirror the bundle evaluator: use the sliding window only when the volume is at least
        # as large as one window, otherwise forward directly (CORR-002).
        sliding_window_size = np.prod(_SLIDING_WINDOW["roi_size"])
        use_inferer = image[0, ...].numel() >= sliding_window_size
        with torch.no_grad():
            try:
                pred = _detector([image.to(_device)], use_inferer=use_inferer)
            except (torch.cuda.OutOfMemoryError, RuntimeError):
                if _device.type == "cuda":
                    logger.warning(
                        "GPU OOM during inference (vol %.1f Mvox), retrying on CPU",
                        image[0, ...].numel() / 1e6,
                    )
                    _detector.to("cpu")
                    _detector.set_sliding_window_inferer(device=None, **_SLIDING_WINDOW)
                    torch.cuda.empty_cache()
                    # CPU has plenty of RAM — skip sliding window to avoid overhead
                    pred = _detector([image.cpu()], use_inferer=False)
                else:
                    raise
        boxes = pred[0]["box"]
        labels = pred[0]["label"]
        scores = pred[0]["label_scores"]

        # Postprocessing: clip boxes to image, convert to world coords, center+whd (cccwhd) format
        post = _postprocessing({"box": boxes, "label": labels, "label_scores": scores, "image": sample["image"]})
        box_world = post["box"]
        label_scores = post["label_scores"]

        # Meta: spacing + preprocessed shape for world→voxel denormalization (CORR-004)
        meta = {}
        if series.meta_path and os.path.exists(series.meta_path):
            with open(series.meta_path) as f:
                meta = json.load(f)
        spacing = meta.get("spacing", settings.target_spacing)
        shape = meta.get("preprocessed_shape")

        detections = []
        for b, s in zip(box_world, label_scores):
            score = float(s)
            if score < settings.model_score_thresh:
                continue
            box = [float(v) for v in b[:6]]  # cx, cy, cz, w, h, d in mm
            vox = world_to_voxel(box, spacing)
            if shape:  # clamp into the preprocessed grid — never trust box coordinates blindly
                vox["voxel_x"] = max(0, min(vox["voxel_x"], shape[2] - 1))
                vox["voxel_y"] = max(0, min(vox["voxel_y"], shape[1] - 1))
                vox["voxel_z"] = max(0, min(vox["voxel_z"], shape[0] - 1))
                vox["slice_index"] = vox["voxel_z"]
            detections.append({
                "box": box,
                "voxel": [vox["voxel_x"], vox["voxel_y"], vox["voxel_z"],
                          vox["voxel_w"], vox["voxel_h"], vox["voxel_d"]],
                "slice_index": vox["slice_index"],
                "score": round(score, 4),
                "label": NODULE_LABEL,
            })

        # Replace previous detections in one transaction — re-runs/retries must not
        # duplicate candidates (CORR-003). Verdicts referencing the old detections
        # are deleted first in the same transaction: a re-run replaces the whole
        # candidate set, so verdicts on stale candidates are meaningless — and
        # their FK references would otherwise block the DELETE (retry path).
        stale_det_ids = db.query(Detection.id).filter(Detection.series_id == series.id)
        db.query(ReviewResult).filter(ReviewResult.detection_id.in_(stale_det_ids)).delete(synchronize_session=False)
        db.query(Detection).filter(Detection.series_id == series.id).delete(synchronize_session=False)
        for det in detections:
            d = Detection(
                series_id=series.id,
                score=det["score"],
                box_x=det["box"][0], box_y=det["box"][1], box_z=det["box"][2],
                box_w=det["box"][3], box_h=det["box"][4], box_d=det["box"][5],
                voxel_x=det["voxel"][0], voxel_y=det["voxel"][1], voxel_z=det["voxel"][2],
                voxel_w=det["voxel"][3], voxel_h=det["voxel"][4], voxel_d=det["voxel"][5],
                slice_index=det["slice_index"],
                label=det["label"],
            )
            db.add(d)

        # 候选集已整体替换——旧审核结果随之失效，series 回到待审核状态
        series.review_status = "not_reviewed"
        series.reviewer_id = None
        series.lease_expires_at = None
        series.processing_status = "detected"
        db.commit()
        return {"status": "ok", "series_id": series_id, "detection_count": len(detections)}
    except Exception as e:
        # Never let error-bookkeeping mask the root cause (CORR-011/BP-013)
        logger.exception("inference failed for series %s", series_id)
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


# ---------------------------------------------------------------------------
# DeepLung 变体（model_variant=deeplung）：torch1.0 老容器，docker 子进程调起。
# 输入为原始 DICOM 目录（series.raw_path），输出 nodes=[conf,z,y,x,d_mm]，
# z/y/x 为原始 DICOM 体素索引；坐标映射与远端 bundle 导入保持一致。
# 已验证的数学一致：box_mm = idx * original_spacing（[x,y,z] 序），
# voxel_pre = clamp(round(box_mm / spacing))（spacing [x,y,z] 序、shape [z,y,x] 序）。
# ---------------------------------------------------------------------------


def _pick_gpu() -> str | None:
    """nvidia-smi 选显存空闲最多的卡；任何异常返回 None（调用方回退 CPU）。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.free", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
        best_idx, best_free = None, -1
        for line in out.strip().splitlines():
            idx, free = line.split(",")
            if int(free) > best_free:
                best_idx, best_free = idx.strip(), int(free)
        return best_idx
    except Exception:
        return None


def _resolve_deeplung_gpu() -> str | None:
    """按 deeplung_gpu 配置解析 GPU 序号：cpu→None；auto→最闲卡；数字→固定卡。"""
    cfg = str(settings.deeplung_gpu).strip().lower()
    if cfg == "cpu":
        return None
    if cfg == "auto":
        return _pick_gpu()
    return cfg


def _sigmoid(x: float) -> float:
    """数值稳定的 sigmoid（conf 为 logit，极端值不溢出）。"""
    return 0.5 * (1.0 + math.tanh(0.5 * x))


def _deeplung_nodes_to_detections(nodes: list, meta: dict) -> list:
    """DeepLung nodes → detections 字典列表（与 monai 路径同一组字段）。"""
    osp = meta["original_spacing"]  # [x,y,z] sitk 序
    tsp = meta["spacing"]  # [x,y,z]
    shape_zyx = meta["preprocessed_shape"]  # numpy [z,y,x] 序——注意与 spacing 轴序不同

    detections = []
    for conf, z, y, x, d in nodes:
        bx, by, bz = x * osp[0], y * osp[1], z * osp[2]
        vx = max(0, min(int(round(bx / tsp[0])), shape_zyx[2] - 1))
        vy = max(0, min(int(round(by / tsp[1])), shape_zyx[1] - 1))
        vz = max(0, min(int(round(bz / tsp[2])), shape_zyx[0] - 1))
        vw = max(1, int(round(d / tsp[0])))
        vh = max(1, int(round(d / tsp[1])))
        vd = max(1, int(round(d / tsp[2])))
        detections.append({
            "box": [bx, by, bz, float(d), float(d), float(d)],  # cx,cy,cz mm + 直径 mm 立方盒
            "voxel": [vx, vy, vz, vw, vh, vd],
            "slice_index": vz,
            "score": round(_sigmoid(conf), 4),
            "label": NODULE_LABEL,
        })
    return detections


def _run_deeplung(series, out_json: str) -> None:
    """docker 子进程跑 DeepLung 检测，产物写到 out_json（容器内 /out/result.json）。"""
    gpu = _resolve_deeplung_gpu()
    cmd = ["docker", "run", "--rm", "--entrypoint", "python3.6"]
    if gpu is not None:
        cmd += ["--gpus", f"device={gpu}"]
    cmd += [
        "-e", f"DEEPLUNG_LOGIT_THRESH={settings.deeplung_logit_thresh}",
        "-e", f"DEEPLUNG_MAX_NODES={settings.deeplung_max_nodes}",
        "-v", f"{settings.deeplung_code_dir}:/deeplung/ProcByModel:ro",
        "-v", f"{settings.deeplung_runner_dir}:/deeplung-run:ro",
        "-v", f"{series.raw_path}:/input:ro",
        "-v", f"{os.path.dirname(out_json)}:/out",
    ]
    # raw 目录为 symlink 农场时（全量摄入省空间），symlink 目标是宿主绝对路径，
    # 需把目标根按同路径挂进容器才能解析；空串不挂载（copy 模式无需此挂载）
    if settings.deeplung_raw_mount_root:
        cmd += ["-v", f"{settings.deeplung_raw_mount_root}:{settings.deeplung_raw_mount_root}:ro"]
    cmd += [
        settings.deeplung_image,
        "/deeplung-run/run_detect.py", "/input", settings.deeplung_ckpt, "/out/result.json",
    ]
    if gpu is not None:
        cmd.append(gpu)
    logger.info("deeplung start (series=%s, gpu=%s)", series.id, gpu or "cpu")
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=settings.deeplung_timeout)
    logger.info("deeplung done (series=%s): %s", series.id, (proc.stdout or "").strip()[-200:])


def _run_deeplung_sync(series_id: int):
    """DeepLung 推理同步体。状态迁移/旧候选清理/错误登记与 monai 路径同一模式。"""
    from app.database import SessionLocal
    from app.models.series import Series
    from app.models.detection import Detection
    from app.models.review_result import ReviewResult

    db = SessionLocal()
    series = None
    try:
        series = db.query(Series).filter(Series.id == series_id).first()
        if not series or not series.raw_path:
            return {"status": "error", "reason": "Series not ready for inference"}

        series.processing_status = "inferring"
        db.commit()

        # 坐标映射依赖预处理 meta（original_spacing/spacing/preprocessed_shape），
        # 缺 meta 无法把原始体素索引映到预处理网格——按未就绪处理
        if not series.meta_path or not os.path.exists(series.meta_path):
            raise FileNotFoundError(f"series {series_id} 缺 meta.json，请先完成预处理")
        with open(series.meta_path) as f:
            meta = json.load(f)

        out_dir = os.path.join(settings.deeplung_out_root, str(series.id))
        os.makedirs(out_dir, exist_ok=True)
        out_json = os.path.join(out_dir, "result.json")

        _run_deeplung(series, out_json)

        with open(out_json) as f:
            nodes = json.load(f).get("nodes", [])
        detections = _deeplung_nodes_to_detections(nodes, meta)

        # 单事务整体替换候选集（含关联 review_results 先删），与 monai 路径同一语义
        stale_det_ids = db.query(Detection.id).filter(Detection.series_id == series.id)
        db.query(ReviewResult).filter(ReviewResult.detection_id.in_(stale_det_ids)).delete(synchronize_session=False)
        db.query(Detection).filter(Detection.series_id == series.id).delete(synchronize_session=False)
        for det in detections:
            d = Detection(
                series_id=series.id,
                score=det["score"],
                box_x=det["box"][0], box_y=det["box"][1], box_z=det["box"][2],
                box_w=det["box"][3], box_h=det["box"][4], box_d=det["box"][5],
                voxel_x=det["voxel"][0], voxel_y=det["voxel"][1], voxel_z=det["voxel"][2],
                voxel_w=det["voxel"][3], voxel_h=det["voxel"][4], voxel_d=det["voxel"][5],
                slice_index=det["slice_index"],
                label=det["label"],
                source="deeplung",
            )
            db.add(d)

        # 候选集已整体替换——旧审核结果随之失效，series 回到待审核状态
        series.review_status = "not_reviewed"
        series.reviewer_id = None
        series.lease_expires_at = None
        series.processing_status = "detected"
        db.commit()
        return {"status": "ok", "series_id": series_id, "detection_count": len(detections)}
    except Exception as e:
        # 与 monai 路径同一模式（CORR-011/BP-013）：先 rollback 再做错误登记
        logger.exception("deeplung inference failed for series %s", series_id)
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
