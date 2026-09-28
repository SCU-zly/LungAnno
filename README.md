# DeepLNAnno（DeepLN 标注系统）

AI 辅助的胸部 CT 肺结节检测与标注平台。系统完成 DICOM 预处理与候选结节检测，审核员在浏览器中按「批次 → 患者 → 检查日期 → CT 序列」逐层审核。

本仓库提供应用代码，不包含患者数据、临床表、模型权重或内部推理镜像。本文所有路径、账号和批次示例均为占位或虚构示例。患者字段与数据库结构保留，部署者需自行准备有权使用的数据。

## 功能

- 自动预处理、重采样、肺掩膜与候选结节检测。
- Cornerstone3D 查看器，支持窗宽窗位、翻片和候选框定位；HTJ2K 按层加载，缺少压缩产物时回退 NIfTI 整卷加载。
- 候选结节接受 / 拒绝 / 不确定；支持多人独立审核、本人重审覆盖及管理员回退。
- 管理员创建账号、重置密码、分配批次访问权限；审核员自行修改密码。
- DICOM 目录摄入、患者名单及清单驱动摄入、临床 CSV 导入、远端结果 bundle 导入。

## 架构

```text
浏览器（React + Cornerstone3D）
  → nginx :8080（frontend/dist；/api 反代到 127.0.0.1:8000）
  → FastAPI（宿主机）→ PostgreSQL
                    → Redis → arq worker（宿主机）→ 存储目录
```

后端使用 FastAPI、SQLAlchemy、Alembic、arq、SimpleITK 和 MONAI。前端使用 React、TypeScript、Vite 和 OpenJPH wasm。部署指南见 [deploy/README.md](deploy/README.md)。

## 检测模型

公开版本以当前代码快照发布，模型通过 `MODEL_VARIANT` 选择，不需要切换分支。

| 配置 | 所需资产 |
|---|---|
| `dlcsd`（默认） | 自行下载 `DLCSD-mD.pt` 和 MONAI bundle 配置，设置模型路径 |
| `deeplung` | 自备兼容的推理代码、容器镜像和权重，并配置 `DEEPLUNG_*` |
| `monai_bundle` | 另行准备 `lung_nodule_ct_detection` bundle，配置 `MODEL_BUNDLE_ROOT` |

