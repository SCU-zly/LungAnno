import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import api from "../api/client";
import { useAuth } from "../store/auth";

interface SeriesItem { series_id: number; series_uid: string; review_status: string; processing_status?: string | null; detection_count: number | null; }

const statusColors: Record<string, string> = { not_reviewed: "var(--warning)", in_review: "var(--primary)", reviewed: "var(--success)" };
const statusLabels: Record<string, string> = { not_reviewed: "未审核", in_review: "审核中", reviewed: "已审核" };
/** 处理状态中文标签（检测完成时不显示，避免与审核状态重复） */
const procLabels: Record<string, string> = {
  registered: "待处理",
  preprocessing: "预处理中",
  preprocessed: "待推理",
  inferring: "推理中",
  ready_for_review: "待审核",
  error: "处理失败",
};

export default function SeriesList() {
  const { batchId } = useParams();
  const navigate = useNavigate();
  const { role } = useAuth();
  const [series, setSeries] = useState<SeriesItem[]>([]);
  const [filter, setFilter] = useState("all");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [revertError, setRevertError] = useState<string | null>(null);

  const loadSeries = () => {
    setLoadError(null);
    api
      .get(`/series/batches/${batchId}`)
      .then(({ data }) => setSeries(data.series_list))
      .catch(() => setLoadError("Series 列表加载失败"));
  };

  useEffect(() => {
    loadSeries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  /** 管理员回退已提交的审核结果（仅 reviewed 行可见按钮） */
  const handleRevert = async (e: React.MouseEvent, seriesId: number) => {
    e.stopPropagation();
    if (!window.confirm(`确认回退 Series ${seriesId} 的审核结果？\n将删除已提交的审核结论，序列回到未审核状态。`)) return;
    setRevertError(null);
    try {
      await api.post(`/series/${seriesId}/revert-review`);
      loadSeries();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setRevertError(detail ?? "回退失败，请重试");
    }
  };

  const filtered = filter === "all" ? series : series.filter((s) => s.review_status === filter);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
      <h1 style={{ marginBottom: 16 }}>Series 列表</h1>
      <div style={{ marginBottom: 16 }}>
        {["all", "not_reviewed", "in_review", "reviewed"].map((f) => (
          <button key={f} onClick={() => setFilter(f)} style={{ marginRight: 8, padding: "6px 14px", border: "1px solid #ddd", borderRadius: 4, background: filter === f ? "var(--primary)" : "#fff", color: filter === f ? "#fff" : "var(--text)", cursor: "pointer" }}>
            {f === "all" ? "全部" : statusLabels[f]}
          </button>
        ))}
      </div>
      {loadError && (
        <div style={{ textAlign: "center", padding: 24, color: "var(--danger)" }}>
          <p style={{ marginBottom: 12 }}>{loadError}</p>
          <button onClick={loadSeries} style={{ padding: "6px 14px", border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: "pointer" }}>
            重试
          </button>
        </div>
      )}
      {revertError && <p style={{ color: "var(--danger)", fontSize: 13 }}>{revertError}</p>}
      {filtered.map((s) => (
        <div key={s.series_id} onClick={() => navigate(`/series/${s.series_id}/review`)} style={{ background: "var(--card)", padding: 16, marginBottom: 8, borderRadius: 8, cursor: "pointer", boxShadow: "0 1px 3px rgba(0,0,0,.08)", display: "flex", justifyContent: "space-between" }}>
          <span>{s.series_uid}</span>
          <span style={{ display: "flex", gap: 16, alignItems: "center" }}>
            {s.processing_status && s.processing_status !== "detected" && (
              <span style={{ fontSize: 12, color: s.processing_status === "error" ? "var(--danger)" : "var(--muted)" }}>
                {procLabels[s.processing_status] ?? s.processing_status}
              </span>
            )}
            {s.detection_count != null && <span style={{ color: "var(--muted)" }}>{s.detection_count} 候选结节</span>}
            <span style={{ padding: "2px 10px", borderRadius: 12, background: statusColors[s.review_status] ?? "var(--muted)", color: "#fff", fontSize: 12 }}>{statusLabels[s.review_status] ?? s.review_status}</span>
            {role === "admin" && s.review_status === "reviewed" && (
              <button
                onClick={(e) => handleRevert(e, s.series_id)}
                style={{ padding: "4px 12px", fontSize: 12, border: "1px solid var(--warning)", borderRadius: 4, background: "#fff", color: "var(--warning)", cursor: "pointer" }}
              >
                回退审核
              </button>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
