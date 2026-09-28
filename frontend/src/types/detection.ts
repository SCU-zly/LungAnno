/**
 * API 契约类型：GET /series/{id}/detections 的条目（对应后端 DetectionItem）。
 *
 * 坐标语义（与后端 SA-06 一致）：
 * - box_* 为世界坐标（mm），中心点 + 宽高深（cccwhd），真源
 * - voxel_* 为预处理体积网格的派生体素坐标（快速叠加用）
 * - slice_index 为体素 z 中心所在切片
 */
export interface Detection {
  id: number;
  score: number;
  slice_index: number;
  box_x: number;
  box_y: number;
  box_z: number;
  box_w: number;
  box_h: number;
  box_d: number;
  voxel_x: number;
  voxel_y: number;
  voxel_z: number;
  voxel_w: number;
  voxel_h: number;
  voxel_d: number;
}

/** 分数分档阈值：≥HIGH_SCORE_THRESHOLD 视为高置信（红色系），低于为低置信（黄色系） */
export const HIGH_SCORE_THRESHOLD = 0.5;
