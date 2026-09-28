"""独立版序列预处理：DICOM 目录 → volume.nii.gz + meta.json。

与 DeepAnno 后端 app/worker/preprocess.py 的算法逐项对齐（重采样目标网格、
int16 落盘、meta.json schema），供远端服务器在无后端/无 DB 环境下产出
可直接导入本系统的预处理产物。坐标映射依赖 meta.json 的
original_spacing / spacing / preprocessed_shape，schema 不可擅改。

用法:
  python3 preprocess_series.py <dicom_dir> <out_dir> [--spacing x,y,z] [--lungmask]

产物（out_dir 下）:
  volume.nii.gz  int16、target_spacing 网格（gzip 压缩，公网传输友好）
  meta.json      {"spacing","original_spacing","original_shape",
                  "preprocessed_shape","affine","hu_window"}

退出码: 0=成功；2=有效切片不足（非体积 CT）；1=其他错误。
lungmask 为可选项（仅影响显示体数据的肺外置零，不影响结节坐标）；
远端无 lungmask 环境时不加 --lungmask 即可。
"""
import json
import os
import sys

import numpy as np
import pydicom
import SimpleITK as sitk

DEFAULT_SPACING = [0.703125, 0.703125, 1.25]  # 与 backend app/config.py target_spacing 一致
MIN_SLICES = 8  # 与 worker 一致：scout/截图等小序列直接拦截


def read_slices(dcm_files):
    """读取 DICOM 切片及排序/间距所需几何标签（同 worker._read_slices）。"""
    slices = []
    for fp in dcm_files:
        try:
            ds = pydicom.dcmread(fp)
            slope = float(getattr(ds, "RescaleSlope", 1))
            intercept = float(getattr(ds, "RescaleIntercept", 0))
            img = ds.pixel_array.astype(np.float32)
            img = slope * img + intercept  # HU
            if getattr(ds, "PhotometricInterpretation", "") == "MONOCHROME1":
                img = -img
            ipp = getattr(ds, "ImagePositionPatient", None)
            ps = getattr(ds, "PixelSpacing", None)
            thickness = getattr(ds, "SliceThickness", None)
            slices.append({
                "img": img,
                "position": [float(v) for v in ipp] if ipp is not None else None,
                "instance": getattr(ds, "InstanceNumber", None),
                "pixel_spacing": [float(v) for v in ps] if ps is not None else None,
                "slice_thickness": float(thickness) if thickness else None,
            })
        except Exception:
            continue
    return slices


def scan_axis(positions):
    """ImagePositionPatient 主变化轴（同 worker._scan_axis）。"""
    spans = positions.max(axis=0) - positions.min(axis=0)
    return int(np.argmax(spans))


def sort_slices(slices):
    """按扫描轴位置排序（同 worker._sort_slices）。"""
    if len(slices) < 2:
        return
    if all(s["position"] is not None for s in slices):
        axis = scan_axis(np.array([s["position"] for s in slices]))
        slices.sort(key=lambda s: s["position"][axis])
    elif all(s["instance"] is not None for s in slices):
        slices.sort(key=lambda s: int(s["instance"]))


def original_spacing(slices):
    """由几何标签推 (x, y, z) 间距 mm（同 worker._original_spacing）。"""
    ps = next((s["pixel_spacing"] for s in slices if s["pixel_spacing"]), None)
    row_sp, col_sp = (ps[0], ps[1]) if ps else (1.0, 1.0)
    z_sp = None
    if len(slices) >= 2 and all(s["position"] is not None for s in slices):
        axis = scan_axis(np.array([s["position"] for s in slices]))
        coords = sorted(s["position"][axis] for s in slices)
        gaps = np.diff(coords)
        gaps = gaps[gaps > 0]
        if len(gaps):
            z_sp = float(np.median(gaps))
    if z_sp is None:
        z_sp = next((s["slice_thickness"] for s in slices if s["slice_thickness"]), 1.0)
    return (col_sp, row_sp, z_sp)  # SimpleITK 序: (x, y, z)


def preprocess_dir(dcm_dir, out_dir, target_sp, use_lungmask=False):
    """预处理一个序列目录，写 volume.nii.gz + meta.json。返回 meta dict。"""
    dcm_files = sorted(os.path.join(dcm_dir, f) for f in os.listdir(dcm_dir) if f.endswith(".dcm"))
    if not dcm_files:
        dcm_files = sorted(os.path.join(dcm_dir, f) for f in os.listdir(dcm_dir))

    slices = read_slices(dcm_files)
    if not slices:
        raise RuntimeError("No valid DICOM slices")
    if len(slices) < MIN_SLICES:
        raise TooFewSlicesError("仅 {} 张有效切片（<{}），非体积 CT 序列".format(len(slices), MIN_SLICES))

    sort_slices(slices)
    osp = original_spacing(slices)

    volume = np.stack([s["img"] for s in slices], axis=0)
    original_shape = volume.shape  # numpy (z, y, x)

    sitk_vol = sitk.GetImageFromArray(volume.astype(np.float32))
    sitk_vol.SetSpacing(osp)
    in_size = sitk_vol.GetSize()
    new_size = [max(1, int(round(in_size[i] * osp[i] / target_sp[i]))) for i in range(3)]
    resampled = sitk.Resample(sitk_vol, new_size, sitk.Transform(), sitk.sitkLinear,
                              sitk_vol.GetOrigin(), target_sp, sitk_vol.GetDirection(),
                              0.0, sitk.sitkFloat32)
    preprocessed = sitk.GetArrayFromImage(resampled).astype(np.float32)

    if use_lungmask:
        try:
            from lungmask import LMInferer
            inferer = LMInferer()
            lm_input = np.clip((preprocessed + 1024) / 1324 * 255, 0, 255).astype(np.uint8)
            lung_mask = inferer.apply(lm_input[np.newaxis, ...])[0]
            preprocessed[lung_mask == 0] = -1024
        except Exception as e:
            print("lungmask 失败，跳过（不影响结节坐标）: {}".format(e), file=sys.stderr)

    os.makedirs(out_dir, exist_ok=True)
    nifti_path = os.path.join(out_dir, "volume.nii.gz")
    out_img = sitk.GetImageFromArray(preprocessed)
    out_img.SetSpacing(target_sp)
    out_img = sitk.Cast(out_img, sitk.sitkInt16)  # int16：体积减半且 gzip 更优
    sitk.WriteImage(out_img, nifti_path)

    meta = {
        "spacing": list(target_sp),
        "original_spacing": list(osp),
        "original_shape": list(original_shape),
        "preprocessed_shape": list(preprocessed.shape),
        "affine": "identity",
        "hu_window": [-1024, 300],
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return meta


class TooFewSlicesError(RuntimeError):
    pass


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    dcm_dir, out_dir = args
    target_sp = DEFAULT_SPACING
    for i, a in enumerate(sys.argv):
        if a == "--spacing" and i + 1 < len(sys.argv):
            target_sp = [float(v) for v in sys.argv[i + 1].split(",")]
    use_lungmask = "--lungmask" in sys.argv

    try:
        meta = preprocess_dir(dcm_dir, out_dir, target_sp, use_lungmask)
    except TooFewSlicesError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    print("OK shape={} osp={}".format(meta["preprocessed_shape"], meta["original_spacing"]))


if __name__ == "__main__":
    main()
