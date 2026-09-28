import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import api, { fetchSeriesInfo, fetchPatientMetadata, fetchMyReview, type SeriesInfo, type PatientMetadata } from "../api/client";
import CornerstoneViewer, { type CornerstoneViewerHandle } from "../components/viewer/CornerstoneViewer";
import { HIGH_SCORE_THRESHOLD, type Detection } from "../types/detection";

type Verdict = "accepted" | "rejected" | "uncertain" | null;

/** 患者信息字段优先展示顺序（存在的键按此序排列，其余非空键随后） */
const PREFERRED_FIELD_ORDER = [
  "性别", "年龄", "吸烟史", "手术日期", "tumor_loc", "肿瘤大小", "Histology", "组织学类型",
  "分化程度", "T分期", "N_stage", "M_stage", "TNM分期", "Stage", "基因检测", "EGFR", "ALK-V", "ROS-1", "PD-L1",
];

/** 过滤无效字段值：空串 / nan 不展示 */
function isValidFieldValue(v: string): boolean {
  const t = v.trim();
  return t !== "" && t.toLowerCase() !== "nan";
}

/** 从 axios 错误中提取后端 detail 信息 */
function apiErrorMessage(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail ?? fallback;
}

export default function ReviewPage() {
  const { seriesId } = useParams();
  const viewerRef = useRef<CornerstoneViewerHandle>(null);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [verdicts, setVerdicts] = useState<Record<number, Verdict>>({});
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const navigate = useNavigate();
  // 候选选中是单选：点卡片 = 只显示该候选框并跳片（再选别的即切换）/
  // 再点一次 = 取消隐藏；点画布上的框 = 取消选中（画布只画选中框）
  const toggleSelect = (id: number, jumpTo?: number) => {
    const adding = selectedId !== id;
    setSelectedId(adding ? id : null);
    if (adding && jumpTo !== undefined) viewerRef.current?.jumpToWorldZ(jumpTo);
  };
  // BboxOverlay 的 prop 是集合签名，包一层单例集合（overlay 零改动）
  const selectedSet = useMemo(() => (selectedId === null ? new Set<number>() : new Set([selectedId])), [selectedId]);
  // 返回该序列所属的日期列表（系列信息未载到时退回批次列表）
  const handleBack = () => {
    if (seriesInfo?.batch_id != null && seriesInfo.patient_id) navigate(`/batches/${seriesInfo.batch_id}/patients/${seriesInfo.patient_id}`);
    else navigate("/batches");
  };
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // 左上角叠加的序列信息 + 右侧面板底部的患者临床信息
  const [seriesInfo, setSeriesInfo] = useState<SeriesInfo | null>(null);
  const [patientMeta, setPatientMeta] = useState<PatientMetadata | null>(null);
  // 重审：本人上次提交的结论快照（回显 + 差异比较）；submittedByMe 区分首审/重审
  const [originalVerdicts, setOriginalVerdicts] = useState<Record<number, Verdict>>({});
  const [submittedByMe, setSubmittedByMe] = useState(false);

  const loadDetections = () => {
    setLoadError(null);
    api
      .get(`/series/${seriesId}/detections`)
      .then(({ data }) => setDetections(data))
      .catch((e) => setLoadError(apiErrorMessage(e, "候选列表加载失败")));
  };

  useEffect(() => {
    loadDetections();
    // 多人审核语义：claim 必成功（进入即审核），失败仅在网络异常时静默忽略
    api.post(`/series/${seriesId}/claim`).catch(() => {});
    fetchSeriesInfo(seriesId!).then(setSeriesInfo).catch(() => {});
    fetchPatientMetadata(seriesId!).then(setPatientMeta).catch(() => {});
    // 重审回显：拉取本人上次提交的结论作为初始 verdicts 与快照
    fetchMyReview(seriesId!)
      .then((data) => {
        if (data.submitted) {
          const parsed: Record<number, Verdict> = {};
          for (const [k, v] of Object.entries(data.verdicts)) parsed[Number(k)] = v as Verdict;
          setVerdicts(parsed);
          setOriginalVerdicts(parsed);
          setSubmittedByMe(true);
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesId]);

  const handleVerdict = (detId: number, verdict: Verdict) => {
    setVerdicts((prev) => ({ ...prev, [detId]: verdict }));
  };

  // 确认框与提交：未标记的候选后端默认记为拒绝（确认框里明示条数）
  const [showConfirm, setShowConfirm] = useState(false);
  const markedCounts = { accepted: 0, rejected: 0, uncertain: 0 };
  for (const v of Object.values(verdicts)) {
    if (v) markedCounts[v] += 1;
  }
  const markedTotal = markedCounts.accepted + markedCounts.rejected + markedCounts.uncertain;
  const unmarkedTotal = detections.length - markedTotal;
  // 重审差异：与本人上次提交快照逐项比较（黄色「重新提交」仅在有不同时出现）
  const diffCounts = { accepted: 0, rejected: 0, uncertain: 0 };
  let isDirty = false;
  if (submittedByMe) {
    const keys = new Set([...Object.keys(verdicts), ...Object.keys(originalVerdicts)]);
    for (const k of keys) {
      const cur = verdicts[Number(k)] ?? null;
      const prev = originalVerdicts[Number(k)] ?? null;
      if (cur !== prev) {
        isDirty = true;
        if (cur) diffCounts[cur] += 1;
      }
    }
  }
  const canSubmit = detections.length > 0 && !submitting && (!submittedByMe || isDirty);

  const doSubmit = async () => {
    if (submitting) return;
    const v = Object.entries(verdicts).map(([detection_id, verdict]) => ({ detection_id: Number(detection_id), verdict }));
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.post(`/series/${seriesId}/submit-review`, { verdicts: v });
      // 提交完成返回上一级（该患者的检查日期列表；信息未载到则退批次列表）
      if (seriesInfo?.batch_id != null && seriesInfo.patient_id) navigate(`/batches/${seriesInfo.batch_id}/patients/${seriesInfo.patient_id}`);
      else navigate("/batches");
    } catch (e) {
      setSubmitError(apiErrorMessage(e, "提交失败，请重试"));
      setSubmitting(false);
    }
  };

  // Enter 绑定：确认框开着=确认提交，没开=弹出确认框（等同点提交按钮）；
  // 焦点在输入控件/按钮上时不拦（避免与原生 Enter 行为冲突）
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || e.isComposing) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "BUTTON") return;
      if (showConfirm) doSubmit();
      else if (canSubmit) setShowConfirm(true);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showConfirm, canSubmit, submitting, verdicts, seriesInfo]);

  return (
    <div style={{ display: "flex", height: "100vh", position: "relative" }}>
      {/* 左上角序列信息叠加层（黑底查看器之上，不拦截鼠标） */}
      {seriesInfo && (
        <div style={{ position: "absolute", top: 10, left: 12, zIndex: 20, pointerEvents: "none", color: "#fff", textShadow: "0 1px 2px rgba(0,0,0,.8)" }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Series #{seriesInfo.series_id}</div>
          <div style={{ fontSize: 11, opacity: 0.75 }}>
            {seriesInfo.series_uid.length > 40 ? `${seriesInfo.series_uid.slice(0, 40)}…` : seriesInfo.series_uid}
          </div>
        </div>
      )}
      {/* Viewer */}
      <CornerstoneViewer
        ref={viewerRef}
        seriesId={Number(seriesId)}
        detections={detections}
        selectedIds={selectedSet}
        onSelectDetection={(id) => toggleSelect(id)}
      />
      {/* Candidate Panel */}
      <div style={{ width: 360, background: "var(--card)", overflow: "auto", borderLeft: "1px solid #e5e7eb", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: 16, borderBottom: "1px solid #e5e7eb" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3>候选结节 ({detections.length})</h3>
            <button onClick={handleBack} style={{ padding: "4px 10px", fontSize: 12, border: "1px solid #ddd", borderRadius: 4, background: "transparent", color: "var(--primary)", cursor: "pointer" }}>
              ← 返回列表
            </button>
          </div>
        </div>
        <div style={{ flex: 1, overflow: "auto", padding: 12 }}>
          {loadError ? (
            <div style={{ textAlign: "center", padding: 24, color: "var(--danger)" }}>
              <p style={{ marginBottom: 12 }}>{loadError}</p>
              <button onClick={loadDetections} style={{ padding: "6px 14px", border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: "pointer" }}>
                重试
              </button>
            </div>
          ) : (
            [...detections].sort((a, b) => b.score - a.score).map((d) => {
              const v = verdicts[d.id];
              return (
                <div
                  key={d.id}
                  onClick={() => toggleSelect(d.id, d.box_z)}
                  style={{ padding: 10, marginBottom: 8, borderRadius: 6, border: `1px solid ${selectedId === d.id ? "var(--primary)" : v === "accepted" ? "var(--success)" : v === "rejected" ? "var(--danger)" : v === "uncertain" ? "var(--warning)" : "#e5e7eb"}`, cursor: "pointer", background: selectedId === d.id ? "rgba(59,130,246,0.08)" : "transparent" }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                    <strong>#{d.id}</strong>
                    <span style={{ fontSize: 12, color: d.score > HIGH_SCORE_THRESHOLD ? "var(--danger)" : "var(--muted)" }}>score: {d.score.toFixed(2)}</span>
                  </div>
                  <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>Slice {d.slice_index}</div>
                  <div style={{ display: "flex", gap: 4 }}>
                    {(["accepted", "rejected", "uncertain"] as Verdict[]).map((verdict) => (
                      <button key={verdict} onClick={(e) => { e.stopPropagation(); handleVerdict(d.id, verdict); }} style={{ flex: 1, padding: "4px 0", fontSize: 12, border: "none", borderRadius: 3, cursor: "pointer", background: v === verdict ? (verdict === "accepted" ? "var(--success)" : verdict === "rejected" ? "var(--danger)" : "var(--warning)") : "#f1f5f9", color: v === verdict ? "#fff" : "var(--text)" }}>
                        {verdict === "accepted" ? "接受" : verdict === "rejected" ? "拒绝" : "不确定"}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </div>
        {/* 患者临床信息卡片：候选列表之后、提交按钮之前 */}
        {patientMeta && (
          <div style={{ padding: 12, borderTop: "1px solid #e5e7eb", maxHeight: 220, overflow: "auto", fontSize: 12 }}>
            <div style={{ marginBottom: 8 }}>
              <strong style={{ fontSize: 13 }}>患者信息</strong>
              {patientMeta.name && <span style={{ marginLeft: 8 }}>{patientMeta.name}</span>}
            </div>
            {(() => {
              const fields = patientMeta.fields ?? {};
              // 优先键按预定义顺序排列，其余非空键随后
              const orderedKeys = [
                ...PREFERRED_FIELD_ORDER.filter((k) => k in fields && isValidFieldValue(fields[k])),
                ...Object.keys(fields).filter((k) => !PREFERRED_FIELD_ORDER.includes(k) && isValidFieldValue(fields[k])),
              ];
              if (orderedKeys.length === 0) return <p style={{ color: "var(--muted)", margin: 0 }}>暂无临床信息</p>;
              return (
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <tbody>
                    {orderedKeys.map((k) => (
                      <tr key={k}>
                        <td style={{ padding: "2px 8px 2px 0", color: "var(--muted)", whiteSpace: "nowrap", verticalAlign: "top" }}>{k}</td>
                        <td style={{ padding: "2px 0", color: "var(--text)" }}>{fields[k]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              );
            })()}
          </div>
        )}
        <div style={{ padding: 16, borderTop: "1px solid #e5e7eb" }}>
          {submitError && <p style={{ marginBottom: 8, fontSize: 12, color: "var(--danger)" }}>{submitError}</p>}
          <button
            onClick={() => setShowConfirm(true)}
            disabled={!canSubmit}
            style={{
              width: "100%", padding: 12, border: "none", borderRadius: 6, fontSize: 15, color: "#fff",
              // 三态：首审绿 / 已提交未改灰 / 重审有改动黄
              background: !submittedByMe ? (canSubmit ? "var(--success)" : "#ccc") : isDirty ? "var(--warning)" : "#ccc",
              cursor: canSubmit ? "pointer" : "not-allowed",
            }}
          >
            {submitting
              ? "提交中…"
              : !submittedByMe
                ? `提交审核（已标记 ${markedTotal}/${detections.length}，未标记默认拒绝）`
                : isDirty
                  ? "重新提交"
                  : "已提交（未修改）"}
          </button>
        </div>
      </div>

      {/* 提交确认框：明示已标记分布与“未标记默认拒绝”条数（Enter=确认，Esc=取消） */}
      {showConfirm && (
        <div
          onClick={() => !submitting && setShowConfirm(false)}
          onKeyDown={(e) => { if (e.key === "Escape" && !submitting) setShowConfirm(false); }}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: "var(--card)", padding: 24, borderRadius: 8, width: 340, boxShadow: "0 4px 16px rgba(0,0,0,.2)" }}
          >
            <h3 style={{ marginBottom: 16 }}>{submittedByMe ? "确认重新提交？" : "确认提交审核？"}</h3>
            <div style={{ fontSize: 14, lineHeight: 1.9, marginBottom: 8 }}>
              <div>接受：<strong style={{ color: "var(--success)" }}>{markedCounts.accepted}</strong></div>
              <div>不确定：<strong style={{ color: "var(--warning)" }}>{markedCounts.uncertain}</strong></div>
              <div>拒绝（已标记）：<strong style={{ color: "var(--danger)" }}>{markedCounts.rejected}</strong></div>
              <div style={{ color: "var(--muted)" }}>未标记 <strong>{unmarkedTotal}</strong> 个候选将默认记为拒绝</div>
            </div>
            {submittedByMe && (
              <div style={{ fontSize: 13, lineHeight: 1.9, padding: "8px 0", borderTop: "1px dashed #e5e7eb", color: "var(--text)" }}>
                <div style={{ color: "var(--muted)", marginBottom: 2 }}>相对上次提交的改动：</div>
                <div>改为接受：<strong style={{ color: "var(--success)" }}>{diffCounts.accepted}</strong></div>
                <div>改为拒绝：<strong style={{ color: "var(--danger)" }}>{diffCounts.rejected}</strong></div>
                <div>改为不确定：<strong style={{ color: "var(--warning)" }}>{diffCounts.uncertain}</strong></div>
              </div>
            )}
            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button onClick={doSubmit} disabled={submitting} style={{ flex: 1, padding: 10, border: "none", borderRadius: 4, background: "var(--success)", color: "#fff", cursor: "pointer", fontSize: 14, opacity: submitting ? 0.6 : 1 }}>
                {submitting ? "提交中…" : "确认提交 (Enter)"}
              </button>
              <button onClick={() => setShowConfirm(false)} disabled={submitting} style={{ flex: 1, padding: 10, border: "1px solid #ddd", borderRadius: 4, background: "transparent", cursor: "pointer", fontSize: 14 }}>
                取消 (Esc)
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
