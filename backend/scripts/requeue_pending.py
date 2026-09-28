"""断电/进程崩溃后的任务恢复：重置中间态序列并重新入队（幂等，可反复执行）。

arq 任务随 Redis 丢失或 worker 被杀后，series.processing_status 可能永远停在
preprocessing/inferring（摄入续跑只认 registered/error，见 ISS 续跑逻辑）。本脚本：
  preprocessing/inferring → registered（产物不完整；预处理成功后会自动接力推理）；
  所有 registered   → 入队 preprocess_series；
  所有 preprocessed → 入队 run_inference（预处理产物完整，直接补推理）。
detected 不动；error 不动（DeepLung 肺掩膜校验等确定性失败，重跑结果相同，人工核对后再处理）。

用法（backend 环境，cwd=backend）:
  python3 scripts/requeue_pending.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.series import Series


async def _enqueue(jobs: list):
    from arq import create_pool
    from app.worker.queue import redis_settings

    pool = await create_pool(redis_settings)
    try:
        for func, sid in jobs:
            await pool.enqueue_job(func, sid)
    finally:
        await pool.close()


def main():
    db = SessionLocal()
    try:
        stuck = db.query(Series).filter(
            Series.processing_status.in_(("preprocessing", "inferring"))
        ).all()
        for s in stuck:
            s.processing_status = "registered"
        db.commit()
        registered = [r[0] for r in db.query(Series.id).filter(
            Series.processing_status == "registered").all()]
        preprocessed = [r[0] for r in db.query(Series.id).filter(
            Series.processing_status == "preprocessed").all()]
    finally:
        db.close()

    jobs = [("preprocess_series", sid) for sid in registered]
    jobs += [("run_inference", sid) for sid in preprocessed]
    if jobs:
        asyncio.run(_enqueue(jobs))
    print(f"重置中间态 {len(stuck)}；入队 preprocess {len(registered)}、inference {len(preprocessed)}")


if __name__ == "__main__":
    main()
