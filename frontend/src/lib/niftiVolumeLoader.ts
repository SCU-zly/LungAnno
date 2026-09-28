/**
 * 自研 NIfTI volume loader —— 替代 @cornerstonejs/nifti-volume-loader 的
 * cornerstoneNiftiImageVolumeLoader。
 *
 * 背景（两条）：
 * 1. 官方 loader（v1.60.0）分配体数据时无条件调用 create*SharedArray，
 *    非 cross-origin isolated 页面（公网 HTTP）必抛
 *    "NOT cross-origin isolated"，且不读 cornerstone 的 SharedArrayBuffer 开关；
 * 2. 官方实现对 int16 体数据先 JS 软解压、再做一次整卷反转拷贝
 *    （invertDataPerFrame，其返回值被上游丢弃，实为 no-op）、再 int16→Float32
 *    拷贝——主线程秒级阻塞、内存翻倍。
 *
 * 本实现的传输+解析路径（URL 取流、RAS→LPS、元数据、modality scaling）与官方
 * v1.60.0 对齐，差异仅在性能优化：
 * - scalarData 零拷贝：直接复用解压后的 TypedArray 视图（Int16Array 是
 *   cornerstone 一等公民 scalarData 类型——DICOM CT 的标准路径），
 *   内存与 GPU 纹理上传较 float32 路径减半；
 * - 删除 invertDataPerFrame no-op（省一次整卷分配+拷贝）；
 * - modality scaling 增加 slope=1/inter=0 早退（免 7700 万次空转循环）；
 * - 服务端以 Content-Encoding: gzip 传输时浏览器已原生流式解压，
 *   本文件的 isCompressed/decompress 仅作非 gzip 客户端兜底。
 * 将来上 HTTPS 后如需换回官方 loader，只需改回 useViewer 中的 import 与
 * registerVolumeLoader 调用（见 deploy/README.md 的 HTTPS 章节）。
 */
import * as NiftiReader from "nifti-reader-js";
import { cache, Enums, type Types } from "@cornerstonejs/core";
import { NiftiImageVolume, helpers } from "@cornerstonejs/nifti-volume-loader";
import { decodeHtj2kSlice } from "./htj2kDecoder";

const { makeVolumeMetadata } = helpers;

const NIFTI_LOADER_SCHEME = "nifti";

// NIfTI datatype codes（与官方 loader 的 niftiConstants 一致）
const NIFTI_TYPE_UINT8 = 2;
const NIFTI_TYPE_INT16 = 4;
const NIFTI_TYPE_FLOAT32 = 16;

/* eslint-disable @typescript-eslint/no-explicit-any */
type NiftiHeader = any;

function fetchArrayBuffer(
  url: string,
  signal?: AbortSignal,
  onProgress?: (loaded: number, total: number) => void,
): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("GET", url, true);
    xhr.responseType = "arraybuffer";
    const onLoadHandler = () => {
      signal?.removeEventListener("abort", onAbortHandler);
      resolve(xhr.response as ArrayBuffer);
    };
    const onAbortHandler = () => {
      xhr.abort();
      xhr.removeEventListener("load", onLoadHandler);
      reject(new Error("Request aborted"));
    };
    xhr.addEventListener("load", onLoadHandler);
    if (onProgress) {
      xhr.onprogress = (e) => onProgress(e.loaded, e.total);
    }
    xhr.addEventListener("error", () => reject(new Error(`volume 下载失败: ${url}`)));
    if (signal?.aborted) {
      xhr.abort();
      reject(new Error("Request aborted"));
    } else if (signal) {
      signal.addEventListener("abort", onAbortHandler);
    }
    xhr.send();
  });
}

function getTypedNiftiArray(datatypeCode: number, niftiImageBuffer: ArrayBuffer): Uint8Array | Float32Array | Int16Array {
  switch (datatypeCode) {
    case NIFTI_TYPE_UINT8:
      return new Uint8Array(niftiImageBuffer);
    case NIFTI_TYPE_FLOAT32:
      return new Float32Array(niftiImageBuffer);
    case NIFTI_TYPE_INT16:
      return new Int16Array(niftiImageBuffer);
    default:
      throw new Error(`datatypeCode ${datatypeCode} is not yet supported`);
  }
}

