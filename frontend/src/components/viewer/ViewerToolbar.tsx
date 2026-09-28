import { HU_MAX, HU_MIN, LUNG_WINDOW, MEDIASTINUM_WINDOW, WW_MAX, WW_MIN, type WindowLevel } from "../../hooks/useWindowLevel";

interface ViewerToolbarProps {
  sliceIndex: number;
  numSlices: number;
  onSliceChange: (slice: number) => void;
  windowLevel: WindowLevel;
  onWindowLevelChange: (wl: WindowLevel) => void;
}

const btnStyle: React.CSSProperties = {
  padding: "6px 14px",
  background: "#333",
  color: "#fff",
  border: "none",
  borderRadius: 4,
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const sliderLabel: React.CSSProperties = { color: "#9ca3af", fontSize: 12, width: 64 };

/** 查看器工具栏：切片导航 + 窗宽窗位调节（滑块 + 预设）。 */
export default function ViewerToolbar({
  sliceIndex,
  numSlices,
  onSliceChange,
  windowLevel,
  onWindowLevelChange,
}: ViewerToolbarProps) {
  const presetActive = (p: WindowLevel) => windowLevel.wl === p.wl && windowLevel.ww === p.ww;

  return (
    <div style={{ padding: "10px 14px", background: "#1a1a1a", display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
      <button onClick={() => onSliceChange(sliceIndex - 1)} disabled={sliceIndex <= 0} style={btnStyle}>上一帧</button>
      <input
        type="range"
        min={0}
        max={Math.max(0, numSlices - 1)}
        value={sliceIndex}
        onChange={(e) => onSliceChange(Number(e.target.value))}
        style={{ width: 180 }}
      />
      <span style={{ color: "#fff", fontSize: 13, minWidth: 90 }}>
        Slice: {sliceIndex} / {Math.max(0, numSlices - 1)}
      </span>
      <button onClick={() => onSliceChange(sliceIndex + 1)} disabled={sliceIndex >= numSlices - 1} style={btnStyle}>下一帧</button>

      <span style={{ width: 1, height: 24, background: "#333" }} />

      <label style={sliderLabel}>窗位 {windowLevel.wl}</label>
      <input
        type="range"
        min={HU_MIN}
        max={HU_MAX}
        step={1}
        value={windowLevel.wl}
        onChange={(e) => onWindowLevelChange({ ...windowLevel, wl: Number(e.target.value) })}
        style={{ width: 140 }}
      />
      <label style={sliderLabel}>窗宽 {windowLevel.ww}</label>
      <input
        type="range"
        min={WW_MIN}
        max={WW_MAX}
        step={10}
        value={windowLevel.ww}
        onChange={(e) => onWindowLevelChange({ ...windowLevel, ww: Number(e.target.value) })}
        style={{ width: 140 }}
      />
      <button
        onClick={() => onWindowLevelChange({ ...LUNG_WINDOW })}
        style={{ ...btnStyle, background: presetActive(LUNG_WINDOW) ? "#444" : "#333" }}
      >
        肺窗 (WL:{LUNG_WINDOW.wl}/WW:{LUNG_WINDOW.ww})
      </button>
      <button
        onClick={() => onWindowLevelChange({ ...MEDIASTINUM_WINDOW })}
        style={{ ...btnStyle, background: presetActive(MEDIASTINUM_WINDOW) ? "#444" : "#333" }}
      >
        纵隔窗 (WL:{MEDIASTINUM_WINDOW.wl}/WW:{MEDIASTINUM_WINDOW.ww})
      </button>
    </div>
  );
}
