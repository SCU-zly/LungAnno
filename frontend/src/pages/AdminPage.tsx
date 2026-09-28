import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api, {
  fetchAdminUsers,
  createAdminUser,
  fetchBatchAccess,
  saveBatchAccess,
  resetUserPassword,
  deleteUser,
  type AdminUser,
} from "../api/client";
import UserMenu from "../components/UserMenu";
import { useAuth } from "../store/auth";

interface BatchItem { batch_id: number; batch_name: string; }

/** 从 axios 错误中提取后端 detail 信息 */
function apiErrorMessage(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail ?? fallback;
}

/** 单个批次的授权面板：展开时拉取已授权 user_ids，勾选 reviewer 后保存 */
function BatchAccessPanel({ batchId, reviewers }: { batchId: number; reviewers: AdminUser[] }) {
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    fetchBatchAccess(batchId)
      .then(({ user_ids }) => setChecked(new Set(user_ids)))
      .catch((e) => setLoadError(apiErrorMessage(e, "授权信息加载失败")));
  }, [batchId]);

  const toggle = (id: number) => {
    setSaved(false);
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSave = async () => {
    if (saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await saveBatchAccess(batchId, [...checked]);
      setSaved(true);
    } catch (e) {
      setSaveError(apiErrorMessage(e, "保存失败，请重试"));
    } finally {
      setSaving(false);
    }
  };

  if (loadError) return <div style={{ padding: "8px 16px", fontSize: 13, color: "var(--danger)" }}>{loadError}</div>;
  return (
    <div style={{ padding: "8px 16px 12px", borderTop: "1px solid #e5e7eb" }}>
      {reviewers.length === 0 ? (
        <p style={{ fontSize: 13, color: "var(--muted)" }}>暂无审核员账号</p>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 8 }}>
          {reviewers.map((u) => (
            <label key={u.id} style={{ fontSize: 13, cursor: "pointer" }}>
              <input type="checkbox" checked={checked.has(u.id)} onChange={() => toggle(u.id)} style={{ marginRight: 4 }} />
              {u.username}
            </label>
          ))}
        </div>
      )}
      <button
        onClick={handleSave}
        disabled={saving}
        style={{ padding: "6px 14px", border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: saving ? "not-allowed" : "pointer" }}
      >
        {saving ? "保存中…" : "保存"}
      </button>
      {saved && <span style={{ marginLeft: 8, fontSize: 13, color: "var(--success)" }}>已保存</span>}
      {saveError && <span style={{ marginLeft: 8, fontSize: 13, color: "var(--danger)" }}>{saveError}</span>}
    </div>
  );
}

