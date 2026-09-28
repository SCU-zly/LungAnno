# 远端预处理与检测

此目录提供远端 DICOM 预处理、DeepLung 检测及 bundle 打包脚本。推理代码、模型权重、镜像与数据需自行准备，不随仓库提供。所有路径、镜像名和批次名均为占位示例。

## 准备与运行

宿主机需要 Python、`pydicom` 和 Docker。GPU 模式还需配置容器 GPU 运行环境。`--code-dir` 目录须包含兼容的 `wjydetector/` 和权重。权重通过容器内路径 `--ckpt` 显式指定。

在本目录构建预处理镜像：

```bash
python -m pip install pydicom
docker build -f Dockerfile.preproc -t ctanno-preproc:1.0 .
python run_remote_batch.py \
  --input-root /path/to/dicom \
  --out /path/to/output-bundle \
  --code-dir /path/to/ProcByModel \
  --deeplung-image your-deeplung-image:tag \
  --ckpt /deeplung/ProcByModel/ckpts/your-weights.ckpt \
  --gpu auto --limit 3
```

先在小批次验证输入和产物，再扩大范围。脚本扫描整个 `--input-root`；`--limit` 只限制处理数，不限制扫描范围。共享或受限磁盘应传入一个小批次目录。默认 `--gpu all` 会使用多卡并行，应在共享环境中显式使用 `--gpu auto`、指定卡号或 `--gpu cpu`。

默认筛选 CT、层厚 ≤1.5 mm、实例数 ≥50；参数可通过 `--help` 查看。阈值 `--logit-thresh` 默认 1，与在线配置默认值可能不同，请按所用模型及验证方案显式设置。产物齐全的序列会跳过，重跑同一命令可继续处理。

## bundle 格式

```text
bundle/
  manifest.csv              每序列状态与错误原因
  skipped.csv               被筛选剔除的序列
  checksums.sha256          series/ 下文件的校验和
  series/<series_uid>/
    volume.nii.gz           预处理体数据
    meta.json               坐标映射元数据
    detect.json             DeepLung nodes
    info.json               患者、检查和序列元数据
    error.txt / run.log     失败记录与日志（如有）
```

此 bundle 包含患者信息，不能上传到公开代码仓库。传输前评估容量，在数据盘上串行、分批、限速传输。

## 导入

在目标系统的 `backend/` 目录、已激活虚拟环境时运行：

```bash
python scripts/import_remote_bundle.py /path/to/output-bundle --name 示例批次 --limit 3
python scripts/import_remote_bundle.py /path/to/output-bundle --name 示例批次 --reuse-batch --htj2k
```

提供校验清单时，导入前校验 SHA-256；清单缺失时当前脚本仅告警并跳过，因此应先确认清单已随包传输；仅导入成功序列，按序列 UID 去重。导入结果为待审核状态，坐标映射复用在线推理逻辑；报告写入 `backend/logs/`。该导入脚本直接写入存储，应单独安排小批次执行，避免与在线摄入争用磁盘。