DLCSD-mD 的下载入口：[Zenodo 模型记录](https://zenodo.org/records/14967976)，相关说明见 [作者项目](https://github.com/fitushar/AI-in-Lung-Health-Benchmarking-Detection-and-Diagnostic-Models-Across-Multiple-CT-Scan-Datasets)。使用前核对下载记录中的权重许可证；应用代码的 MIT 许可证不替代模型许可证。DeepLung 所需内部资产不随本仓库分发。

## 安装与启动

以下命令从仓库根目录开始，面向 Linux、Python 3.12、Node.js 20+、Docker Compose。GPU 为可选项；使用 GPU 时另行安装匹配驱动与 PyTorch。无需为验证部署先导入真实影像。

### 1. Python 环境和配置

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
# 名单 / 临床表导入需要的可选依赖
pip install -r backend/requirements-import.txt
cp backend/.env.example backend/.env
```

编辑 `backend/.env`：设置可写且持久的 `STORAGE_ROOT`、随机 `JWT_SECRET`、数据库连接及模型路径。可用 `python -c 'import secrets; print(secrets.token_urlsafe(48))'` 生成密钥。复制的示例口令只用于本地开发；生产时同时修改 Compose 中 PostgreSQL 的口令及 `DATABASE_URL`。已有数据库卷需要单独变更数据库角色口令。

### 2. 模型准备

DLCSD 路径仍复用 MONAI bundle 的后处理配置，因此也需要准备 `lung_nodule_ct_detection`（代码使用版本 `0.1.1`）。在已激活的虚拟环境中运行：

```bash
python -c 'from pathlib import Path; from monai.bundle import download; download(name="lung_nodule_ct_detection", version="0.1.1", bundle_dir=str(Path.home()/".cache"/"monai"))'
```

确认 `~/.cache/monai/lung_nodule_ct_detection/configs/inference.json` 存在。另从上述模型记录下载 `DLCSD-mD.pt`，放在自己的模型目录，然后在 `backend/.env` 设置：

```dotenv
MODEL_VARIANT=dlcsd
MODEL_BUNDLE_ROOT=~/.cache/monai
DLCSD_MODEL_PATH=/path/to/models/DLCSD-mD.pt
MODEL_CUDA_DEVICE=cuda:0
ARQ_MAX_JOBS=1
```

`/path/to/models` 为占位路径，需替换。CPU 部署可设置 `MODEL_CUDA_DEVICE=cpu`。权重不随仓库提供；缺少权重时无法完成推理。

### 3. 基础设施、数据库与管理员

```bash
docker compose up -d postgres redis
cd backend
alembic upgrade head
read -rsp 'New admin password: ' ADMIN_PASSWORD; echo
python scripts/create_admin.py admin "$ADMIN_PASSWORD"
unset ADMIN_PASSWORD
cd ..
```

管理员密码至少 8 位。该脚本会重置已有同名管理员的密码，应仅在初始化或明确需要重置时执行。

### 4. 前端构建与服务启动

```bash
cd frontend
npm ci
npm run build
cd ..
bash deploy/start_backend.sh
docker compose up -d web
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8080/api/health
```

浏览器打开 `http://localhost:8080`。启动脚本默认使用 `backend/.venv/bin`，可通过 `PYBIN` 指向其他虚拟环境的 `bin` 目录；日志写入 `backend/logs/`。API 和 worker 读取 `backend/.env`。

这些命令使用宿主机 API/worker；不要同时启动 Compose 中的 `api`/`worker`。后两者为可选开发服务，需要自行配置容器可访问的模型和存储路径。systemd 自启**需要单独安装配置**，仓库不会自动安装服务。

## 使用与数据导入

管理员登录后创建审核账号并授权批次。审核员进入序列，逐个检查候选并提交结果；未标记候选在提交时会记为拒绝。同一序列允许多人分别提交，重审覆盖本人旧结论；管理员回退会清除该序列全部审核结果。

摄入 API：以管理员身份请求 `POST /api/batches/ingest`，JSON 为 `{"directory_path":"/path/to/dicom","batch_name":"示例批次"}`。目录指后端可访问的路径，扫描注册后任务入队。API 默认不启用名单脚本的薄层过滤。

脚本在激活虚拟环境且 `cwd=backend` 时使用。下列路径均须替换为自己的输入：

```bash
python scripts/ingest_test_batch.py --xlsx /path/to/patients.xlsx --raw-root /path/to/dicom --name 示例批次
python scripts/import_clinical_csv.py --csv /path/to/clinical.csv
python scripts/ingest_cohort_batch.py --manifest-dir /path/to/manifests --raw-root /path/to/dicom --limit 3
```

- xlsx 名单需要 `姓名` 列。脚本在原始目录的前两层按姓名匹配文件夹，默认选择层厚 ≤1.0 mm 且实例数 ≥50 的序列；同名多目录会全部处理，应先核对名单与目录。
- 临床 CSV 使用 `住院号`、`姓名` 列；住院号按既有规则转为无前导零的整数字符串，其余非空字段保留。
- 清单目录包含 `episode_status.csv`（`episode_id,inventory_status,selected_series_uid`）及 `cohort_manifest.csv`（`episode_id,patient_id,ct_dir`，可选 `name` 和临床字段）。相对 `ct_dir` 以 `--raw-root` 为基准，绝对路径原样保留；`--source-raw-prefix /legacy/dicom` 可按完整目录前缀映射到新的原始根目录。原始数据通过符号链接复用。
- 远端推理与导入参见 [deploy/remote/README.md](deploy/remote/README.md)。这些脚本会产生含患者信息的运行日志和导出包，均不应提交到公开仓库。

共享存储上按小批次串行处理，保持 `ARQ_MAX_JOBS=1`。不要对数据盘运行无范围限制的递归扫描或并发大文件传输。

## 配置参考

完整示例见 [backend/.env.example](backend/.env.example)。

| 变量 | 默认或用途 |
|---|---|
| `DATABASE_URL` / `REDIS_URL` | PostgreSQL 连接 / Redis 队列 |
| `JWT_SECRET` | 必须替换示例值 |
| `STORAGE_ROOT` | `/data/ct-storage`，需可写且持久 |
| `MODEL_VARIANT` | `dlcsd` |
| `DLCSD_MODEL_PATH` | `~/.cache/dlcsd-md/DLCSD-mD.pt` |
| `MODEL_CUDA_DEVICE` | `cuda:0`，可设 `cpu` |
| `ARQ_MAX_JOBS` | `1` |
| `DETECTION_TOP_K` | `0`，返回全部候选 |
| `HTJ2K_ENABLED` / `HTJ2K_QSTEP` | `true` / `8e-05` |

HTJ2K 编码器说明及重建步骤见 [deploy/htj2k/README.md](deploy/htj2k/README.md)。随附二进制面向 x86_64 Linux；其他架构需重建或关闭 HTJ2K。

## 目录与许可证

- `backend/`：API、任务队列、迁移与导入脚本。
- `frontend/`：查看器、账号管理与审核界面。
- `deploy/`：部署说明、nginx 配置、编码与推理运行器。

感谢 [MONAI](https://monai.io/)、[DLCSD-mD](https://github.com/fitushar/AI-in-Lung-Health-Benchmarking-Detection-and-Diagnostic-Models-Across-Multiple-CT-Scan-Datasets)、[DeepLung](https://github.com/wentaozhu/DeepLung)、[Cornerstone3D](https://www.cornerstonejs.org/) 与 [OpenJPH](https://github.com/aous72/OpenJPH)。

MIT License，版权归 **四川大学智能医学中心** 所有，见 [LICENSE](LICENSE)。第三方代码、工具和模型遵循其各自许可证。本系统用于科研与辅助标注，输出不构成诊断结论。