/** 与官方 affineUtilities.parseAffineMatrix 相同 */
function parseAffineMatrix(affine: number[][]) {
  const origin = [affine[0][3], affine[1][3], affine[2][3]];
  const spacing = [
    Math.sqrt(affine[0][0] ** 2 + affine[1][0] ** 2 + affine[2][0] ** 2),
    Math.sqrt(affine[0][1] ** 2 + affine[1][1] ** 2 + affine[2][1] ** 2),
    Math.sqrt(affine[0][2] ** 2 + affine[1][2] ** 2 + affine[2][2] ** 2),
  ];
  const orientation = [
    affine[0][0] / spacing[0],
    affine[0][1] / spacing[1],
    affine[0][2] / spacing[2],
    affine[1][0] / spacing[0],
    affine[1][1] / spacing[1],
    affine[1][2] / spacing[2],
    affine[2][0] / spacing[0],
    affine[2][1] / spacing[1],
    affine[2][2] / spacing[2],
  ];
  return { origin, orientation, spacing };
}

/** 与官方 convert.js rasToLps 相同（NIfTI RAS+ → cornerstone LPS） */
function rasToLps(niftiHeader: NiftiHeader) {
  const { affine } = niftiHeader;
  const { orientation, origin, spacing } = parseAffineMatrix(affine);
  const newOrigin = [-origin[0], -origin[1], origin[2]];
  const newOrientation = [
    -orientation[0],
    -orientation[3],
    orientation[6],
    -orientation[1],
    -orientation[4],
    orientation[7],
    -orientation[2],
    -orientation[5],
    orientation[8],
  ];
  return {
    origin: newOrigin,
    orientation: newOrientation,
    spacing,
  };
}

/**
 * modality scaling（scl_slope/scl_inter），与官方 modalityScaleNifti 相同，
 * 增加 slope=1 && inter=0 的恒等早退（大体积下省一次全数组空转）。
 */
function applyModalityScale(array: Uint8Array | Float32Array | Int16Array, niftiHeader: NiftiHeader) {
  const { scl_slope, scl_inter } = niftiHeader;
  if (!scl_slope || scl_slope === 0 || Number.isNaN(scl_slope)) {
    return;
  }
  if (scl_slope === 1 && (scl_inter === 0 || !scl_inter)) {
    return;
  }
  for (let i = 0; i < array.length; i++) {
    array[i] = array[i] * scl_slope + scl_inter;
  }
}

async function fetchAndAllocateNiftiVolume(volumeId: string): Promise<NiftiImageVolume> {
  const niftiURL = volumeId.substring(NIFTI_LOADER_SCHEME.length + 1);
  const controller = new AbortController();

  // 下载进度经 window 事件透出（loaded/total 为传输层字节，Content-Encoding:
  // gzip 时即压缩后大小），useViewer 据此渲染进度条
  const onProgress = (loaded: number, total: number) => {
    window.dispatchEvent(new CustomEvent("nifti-volume-progress", { detail: { volumeId, loaded, total } }));
  };

  // 优先 HTJ2K bundle（体积再小 3-9 倍，wasm 解码）；无产物或失败自动回退 NIfTI
  const htj2kVolume = await tryFetchHtj2kVolume(volumeId, niftiURL, controller, onProgress);
  if (htj2kVolume) {
    return htj2kVolume;
  }

  let niftiBuffer = await fetchArrayBuffer(niftiURL, controller.signal, onProgress);

  let niftiHeader: NiftiHeader = null;
  let niftiImage: ArrayBuffer | null = null;
  if (NiftiReader.isCompressed(niftiBuffer)) {
    // 兜底分支：服务端未走 Content-Encoding: gzip 传输（非浏览器客户端/旧数据）
    niftiBuffer = NiftiReader.decompress(niftiBuffer) as ArrayBuffer;
  }
  if (NiftiReader.isNIFTI(niftiBuffer)) {
    niftiHeader = NiftiReader.readHeader(niftiBuffer);
    niftiImage = NiftiReader.readImage(niftiHeader, niftiBuffer);
  }
  if (!niftiHeader || !niftiImage) {
    throw new Error("volume 响应不是合法的 NIfTI 文件");
  }

  // 零拷贝：scalarData 直接复用解压缓冲上的 TypedArray 视图
  const scalarData = getTypedNiftiArray(niftiHeader.datatypeCode, niftiImage);
  const { orientation, origin, spacing } = rasToLps(niftiHeader);
  applyModalityScale(scalarData, niftiHeader);
  const volumeMetadata = makeVolumeMetadata(niftiHeader, orientation, scalarData);

  const scanAxisNormal = [orientation[6], orientation[7], orientation[8]];
  const { ImageOrientationPatient, Columns, Rows } = volumeMetadata;
  const rowCosineVec = [ImageOrientationPatient[0], ImageOrientationPatient[1], ImageOrientationPatient[2]];
  const colCosineVec = [ImageOrientationPatient[3], ImageOrientationPatient[4], ImageOrientationPatient[5]];

  const { dims } = niftiHeader;
  const numFrames = dims[3];
  const dimensions: Types.Point3 = [Columns, Rows, numFrames];
  const direction = new Float32Array([
    rowCosineVec[0],
    rowCosineVec[1],
    rowCosineVec[2],
    colCosineVec[0],
    colCosineVec[1],
    colCosineVec[2],
    scanAxisNormal[0],
    scanAxisNormal[1],
    scanAxisNormal[2],
  ]);

  const sizeInBytes = scalarData.byteLength;
  if (!cache.isCacheable(sizeInBytes)) {
    throw new Error(Enums.Events.CACHE_SIZE_EXCEEDED);
  }
  cache.decacheIfNecessaryUntilBytesAvailable(sizeInBytes);

  return new NiftiImageVolume(
    {
      volumeId,
      metadata: volumeMetadata,
      dimensions,
      spacing: spacing as Types.Point3,
      origin: origin as Types.Point3,
      direction,
      scalarData,
      sizeInBytes,
      imageIds: [],
    },
    {
      loadStatus: { loaded: false, loading: false, callbacks: [] },
      controller,
    },
  );
}

