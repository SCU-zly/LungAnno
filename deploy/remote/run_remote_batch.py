#!/usr/bin/env python3
"""远端批量驱动：扫描原始 DICOM → 筛选薄层 CT → 预处理 + DeepLung 检测 → 打包 bundle。

只依赖宿host python3 + pydicom（pip install --user pydicom）；重活全在 docker 里：
  - 预处理容器 ctanno-preproc:1.0（由 Dockerfile.preproc 构建，内含 preprocess_series.py）
  - DeepLung 容器（老 torch1.0 镜像，需自行准备）

bundle 产物布局（与本地导入脚本 import_remote_bundle.py 的约定，勿擅改）:
  <bundle>/manifest.csv                 每序列一行（含 status/error_reason），utf-8-sig
  <bundle>/skipped.csv                  被筛选剔除的序列 + 原因
  <bundle>/checksums.sha256             对 series/ 全部产物的 sha256（传输后校验）
  <bundle>/series/<series_uid>/volume.nii.gz   预处理体数据（int16, 目标网格）
  <bundle>/series/<series_uid>/meta.json       坐标映射元数据（schema 同后端 worker）
  <bundle>/series/<series_uid>/detect.json     DeepLung 原始 nodes 输出
  <bundle>/series/<series_uid>/info.json       patient/study/series 展示元数据
  <bundle>/series/<series_uid>/error.txt       失败序列的错误信息（status=error 时）

断点续跑：四个产物齐全的序列自动跳过；重跑同一命令即可。
用法:
  python3 run_remote_batch.py --input-root <原始数据根> --out <bundle目录> \
      --code-dir <ProcByModel路径> --deeplung-image <镜像> --ckpt <容器内权重路径> [--limit 3] [--gpu auto|cpu|N]
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys


_UI_PATTERN = re.compile(r"^[0-9.]{1,64}$")  # DICOM UID 白名单（ISS-007 同口径）
DEFAULT_SPACING = "0.703125,0.703125,1.25"  # 与后端 target_spacing 一致


def log(msg):
    print(msg, flush=True)


def scan_dicoms(root):
    """递归扫描，按 (study_uid, series_uid) 聚合。返回 {(study_uid, series_uid): info}。"""
    import pydicom

    groups = {}
    for dirpath, _, files in os.walk(root):
        for fname in files:
            fpath = os.path.join(dirpath, fname)
            try:
                ds = pydicom.dcmread(fpath, stop_before_pixels=True)
                study_uid = str(ds.StudyInstanceUID)
                series_uid = str(ds.SeriesInstanceUID)
                if not (_UI_PATTERN.match(study_uid) and _UI_PATTERN.match(series_uid)):
                    continue
                g = groups.setdefault((study_uid, series_uid), {
                    "files": [], "patient_id": "", "patient_name": "", "study_date": "",
                    "series_description": "", "modality": "", "slice_thickness": None,
                })
                g["files"].append(fpath)
                if not g["patient_id"]:
                    g["patient_id"] = str(getattr(ds, "PatientID", "unknown"))[:128]
                    g["patient_name"] = str(getattr(ds, "PatientName", "") or "")[:128]
                    g["study_date"] = str(getattr(ds, "StudyDate", "") or "")[:8]
                    g["modality"] = str(getattr(ds, "Modality", "") or "")
                    g["series_description"] = str(getattr(ds, "SeriesDescription", "") or "")[:128]
                    try:
                        th = float(getattr(ds, "SliceThickness", 0) or 0)
                    except (TypeError, ValueError):
                        th = 0.0
                    g["slice_thickness"] = th if th > 0 else None
            except Exception:
                continue
    return groups


def skip_reason(info, max_thickness, min_instances):
    """返回剔除原因；合格返回 None。"""
    if info["modality"] and info["modality"] != "CT":
        return "Modality={} 非 CT".format(info["modality"])
    if info["slice_thickness"] is None:
        return "层厚缺失"
    if info["slice_thickness"] > max_thickness:
        return "层厚 {}mm > {}mm".format(info["slice_thickness"], max_thickness)
    if len(info["files"]) < min_instances:
        return "实例数 {} < {}".format(len(info["files"]), min_instances)
    return None


def pick_gpu():
    """nvidia-smi 选显存占用最低的卡；失败返回 None（CPU 兜底）。"""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
            universal_newlines=True)
        best, best_mem = None, None
        for line in out.strip().splitlines():
            idx, mem = [int(v.strip()) for v in line.split(",")]
            if best_mem is None or mem < best_mem:
                best, best_mem = str(idx), mem
        return best
    except Exception:
        return None


def stage_series(files, staging_dir):
    """把序列文件硬链接进 staging 目录（DeepLung 容器需要单序列目录）；失败退化为复制。"""
    os.makedirs(staging_dir, exist_ok=True)
    for f in files:
        dest = os.path.join(staging_dir, os.path.basename(f))
        if os.path.lexists(dest):
            continue
        try:
            os.link(f, dest)
        except OSError:
            shutil.copy2(f, dest)


def run_cmd(cmd, timeout, log_path):
    """跑子进程，stdout/stderr 落 log；返回 (returncode, 尾部输出)。"""
    with open(log_path, "ab") as lf:
        lf.write(("\n$ " + " ".join(cmd) + "\n").encode())
        lf.flush()
        proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode


def sha256_tree(root, out_csv):
    """对 root 下所有文件生成 sha256sum 兼容清单（相对路径）。"""
    rows = []
    for dirpath, _, files in os.walk(root):
        for fname in sorted(files):
            fpath = os.path.join(dirpath, fname)
            h = hashlib.sha256()
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            rows.append((h.hexdigest(), os.path.relpath(fpath, os.path.dirname(root))))
    rows.sort(key=lambda r: r[1])
    with open(out_csv, "w") as f:
        for digest, rel in rows:
            f.write("{}  {}\n".format(digest, rel))


MANIFEST_FIELDS = ["patient_id", "patient_name", "study_uid", "series_uid", "study_date",
                   "series_description", "slice_thickness", "instance_count", "status", "error_reason"]


def list_gpu_indices():
    """全部 GPU 序号（nvidia-smi）；无 GPU/查询失败返回 []。"""
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
                                      universal_newlines=True)
        return [line.strip() for line in out.strip().splitlines() if line.strip()]
    except Exception:
        return []


def resolve_gpu(spec):
    """解析 --gpu：cpu→None；auto→最闲卡；all→最闲卡（单卡兜底；多卡分片见 main）；数字→固定卡。"""
    spec = str(spec).strip().lower()
    if spec == "cpu":
        return None
    if spec in ("auto", "all"):
        return pick_gpu()
    return spec


def run_shard(args, eligible, gpu, bundle, manifest_name):
    """处理一个序列分片：逐序列预处理+检测，写 manifest 文件，返回行列表。"""
    uid_gid = "{}:{}".format(os.getuid(), os.getgid())
    series_root = os.path.join(bundle, "series")
    staging_root = os.path.join(bundle, "_staging")
    os.makedirs(series_root, exist_ok=True)

    manifest_rows = []
    ok = err = 0
    for i, (study_uid, series_uid, info) in enumerate(eligible, 1):
        tag = "[{}/{}] {}".format(i, len(eligible), series_uid)
        sdir = os.path.join(series_root, series_uid)
        os.makedirs(sdir, exist_ok=True)
        row = {
            "patient_id": info["patient_id"], "patient_name": info["patient_name"],
            "study_uid": study_uid, "series_uid": series_uid, "study_date": info["study_date"],
            "series_description": info["series_description"], "slice_thickness": info["slice_thickness"],
            "instance_count": len(info["files"]), "status": "", "error_reason": "",
        }
        products = [os.path.join(sdir, p) for p in ("volume.nii.gz", "meta.json", "detect.json", "info.json")]
        if all(os.path.isfile(p) for p in products):
            row["status"] = "ok"
            ok += 1
            log("{} 产物齐全，跳过（断点续跑）".format(tag))
            manifest_rows.append(row)
            continue

        staging = os.path.join(staging_root, series_uid)
        log_path = os.path.join(sdir, "run.log")
        try:
            stage_series(info["files"], staging)

            # 1) 预处理（preproc 容器）
            rc = run_cmd([
                "docker", "run", "--rm", "--user", uid_gid,
                "-v", "{}:/input:ro".format(staging), "-v", "{}:/work".format(sdir),
                args.preproc_image,
                "python3", "/opt/preprocess_series.py", "/input", "/work", "--spacing", args.spacing,
            ], args.timeout, log_path)
            if rc != 0:
                raise RuntimeError("预处理失败 rc={}（详见 run.log）".format(rc))

            # 2) DeepLung 检测（老 torch1.0 容器，命令与本地 inference.py 同构）
            cmd = ["docker", "run", "--rm", "--entrypoint", "python3.6"]
            if gpu is not None:
                cmd += ["--gpus", "device={}".format(gpu)]
            cmd += [
                "-e", "DEEPLUNG_LOGIT_THRESH={}".format(args.logit_thresh),
                "-e", "DEEPLUNG_MAX_NODES={}".format(args.max_nodes),
                "-e", "HOME=/tmp",
                "-v", "{}:/deeplung/ProcByModel:ro".format(args.code_dir),
                "-v", "{}:/deeplung-run:ro".format(os.path.abspath(args.runner_dir)),
                "-v", "{}:/input:ro".format(staging),
                "-v", "{}:/out".format(sdir),
                args.deeplung_image,
                "/deeplung-run/run_detect.py", "/input", args.ckpt, "/out/detect.json",
            ]
            if gpu is not None:
                cmd.append(gpu)
            rc = run_cmd(cmd, args.timeout, log_path)
            if rc != 0 or not os.path.isfile(os.path.join(sdir, "detect.json")):
                raise RuntimeError("DeepLung 检测失败 rc={}（详见 run.log；肺掩膜体积校验失败为确定性失败，勿重跑）".format(rc))

            with open(os.path.join(sdir, "info.json"), "w") as f:
                json.dump({k: row[k] for k in (
                    "patient_id", "patient_name", "study_uid", "series_uid",
                    "study_date", "series_description", "slice_thickness", "instance_count")}, f,
                    ensure_ascii=False, indent=2)
            row["status"] = "ok"
            ok += 1
            log("{} OK".format(tag))
        except Exception as e:
            row["status"] = "error"
            row["error_reason"] = str(e)[:500]
            with open(os.path.join(sdir, "error.txt"), "w") as f:
                f.write(str(e) + "\n")
            err += 1
            log("{} ERROR: {}".format(tag, e))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        manifest_rows.append(row)

    with open(os.path.join(bundle, manifest_name), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(manifest_rows)
    log("本分片完成：ok={} error={}".format(ok, err))
    return manifest_rows


def _strip_gpu_arg(argv):
    """从 argv 中移除 --gpu 及其值（子进程重定向到具体卡号用）。"""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--gpu":
            skip = True
            continue
        if a.startswith("--gpu="):
            continue
        out.append(a)
    return out


def main():
    ap = argparse.ArgumentParser(description="远端预处理+DeepLung 检测打包")
    ap.add_argument("--input-root", required=True, help="原始 DICOM 数据根目录（任意嵌套）")
    ap.add_argument("--out", required=True, help="bundle 输出目录")
    ap.add_argument("--code-dir", required=True, help="DeepLung ProcByModel 目录（含 wjydetector/ 与 ckpts/your-weights.ckpt）")
    ap.add_argument("--runner-dir", default=os.path.dirname(os.path.abspath(__file__)),
                    help="run_detect.py 所在目录（默认取本脚本同目录）")
    ap.add_argument("--deeplung-image", required=True)
    ap.add_argument("--preproc-image", default="ctanno-preproc:1.0")
    ap.add_argument("--ckpt", required=True, help="容器内权重路径")
    ap.add_argument("--spacing", default=DEFAULT_SPACING)
    ap.add_argument("--max-thickness", type=float, default=1.5)
    ap.add_argument("--min-instances", type=int, default=50)
    ap.add_argument("--logit-thresh", default="1", help="检测阈值（按模型设置 DEEPLUNG_LOGIT_THRESH）")
    ap.add_argument("--max-nodes", default="10")
    ap.add_argument("--gpu", default="all", help="all=占满全部卡（默认）|auto=最闲单卡|cpu|卡号")
    ap.add_argument("--timeout", type=int, default=1800, help="单序列单阶段超时秒")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个合格序列（试跑）")
    # 内部参数：多卡分片子进程用，勿手动指定
    ap.add_argument("--shard", default="0/1", help=argparse.SUPPRESS)
    ap.add_argument("--shard-tag", default="", help=argparse.SUPPRESS)
    args = ap.parse_args()

    bundle = os.path.abspath(args.out)
    os.makedirs(os.path.join(bundle, "series"), exist_ok=True)

    log("扫描 {} ...".format(args.input_root))
    groups = scan_dicoms(args.input_root)
    log("共 {} 个序列".format(len(groups)))

    eligible, skipped = [], []
    for (study_uid, series_uid), info in sorted(groups.items()):
        reason = skip_reason(info, args.max_thickness, args.min_instances)
        if reason:
            skipped.append({"series_uid": series_uid, "reason": reason})
        else:
            eligible.append((study_uid, series_uid, info))
    log("合格 {} 个，剔除 {} 个".format(len(eligible), len(skipped)))
    if args.limit:
        eligible = eligible[:args.limit]
        log("试跑模式：只处理前 {} 个".format(len(eligible)))

    # 子进程（多卡分片）：只跑自己那片，写 manifest.<tag>.csv 后退出
    if args.shard != "0/1":
        idx, total = [int(v) for v in args.shard.split("/")]
        gpu = resolve_gpu(args.gpu)
        run_shard(args, eligible[idx::total], gpu, bundle,
                  "manifest.{}.csv".format(args.shard_tag or "shard{}".format(idx)))
        return

    # 父进程：skipped.csv 只在这里写
    with open(os.path.join(bundle, "skipped.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["series_uid", "reason"])
        w.writeheader()
        w.writerows(skipped)

    gpus = list_gpu_indices()
    if args.gpu.strip().lower() == "all" and len(gpus) > 1 and eligible:
        # 多卡并行：每卡一个子进程，交错分片，日志各落 _shard<i>.log
        log("多卡模式：{} 张卡全部启用（{}）".format(len(gpus), ",".join(gpus)))
        procs = []
        for i, g in enumerate(gpus):
            argv = _strip_gpu_arg(sys.argv[1:]) + ["--gpu", g, "--shard", "{}/{}".format(i, len(gpus)),
                                                   "--shard-tag", "shard{}".format(i)]
            logf = open(os.path.join(bundle, "_shard{}.log".format(i)), "w")
            procs.append(subprocess.Popen([sys.executable, os.path.abspath(__file__)] + argv,
                                          stdout=logf, stderr=subprocess.STDOUT))
            log("  分片 {} → GPU {}（日志 _shard{}.log）".format(i, g, i))
        rcs = [p.wait() for p in procs]
        # 合并分片 manifest
        rows = []
        for i in range(len(gpus)):
            mpath = os.path.join(bundle, "manifest.shard{}.csv".format(i))
            if os.path.isfile(mpath):
                with open(mpath, encoding="utf-8-sig", newline="") as f:
                    rows.extend(csv.DictReader(f))
        rows.sort(key=lambda r: r["series_uid"])
        with open(os.path.join(bundle, "manifest.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            w.writeheader()
            w.writerows(rows)
        ok = sum(1 for r in rows if r["status"] == "ok")
        err = sum(1 for r in rows if r["status"] == "error")
        log("分片退出码 {}；合并 manifest：ok={} error={}".format(rcs, ok, err))
    else:
        gpu = resolve_gpu(args.gpu)
        log("单卡/兜底模式：gpu={}".format(gpu or "cpu"))
        rows = run_shard(args, eligible, gpu, bundle, "manifest.csv")
        ok = sum(1 for r in rows if r["status"] == "ok")
        err = sum(1 for r in rows if r["status"] == "error")

    sha256_tree(os.path.join(bundle, "series"), os.path.join(bundle, "checksums.sha256"))
    log("完成：ok={} error={} skipped={}，bundle={}".format(ok, err, len(skipped), bundle))


if __name__ == "__main__":
    main()
