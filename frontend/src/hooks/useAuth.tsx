import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { get, post, resetCsrf } from "../api/client";
import type { User } from "../api/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  refresh: async () => {},
  logout: async () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await get<{ authenticated: boolean; user?: User }>("/api/auth/me/");
      setUser(me.authenticated && me.user ? me.user : null);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await post("/api/auth/logout/");
    } finally {
      resetCsrf();
      setUser(null);
    }
  }, []);

  useEffect(() => {
    refresh();
    const onUnauthorized = () => setUser(null);
    window.addEventListener("bc:unauthorized", onUnauthorized);
    return () => window.removeEventListener("bc:unauthorized", onUnauthorized);
  }, [refresh]);

  useEffect(() => {
    const theme = user?.preferences.theme || "system";
    document.documentElement.dataset.theme = theme;
  }, [user]);

  return <AuthContext.Provider value={{ user, loading, refresh, logout }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
