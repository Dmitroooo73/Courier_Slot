import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest, AuthError, restoreSession, signIn, signOut } from "./api/auth";
import type { Account, Session } from "./api/auth";

export function useAuth() {
  const current = useRef<Session | null>(null);
  const [user, setUser] = useState<Session["user"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const apply = useCallback((session: Session | null) => {
    current.current = session;
    setUser(session?.user ?? null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void restoreSession().then((session) => {
      if (!cancelled) apply(session);
    }).catch(() => {
      if (!cancelled) setError("Не удалось восстановить сессию. Можно войти заново.");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [apply]);

  async function enter(mode: "login" | "register", login: string, password: string) {
    apply(await signIn(mode, login, password));
    setError("");
  }

  async function exit() {
    await signOut();
    apply(null);
    setError("");
  }

  const request = useCallback(async (path: string, options?: RequestInit) => {
    const session = current.current;
    if (!session) throw new AuthError(401, "Войдите в аккаунт.");
    try {
      return await apiRequest(session, path, options);
    } catch (error) {
      if (!(error instanceof AuthError) || error.status !== 401 || options?.signal?.aborted) throw error;
      const next = current.current === session ? await restoreSession() : current.current;
      if (current.current === session) apply(next);
      if (!next || next.user.id !== session.user.id) throw new AuthError(401, "Сессия закончилась. Войдите снова.");
      return apiRequest(next, path, options);
    }
  }, [apply]);

  function updateUser(account: Account) {
    if (current.current?.user.id === account.id) apply({ ...current.current, user: account });
  }

  function forgetSession() {
    apply(null);
    setError("");
  }

  return { user, loading, error, enter, exit, request, updateUser, forgetSession };
}

export type AuthState = ReturnType<typeof useAuth>;
