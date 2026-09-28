import { useCallback, useEffect, useRef, useState } from "react";
import {
  init as csInit,
  setUseSharedArrayBuffer,
  RenderingEngine,
  imageLoader,
  eventTarget,
  Enums,
  type Types,
} from "@cornerstonejs/core";
import { fetchSeriesInfo, invalidateSeriesInfo, registerSliceImageLoader } from "../lib/sliceImageLoader";

const ENGINE_ID = "ct-review-engine";
const VIEWPORT_ID = "ct-review-viewport";
/** 预取半径（当前层前后各 N 层） */
const PREFETCH_RADIUS = 8;

let csReady: Promise<boolean> | null = null;
function ensureCornerstoneInit() {
  if (!csReady) {
    csReady = (async () => {
      const ok = await csInit();
      // HTTP（非安全上下文）部署下 SharedArrayBuffer 不存在——强制 cornerstone
      // 走普通 ArrayBuffer 分配路径（配合 sab-polyfill.ts）。上 HTTPS 后可移除
      // 此行与 polyfill，恢复 AUTO/TRUE 以获得跨 worker 共享内存的解码性能。
      setUseSharedArrayBuffer(Enums.SharedArrayBufferModes.FALSE);
      return ok;
    })();
  }
  return csReady;
}

export interface ViewerState {
  viewport: Types.IStackViewport | null;
  sliceIndex: number;
  numSlices: number;
  loading: boolean;
  error: string | null;
  /** 兼容旧整卷下载 UI；按需取片模式下恒为 null */
  progress: { loaded: number; total: number } | null;
}

/**
 * Cornerstone3D StackViewport 生命周期（按需取片模式）。
 * 每层经 ctslice image loader 从后端 HTJ2K bundle 单取（~30-200KB）+ wasm 解码；
 * 渲染后对邻近层预取（进 cornerstone 缓存）。切片索引与后端 slice_index 一致
 * （StackViewport 无体积模式的 z 镜像问题）。
 */
export function useViewer(seriesId: number, canvasRef: React.RefObject<HTMLDivElement>) {
  const [state, setState] = useState<ViewerState>({
    viewport: null,
    sliceIndex: 0,
    numSlices: 0,
    loading: false,
    error: null,
    progress: null,
  });
  const engineRef = useRef<RenderingEngine | null>(null);
  const viewportRef = useRef<Types.IStackViewport | null>(null);
  const spacingZRef = useRef<number>(1);

  useEffect(() => {
    let disposed = false;
    let prefetchHandler: ((evt: Event) => void) | null = null;
    const engineId = ENGINE_ID;
    const viewportId = VIEWPORT_ID;

    const prefetchAround = (center: number, imageIds: string[]) => {
      for (let dz = 1; dz <= PREFETCH_RADIUS; dz++) {
        for (const z of [center + dz, center - dz]) {
          if (z < 0 || z >= imageIds.length) continue;
          // cornerstone 内部对已在缓存/加载中的 imageId 去重，重复调用代价可忽略
          imageLoader.loadImage(imageIds[z]).catch(() => {});
        }
      }
    };

    (async () => {
      try {
        setState((s) => ({ ...s, loading: true, error: null }));
        await ensureCornerstoneInit();
        registerSliceImageLoader();
        invalidateSeriesInfo(seriesId);
        const info = await fetchSeriesInfo(seriesId);
        spacingZRef.current = info.spacing[2];
        if (disposed) return;

        const engine = new RenderingEngine(engineId);
        engineRef.current = engine;
        const element = canvasRef.current;
        if (!element) throw new Error("查看器容器未挂载");

        engine.enableElement({
          viewportId,
          // STACK = 逐切片视图（按需取片）；切片索引即后端 slice_index
          type: Enums.ViewportType.STACK,
          element,
          defaultOptions: { background: [0, 0, 0] },
        });
        const viewport = engine.getViewport(viewportId) as Types.IStackViewport;
        viewportRef.current = viewport;

        const imageIds = Array.from({ length: info.count }, (_, z) => `ctslice://series/${seriesId}/z/${z}`);
        const mid = Math.floor(info.count / 2);
        await viewport.setStack(imageIds, mid);
        if (disposed) return;

        // 每次渲染后预取当前层邻近层（滚动时命中缓存）
        prefetchHandler = (evt: Event) => {
          const detail = (evt as CustomEvent).detail;
          if (detail?.viewportId !== VIEWPORT_ID) return;
          prefetchAround(viewport.getSliceIndex(), imageIds);
        };
        eventTarget.addEventListener(Enums.Events.IMAGE_RENDERED, prefetchHandler);

        engine.render();
        prefetchAround(mid, imageIds);

        setState({
          viewport,
          sliceIndex: viewport.getSliceIndex(),
          numSlices: info.count,
          loading: false,
          error: null,
          progress: null,
        });
      } catch (e) {
        if (!disposed) setState((s) => ({ ...s, loading: false, error: e instanceof Error ? e.message : String(e) }));
      }
    })();

    return () => {
      disposed = true;
      if (prefetchHandler) {
        eventTarget.removeEventListener(Enums.Events.IMAGE_RENDERED, prefetchHandler);
        prefetchHandler = null;
      }
      engineRef.current?.destroy();
      engineRef.current = null;
      viewportRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesId]);

  const setSlice = useCallback((index: number) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const clamped = Math.max(0, Math.min(index, viewport.getNumberOfSlices() - 1));
    if (clamped !== viewport.getSliceIndex()) {
      // StackViewport 自带 load+render（缓存命中时近瞬时）
      viewport.setImageIdIndex(clamped);
      setState((s) => ({ ...s, sliceIndex: clamped }));
    }
  }, []);

  /**
   * 按世界 z（mm，后端检测坐标系：identity 几何 worldZ = voxel_z * spacing）跳片。
   * 按需取片模式下切片索引与后端 slice_index 一致，直接换算索引即可。
   */
  const jumpToWorldZ = useCallback((worldZ: number) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const index = Math.round(worldZ / spacingZRef.current);
    setSlice(index);
  }, [setSlice]);

  /** Subscribe to image renders of this viewport (for bbox redraw on pan/zoom/scroll). */
  const subscribeToRender = useCallback((cb: () => void) => {
    const handler = (evt: Event) => {
      const detail = (evt as CustomEvent).detail;
      if (detail?.viewportId === VIEWPORT_ID) cb();
    };
    eventTarget.addEventListener(Enums.Events.IMAGE_RENDERED, handler);
    return () => eventTarget.removeEventListener(Enums.Events.IMAGE_RENDERED, handler);
  }, []);

  return { ...state, setSlice, jumpToWorldZ, subscribeToRender };
}