/** 管理后台：用户管理 + 批次授权（仅 admin 可见，路由层已拦截） */
export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("reviewer");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [batches, setBatches] = useState<BatchItem[]>([]);
  const [batchesError, setBatchesError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  // 重置密码弹窗状态
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [resetPwd, setResetPwd] = useState("");
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [resetDoneFor, setResetDoneFor] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const { username: currentUsername } = useAuth();

  const handleDelete = async (u: AdminUser) => {
    if (!window.confirm(`确认删除用户「${u.username}」？\n其批次授权与序列认领将被清除（有审核记录的用户会被后端拒绝删除）。`)) return;
    setDeleteError(null);
    try {
      await deleteUser(u.id);
      loadUsers();
    } catch (err) {
      setDeleteError(apiErrorMessage(err, "删除失败，请重试"));
    }
  };

  const loadUsers = () => {
    setUsersError(null);
    fetchAdminUsers()
      .then(setUsers)
      .catch((e) => setUsersError(apiErrorMessage(e, "用户列表加载失败")));
  };

  useEffect(() => {
    loadUsers();
    api
      .get("/batches")
      .then(({ data }) => setBatches(data))
      .catch(() => setBatchesError("批次列表加载失败"));
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (creating || !username || !password) return;
    setCreating(true);
    setCreateError(null);
    try {
      await createAdminUser({ username, password, role });
      setUsername("");
      setPassword("");
      setRole("reviewer");
      loadUsers();
    } catch (err) {
      setCreateError(apiErrorMessage(err, "创建失败，请重试"));
    } finally {
      setCreating(false);
    }
  };

  const reviewers = users.filter((u) => u.role === "reviewer");
  const inputStyle = { padding: "6px 10px", border: "1px solid #ddd", borderRadius: 4, fontSize: 14 } as const;

  const openReset = (u: AdminUser) => {
    setResetTarget(u);
    setResetPwd("");
    setResetError(null);
  };

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    if (resetting || !resetTarget) return;
    if (resetPwd.length < 8) {
      setResetError("新密码至少 8 位");
      return;
    }
    setResetting(true);
    setResetError(null);
    try {
      await resetUserPassword(resetTarget.id, resetPwd);
      setResetDoneFor(resetTarget.username);
      setResetTarget(null);
    } catch (err) {
      setResetError(apiErrorMessage(err, "重置失败，请重试"));
    } finally {
      setResetting(false);
    }
  };

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1>管理后台</h1>
        <UserMenu />
      </div>
      <p style={{ marginBottom: 16 }}>
        <Link to="/batches" style={{ color: "var(--primary)" }}>← 返回批次列表</Link>
      </p>

      {/* 用户管理 */}
      <h2 style={{ marginBottom: 12 }}>用户管理</h2>
      <div style={{ background: "var(--card)", padding: 16, marginBottom: 16, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,.08)" }}>
        {usersError ? (
          <div style={{ textAlign: "center", padding: 24, color: "var(--danger)" }}>
            <p style={{ marginBottom: 12 }}>{usersError}</p>
            <button onClick={loadUsers} style={{ padding: "6px 14px", border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: "pointer" }}>
              重试
            </button>
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--muted)" }}>
                <th style={{ padding: "6px 8px", borderBottom: "1px solid #e5e7eb" }}>ID</th>
                <th style={{ padding: "6px 8px", borderBottom: "1px solid #e5e7eb" }}>用户名</th>
                <th style={{ padding: "6px 8px", borderBottom: "1px solid #e5e7eb" }}>角色</th>
                <th style={{ padding: "6px 8px", borderBottom: "1px solid #e5e7eb" }}>创建时间</th>
                <th style={{ padding: "6px 8px", borderBottom: "1px solid #e5e7eb" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td style={{ padding: "6px 8px", borderBottom: "1px solid #f1f5f9" }}>{u.id}</td>
                  <td style={{ padding: "6px 8px", borderBottom: "1px solid #f1f5f9" }}>{u.username}</td>
                  <td style={{ padding: "6px 8px", borderBottom: "1px solid #f1f5f9" }}>{u.role === "admin" ? "管理员" : "审核员"}</td>
                  <td style={{ padding: "6px 8px", borderBottom: "1px solid #f1f5f9", color: "var(--muted)" }}>{u.created_at}</td>
                  <td style={{ padding: "6px 8px", borderBottom: "1px solid #f1f5f9" }}>
                    <button onClick={() => openReset(u)} style={{ padding: "3px 10px", fontSize: 12, border: "1px solid #ddd", borderRadius: 4, background: "transparent", cursor: "pointer" }}>
                      重置密码
                    </button>
                    {resetDoneFor === u.username && <span style={{ marginLeft: 8, fontSize: 12, color: "var(--success)" }}>已重置</span>}
                    {u.username !== currentUsername && (
                      <button onClick={() => handleDelete(u)} style={{ marginLeft: 8, padding: "3px 10px", fontSize: 12, border: "1px solid var(--danger)", borderRadius: 4, background: "transparent", color: "var(--danger)", cursor: "pointer" }}>
                        删除
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <form onSubmit={handleCreate} style={{ display: "flex", gap: 8, marginTop: 16, alignItems: "center", flexWrap: "wrap" }}>
          <input placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} style={inputStyle} />
          <input placeholder="密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={inputStyle} />
          <select value={role} onChange={(e) => setRole(e.target.value)} style={inputStyle}>
            <option value="reviewer">审核员</option>
            <option value="admin">管理员</option>
          </select>
          <button
            type="submit"
            disabled={creating || !username || !password}
            style={{ padding: "6px 14px", border: "none", borderRadius: 4, background: !creating && username && password ? "var(--primary)" : "#ccc", color: "#fff", cursor: !creating && username && password ? "pointer" : "not-allowed" }}
          >
            {creating ? "创建中…" : "创建用户"}
          </button>
        </form>
        {createError && <p style={{ marginTop: 8, fontSize: 13, color: "var(--danger)" }}>{createError}</p>}
        {deleteError && <p style={{ marginTop: 8, fontSize: 13, color: "var(--danger)" }}>{deleteError}</p>}
      </div>

      {/* 批次授权 */}
      <h2 style={{ marginBottom: 12 }}>批次授权</h2>
      {batchesError && <p style={{ color: "var(--danger)" }}>{batchesError}</p>}
      {!batchesError && batches.length === 0 && <p style={{ color: "var(--muted)" }}>暂无批次数据</p>}
      {batches.map((b) => (
        <div key={b.batch_id} style={{ background: "var(--card)", marginBottom: 8, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,.08)" }}>
          <div
            onClick={() => setExpandedId(expandedId === b.batch_id ? null : b.batch_id)}
            style={{ padding: 16, cursor: "pointer", display: "flex", justifyContent: "space-between" }}
          >
            <strong>{b.batch_name}</strong>
            <span style={{ color: "var(--muted)", fontSize: 13 }}>{expandedId === b.batch_id ? "收起 ▲" : "授权 ▼"}</span>
          </div>
          {expandedId === b.batch_id && <BatchAccessPanel batchId={b.batch_id} reviewers={reviewers} />}
        </div>
      ))}

      {/* 重置密码弹窗 */}
      {resetTarget && (
        <div
          onClick={() => setResetTarget(null)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 100 }}
        >
          <form
            onClick={(e) => e.stopPropagation()}
            onSubmit={handleReset}
            style={{ background: "var(--card)", padding: 24, borderRadius: 8, width: 320, boxShadow: "0 4px 16px rgba(0,0,0,.2)" }}
          >
            <h3 style={{ marginBottom: 16 }}>重置密码：{resetTarget.username}</h3>
            {resetError && <p style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>{resetError}</p>}
            <input
              type="password"
              placeholder="新密码（至少 8 位）"
              value={resetPwd}
              onChange={(e) => setResetPwd(e.target.value)}
              style={{ width: "100%", padding: 10, marginBottom: 12, border: "1px solid #ddd", borderRadius: 4, fontSize: 14 }}
              autoFocus
            />
            <div style={{ display: "flex", gap: 8 }}>
              <button type="submit" disabled={resetting || !resetPwd} style={{ flex: 1, padding: 8, border: "none", borderRadius: 4, background: "var(--primary)", color: "#fff", cursor: "pointer", opacity: resetting || !resetPwd ? 0.6 : 1 }}>
                {resetting ? "提交中…" : "确认重置"}
              </button>
              <button type="button" onClick={() => setResetTarget(null)} style={{ flex: 1, padding: 8, border: "1px solid #ddd", borderRadius: 4, background: "transparent", cursor: "pointer" }}>
                取消
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
