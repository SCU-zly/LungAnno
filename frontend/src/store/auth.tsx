import React, { createContext, useContext, useState, useCallback } from "react";

interface AuthState { isAuthenticated: boolean; username: string | null; role: string | null; }
interface AuthContextType extends AuthState { login: (username: string, access: string, refresh: string, role?: string | null) => void; logout: () => void; }

const AuthContext = createContext<AuthContextType>(null!);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({ isAuthenticated: !!localStorage.getItem("access_token"), username: localStorage.getItem("username"), role: localStorage.getItem("role") });
  const login = useCallback((username: string, access: string, refresh: string, role?: string | null) => {
    localStorage.setItem("access_token", access);
    localStorage.setItem("refresh_token", refresh);
    localStorage.setItem("username", username);
    localStorage.setItem("role", role ?? "reviewer");
    setState({ isAuthenticated: true, username, role: role ?? "reviewer" });
  }, []);
  const logout = useCallback(() => { localStorage.clear(); setState({ isAuthenticated: false, username: null, role: null }); }, []);
  return <AuthContext.Provider value={{ ...state, login, logout }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
