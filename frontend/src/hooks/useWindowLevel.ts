import { useEffect, useRef, useState } from "react";
import type { Types } from "@cornerstonejs/core";

export interface WindowLevel {
  wl: number;
  ww: number;
}

/** 肺窗预设 */
export const LUNG_WINDOW: WindowLevel = { wl: -600, ww: 1500 };
/** 纵隔窗预设 */
export const MEDIASTINUM_WINDOW: WindowLevel = { wl: 50, ww: 350 };

/** HU 裁剪域（与后端预处理 clip [-1024, 300] 一致），用作窗位滑块边界 */
export const HU_MIN = -1024;
export const HU_MAX = 300;
/** 窗宽滑块边界 */
export const WW_MIN = 100;
export const WW_MAX = 2000;

/** 窗宽窗位 → Cornerstone VOIRange（lower/upper）换算 */
export function toVoiRange({ wl, ww }: WindowLevel) {
  return { lower: wl - ww / 2, upper: wl + ww / 2 };
}

/**
 * 窗宽窗位状态 + 应用到 VolumeViewport。
 * 用 requestAnimationFrame 合并同一帧内的多次滑块变化，避免每 tick 全量重绘。
 */
export function useWindowLevel(viewport: Types.IStackViewport | Types.IVolumeViewport | null) {
  const [windowLevel, setWindowLevel] = useState<WindowLevel>({ ...LUNG_WINDOW });
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!viewport) return;
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      viewport.setProperties({ voiRange: toVoiRange(windowLevel) });
      viewport.getRenderingEngine().render();
      rafRef.current = null;
    });
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [viewport, windowLevel]);

  return { windowLevel, setWindowLevel };
}
