/**
 * 按需取片 image loader（scheme: ctslice）。
 *
 * imageId: `ctslice://series/{seriesId}/z/{z}`
 * - 元数据：volume/info（HTJ2K bundle 头）按 series 缓存
 * - 像素：slice 端点取 j2c 码流 → openjph wasm 解码为 Int16Array
 * - 几何：identity（origin=0、direction=单位阵），与检测坐标同系
 *   （world mm = voxel * spacing）；imagePositionPatient=[0,0,z*sz]
 */
import { imageLoader, metaData } from "@cornerstonejs/core";
import { decodeHtj2kSlice } from "./htj2kDecoder";
import { VOLUME_BASE } from "../api/client";

export interface SliceSeriesInfo {
  shape: [number, number, number]; // [z,y,x]
  dtype: string;
  spacing: [number, number, number]; // [x,y,z]
  voi: { windowCenter: number; windowWidth: number };
  count: number;
}

const infoCache = new Map<number, SliceSeriesInfo>();

export async function fetchSeriesInfo(seriesId: number): Promise<SliceSeriesInfo> {
  const cached = infoCache.get(seriesId);
  if (cached) return cached;
  const token = localStorage.getItem("access_token") ?? "";
  const resp = await fetch(`${VOLUME_BASE}/series/${seriesId}/volume/info?token=${encodeURIComponent(token)}`);
  if (!resp.ok) throw new Error(`体数据信息加载失败（HTTP ${resp.status}）`);
  const info = (await resp.json()) as SliceSeriesInfo;
  infoCache.set(seriesId, info);
  return info;
}

/** 切换 series 后调用，避免跨序列元数据串用 */
export function invalidateSeriesInfo(seriesId?: number) {
  if (seriesId === undefined) infoCache.clear();
  else infoCache.delete(seriesId);
}

function parseImageId(imageId: string): { seriesId: number; z: number } {
  const m = imageId.match(/^ctslice:\/\/series\/(\d+)\/z\/(\d+)$/);
  if (!m) throw new Error(`非法 ctslice imageId: ${imageId}`);
  return { seriesId: Number(m[1]), z: Number(m[2]) };
}

async function loadSliceImage(imageId: string) {
  const { seriesId, z } = parseImageId(imageId);
  const info = await fetchSeriesInfo(seriesId);
  const token = localStorage.getItem("access_token") ?? "";
  const resp = await fetch(`${VOLUME_BASE}/series/${seriesId}/slice?z=${z}&token=${encodeURIComponent(token)}`);
  if (!resp.ok) throw new Error(`切片 ${z} 加载失败（HTTP ${resp.status}）`);
  const bytes = new Uint8Array(await resp.arrayBuffer());
  const pixelData = await decodeHtj2kSlice(bytes);

  const [, h, w] = info.shape;
  const [sx, sy, sz] = info.spacing;
  const { windowCenter, windowWidth } = info.voi;
  return {
    imageId,
    pixelData,
    getPixelData: () => pixelData,
    rows: h,
    columns: w,
    width: w * sx,
    height: h * sy,
    rowPixelSpacing: sy,
    columnPixelSpacing: sx,
    minPixelValue: windowCenter - windowWidth / 2,
    maxPixelValue: windowCenter + windowWidth / 2,
    slope: 1,
    intercept: 0,
    windowCenter,
    windowWidth,
    voiLut: [{ windowCenter, windowWidth }],
    color: false,
    rgba: false,
    invert: false,
    sizeInBytes: pixelData.byteLength,
    imageOrientationPatient: [1, 0, 0, 0, 1, 0],
    imagePositionPatient: [0, 0, z * sz],
    frameOfReferenceUID: "1.2.3",
    modality: "CT",
  };
}

let registered = false;

/** 注册 ctslice loader + 元数据 provider（幂等） */
export function registerSliceImageLoader() {
  if (registered) return;
  registered = true;

  imageLoader.registerImageLoader("ctslice", (imageId: string) => ({ promise: loadSliceImage(imageId) }));

  metaData.addProvider((type: string, imageId: string) => {
    if (!imageId.startsWith("ctslice://")) return;
    const { seriesId, z } = parseImageId(imageId);
    const info = infoCache.get(seriesId);
    if (!info) return;
    const [, h, w] = info.shape;
    const [sx, sy, sz] = info.spacing;
    const { windowCenter, windowWidth } = info.voi;

    if (type === "imagePlaneModule") {
      return {
        rows: h,
        columns: w,
        rowCosines: [1, 0, 0],
        columnCosines: [0, 1, 0],
        imageOrientationPatient: [1, 0, 0, 0, 1, 0],
        imagePositionPatient: [0, 0, z * sz],
        rowPixelSpacing: sy,
        columnPixelSpacing: sx,
        frameOfReferenceUID: "1.2.3",
        sliceThickness: sz,
      };
    }
    if (type === "imagePixelModule") {
      return {
        rows: h,
        columns: w,
        pixelSpacing: [sy, sx],
        bitsAllocated: 16,
        photometricInterpretation: "MONOCHROME2",
        samplesPerPixel: 1,
        pixelRepresentation: 1,
        windowCenter,
        windowWidth,
        modality: "CT",
      };
    }
    if (type === "generalSeriesModule") {
      return { modality: "CT", seriesInstanceUID: `ctanno.series.${seriesId}` };
    }
    if (type === "voiLutModule") {
      return { windowCenter: [windowCenter], windowWidth: [windowWidth] };
    }
    if (type === "modalityLutModule") {
      return { rescaleSlope: 1, rescaleIntercept: 0, modalityLUTType: "identity" };
    }
    return undefined;
  });
}
