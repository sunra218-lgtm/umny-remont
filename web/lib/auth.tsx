"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { api, hasAccessToken, refreshSession, setAccessToken } from "./api";
import type { Me } from "./types";

type AuthState = {
  me: Me | null;
  loading: boolean;
  reload: () => Promise<void>;
  signIn: (accessToken: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  // Приложение бригады (/l/…, /lite) входит по ключу телефона и ведёт сеанс само.
  const pathname = usePathname();
  const isLite = pathname.startsWith("/lite") || pathname.startsWith("/l/");

  const reload = useCallback(async () => {
    if (isLite) {
      setLoading(false);
      return;
    }
    try {
      if (!hasAccessToken() && !(await refreshSession())) {
        setMe(null);
        return;
      }
      setMe(await api<Me>("/me"));
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, [isLite]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const signIn = useCallback(
    async (token: string) => {
      setAccessToken(token);
      await reload();
    },
    [reload],
  );

  const signOut = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setAccessToken(null);
      setMe(null);
    }
  }, []);

  return <AuthContext.Provider value={{ me, loading, reload, signIn, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth вне AuthProvider");
  return ctx;
}

/** Перенаправляет на вход, если пользователь не вошёл (и в кабинет, если нет прав админа). */
export function useRequireAuth({ admin = false } = {}): AuthState {
  const auth = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (auth.loading) return;
    if (!auth.me) router.replace("/login");
    else if (admin && !auth.me.is_platform_admin) router.replace("/cabinet");
  }, [auth.loading, auth.me, admin, router]);
  return auth;
}
