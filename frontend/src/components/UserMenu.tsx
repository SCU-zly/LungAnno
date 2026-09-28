import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../store/auth";
import { changePassword, logoutRequest } from "../api/client";

/**
 * 账户菜单：显示当前用户与角色，提供「修改密码」「退出登录」（全账号可用）。
 * compact 模式只渲染小字文字按钮，用于审核页候选面板头部等紧凑位置。
 * 退出为纯前端动作（JWT 无状态，丢弃本地令牌即失效）。
 */
export default function UserMenu({ compact = false }: { compact?: boolean }) {
  const { username, role, logout } = useAuth();
  const navigate = useNavigate();
  const [showModal, setShowModal] = useState(false);
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirmPwd, setConfirmPwd] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleLogout = async () => {
    // 先通知服务端吊销全部会话（单点登录）；失败也照常清本地登录态
    try {
      await logoutRequest();
    } catch {}
    logout();
    navigate("/login");
  };

  const openModal = () => {
    setOldPwd("");
    setNewPwd("");
    setConfirmPwd("");
    setError(null);
    setShowModal(true);
  };

  const handleChange = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    if (newPwd.length < 8) {
      setError("新密码至少 8 位");
      return;
    }
    if (newPwd !== confirmPwd) {
      setError("两次输入的新密码不一致");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await changePassword(oldPwd, newPwd);
      // 改密后旧令牌不再可信，强制重新登录（JWT 无状态，服务端无法召回在途令牌）
      alert("密码已修改，请使用新密码重新登录");
      handleLogout();
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : "修改失败，请重试");
      setSubmitting(false);
    }
  };

  return (
    <>
      <span style={{ display: "inline-flex", alignItems: "center", gap: compact ? 8 : 12, fontSize: compact ? 12 : 13 }}>
        {!compact && (
          <span style={{ color: "var(--muted)" }}>
            {username}（{role === "admin" ? "管理员" : "审核员"}）
          </span>
        )}
        <a href="#" onClick={(e) => { e.preventDefault(); openModal(); }} style={{ color: "var(--primary)" }}>修改密码</a>
        <a href="#" onClick={(e) => { e.preventDefault(); handleLogout(); }} style={{ color: "var(--danger)" }}>退出登录</a>
      </span>

      {showModal && (
        <div
          onClick={() => setShowModal(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
        >
          <form
            onClick={(e) => e.stopPropagation()}
            onSubmit={handleChange}
            style={{ background: "var(--card)", padding: 24, borderRadius: 8, width: 320, boxShadow: "0 4px 16px rgba(0,0,0,.2)" }}
          >
            <h3 style={{ marginBottom: 16 }}>修改密码</h3>
            {error && <p style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>{error}</p>}
            <input type="password" placeholder="原密码" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)} style={inputStyle} autoFocus />
            <input type="password" placeholder="新密码（至少 8 位）" value={newPwd} onChange={(e) => setNewPwd(e.target.value)} style={inputStyle} />
            <input type="password" placeholder="确认新密码" value={confirmPwd} onChange={(e) => setConfirmPwd(e.target.value)} style={inputStyle} />
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" disabled={submitting || !oldPwd || !newPwd} style={{ flex: 1, padding: 8, border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: "pointer", opacity: submitting || !oldPwd || !newPwd ? 0.6 : 1 }}>
                {submitting ? "提交中…" : "确认修改"}
              </button>
              <button type="button" onClick={() => setShowModal(false)} style={{ flex: 1, padding: 8, border: "1px solid #ddd", borderRadius: 4, background: "transparent", cursor: "pointer" }}>
                取消
              </button>
            </div>
            <p style={{ marginTop: 12, fontSize: 12, color: "var(--muted)" }}>修改成功后需要重新登录。</p>
          </form>
        </div>
      )}
    </>
  );
}

const inputStyle: React.CSSProperties = { width: "100%", padding: 10, marginBottom: 12, border: "1px solid #ddd", borderRadius: 4, fontSize: 14 };
