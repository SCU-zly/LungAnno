"""导入远端预标注 bundle：远端已完成预处理 + DeepLung 检测，本地只登记入库。

bundle 契约（deploy/remote/run_remote_batch.py 产出，见该文件 docstring）：
  manifest.csv            每序列一行，status=ok 的才导入
  checksums.sha256        series/ 目录全部产物的 sha256（导入前校验）
  series/<uid>/volume.nii.gz  预处理体数据 → <storage>/<batch>/<study>/<series>/preprocessed/<uid>.nii.gz
  series/<uid>/meta.json      → <base>/meta.json（坐标映射依赖，schema 同 worker）
  series/<uid>/detect.json    DeepLung nodes → Detection 行（复用 inference 的映射函数）
  series/<uid>/info.json      patient/study 展示元数据

与在线摄入的差异：无 raw DICOM（series.raw_path 置 NULL），processing_status 直接
置 detected、review_status=not_reviewed。按 series_uid 判重，幂等可续跑。

用法（backend venv，cwd=backend）:
  python3 scripts/import_remote_bundle.py /path/to/bundle --name "远端预标注-xxx" [--limit 3]
  python3 scripts/import_remote_bundle.py /path/to/bundle --name "远端预标注-xxx" --reuse-batch --htj2k
"""
import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import SessionLocal
from app.models.batch import Batch
from app.models.study import Study
from app.models.series import Series
from app.models.detection import Detection
from app.models.batch_study import batch_studies
from app.storage.layout import ensure_series_dirs
from app.worker.inference import _deeplung_nodes_to_detections

from sqlalchemy import select


