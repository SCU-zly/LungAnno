"""按清单摄入批次，保留患者字段和既有数据库结构。

读取 episode_status.csv 中 inventory_status=done 的条目；flagged 条目列入报告。
cohort_manifest.csv 提供 episode_id、patient_id、ct_dir 和可选临床信息。
raw DICOM 使用符号链接，已完成的序列仅补批次关联。

用法（cwd=backend；以下路径仅为占位示例）:
  python3 scripts/ingest_cohort_batch.py --manifest-dir /path/to/manifests --raw-root /path/to/dicom
可用 --source-raw-prefix /legacy/dicom 映射历史前缀；相对 ct_dir 以 raw-root 为基准。
"""
import argparse
import asyncio
import csv
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DEFAULT_BATCH = "清单预标注批次"

# cohort_manifest → patient_clinical.payload 的键映射（前端面板按中文键通用展示）
CLINICAL_KEY_MAP = {
    "sex": "性别",
    "age": "年龄",
    "surgery_date": "手术日期",
    "pre_surg_CTC": "术前CTC",
    "tumor_size_cm": "肿瘤大小",
    "tumor_loc": "tumor_loc",
    "TNM_stage": "TNM分期",
}


def _read_csv(path: str) -> list:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _map_ct_dir(ct_dir: str, raw_root: str, source_raw_prefix: str | None = None) -> str:
    """按完整路径组件映射历史前缀；相对路径以 raw_root 为基准。"""
    raw_root = os.path.abspath(os.path.expanduser(raw_root))
    ct_dir = os.path.normpath(os.path.expanduser(ct_dir))
    if source_raw_prefix:
        prefix = os.path.normpath(os.path.expanduser(source_raw_prefix))
        if ct_dir == prefix:
            return raw_root
        if ct_dir.startswith(prefix.rstrip(os.sep) + os.sep):
            return os.path.join(raw_root, os.path.relpath(ct_dir, prefix))
    return ct_dir if os.path.isabs(ct_dir) else os.path.join(raw_root, ct_dir)


async def _enqueue_all(series_ids: list):
    from arq import create_pool
    from app.worker.queue import redis_settings

    pool = await create_pool(redis_settings)
    try:
        for sid in series_ids:
            await pool.enqueue_job("preprocess_series", sid)
    finally:
        await pool.close()


def _upsert_clinical(db, patient_id: str, name: str, cohort_row: dict, episode_row: dict):
    """cohort 临床字段 → patient_clinical（幂等 upsert，已有记录只补缺不覆盖）。"""
    from app.models.patient_clinical import PatientClinical

    payload = {"住院号": patient_id}
    for src, dst in CLINICAL_KEY_MAP.items():
        val = (cohort_row.get(src) or "").strip()
        if val:
            payload[dst] = val
    ct_date = (episode_row.get("ct_date") or "").strip()
    if ct_date:
        payload["CT日期"] = ct_date
    interval = (episode_row.get("ct_surgery_interval_days") or "").strip()
    if interval:
        payload["CT-手术间隔(天)"] = interval

    existing = db.query(PatientClinical).filter(PatientClinical.patient_id == patient_id).first()
    if existing:
        merged = dict(existing.payload or {})
        for k, v in payload.items():
            merged.setdefault(k, v)
        existing.payload = merged
        if not existing.name and name:
            existing.name = name
    else:
        db.add(PatientClinical(patient_id=patient_id, name=name or None, payload=payload))


