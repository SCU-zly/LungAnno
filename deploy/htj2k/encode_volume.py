"""HTJ2K 体数据编码器：.nii.gz → .htj2k bundle（逐 slice OpenJPH 有损编码）。

用法:
  python3 deploy/htj2k/encode_volume.py <nii.gz 路径> [-q QSTEP]

产物: 同目录 <series_uid>.htj2k，布局:
  magic "CTJ2K001" (8B) | header_len uint32LE | header JSON | 逐 slice .j2c 段
  header: {shape[z,y,x], dtype, spacing[x,y,z], voi:{windowCenter,windowWidth},
           qstep, count, offsets[], sizes[]}

qstep 甜点（16bit signed CT，series 7 实测）:
  8e-05  → 9.0x（148MB→16.3MB）max_err ~11 HU（默认，视觉无损）
  1.6e-04 → 15.7x（→9.4MB）max_err ~22 HU（p99 ~10 HU，更激进）
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np
import SimpleITK as sitk

OJPH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ojph_compress")
MAGIC = b"CTJ2K001"
DEFAULT_QSTEP = 8e-05


def encode_slice(slice_i16: np.ndarray, qstep: float, workdir: str) -> bytes:
    h, w = slice_i16.shape
    raw = os.path.join(workdir, "s.raw")
    j2c = os.path.join(workdir, "s.j2c")
    slice_i16.astype("<i2").tofile(raw)
    subprocess.run(
        [OJPH, "-i", raw, "-o", j2c, "-dims", f"{{{w},{h}}}",
         "-num_comps", "1", "-signed", "true", "-bit_depth", "16",
         "-downsamp", "{1,1}", "-reversible", "false", "-qstep", str(qstep)],
        check=True, capture_output=True,
    )
    with open(j2c, "rb") as f:
        return f.read()


def encode_volume(nii_path: str, qstep: float = DEFAULT_QSTEP) -> str:
    img = sitk.ReadImage(nii_path)
    vol = sitk.GetArrayFromImage(img)  # [z,y,x] int16
    if vol.dtype != np.int16:
        vol = vol.astype(np.int16)
    zdim, h, w = vol.shape
    spacing = list(img.GetSpacing())  # [x,y,z]

    # VOI（与 nifti loader 的 makeVolumeMetadata 同法：中层 min/max）
    mid = vol[zdim // 2]
    voi = {"windowCenter": float((int(mid.max()) + int(mid.min())) / 2),
           "windowWidth": float(int(mid.max()) - int(mid.min()))}

    segments, offsets, sizes = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for z in range(zdim):
            seg = encode_slice(np.ascontiguousarray(vol[z]), qstep, td)
            offsets.append(sum(sizes))
            sizes.append(len(seg))
            segments.append(seg)

    header = {
        "shape": [int(zdim), int(h), int(w)],
        "dtype": "int16le",
        "spacing": [float(v) for v in spacing],
        "voi": voi,
        "qstep": qstep,
        "count": int(zdim),
        "offsets": offsets,
        "sizes": sizes,
    }
    header_bytes = json.dumps(header, separators=(",", ":")).encode()

    out_path = nii_path[:-7] + ".htj2k" if nii_path.endswith(".nii.gz") else nii_path + ".htj2k"
    with open(out_path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<I", len(header_bytes)))
        f.write(header_bytes)
        for seg in segments:
            f.write(seg)

    raw_mb = vol.nbytes / 1048576
    out_mb = os.path.getsize(out_path) / 1048576
    print(f"{os.path.basename(out_path)}: {raw_mb:.0f}MB -> {out_mb:.1f}MB ({raw_mb/out_mb:.1f}x, qstep={qstep})")
    return out_path


if __name__ == "__main__":
    q = float(sys.argv[sys.argv.index("-q") + 1]) if "-q" in sys.argv else DEFAULT_QSTEP
    encode_volume(sys.argv[1], q)
