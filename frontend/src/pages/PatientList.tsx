import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { fetchPatients, type PatientItem } from "../api/client";
import UserMenu from "../components/UserMenu";

/** 批次 → 患者列表页：卡片式，点击进入该患者的检查日期/序列列表 */
export default function PatientList() {
  const { batchId } = useParams();
  const [patients, setPatients] = useState<PatientItem[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadPatients = () => {
    setLoadError(null);
    fetchPatients(batchId!)
      .then(setPatients)
      .catch(() => setLoadError("患者列表加载失败"));
  };

  useEffect(() => {
    loadPatients();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  return (
    <div style={{ maxWidth: 800, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1>患者列表</h1>
        <UserMenu />
      </div>
      <p style={{ marginBottom: 16 }}>
        <Link to="/batches" style={{ color: "var(--primary)" }}>← 返回批次列表</Link>
      </p>
      {loadError ? (
        <div style={{ textAlign: "center", padding: 24, color: "var(--danger)" }}>
          <p style={{ marginBottom: 12 }}>{loadError}</p>
          <button onClick={loadPatients} style={{ padding: "6px 14px", border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: "pointer" }}>
            重试
          </button>
        </div>
      ) : patients.length === 0 && <p style={{ color: "var(--muted)" }}>暂无患者数据</p>}
      {patients.map((p) => (
        <Link
          key={p.patient_id}
          to={`/batches/${batchId}/patients/${p.patient_id}`}
          state={{ name: p.name, display_id: p.display_id }}
          style={{ display: "block", background: "var(--card)", padding: 16, marginBottom: 8, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,.08)", color: "var(--text)" }}
        >
          <strong style={{ fontSize: 18 }}>{p.name}</strong>
          <span style={{ float: "right", color: "var(--muted)" }}>{p.reviewed_count}/{p.series_total} 已审核</span>
          <div style={{ marginTop: 6, fontSize: 12, color: "var(--muted)" }}>ID: {p.display_id}</div>
        </Link>
      ))}
    </div>
  );
}
