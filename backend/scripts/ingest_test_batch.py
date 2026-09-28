"""一次性脚本：按患者名单 xlsx 匹配 RawData 目录，建批次并摄入薄层序列。

流程：读姓名 → 在 RawData 各分组子目录（如 1-50、1001-1100，最多下钻两层）按
basename 包含姓名匹配文件夹（同名多文件夹全部保留并打印）→ create_batch(批次名)
→ 逐文件夹 ingest_into_batch(slice_thickness_max=1.0, min_instances=50)
→ to_enqueue 序列按产物齐全与否入队推理/预处理（arq 直连 redis，url 读 settings.redis_url）。

用法（backend venv，cwd=backend）:
  python3 scripts/ingest_test_batch.py --xlsx /path/to/patients.xlsx --raw-root /path/to/dicom --name 示例批次
"""
import argparse
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DEFAULT_BATCH = "测试批次"
SLICE_THICKNESS_MAX = 1.0
MIN_INSTANCES = 50


def _find_patient_folders(root: str, name: str) -> list:
    """在 root 的分组子目录里找 basename 包含姓名的文件夹（最多下钻两层）。"""
    matches = []
    # 第一层：分组目录（1-50、1001-1100 …）或直属患者文件夹
    for entry1 in sorted(os.listdir(root)):
        path1 = os.path.join(root, entry1)
        if not os.path.isdir(path1):
            continue
        if name in entry1:
            matches.append(path1)
        # 第二层：分组内的患者文件夹
        for entry2 in sorted(os.listdir(path1)):
            path2 = os.path.join(path1, entry2)
            if os.path.isdir(path2) and name in entry2:
                matches.append(path2)
    return matches


def _series_detail_lines(db, series_ids: list) -> list:
    """薄层序列明细行（厚度/描述/层数——层数取 raw 目录文件数）。"""
    from app.models.series import Series

    lines = []
    for s in db.query(Series).filter(Series.id.in_(series_ids)).all():
        count = len(os.listdir(s.raw_path)) if s.raw_path and os.path.isdir(s.raw_path) else 0
        lines.append(
            f"    series_id={s.id} 厚度={s.slice_thickness}mm 描述={s.series_description or '-'} 层数={count}"
        )
    return lines


async def _enqueue_all(jobs: list):
    """jobs: [(series_id, has_artifacts)]；产物齐全走推理，否则从头预处理。"""
    from arq import create_pool
    from app.worker.queue import redis_settings  # 由 settings.redis_url 解析，单一来源

    pool = await create_pool(redis_settings)
    try:
        for series_id, has_artifacts in jobs:
            if has_artifacts:
                await pool.enqueue_job("run_inference", series_id)
            else:
                await pool.enqueue_job("preprocess_series", series_id)
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description="按 xlsx 名单摄入患者薄层序列并建批次")
    parser.add_argument("--xlsx", required=True, help="患者名单 xlsx（含「姓名」列）")
    parser.add_argument("--name", default=DEFAULT_BATCH, help="新批次名（已存在则报错退出）")
    parser.add_argument("--raw-root", required=True, help="患者 DICOM 目录根路径")
    args = parser.parse_args()

    import pandas as pd
    from app.database import SessionLocal
    from app.models.series import Series
    from app.ingestion.service import create_batch, ingest_into_batch
    xlsx_path = os.path.abspath(os.path.expanduser(args.xlsx))
    raw_root = os.path.abspath(os.path.expanduser(args.raw_root))
    batch_name = args.name

    names = [str(n).strip() for n in pd.read_excel(xlsx_path)["姓名"].dropna().tolist()]
    print(f"待匹配患者 {len(names)} 人: {names}（名单 {xlsx_path}）")

    db = SessionLocal()
    enqueue_jobs = []
    unmatched = []
    skipped_all = []
    try:
        from app.models.batch import Batch
        if db.query(Batch).filter(Batch.name == batch_name).first():
            print(f"批次 {batch_name!r} 已存在，退出（不合并不覆盖）")
            sys.exit(1)
        batch = create_batch(
            db,
            batch_name,
            description=f"{date.today().isoformat()} 从 {xlsx_path}（{len(names)} 人名单）匹配 {raw_root} 摄入；"
                        f"仅薄层（层厚<={SLICE_THICKNESS_MAX}mm 且层数>={MIN_INSTANCES}）。",
        )

        for name in names:
            folders = _find_patient_folders(raw_root, name)
            if not folders:
                unmatched.append(name)
                print(f"[{name}] 未匹配到文件夹")
                continue
            print(f"[{name}] 匹配 {len(folders)} 个文件夹:")
            for folder in folders:
                print(f"  {folder}")

            for folder in folders:
                result = ingest_into_batch(
                    db, batch, folder,
                    slice_thickness_max=SLICE_THICKNESS_MAX,
                    min_instances=MIN_INSTANCES,
                )
                skipped_all.extend(result["skipped_series"])
                print(f"  摄入 {folder}：新注册 {result['series_count']} 序列，待入队 {len(result['to_enqueue'])}")
                for line in _series_detail_lines(db, result["to_enqueue"]):
                    print(line)

                for sid in result["to_enqueue"]:
                    s = db.query(Series).filter(Series.id == sid).first()
                    has_artifacts = bool(s.preprocessed_path and os.path.isfile(s.preprocessed_path))
                    enqueue_jobs.append((sid, has_artifacts))
    finally:
        db.close()

    if enqueue_jobs:
        asyncio.run(_enqueue_all(enqueue_jobs))
    print(f"已入队 {len(enqueue_jobs)} 个任务（推理 {sum(1 for _, ok in enqueue_jobs if ok)}，"
          f"预处理 {sum(1 for _, ok in enqueue_jobs if not ok)}）")

    if unmatched:
        print(f"未匹配患者（{len(unmatched)}）: {unmatched}")
    if skipped_all:
        print(f"跳过序列（{len(skipped_all)}）:")
        for item in skipped_all:
            print(f"  {item['series_uid']}: {item['reason']}")


if __name__ == "__main__":
    main()
