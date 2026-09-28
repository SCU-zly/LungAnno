import axios from "axios";

/**
 * 后端 API 基础地址（单一配置源，供 axios 与 XHR 型加载器共同引用）。
 * 默认 "/api" 走 Vite proxy 同源转发——远程访问 / 127.0.0.1 / 局域网 IP 访问均可用，
 * 且全链路同源后 CORS/COEP 不再相关（SEC-005）；生产部署可用 VITE_API_BASE 覆盖。
 */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";
// NIfTI 加载必须同源（COEP require-corp），开发走 Vite proxy
export const VOLUME_BASE = "/api";

const api = axios.create({ baseURL: API_BASE });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retry) {
      error.config._retry = true;
      const refresh = localStorage.getItem("refresh_token");
      if (refresh) {
        try {
          const { data } = await axios.post(`${API_BASE}/auth/refresh`, { refresh_token: refresh });
          localStorage.setItem("access_token", data.access_token);
          localStorage.setItem("refresh_token", data.refresh_token);
          error.config.headers.Authorization = `Bearer ${data.access_token}`;
          return api(error.config);
        } catch {}
      }
      localStorage.clear();
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export default api;

/* ------------------------------------------------------------------ */
/* 新端点封装：页面统一走这里，后端路径微调时只需改这一处               */
/* ------------------------------------------------------------------ */

/** 批次内患者条目（GET /batches/{batchId}/patients） */
export interface PatientItem {
  patient_id: string; // 含前导零，用于 URL
  display_id: string;
  name: string;
  series_total: number;
  reviewed_count: number;
}

/** 患者下序列条目（GET /batches/{batchId}/patients/{patientId}/series） */
export interface PatientSeriesItem {
  series_id: number;
  series_uid: string;
  study_date: string; // "20200419" 或 ""
  series_description: string;
  slice_thickness: number | null;
  processing_status: string;
  review_status: string;
  detection_count: number;
}

/** 序列基本信息（GET /series/{seriesId}/info） */
export interface SeriesInfo {
  series_id: number;
  series_uid: string;
  patient_id: string;
  patient_name: string;
  study_date: string;
  slice_thickness: number | null;
  series_description: string;
  processing_status: string;
  review_status: string;
  batch_id: number | null; // 所属批次（审核页「返回列表」定位用）
}

/** 患者临床信息（GET /series/{seriesId}/patient-metadata），fields 可能为空对象 */
export interface PatientMetadata {
  patient_id: string | null;
  name: string | null;
  fields: Record<string, string>;
}

/** 管理后台用户条目（GET /admin/users） */
export interface AdminUser {
  id: number;
  username: string;
  role: string;
  created_at: string;
}

export const fetchPatients = (batchId: string | number) =>
  api.get<PatientItem[]>(`/batches/${batchId}/patients`).then((r) => r.data);

export const fetchPatientSeries = (batchId: string | number, patientId: string) =>
  api.get<PatientSeriesItem[]>(`/batches/${batchId}/patients/${patientId}/series`).then((r) => r.data);

export const fetchSeriesInfo = (seriesId: string | number) =>
  api.get<SeriesInfo>(`/series/${seriesId}/info`).then((r) => r.data);

export const fetchPatientMetadata = (seriesId: string | number) =>
  api.get<PatientMetadata>(`/series/${seriesId}/patient-metadata`).then((r) => r.data);

export const fetchAdminUsers = () =>
  api.get<AdminUser[]>("/admin/users").then((r) => r.data);

export const createAdminUser = (body: { username: string; password: string; role: string }) =>
  api.post<{ id: number; username: string; role: string }>("/admin/users", body).then((r) => r.data);

export const fetchBatchAccess = (batchId: string | number) =>
  api.get<{ user_ids: number[] }>(`/admin/batches/${batchId}/access`).then((r) => r.data);

export const saveBatchAccess = (batchId: string | number, userIds: number[]) =>
  api.put<{ user_ids: number[] }>(`/admin/batches/${batchId}/access`, { user_ids: userIds }).then((r) => r.data);

/** 修改自己的密码（POST /auth/change-password），成功后应强制重新登录 */
export const changePassword = (oldPassword: string, newPassword: string) =>
  api.post<{ message: string }>("/auth/change-password", { old_password: oldPassword, new_password: newPassword }).then((r) => r.data);

/** 管理员重置用户密码（POST /admin/users/{id}/reset-password） */
export const resetUserPassword = (userId: number, password: string) =>
  api.post<{ message: string }>(`/admin/users/${userId}/reset-password`, { password }).then((r) => r.data);

/** 管理员删除用户（DELETE /admin/users/{id}；有审核记录的用户后端会拒绝） */
export const deleteUser = (userId: number) =>
  api.delete<{ message: string }>(`/admin/users/${userId}`).then((r) => r.data);

/** 当前用户在本序列的既有审核结论（GET /series/{id}/my-review，重审回显） */
export interface MyReview {
  submitted: boolean;
  verdicts: Record<string, string>; // detection_id(字符串键) -> verdict
}
export const fetchMyReview = (seriesId: string | number) =>
  api.get<MyReview>(`/series/${seriesId}/my-review`).then((r) => r.data);

/** 服务端登出（POST /auth/logout）：吊销本账号全部会话（单点登录配套） */
export const logoutRequest = () => api.post<{ message: string }>("/auth/logout").then((r) => r.data);
