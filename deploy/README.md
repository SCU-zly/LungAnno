# 部署指南

安装、模型准备、迁移与管理员创建见 [项目 README](../README.md)。下文路径均为占位示例，不包含任何已部署服务器的地址或配置。

## 运行架构

Linux 宿主机运行 uvicorn API（8000）和 arq worker；Docker 运行 PostgreSQL（5432）、Redis（6379）及 nginx（8080）。nginx 托管 `frontend/dist`，将 `/api/` 请求转发到 `127.0.0.1:8000/`。Compose 的 web 使用 host 网络。

从仓库根目录运行：

```bash
docker compose up -d postgres redis
bash deploy/start_backend.sh
docker compose up -d web
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8080/api/health
```

启动脚本自动定位仓库，默认使用 `backend/.venv/bin`，日志写入 `backend/logs/`。它会跳过检测到已运行的 API/worker，适用于单实例部署。多实例或长期运行建议交由服务管理器管理。

## 更新与自启

前端变更后在 `frontend/` 执行 `npm ci && npm run build`。后端变更后由部署者使用实际的服务管理方式重启 API/worker。数据库迁移在激活虚拟环境后，于 `backend/` 执行 `alembic upgrade head`。

Compose 的基础设施容器设置了 `restart: unless-stopped`，前提是 Docker 服务已配置开机启动。**API/worker 的 systemd 服务需要单独创建并启用**；本仓库没有安装或启用它们。

若采用用户级 systemd，请分别配置 API 和 worker 的 `WorkingDirectory=/path/to/repo/backend` 及虚拟环境命令：

```text
/path/to/repo/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
/path/to/repo/backend/.venv/bin/python -m arq app.worker.run_worker.WorkerSettings
```

按部署需求设置 `Restart=on-failure`、依赖顺序和用户登录退出后的持续运行。不要同时使用多个管理方式启动同一个 worker。

## 网络与数据

- 公网部署需自行配置域名、TLS 证书及 HTTPS 反向代理。示例 Compose 的数据库和 Redis 端口用于开发，生产应限制为回环或受控网络。
- 同步修改数据库实际口令和应用连接串，并设置随机 `JWT_SECRET`。
- `STORAGE_ROOT` 应指向持久、可写目录。数据库和影像需配套备份；迁移存储后检查容器挂载。
- 共享或吞吐受限磁盘上保持 `ARQ_MAX_JOBS=1`，串行分批摄入与限速传输，先评估容量。日志、临床表及 bundle 含患者信息，应留在受控存储。

## 故障处理

先检查健康端点及 `backend/logs/`。任务中断后，可在确认旧 worker 已停止、没有在途任务时运行 `python scripts/requeue_pending.py`（`cwd=backend`，激活虚拟环境），然后启动 worker。不要在正常处理期间重置任务状态。

失败序列可通过界面的重试功能重新处理。管理员在序列列表回退审核会删除审核结论并释放审核状态，保留检测候选。
