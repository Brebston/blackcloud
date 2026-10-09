import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { get, patch, post, resetCsrf } from "../api/client";
import type { Preferences, User } from "../api/types";
import { applyAppearance } from "../lib/appearance";

interface AuthState {
  user: User | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  /** Зберігає частину налаштувань на сервері й одразу оновлює їх локально. */
  updatePreferences: (changes: Partial<Preferences>) => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  refresh: async () => {},
  logout: async () => {},
  updatePreferences: async () => {},
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

  const updatePreferences = useCallback(async (changes: Partial<Preferences>) => {
    const saved = await patch<Preferences>("/api/account/preferences/", changes);
    setUser((u) => (u ? { ...u, preferences: { ...u.preferences, ...saved } } : u));
  }, []);

  // Після входу джерело істини — налаштування акаунта; без входу — вибір, збережений у браузері
  useEffect(() => {
    if (user) applyAppearance(user.preferences.theme, user.preferences.accent);
  }, [user]);

  return (
    <AuthContext.Provider value={{ user, loading, refresh, logout, updatePreferences }}>{children}</AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
