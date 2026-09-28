# DeepLung 古早检测模型运行环境（torch 1.0.0 / py3.6 / CUDA 10.2）
# 注：DeepLung 权重与推理代码为本中心内部资产，不随仓库发布；以下 <…> 路径按内部部署约定替换。
#
# 构建（构建上下文指向内部代码目录，wheel 来自其中，不入 git）：
#   docker build -f deploy/deeplung/Dockerfile <内部 ProcByModel 目录> -t deeplung:1.0
#
# 运行（CPU 兜底路径，代码自带 gpu=None 分支）：
#   docker run --rm -v <内部代码目录>:/deeplung:ro \
#     -v <序列 DICOM 目录>:/input:ro -v <输出目录>:/out \
#     deeplung:1.0 python3.6 /deeplung/../deeplung-run/run_detect.py /input/<series_dir> /deeplung/ckpts/<权重>.ckpt /out/<name>.json
FROM nvidia/cuda:10.2-cudnn7-devel-ubuntu18.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.6 python3.6-dev python3-pip python3-setuptools python3-wheel \
    && rm -rf /var/lib/apt/lists/*

# py3.6 兼容的最后一个 pip
RUN python3.6 -m pip install --no-cache-dir "pip==20.3.4"

# 科学计算栈钉版：np.int 需要 numpy<1.20；scipy.ndimage.filters 需要 scipy<1.8；
# py3.6 的末代兼容版本
RUN python3.6 -m pip install --no-cache-dir \
        numpy==1.19.5 \
        scipy==1.5.4 \
        scikit-image==0.17.2 \
        SimpleITK==1.2.4 \
        pydicom==2.0.0 \
        pandas==1.1.5

# 随包 torch 1.0.0（cp36）——构建上下文里的 wheel
COPY torch-1.0.0-cp36-cp36m-linux_x86_64.whl /tmp/
RUN python3.6 -m pip install --no-cache-dir /tmp/torch-1.0.0-cp36-cp36m-linux_x86_64.whl \
    && rm /tmp/torch-1.0.0-cp36-cp36m-linux_x86_64.whl

WORKDIR /workspace
CMD ["python3.6"]
