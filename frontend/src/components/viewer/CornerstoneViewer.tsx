import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";
import { useViewer } from "../../hooks/useViewer";
import { useWindowLevel } from "../../hooks/useWindowLevel";
import BboxOverlay from "./BboxOverlay";
import ViewerToolbar from "./ViewerToolbar";
import type { Detection } from "../../types/detection";

export interface CornerstoneViewerHandle {
  /** 跳转到指定切片（clamp 到 [0, numSlices-1]，cornerstone 索引约定） */
  setSlice: (slice: number) => void;
  /** 按世界 z（mm，后端检测坐标系）跳片——候选定位必须用它（切片索引与后端一致） */
  jumpToWorldZ: (worldZ: number) => void;
}

interface CornerstoneViewerProps {
  seriesId: number;
  detections: Detection[];
  /** 已选中候选集合（可多选）；只有选中的候选才画框 */
  selectedIds: Set<number>;
  onSelectDetection: (id: number) => void;
}

/**
 * CT 切片查看器：Cornerstone3D StackViewport 按需取片渲染（HTJ2K 单切片），
 * 叠加候选结节 bbox（BboxOverlay），工具栏负责切片滚动与窗宽窗位。
 * 切片状态由 useViewer 单一持有，父级通过 ref 调用 setSlice。
 */
const CornerstoneViewer = forwardRef<CornerstoneViewerHandle, CornerstoneViewerProps>(
  function CornerstoneViewer({ seriesId, detections, selectedIds, onSelectDetection }, ref) {
    const canvasHostRef = useRef<HTMLDivElement>(null);
    const { viewport, numSlices, sliceIndex, loading, error, progress, setSlice, jumpToWorldZ, subscribeToRender } = useViewer(
      seriesId,
      canvasHostRef
    );
    const { windowLevel, setWindowLevel } = useWindowLevel(viewport);

    useImperativeHandle(ref, () => ({ setSlice, jumpToWorldZ }), [setSlice, jumpToWorldZ]);

    // 键盘上下键滚动切片（容器聚焦时生效）
    const handleKeyDown = useCallback(
      (e: React.KeyboardEvent) => {
        if (e.key === "ArrowDown" || e.key === "PageDown") {
          e.preventDefault();
          setSlice(sliceIndex + 1);
        } else if (e.key === "ArrowUp" || e.key === "PageUp") {
          e.preventDefault();
          setSlice(sliceIndex - 1);
        }
      },
      [sliceIndex, setSlice]
    );

    // 鼠标滚轮滚动切片：挂原生非 passive 监听。React 的 onWheel 是 passive
    // 监听器，内部 preventDefault 无效并刷 "Unable to preventDefault" 告警，
    // 且无法阻止页面联动滚动。
    const sliceIndexRef = useRef(sliceIndex);
    sliceIndexRef.current = sliceIndex;
    useEffect(() => {
      const el = canvasHostRef.current;
      if (!el) return;
      const onWheel = (e: WheelEvent) => {
        e.preventDefault();
        setSlice(sliceIndexRef.current + (e.deltaY > 0 ? 1 : -1));
      };
      el.addEventListener("wheel", onWheel, { passive: false });
      return () => el.removeEventListener("wheel", onWheel);
    }, [setSlice]);

    return (
      <div style={{ flex: 1, background: "#000", position: "relative", display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div
          ref={canvasHostRef}
          tabIndex={0}
          onKeyDown={handleKeyDown}
          style={{ flex: 1, position: "relative", outline: "none" }}
        >
          {loading && (
            <div className="viewer-loading">
              <div className="viewer-spinner" />
              {(() => {
                const mb = (n: number) => (n / 1048576).toFixed(0);
                // 下载完成（loaded==total）后进入解析/纹理上传阶段，无进度量
                const downloading = progress && (progress.total === 0 || progress.loaded < progress.total);
                if (downloading && progress.total > 0) {
                  const pct = Math.min(100, Math.round((progress.loaded / progress.total) * 100));
                  return (
                    <>
                      <div>正在下载体积数据 {pct}%（{mb(progress.loaded)} / {mb(progress.total)} MB）</div>
                      <div className="viewer-progress"><div style={{ width: `${pct}%` }} /></div>
                    </>
                  );
                }
                if (downloading) {
                  return <div>正在下载体积数据（{mb(progress.loaded)} MB）…</div>;
                }
                return <div>{progress ? "下载完成，正在解析并上传纹理…" : "正在加载体积数据…"}</div>;
              })()}
            </div>
          )}
          {error && (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#f87171", zIndex: 10, background: "rgba(0,0,0,0.6)", padding: 24, textAlign: "center" }}>
              加载失败：{error}
            </div>
          )}
          <BboxOverlay
            viewport={viewport}
            detections={detections}
            sliceIndex={sliceIndex}
            selectedIds={selectedIds}
            onSelectDetection={onSelectDetection}
            subscribeToRender={subscribeToRender}
          />
        </div>
        <ViewerToolbar
          sliceIndex={sliceIndex}
          numSlices={numSlices}
          onSliceChange={setSlice}
          windowLevel={windowLevel}
          onWindowLevelChange={setWindowLevel}
        />
      </div>
    );
  }
);

export default CornerstoneViewer;
