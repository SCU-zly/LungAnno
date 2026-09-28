"""DeepLung 检测 runner（容器内执行，宿主代码以只读挂载注入）。

用法:
  python3.6 run_detect.py <dicom_dir> <ckpt> <out.json> [gpu]

输出 JSON:
  {"nodes": [[conf, z, y, x, d_mm], ...], "img_shape": [z,y,x], "spacing": [z,y,x], "elapsed_sec": t}
坐标含义（pbb2axis 约定）：z/y/x 为原始 DICOM 体素索引（整数网格），d_mm 为直径 mm。
"""
import sys
import json
import time

sys.path.insert(0, "/deeplung/ProcByModel")

from wjydetector.detector import LNDetector, Args


def main():
    dicom_dir, ckpt, out = sys.argv[1], sys.argv[2], sys.argv[3]
    gpu = sys.argv[4] if len(sys.argv) > 4 else None
    t0 = time.time()
    det = LNDetector(Args(ckpt, gpu=gpu, batch_size=1, method="lfz"))
    nodes, img_shape, spacing = det(dicom_dir)
    payload = {
        "nodes": [[float(v) for v in row] for row in nodes],
        "img_shape": [int(v) for v in img_shape],
        "spacing": [float(v) for v in spacing],
        "elapsed_sec": round(time.time() - t0, 2),
    }
    with open(out, "w") as f:
        json.dump(payload, f)
    print("OK {} nodes, {}s".format(len(nodes), payload["elapsed_sec"]))


if __name__ == "__main__":
    main()
