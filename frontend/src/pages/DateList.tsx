import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { fetchPatientSeries, type PatientSeriesItem } from "../api/client";
import UserMenu from "../components/UserMenu";

/** 处理状态中文标签（与批次列表页一致） */
const procLabels: Record<string, string> = {
  registered: "待处理",
  preprocessing: "预处理中",
  preprocessed: "待推理",
  inferring: "推理中",
  detected: "检测完成",
  ready_for_review: "待审核",
  error: "失败",
};

/** study_date（"20200419"）格式化为 YYYY-MM-DD，空串显示「未知日期」 */
function formatStudyDate(d: string): string {
  if (!d) return "未知日期";
  if (d.length === 8) return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
  return d;
}

/** 患者 → 检查日期/序列列表页：同一日期多个薄层序列各成条目，点击进入审核页 */
export default function DateList() {
  const { batchId, patientId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  // 姓名/ID 由上一页 Link state 传入；直接刷新时退化为显示 patientId
  const state = (location.state ?? {}) as { name?: string; display_id?: string };
  const [series, setSeries] = useState<PatientSeriesItem[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSeries = () => {
    setLoadError(null);
    fetchPatientSeries(batchId!, patientId!)
      .then(setSeries)
      .catch(() => setLoadError("序列列表加载失败"));
  };

  useEffect(() => {
    loadSeries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId, patientId]);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1>
          {state.name ?? patientId}
          <span style={{ fontSize: 14, fontWeight: "normal", color: "var(--muted)", marginLeft: 8 }}>（ID: {state.display_id ?? patientId}）</span>
        </h1>
        <UserMenu />
      </div>
      <p style={{ marginBottom: 16 }}>
        <Link to={`/batches/${batchId}`} style={{ color: "var(--primary)" }}>← 返回患者列表</Link>
      </p>
      {loadError ? (
        <div style={{ textAlign: "center", padding: 24, color: "var(--danger)" }}>
          <p style={{ marginBottom: 12 }}>{loadError}</p>
          <button onClick={loadSeries} style={{ padding: "6px 14px", border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: "pointer" }}>
            重试
          </button>
        </div>
      ) : series.length === 0 && <p style={{ color: "var(--muted)" }}>暂无序列数据</p>}
      {series.map((s) => (
        <div
          key={s.series_id}
          onClick={() => navigate(`/series/${s.series_id}/review`)}
          style={{ background: "var(--card)", padding: 16, marginBottom: 8, borderRadius: 8, cursor: "pointer", boxShadow: "0 1px 3px rgba(0,0,0,.08)", display: "flex", justifyContent: "space-between" }}
        >
          <span>
            <strong>{formatStudyDate(s.study_date)}</strong>
            <span style={{ marginLeft: 12, color: "var(--muted)" }}>{s.series_description}</span>
            {s.slice_thickness != null && (
              <span style={{ marginLeft: 12, fontSize: 12, color: "var(--muted)" }}>层厚 {s.slice_thickness}mm</span>
            )}
          </span>
          <span style={{ display: "flex", gap: 16, alignItems: "center" }}>
            {s.processing_status && (
              <span style={{ fontSize: 12, color: s.processing_status === "error" ? "var(--danger)" : "var(--muted)" }}>
                {procLabels[s.processing_status] ?? s.processing_status}
              </span>
            )}
            {s.review_status === "reviewed" && (
              <span style={{ fontSize: 12, color: "var(--success)" }}>已审核</span>
            )}
            <span style={{ color: "var(--muted)" }}>候选 {s.detection_count}</span>
          </span>
        </div>
      ))}
    </div>
  );
}
