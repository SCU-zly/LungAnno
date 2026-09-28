import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../api/client";
import { useAuth } from "../store/auth";
import UserMenu from "../components/UserMenu";

interface BatchItem { batch_id: number; batch_name: string; series_total: number; reviewed_count: number; processing?: Record<string, number>; }

/** 处理状态中文标签（批量作业进度展示） */
const procLabels: Record<string, string> = {
  registered: "待处理",
  preprocessing: "预处理中",
  preprocessed: "待推理",
  inferring: "推理中",
  detected: "检测完成",
  ready_for_review: "待审核",
  error: "失败",
};

export default function BatchList() {
  const { role } = useAuth();
  const [batches, setBatches] = useState<BatchItem[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadBatches = () => {
    setLoadError(null);
    api
      .get("/batches")
      .then(({ data }) => setBatches(data))
      .catch(() => setLoadError("批次列表加载失败"));
  };

  useEffect(() => {
    loadBatches();
  }, []);

  return (
    <div style={{ maxWidth: 800, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1>
          批次列表
          {role === "admin" && (
            <Link to="/admin" style={{ marginLeft: 12, fontSize: 14, fontWeight: "normal", color: "var(--primary)" }}>管理后台</Link>
          )}
        </h1>
        <UserMenu />
      </div>
      {loadError ? (
        <div style={{ textAlign: "center", padding: 24, color: "var(--danger)" }}>
          <p style={{ marginBottom: 12 }}>{loadError}</p>
          <button onClick={loadBatches} style={{ padding: "6px 14px", border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: "pointer" }}>
            重试
          </button>
        </div>
      ) : batches.length === 0 && <p style={{ color: "var(--muted)" }}>暂无批次数据</p>}
      {batches.map((b) => (
        <Link key={b.batch_id} to={`/batches/${b.batch_id}`} style={{ display: "block", background: "var(--card)", padding: 16, marginBottom: 8, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,.08)", color: "var(--text)" }}>
          <strong>{b.batch_name}</strong>
          <span style={{ float: "right", color: "var(--muted)" }}>{b.reviewed_count}/{b.series_total} 已审核</span>
          {b.processing && Object.keys(b.processing).length > 0 && (
            <div style={{ marginTop: 6, fontSize: 12, color: "var(--muted)" }}>
              处理进度：{Object.entries(b.processing).map(([st, n]) => (
                <span key={st} style={{ color: st === "error" ? "var(--danger)" : undefined }}>{procLabels[st] ?? st} {n}</span>
              )).reduce<React.ReactNode[]>((acc, el, i) => (i === 0 ? [el] : [...acc, " · ", el]), [])}
            </div>
          )}
        </Link>
      ))}
    </div>
  );
}
