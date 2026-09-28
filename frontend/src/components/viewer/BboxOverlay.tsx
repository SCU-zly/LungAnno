import { useEffect, useRef } from "react";
import { metaData, type Types } from "@cornerstonejs/core";
import type { Detection } from "../../types/detection";

interface BboxOverlayProps {
  viewport: Types.IStackViewport | null;
  detections: Detection[];
  sliceIndex: number;
  /** 已选中的候选 id 集合——只有选中的才画框（绿色） */
  selectedIds: Set<number>;
  onSelectDetection: (id: number) => void;
  subscribeToRender: (cb: () => void) => () => void;
}

interface DrawRect {
  id: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/** 候选框 z 向显示半深上限（mm）：大框（如 >30mm 的大病灶）若按整个直径画，
 * 会横跨几十层、在边缘层面与相邻小候选视觉相贴。封顶后只在中心 ±8mm
 * （约 ±6 层 @1.25mm）内显示；≤16mm 的小结节框不受影响（半深本就 <8mm）。 */
const MAX_HALF_DEPTH_MM = 8;

/**
 * 候选结节 bbox 叠加层：独立 canvas 覆盖在 Cornerstone 画布上。
 * 交互约定：默认不画任何框；仅当选中（右侧面板点击，可多选）时画绿色粗框。
 * 绘制由 IMAGE_RENDERED 订阅驱动（pan/zoom/scroll 自动重绘），
 * 依赖变化（选中集/检测列表）时直接重绘一次。
 * 世界坐标（box_* mm）经 worldToCanvas 投影；先按 z 范围粗筛再投影。
 * 当前切片的世界 z 取当前 imageId 的 imagePlaneModule.imagePositionPatient[2]
 * （StackViewport 相机 focalPoint 跨切片冻结，不能用作切片 z）。
 */
export default function BboxOverlay({
  viewport,
  detections,
  sliceIndex,
  selectedIds,
  onSelectDetection,
  subscribeToRender,
}: BboxOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rectsRef = useRef<DrawRect[]>([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const draw = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx || !viewport) return;
      // 当前切片的世界 z：不能取相机 focalPoint——StackViewport 的相机跨切片
      // 保持（pan/zoom 在滚动时不重置），focalPoint.z 停在初始切片上不动。
      // 必须从当前 imageId 的平面元数据取（identity 几何下 = sliceIndex * sz）。
      const imageId = viewport.getCurrentImageId?.();
      const plane = imageId ? metaData.get("imagePlaneModule", imageId) : undefined;
      const zWorld: unknown = plane?.imagePositionPatient?.[2];
      if (typeof zWorld !== "number") return;

      const base = viewport.getCanvas();
      // DPR 对齐：cornerstone 画布 backing store 是设备像素（CSS×dpr），而
      // worldToCanvas 输出 CSS 像素。叠加层 backing store 与 cornerstone 一致，
      // 变换矩阵按 dpr 缩放后全部用 CSS 像素绘制，任何 DPR 下不错位
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== base.width || canvas.height !== base.height) {
        canvas.width = base.width;
        canvas.height = base.height;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, base.width / dpr, base.height / dpr);
      rectsRef.current = [];

      if (selectedIds.size === 0) return;

      for (const det of detections) {
        // 只画被选中的候选（选中即绿框）
        if (!selectedIds.has(det.id)) continue;
        // z 范围粗筛：世界坐标中心 ± 半深（大框按 MAX_HALF_DEPTH_MM 封顶），跳过与当前切片不相交的框
        if (Math.abs(zWorld - det.box_z) > Math.min(det.box_d / 2, MAX_HALF_DEPTH_MM)) continue;

        const p1 = viewport.worldToCanvas([det.box_x - det.box_w / 2, det.box_y - det.box_h / 2, zWorld]);
        const p2 = viewport.worldToCanvas([det.box_x + det.box_w / 2, det.box_y + det.box_h / 2, zWorld]);

        ctx.strokeStyle = "#22c55e";
        ctx.lineWidth = 4;
        ctx.strokeRect(p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1]);
        rectsRef.current.push({ id: det.id, x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] });
      }
    };

    draw();
    const unsubscribe = subscribeToRender(draw);
    return () => unsubscribe();
  }, [viewport, detections, sliceIndex, selectedIds, subscribeToRender]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // rect 即 CSS 像素坐标系（与 worldToCanvas 输出一致，无需再换算）
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    // 从后往前命中（后绘制的在上层）
    for (let i = rectsRef.current.length - 1; i >= 0; i--) {
      const r = rectsRef.current[i];
      if (x >= r.x1 && x <= r.x2 && y >= r.y1 && y <= r.y2) {
        onSelectDetection(r.id);
        return;
      }
    }
  };

  return (
    // zIndex 2：cornerstone 的 viewport div 是 enableElement 时后插进来的
    // （DOM 顺序在叠加层之后），不显式抬层级会被 CT 画布盖住
    <canvas
      ref={canvasRef}
      onClick={handleClick}
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%", cursor: "crosshair", pointerEvents: "auto", zIndex: 2 }}
    />
  );
}
