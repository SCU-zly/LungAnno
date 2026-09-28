# HTJ2K 体数据压缩（查看器传输专用）

## 组成

- `encode_volume.py` — .nii.gz → .htj2k bundle 编码器（逐 slice OpenJPH 有损编码）
- `bin/ojph_compress`、`bin/ojph_expand` — OpenJPH 可执行文件（动态链接系统运行库）（x86_64 Linux）

## bundle 格式

```
magic "CTJ2K001" (8B) | header_len uint32LE | header JSON | 逐 slice .j2c 段
header: {shape[z,y,x], dtype, spacing[x,y,z], voi, qstep, count, offsets[], sizes[]}
```

## 量化步长

默认 `qstep=8e-05`。压缩比与显示误差取决于图像噪声和采集参数，
应使用自己的验证数据评估。推理始终读取无损 `.nii.gz`，压缩只用于查看器传输。

## 存量数据手动补编码

```bash
python3 deploy/htj2k/encode_volume.py <series.nii.gz> [-q 0.00008]
```

新数据由预处理自动产出（`HTJ2K_ENABLED` / `HTJ2K_QSTEP` 环境变量可配）。

## 重建二进制（如需）

```bash
docker run --rm -v $(pwd)/bin:/out debian:bookworm-slim bash -c "
  apt-get update -qq && apt-get install -y -qq git cmake g++ make ca-certificates &&
  git clone --depth 1 https://github.com/aous72/OpenJPH /src &&
  cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF &&
  cmake --build /build -j &&
  for t in ojph_compress ojph_expand; do find /build -name \$t -type f -exec cp {} /out/ \; ; done"
```

## 前端解码

`@cornerstonejs/codec-openjph`（npm，OpenJPH 官方 wasm 构建）：
`readHeader() → decode() → getDecodedBuffer()`，见 `frontend/src/lib/htj2kDecoder.ts`。