/** 与官方 cornerstoneNiftiImageVolumeLoader 相同的 VolumeLoaderFn 形态 */
export function niftiVolumeLoader(volumeId: string): {
  promise: Promise<NiftiImageVolume>;
  cancel: () => void;
} {
  const niftiVolumePromise = fetchAndAllocateNiftiVolume(volumeId);
  return {
    promise: niftiVolumePromise,
    cancel: () => {},
  };
}

/**
 * HTJ2K bundle 路径：下载 .htj2k（magic+JSON 头+逐 slice 码流），wasm 逐 slice
 * 解码拼成完整 int16 体数据，元数据由 bundle 头自带（identity 几何）。
 * 无产物（404 JSON）或任何失败返回 null，调用方回退 NIfTI 路径。
 */
async function tryFetchHtj2kVolume(
  volumeId: string,
  niftiURL: string,
  controller: AbortController,
  onProgress: (loaded: number, total: number) => void,
): Promise<NiftiImageVolume | null> {
  try {
    const sep = niftiURL.includes("?") ? "&" : "?";
    const buffer = await fetchArrayBuffer(`${niftiURL}${sep}format=htj2k`, controller.signal, onProgress);
    if (buffer.byteLength < 12) return null;
    if (new TextDecoder().decode(new Uint8Array(buffer, 0, 8)) !== "CTJ2K001") return null;

    const dv = new DataView(buffer);
    const headerLen = dv.getUint32(8, true);
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 12, headerLen))) as {
      shape: [number, number, number];
      spacing: [number, number, number];
      voi: { windowCenter: number; windowWidth: number };
      count: number;
      offsets: number[];
      sizes: number[];
    };
    const dataStart = 12 + headerLen;
    const [zdim, h, w] = header.shape;
    const scalarData = new Int16Array(zdim * h * w);
    for (let z = 0; z < header.count; z++) {
      const seg = new Uint8Array(buffer, dataStart + header.offsets[z], header.sizes[z]);
      const slice = await decodeHtj2kSlice(seg);
      scalarData.set(slice, z * h * w);
    }

    const sizeInBytes = scalarData.byteLength;
    if (!cache.isCacheable(sizeInBytes)) {
      throw new Error(Enums.Events.CACHE_SIZE_EXCEEDED);
    }
    cache.decacheIfNecessaryUntilBytesAvailable(sizeInBytes);

    const dimensions: Types.Point3 = [w, h, zdim];
    const spacing: Types.Point3 = [...header.spacing];
    const metadata = {
      BitsAllocated: 16,
      BitsStored: 16,
      SamplesPerPixel: 1,
      HighBit: 15,
      PhotometricInterpretation: "MONOCHROME2",
      PixelRepresentation: 1,
      ImageOrientationPatient: [1, 0, 0, 0, 1, 0],
      PixelSpacing: [header.spacing[0], header.spacing[1]],
      Columns: w,
      Rows: h,
      voiLut: [{ windowCenter: header.voi.windowCenter, windowWidth: header.voi.windowWidth }],
      FrameOfReferenceUID: "1.2.3",
      Modality: "CT",
      VOILUTFunction: "LINEAR",
    };

    return new NiftiImageVolume(
      {
        volumeId,
        metadata,
        dimensions,
        spacing,
        origin: [0, 0, 0],
        direction: new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1]),
        scalarData,
        sizeInBytes,
        imageIds: [],
      },
      {
        loadStatus: { loaded: false, loading: false, callbacks: [] },
        controller,
      },
    );
  } catch {
    return null;
  }
}
