import { useState } from "react";
import type { FormEvent } from "react";
import { authMessage } from "../api/auth";
import type { ZoneForecast } from "../api/dashboard";
import type { AuthState } from "../useAuth";
import { ProfilePanel } from "./Profile";

export function AccountPanel({ auth, zones }: { auth: AuthState; zones: ZoneForecast[] }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await auth.enter(mode, String(values.get("login")), String(values.get("password")));
      setNotice("");
    } catch (failure) {
      setError(authMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  if (auth.loading) return <section className="panel account-panel" role="status">Проверяем сессию…</section>;
  if (auth.user) return <ProfilePanel key={auth.user.id} auth={auth} zones={zones} onPasswordChanged={() => {
    setMode("login");
    setNotice("Пароль обновлён. Войдите с новым паролем.");
    auth.forgetSession();
  }} />;
  return <section className="panel account-panel">
    {(error || auth.error) && <p className="notice" role="alert">{error || auth.error}</p>}
    {notice && <p className="success-note" role="status">{notice}</p>}
      <div className="monitor-modes" aria-label="Действие с аккаунтом">
        <button className="refresh-button" aria-pressed={mode === "login"} disabled={busy} onClick={() => { setMode("login"); setError(""); }}>Вход</button>
        <button className="refresh-button" aria-pressed={mode === "register"} disabled={busy} onClick={() => { setMode("register"); setError(""); }}>Регистрация</button>
      </div>
      <form key={mode} onSubmit={submit} className="auth-form">
        <label>Логин<input name="login" autoComplete="username" autoCapitalize="none" spellCheck={false} minLength={3} maxLength={32} required /></label>
        {mode === "register" && <small>От 3 до 32 символов. Латинские буквы, цифры, точки, дефисы и подчёркивания.</small>}
        <label>Пароль<input type="password" name="password" autoComplete={mode === "register" ? "new-password" : "current-password"} minLength={mode === "register" ? 12 : 1} maxLength={128} required /></label>
        {mode === "register" && <small>Минимум 12 символов.</small>}
        <button className="refresh-button auth-submit" disabled={busy}>{mode === "login" ? "Войти" : "Создать аккаунт"}</button>
      </form>
  </section>;
}
