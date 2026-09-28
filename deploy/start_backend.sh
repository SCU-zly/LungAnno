#!/usr/bin/env bash
# 启动/恢复 ctanno 后端（uvicorn API + arq worker），幂等：已在跑则跳过。
# 若需要 systemd 自启，须单独安装配置服务；
# 本脚本用于手动恢复或调试；与 systemd 并存时靠 pgrep 防重复拉起。
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$PROJECT_ROOT/backend"
PYBIN="${PYBIN:-$BACKEND/.venv/bin}"
if [[ ! -x "$PYBIN/python3" || ! -x "$PYBIN/uvicorn" ]]; then
  echo "Backend virtual environment missing: $PYBIN" >&2
  exit 1
fi
cd "$BACKEND"
mkdir -p logs
TS="$(date +%Y%m%d_%H%M%S)"

# 依赖的 docker 服务先确保在跑（restart=unless-stopped 已配，双保险）
docker start ctanno-pg ctanno-redis >/dev/null 2>&1 || true

set -a; . ./.env; set +a

if pgrep -f "uvicorn app.main:app" >/dev/null; then
  echo "api already running"
else
  nohup "$PYBIN/uvicorn" app.main:app --host 0.0.0.0 --port 8000 >> "logs/api_${TS}.log" 2>&1 &
  echo "api started: $!"
fi

if pgrep -f "arq app.worker.run_worker.WorkerSettings" >/dev/null; then
  echo "worker already running"
else
  nohup "$PYBIN/python3" -m arq app.worker.run_worker.WorkerSettings >> "logs/worker_${TS}.log" 2>&1 &
  echo "worker started: $!"
fi