def _read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _verify_checksums(bundle):
    """sha256 清单校验（sha256sum -c 的纯 python 版）。失败文件列表；无清单则告警跳过。"""
    cks = os.path.join(bundle, "checksums.sha256")
    if not os.path.isfile(cks):
        print("警告：无 checksums.sha256，跳过完整性校验")
        return []
    bad = []
    with open(cks) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            digest, rel = line.split("  ", 1)
            fpath = os.path.join(bundle, rel)
            if not os.path.isfile(fpath):
                bad.append(rel + "（缺失）")
                continue
            h = hashlib.sha256()
            with open(fpath, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != digest:
                bad.append(rel)
    return bad


def _htj2k_encode(nifti_path):
    """查看器传输压缩（可选）：复用 deploy/htj2k/encode_volume.py，失败仅告警。"""
    encoder = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "deploy", "htj2k", "encode_volume.py")
    proc = subprocess.run([sys.executable, encoder, nifti_path, "-q", str(settings.htj2k_qstep)],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        print(f"  htj2k 编码失败（前端将回退 nifti 路径）: {proc.stderr[-200:]}")


def main():
    ap = argparse.ArgumentParser(description="导入远端预标注 bundle（预处理+检测均已在远端完成）")
    ap.add_argument("bundle", help="bundle 根目录")
    ap.add_argument("--name", required=True, help="批次名")
    ap.add_argument("--reuse-batch", action="store_true", help="批次已存在时复用续跑")
    ap.add_argument("--limit", type=int, default=0, help="只导入前 N 例（试跑）")
    ap.add_argument("--htj2k", action="store_true", help="导入后对每例跑 htj2k 编码（查看器加速）")
    ap.add_argument("--skip-checksum", action="store_true", help="跳过 sha256 校验（不建议）")
    args = ap.parse_args()

    bundle = os.path.abspath(args.bundle)
    manifest_path = os.path.join(bundle, "manifest.csv")
    if not os.path.isfile(manifest_path):
        print(f"manifest.csv 不存在: {manifest_path}")
        sys.exit(1)

    if not args.skip_checksum:
        bad = _verify_checksums(bundle)
        if bad:
            print(f"校验和不通过（{len(bad)} 个文件），中止导入: {bad[:10]}")
            sys.exit(1)
        print("sha256 校验通过")

    rows = [r for r in _read_csv(manifest_path) if r["status"] == "ok"]
    errored_remote = [r for r in _read_csv(manifest_path) if r["status"] != "ok"]
    if args.limit:
        rows = rows[: args.limit]
    print(f"manifest：可导入 {len(rows)} 例，远端失败 {len(errored_remote)} 例（不导入）")

    db = SessionLocal()
    report = {"ok": 0, "existing": 0, "errors": []}
    try:
        batch = db.query(Batch).filter(Batch.name == args.name).first()
        if batch and not args.reuse_batch:
            print(f"批次 {args.name!r} 已存在，退出（续跑请加 --reuse-batch）")
            sys.exit(1)
        if not batch:
            batch = Batch(name=args.name,
                          description=f"{datetime.now().date().isoformat()} 远端预标注 bundle 导入"
                                      f"（远端完成预处理+DeepLung 检测；本地无 raw DICOM）：{bundle}")
            db.add(batch)
            db.commit()

        for i, row in enumerate(rows, 1):
            series_uid = row["series_uid"].strip()
            tag = f"[{i}/{len(rows)}] {series_uid}"
            sdir = os.path.join(bundle, "series", series_uid)
            try:
                if db.query(Series).filter(Series.series_uid == series_uid).first():
                    report["existing"] += 1
                    print(f"{tag} 已存在，跳过")
                    continue
                with open(os.path.join(sdir, "meta.json")) as f:
                    meta = json.load(f)
                with open(os.path.join(sdir, "detect.json")) as f:
                    nodes = json.load(f).get("nodes", [])
                with open(os.path.join(sdir, "info.json")) as f:
                    info = json.load(f)

                study_uid = info["study_uid"]
                study = db.query(Study).filter(Study.study_uid == study_uid).first()
                if not study:
                    study = Study(study_uid=study_uid,
                                  patient_id=info.get("patient_id") or "unknown",
                                  patient_name=info.get("patient_name") or None,
                                  study_date=info.get("study_date") or None)
                    db.add(study)
                    db.flush()
                else:
                    if info.get("patient_name"):
                        study.patient_name = info["patient_name"]
                    if info.get("study_date"):
                        study.study_date = info["study_date"]

                linked = db.execute(
                    select(batch_studies).where(
                        batch_studies.c.batch_id == batch.id,
                        batch_studies.c.study_id == study.id)).first()
                if not linked:
                    db.execute(batch_studies.insert().values(batch_id=batch.id, study_id=study.id))

                dirs = ensure_series_dirs(batch.id, study_uid, series_uid)
                nifti_dest = os.path.join(dirs["preprocessed_path"], f"{series_uid}.nii.gz")
                if not os.path.isfile(nifti_dest):
                    shutil.copy2(os.path.join(sdir, "volume.nii.gz"), nifti_dest)
                meta_dest = os.path.join(dirs["base"], "meta.json")
                if not os.path.isfile(meta_dest):
                    shutil.copy2(os.path.join(sdir, "meta.json"), meta_dest)

                series = Series(
                    series_uid=series_uid,
                    study_id=study.id,
                    raw_path=None,  # 远端模式：本地无 raw DICOM
                    preprocessed_path=nifti_dest,
                    meta_path=meta_dest,
                    slice_thickness=float(row["slice_thickness"]) if row.get("slice_thickness") else None,
                    series_description=(row.get("series_description") or "")[:128] or None,
                    processing_status="detected",
                    review_status="not_reviewed",
                )
                db.add(series)
                db.flush()

                for det in _deeplung_nodes_to_detections(nodes, meta):
                    db.add(Detection(
                        series_id=series.id,
                        score=det["score"],
                        box_x=det["box"][0], box_y=det["box"][1], box_z=det["box"][2],
                        box_w=det["box"][3], box_h=det["box"][4], box_d=det["box"][5],
                        voxel_x=det["voxel"][0], voxel_y=det["voxel"][1], voxel_z=det["voxel"][2],
                        voxel_w=det["voxel"][3], voxel_h=det["voxel"][4], voxel_d=det["voxel"][5],
                        slice_index=det["slice_index"],
                        label=det["label"],
                        source="deeplung",
                    ))
                db.commit()
                report["ok"] += 1
                if args.htj2k:
                    _htj2k_encode(nifti_dest)
                if i % 20 == 0 or i == len(rows):
                    print(f"{tag} 进度 {i}/{len(rows)}")
            except Exception as e:
                db.rollback()
                report["errors"].append((series_uid, str(e)))
                print(f"{tag} 导入异常: {e}")
    finally:
        db.close()

    print(f"\n完成：导入 {report['ok']}，已存在跳过 {report['existing']}，异常 {len(report['errors'])}")
    os.makedirs("logs", exist_ok=True)
    log_path = os.path.join("logs", f"import_remote_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"批次: {args.name}\nbundle: {bundle}\n导入: {report['ok']}\n已存在: {report['existing']}\n")
        f.write(f"异常({len(report['errors'])}): {report['errors']}\n")
        f.write(f"远端失败未导入({len(errored_remote)}): "
                f"{[(r['series_uid'], r['error_reason']) for r in errored_remote]}\n")
    print(f"报告已写 {log_path}")


if __name__ == "__main__":
    main()
