"""Application configuration from environment variables."""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    database_url: str = "postgresql://ctanno:ctanno@localhost:5432/ctanno"
    redis_url: str = "redis://localhost:6379"
    jwt_secret: str = "change-me-in-production-use-secrets-manager"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 15
    jwt_refresh_expire_days: int = 7
    storage_root: str = "/data/ct-storage"
    # MONAI lung_nodule_ct_detection bundle root (parent dir of the bundle folder)
    model_bundle_root: str = "~/.cache/monai"
    model_score_thresh: float = 0.01
    target_spacing: List[float] = [0.703125, 0.703125, 1.25]
    use_lungmask: bool = True
    reviewer_lease_minutes: int = 30
    arq_worker_count: int = 1
    # 批量作业配置
    arq_job_timeout: int = 3600  # 真实大 CT 推理可能超过 600s（尤其 GPU OOM 退 CPU 时）
    arq_max_jobs: int = 1  # worker 并行任务数（共享 GPU 服务器默认保持串行）
    model_cuda_device: str = "cuda:0"  # 推理用 GPU（共享服务器上指向空闲卡，避免挤 cuda:0）
    # 候选框按 score 只展示前 K 个（DB 保留全量，读时过滤；放宽无需重跑推理）；0 = 返回全部
    detection_top_k: int = 0
    # 检测模型变体：dlcsd（公开权重）| deeplung（自备资产）| monai_bundle
    model_variant: str = "dlcsd"
    # DLCSD-mD 权重路径（model_variant=dlcsd 时使用；CC BY-NC 4.0 仅限科研）
    dlcsd_model_path: str = "~/.cache/dlcsd-md/DLCSD-mD.pt"
    # DeepLung 检测（model_variant=deeplung 时使用）：docker 子进程调起老容器。
    # 权重、推理代码与容器镜像均为本中心内部资产，不随仓库发布——以下默认值仅为
    # 占位，真实路径由部署环境的 .env 提供（.env 不入库）。
    deeplung_image: str = "your-internal-deeplung-image"
    deeplung_code_dir: str = "/path/to/internal/ProcByModel"  # 宿主代码目录，只读挂载到 /deeplung/ProcByModel
    deeplung_runner_dir: str = "/path/to/repo/deploy/deeplung"  # run_detect.py 所在目录，只读挂载到 /deeplung-run
    deeplung_ckpt: str = "/deeplung/ProcByModel/ckpts/your-weights.ckpt"  # 容器内 ckpt 路径（占位，真实值由 .env 提供）
    deeplung_out_root: str = "/tmp/deeplung-out"  # 每序列输出 <out_root>/<series_id>/result.json
    deeplung_timeout: int = 1800
    deeplung_gpu: str = "auto"  # auto=nvidia-smi 选最闲卡；cpu=强制 CPU；数字字符串=固定卡
    # DeepLung 容器额外只读挂载根：raw 目录为 symlink 农场时（全量摄入省空间），
    # 把 symlink 目标所在的宿主根路径按同路径挂进容器，容器内 symlink 才能解析；空串不挂载
    deeplung_raw_mount_root: str = ""
    # DeepLung 候选二道过滤阈值（logit 空间）与数量上限：默认 3/10 与模型原硬编码一致；
    # 调低阈值（如 1/0）提高召回、候选变多（通过容器环境变量透传）
    deeplung_logit_thresh: str = "3"
    deeplung_max_nodes: int = 10
    # HTJ2K 查看器传输压缩（预处理时随 .nii 一并产出 bundle；qstep 见 deploy/htj2k/encode_volume.py）
    htj2k_enabled: bool = True
    htj2k_qstep: float = 0.00008
    # 是否在 htj2k 编码成功后保留未压缩 .nii（volume 端点对非 gzip 客户端的回退）。
    # 全量摄入时设 false 每例省 ~120M；htj2k 失败时保留 .nii 作回退，不受此开关影响
    keep_plain_nii: bool = True
    cors_origins: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
