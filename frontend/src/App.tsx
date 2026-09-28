import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./store/auth";
import Login from "./pages/Login";
import BatchList from "./pages/BatchList";
import PatientList from "./pages/PatientList";
import DateList from "./pages/DateList";
import ReviewPage from "./pages/ReviewPage";
import AdminPage from "./pages/AdminPage";

function Guard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" />;
}

/** 管理员守卫：未登录回登录页，非 admin 回批次列表 */
function AdminGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, role } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" />;
  return role === "admin" ? <>{children}</> : <Navigate to="/batches" />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/batches" element={<Guard><BatchList /></Guard>} />
          <Route path="/batches/:batchId" element={<Guard><PatientList /></Guard>} />
          <Route path="/batches/:batchId/patients/:patientId" element={<Guard><DateList /></Guard>} />
          <Route path="/series/:seriesId/review" element={<Guard><ReviewPage /></Guard>} />
          <Route path="/admin" element={<AdminGuard><AdminPage /></AdminGuard>} />
          <Route path="*" element={<Navigate to="/batches" />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