def main():
    parser = argparse.ArgumentParser(description="按 manifest 摄入预标注批次")
    parser.add_argument("--name", default=DEFAULT_BATCH, help="新批次名")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 例（试跑用）；0=全量")
    parser.add_argument("--reuse-batch", action="store_true",
                        help="批次已存在时复用续跑（默认报错退出）；已注册序列自动跳过/仅关联")
    parser.add_argument("--manifest-dir", required=True, help="包含两份 CSV 清单的目录")
    parser.add_argument("--raw-root", required=True, help="原始 DICOM 根目录")
    parser.add_argument("--source-raw-prefix", help="可选：清单中需要映射的历史目录前缀")
    args = parser.parse_args()

    from app.database import SessionLocal
    from app.models.series import Series
    from app.ingestion.service import create_batch, ingest_into_batch

    manifest_dir = os.path.abspath(os.path.expanduser(args.manifest_dir))
    episode_status_csv = os.path.join(manifest_dir, "episode_status.csv")
    cohort_manifest_csv = os.path.join(manifest_dir, "cohort_manifest.csv")

    episodes = [r for r in _read_csv(episode_status_csv) if r["inventory_status"] == "done"]
    cohort = {r["episode_id"]: r for r in _read_csv(cohort_manifest_csv)}
    flagged = [r["episode_id"] for r in _read_csv(episode_status_csv) if r["inventory_status"] == "flagged"]
    print(f"manifest 纳入 {len(episodes)} 例（flagged 排除 {len(flagged)} 例）")
    if args.limit:
        episodes = episodes[: args.limit]
        print(f"试跑模式：只处理前 {len(episodes)} 例")

    db = SessionLocal()
    enqueue_ids = []
    report = {"ok": 0, "existing": 0, "missing_dir": [], "missing_series": [], "errors": []}
    try:
        from app.models.batch import Batch
        batch = db.query(Batch).filter(Batch.name == args.name).first()
        if batch and not args.reuse_batch:
            print(f"批次 {args.name!r} 已存在，退出（不合并不覆盖；续跑请加 --reuse-batch）")
            sys.exit(1)
        if not batch:
            batch = create_batch(
                db,
                args.name,
                description=f"{date.today().isoformat()} 按 manifest "
                            f"（episode_status inventory=done，沿用清单 CT 选择逻辑）摄入；"
                            f"raw 为 RawData symlink，未复制。",
            )
            # 批次行先落库：ingest_into_batch 每次调用末尾自带 commit，若首例异常 rollback
            # 会把未提交的 batch 一并回滚，后续迭代将持有失效的 batch 对象
            db.commit()

        for i, ep in enumerate(episodes, 1):
            eid = ep["episode_id"]
            series_uid = ep["selected_series_uid"].strip()
            cohort_row = cohort.get(eid)
            tag = f"[{i}/{len(episodes)}] {eid}"
            if not cohort_row or not cohort_row["ct_dir"].strip():
                report["missing_dir"].append(eid)
                print(f"{tag} 无 ct_dir，跳过")
                continue
            ct_dir = _map_ct_dir(cohort_row["ct_dir"].strip(), args.raw_root, args.source_raw_prefix)
            if not os.path.isdir(ct_dir):
                report["missing_dir"].append(eid)
                print(f"{tag} 目录不存在: {ct_dir}")
                continue

            try:
                result = ingest_into_batch(
                    db, batch, ct_dir,
                    only_series_uids={series_uid},
                    link_mode="symlink",
                )
            except Exception as e:
                db.rollback()
                report["errors"].append((eid, str(e)))
                print(f"{tag} 摄入异常: {e}")
                continue

            series = db.query(Series).filter(Series.series_uid == series_uid).first()
            if not series:
                report["missing_series"].append(eid)
                print(f"{tag} 清单序列未在目录中找到: {series_uid}")
                continue
            if series.id in result["to_enqueue"]:
                report["ok"] += 1
                enqueue_ids.append(series.id)
            else:
                # 已在 DB（测试批次重叠）且状态不可重跑：仅完成批次关联，不入队
                report["existing"] += 1

            _upsert_clinical(
                db, (cohort_row["patient_id"] or eid).strip(),
                (cohort_row.get("name") or "").strip(), cohort_row, ep,
            )
            db.commit()

            if i % 20 == 0 or i == len(episodes):
                print(f"{tag} 进度：已处理 {i}/{len(episodes)}，入队 {len(enqueue_ids)}")
    finally:
        db.close()

    if enqueue_ids:
        asyncio.run(_enqueue_all(enqueue_ids))
    print(f"\n完成：新摄入入队 {report['ok']}，已存在仅关联 {report['existing']}，"
          f"缺目录 {len(report['missing_dir'])}，缺序列 {len(report['missing_series'])}，异常 {len(report['errors'])}")

    # 报告落盘
    os.makedirs("logs", exist_ok=True)
    log_path = os.path.join("logs", f"ingest_cohort_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"批次: {args.name}\n纳入: {len(episodes)}（limit={args.limit}）\n")
        f.write(f"新摄入入队: {report['ok']}\n已存在仅关联: {report['existing']}\n")
        f.write(f"缺目录({len(report['missing_dir'])}): {report['missing_dir']}\n")
        f.write(f"缺序列({len(report['missing_series'])}): {report['missing_series']}\n")
        f.write(f"异常({len(report['errors'])}): {report['errors']}\n")
        f.write(f"flagged 未纳入({len(flagged)}): {flagged}\n")
    print(f"报告已写 {log_path}")


if __name__ == "__main__":
    main()
