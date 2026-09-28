import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../store/auth";
import api from "../api/client";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!username || !password) {
      setError("请输入用户名和密码");
      return;
    }
    setSubmitting(true);
    try {
      const { data } = await api.post("/auth/login", { username, password });
      login(username, data.access_token, data.refresh_token, data.role);
      navigate("/batches");
    } catch (err: unknown) {
      const resp = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
      if (!resp) {
        // 网络错误 / CORS 拦截：请求未到达后端或响应被浏览器拦下，与凭证无关
        setError("无法连接服务器，请检查网络或稍后重试");
      } else if (resp.status === 401) {
        setError("用户名或密码错误");
      } else {
        setError(`登录失败（服务器错误 ${resp.status}），请稍后重试`);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh" }}>
      <form onSubmit={handleSubmit} style={{ background: "var(--card)", padding: 40, borderRadius: 8, boxShadow: "0 1px 3px rgba(0,0,0,.1)", width: 360 }}>
        <h1 style={{ color: "var(--primary)", fontSize: 36, fontWeight: 700, textAlign: "center", margin: "0 0 24px" }}>LungAnno</h1>
        {error && <p style={{ color: "var(--danger)", textAlign: "center", marginBottom: 12 }}>{error}</p>}
        <input placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} style={inputStyle} autoFocus />
        <input type="password" placeholder="密码" value={password} onChange={(e) => setPassword(e.target.value)} style={inputStyle} />
        <button type="submit" disabled={submitting} style={{ width: "100%", padding: 10, background: "var(--primary)", color: "#fff", border: "none", borderRadius: 4, cursor: submitting ? "not-allowed" : "pointer", fontSize: 16, opacity: submitting ? 0.7 : 1 }}>
          {submitting ? "请稍候…" : "登录"}
        </button>
        <p style={{ textAlign: "center", marginTop: 16, fontSize: 13, color: "var(--muted)" }}>账号由管理员统一创建</p>
      </form>
    </div>
  );
}

const inputStyle: React.CSSProperties = { width: "100%", padding: 10, marginBottom: 12, border: "1px solid #ddd", borderRadius: 4, fontSize: 14 };
