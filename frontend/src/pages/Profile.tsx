import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { authMessage, jsonRequest } from "../api/auth";
import type { Profile, ProfileEdit } from "../api/auth";
import type { ZoneForecast } from "../api/dashboard";
import { Avatar } from "../components/Avatar";
import { formatDate, formatDateTime, number } from "../format";
import type { AuthState } from "../useAuth";

export function ProfilePanel({ auth, zones, onPasswordChanged }: {
  auth: AuthState; zones: ZoneForecast[]; onPasswordChanged: () => void;
}) {
  const { request } = auth;
  const [profile, setProfile] = useState<Profile | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [editSection, setEditSection] = useState<"profile" | "avatar" | "security">("profile");

  useEffect(() => {
    const controller = new AbortController();
    void request("/auth/profile", { signal: controller.signal }).then((response) => response.json()).then(setProfile)
      .catch((failure) => { if (!controller.signal.aborted) setError(authMessage(failure)); });
    return () => controller.abort();
  }, [request]);

  async function perform(action: () => Promise<void>, success = "") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(success);
    } catch (failure) {
      setError(authMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function update(options: RequestInit, endpoint = "/auth/profile") {
    const updated: Profile = await (await request(endpoint, options)).json();
    setProfile(updated);
    auth.updateUser(updated.account);
  }

  function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const profileEdit: ProfileEdit = {
      display_name: String(fields.get("display_name")), bio: String(fields.get("bio")),
      birthday: String(fields.get("birthday")) || null, preferred_zone: String(fields.get("preferred_zone")) || null,
    };
    void perform(async () => {
      await update(jsonRequest("PUT", profileEdit));
      setEditing(false);
    }, "Профиль сохранён.");
  }

  function addAvatar(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (file) void perform(async () => {
      if (file.size > 4 * 1024 * 1024) throw new Error("Выберите изображение до 4 МБ.");
      await update({ method: "PUT", body: file }, "/auth/avatar");
      setEditing(false);
    }, "Аватар обновлён.");
  }

  function savePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    void perform(async () => {
      const newPassword = String(fields.get("new_password"));
      if (newPassword !== fields.get("repeat_password")) throw new Error("Новые пароли не совпадают.");
      await request("/auth/change-password", jsonRequest("POST", {
        current_password: String(fields.get("current_password")), new_password: newPassword,
      }));
      onPasswordChanged();
    });
  }

  if (!profile) return <section className="panel account-panel">
    {error ? <p role="alert">{error}</p> : <p role="status">Загружаем профиль…</p>}
    <button className="refresh-button" onClick={() => void perform(auth.exit)} disabled={busy}>Выйти</button>
  </section>;

  const { account } = profile;
  const zoneName = zones.find((zone) => zone.zone_id === account.preferred_zone)?.zone_name;
  const latestBirthday = new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Moscow" }).format(new Date());
  return <div className="account-workspace">
    <section className="panel profile-overview" aria-label="Мой профиль">
      <div className="profile-header">
        <Avatar profile={profile} request={request} />
        <div className="profile-identity">
          <p className="eyebrow">МОЙ ПРОФИЛЬ</p>
          <h2>{profile.display_name || account.login}</h2>
          <p>@{account.login} · {account.role === "admin" ? "Администратор" : "Пользователь"}</p>
        </div>
        <div className="admin-actions">
          <button className="refresh-button primary-action" disabled={busy} aria-expanded={editing} aria-controls="profile-editor"
            onClick={() => { setEditing(!editing); setEditSection("profile"); }}>Редактировать</button>
          {account.role === "admin" && <a className="refresh-button action-link" href="#admin">Админка</a>}
          <button className="refresh-button" onClick={() => void perform(auth.exit)} disabled={busy}>Выйти</button>
        </div>
      </div>
      <div className="profile-about"><h3>О себе</h3><p>{profile.bio || "Описание пока не заполнено."}</p></div>
      <dl className="profile-info">
        <div><dt>День рождения</dt><dd>{profile.birthday ? formatDate(profile.birthday) : "Не указан"}</dd></div>
        <div><dt>Зона при входе</dt><dd>{zoneName ?? "Восток по умолчанию"}</dd></div>
        <div><dt>Аккаунт создан</dt><dd>{formatDateTime(account.created_at)} мск</dd></div>
        <div><dt>Открытых входов</dt><dd>{number(profile.active_sessions)}</dd></div>
      </dl>
      <p className="panel-copy">Профиль виден вам и администраторам.</p>
    </section>
    {error && <p className="notice" role="alert">{error}</p>}
    {notice && <p className="success-note" role="status">{notice}</p>}
    {editing && <section id="profile-editor" className="panel account-section profile-editor" aria-label="Редактирование профиля">
      <div className="directory-heading"><h2>Редактирование профиля</h2>
        <button className="refresh-button" disabled={busy} onClick={() => setEditing(false)}>Отмена</button>
      </div>
      <div className="monitor-modes editor-tabs" aria-label="Раздел редактирования">
        <button className="refresh-button" disabled={busy} aria-pressed={editSection === "profile"} onClick={() => setEditSection("profile")}>Личные данные</button>
        <button className="refresh-button" disabled={busy} aria-pressed={editSection === "avatar"} onClick={() => setEditSection("avatar")}>Аватар</button>
        <button className="refresh-button" disabled={busy} aria-pressed={editSection === "security"} onClick={() => setEditSection("security")}>Пароль</button>
      </div>
      {editSection === "profile" && <form className="auth-form" onSubmit={saveProfile}>
          <label>Имя или ник<input name="display_name" maxLength={50} defaultValue={profile.display_name} /></label>
          <label>О себе<textarea name="bio" maxLength={280} rows={3} defaultValue={profile.bio} /></label>
          <div className="account-fields">
            <label>Дата рождения<input type="date" name="birthday" min="1900-01-01" max={latestBirthday} defaultValue={profile.birthday ?? ""} /></label>
            <label>Зона при входе<select name="preferred_zone" defaultValue={account.preferred_zone ?? ""}>
              <option value="">По умолчанию</option>
              {zones.map((zone) => <option key={zone.zone_id} value={zone.zone_id}>{zone.zone_name}</option>)}
              {account.preferred_zone && !zones.some((zone) => zone.zone_id === account.preferred_zone) &&
                <option value={account.preferred_zone}>Сохранённая зона</option>}
            </select></label>
          </div>
          <button className="refresh-button auth-submit primary-action" disabled={busy}>Сохранить профиль</button>
        </form>}
        {editSection === "avatar" && <div className="account-section">
            <p className="panel-copy">JPEG, PNG или WebP до 4 МБ. Фото обрезается по центру.</p>
            <label className="avatar-field">Выбрать изображение<input type="file" accept="image/jpeg,image/png,image/webp" onChange={addAvatar} disabled={busy} /></label>
            {profile.avatar_id && <button className="refresh-button" disabled={busy} onClick={() => void perform(async () => {
              await update({ method: "DELETE" }, "/auth/avatar");
              setEditing(false);
            }, "Аватар удалён.")}>Убрать аватар</button>}
        </div>}
        {editSection === "security" && <div className="account-section">
            <p className="panel-copy">После смены пароля потребуется заново войти на всех устройствах.</p>
            <form className="auth-form" onSubmit={savePassword}>
              <label>Текущий пароль<input name="current_password" type="password" autoComplete="current-password" maxLength={128} required /></label>
              <label>Новый пароль<input name="new_password" type="password" autoComplete="new-password" minLength={12} maxLength={128} required /></label>
              <label>Повторите новый пароль<input name="repeat_password" type="password" autoComplete="new-password" minLength={12} maxLength={128} required /></label>
              <small>Минимум 12 символов.</small>
              <button className="refresh-button auth-submit" disabled={busy}>Сменить пароль</button>
            </form>
        </div>}
    </section>}
  </div>;
}
