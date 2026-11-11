import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { authMessage, jsonRequest } from "../api/auth";
import type { Profile, UserPage } from "../api/auth";
import type { ZoneForecast } from "../api/dashboard";
import { Avatar } from "../components/Avatar";
import { formatDate, formatDateTime, number } from "../format";
import type { AuthState } from "../useAuth";

const actions = {
  block: { label: "Заблокировать вход", question: "Заблокировать вход?", detail: "Пользователь выйдет на всех устройствах. Войти снова он сможет после разблокировки." },
  unblock: { label: "Разрешить вход", question: "Разрешить вход?", detail: "Пользователь сможет войти с прежним логином и паролем." },
  revoke_sessions: { label: "Завершить входы", question: "Завершить все входы?", detail: "Пользователь выйдет на всех устройствах и сможет сразу войти заново." },
};
type Action = keyof typeof actions;

export function AdminPanel({ auth, zones }: { auth: AuthState; zones: ZoneForecast[] }) {
  if (auth.loading) return <section className="panel" role="status">Проверяем сессию…</section>;
  if (auth.user?.role !== "admin") return <section className="panel account-panel">
    <h2>Доступ администратора</h2>
    <p>{auth.user ? "Для этого раздела нужна роль администратора." : "Войдите под аккаунтом администратора."}</p>
    <a href="#account">Перейти к аккаунту</a>
  </section>;
  return <Directory key={auth.user.id} request={auth.request} zones={zones} />;
}

function Directory({ request, zones }: { request: AuthState["request"]; zones: ZoneForecast[] }) {
  const [filters, setFilters] = useState({ q: "", role: "", status: "" });
  const [page, setPage] = useState<UserPage | null>(null);
  const [after, setAfter] = useState(0);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ ...filters, after: String(after) });
    setError("");
    setLoading(true);
    void request(`/admin/users?${params}`, { signal: controller.signal }).then((response) => response.json())
      .then((result: UserPage) => { if (!controller.signal.aborted) setPage(result); })
      .catch((failure) => { if (!controller.signal.aborted) setError(authMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [after, filters, request, revision]);

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setFilters({ q: String(form.get("q")).trim(), role: String(form.get("role")), status: String(form.get("status")) });
    setAfter(0);
    setSelected(null);
  }

  const stats = page?.stats;
  return <div className="account-workspace">
    {stats && <section className="stats directory-stats" aria-label="Сводка по аккаунтам">
      <article className="stat"><span className="stat-label">Аккаунтов</span><strong>{number(stats.accounts)}</strong></article>
      <article className="stat"><span className="stat-label">Администраторов</span><strong>{number(stats.admins)}</strong></article>
      <article className="stat"><span className="stat-label">Открытых входов</span><strong>{number(stats.active_sessions)}</strong></article>
      <article className="stat"><span className="stat-label">Заблокировано</span><strong>{number(stats.disabled)}</strong></article>
    </section>}
    <div className={"directory-grid" + (selected === null ? "" : " directory-open")}>
      <section className="panel account-section">
        <div className="directory-heading"><h2>Пользователи</h2><button className="refresh-button" disabled={loading} onClick={() => setRevision((value) => value + 1)}>Обновить</button></div>
        <p className="panel-copy">Нажмите на имя, чтобы открыть профиль и управление входом.</p>
        <form className="auth-form directory-filter" onSubmit={search}>
          <label>Поиск<input type="search" name="q" maxLength={50} placeholder="Логин или имя" /></label>
          <label>Роль<select name="role"><option value="">Все</option><option value="user">Пользователь</option><option value="admin">Администратор</option></select></label>
          <label>Вход<select name="status"><option value="">Все</option><option value="active">Разрешён</option><option value="disabled">Заблокирован</option></select></label>
          <button className="refresh-button primary-action" disabled={loading}>Найти</button>
        </form>
        {error ? <p className="notice" role="alert">{error}</p> : loading ? <p className="panel-copy" role="status">Загружаем пользователей…</p> : page?.users.length ? <>
          <div className="table-scroll"><table className="directory-table">
            <thead><tr><th scope="col">Аккаунт</th><th scope="col">Роль</th><th scope="col">Вход</th></tr></thead>
            <tbody>{page.users.map((user) => <tr key={user.id} className={selected === user.id ? "directory-selected" : ""}>
              <th scope="row"><button className="directory-name" type="button" aria-pressed={selected === user.id} onClick={() => setSelected(user.id)}>
                <strong>{user.display_name || user.login}</strong><small>@{user.login}</small>
              </button></th>
              <td>{user.role === "admin" ? "Администратор" : "Пользователь"}</td>
              <td><span className={"pill" + (user.disabled_at ? " pill-risk" : "")}>{user.disabled_at ? "Заблокирован" : "Разрешён"}</span></td>
            </tr>)}</tbody>
          </table></div>
        </> : <p className="panel-copy">Никого не нашли. Попробуйте изменить поиск или фильтры.</p>}
        {!loading && (after > 0 || page?.next_after != null) && <div className="admin-actions">
          {after > 0 && <button className="refresh-button" onClick={() => setAfter(0)}>К началу</button>}
          {page?.next_after != null && <button className="refresh-button" onClick={() => setAfter(page.next_after ?? 0)}>Следующие 50</button>}
        </div>}
      </section>
      {selected !== null && <UserCard key={selected} userId={selected} request={request} zones={zones}
        close={() => setSelected(null)} onAction={() => setRevision((value) => value + 1)} />}
    </div>
  </div>;
}

function UserCard({ userId, request, zones, close, onAction }: {
  userId: number; request: AuthState["request"]; zones: ZoneForecast[]; close: () => void; onAction: () => void;
}) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [pending, setPending] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const path = `/admin/users/${userId}`;

  useEffect(() => {
    const controller = new AbortController();
    void request(path, { signal: controller.signal }).then((response) => response.json()).then(setProfile)
      .catch((failure) => { if (!controller.signal.aborted) setError(authMessage(failure)); });
    return () => controller.abort();
  }, [path, request]);

  async function apply(action: Action) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setProfile(await (await request(`${path}/actions`, jsonRequest("POST", { action }))).json());
      setPending(null);
      setNotice(action === "revoke_sessions" ? "Все входы завершены. Пользователь сможет войти заново."
        : action === "block" ? "Вход заблокирован на всех устройствах. Для восстановления доступа нажмите «Разрешить вход»."
        : "Вход разрешён. Пользователь сможет войти с прежним паролем.");
      onAction();
    } catch (failure) {
      setError(authMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  return <aside className="panel account-section user-card" aria-label="Карточка пользователя">
    <div className="directory-heading"><h2>Профиль</h2><button className="refresh-button" onClick={close} disabled={busy}>Закрыть</button></div>
    {error && <p className="notice" role="alert">{error}</p>}
    {notice && <p className="success-note" role="status">{notice}</p>}
    {profile ? <>
      <div className="user-card-identity">
        <Avatar profile={profile} request={request} />
        <div><strong>{profile.display_name || profile.account.login}</strong><p>@{profile.account.login}</p></div>
      </div>
      {profile.bio && <p className="user-bio">{profile.bio}</p>}
      <div className={"access-explanation" + (profile.disabled_at ? " access-blocked" : "")}>
        <strong>{profile.disabled_at ? "Вход заблокирован" : "Вход разрешён"}</strong>
        <p>{profile.disabled_at ? "Пользователь сможет войти после разблокировки."
          : "Пользователь может входить и пользоваться аккаунтом."}</p>
      </div>
      <dl className="zone-facts user-facts">
        <div><dt>Зарегистрирован</dt><dd>{formatDateTime(profile.account.created_at)} мск</dd></div>
        <div><dt>День рождения</dt><dd>{profile.birthday ? formatDate(profile.birthday) : "Не указан"}</dd></div>
        <div><dt>Зона при входе</dt><dd>{zones.find((zone) => zone.zone_id === profile.account.preferred_zone)?.zone_name ?? "По умолчанию"}</dd></div>
        <div><dt>Открытых входов</dt><dd>{number(profile.active_sessions)}</dd></div>
      </dl>
      {profile.account.role === "user" && (pending ? <div className="admin-confirm">
        <strong>{actions[pending].question}</strong>
        <p>Аккаунт @{profile.account.login}. {actions[pending].detail}</p>
        <div className="admin-actions">
          <button className="refresh-button primary-action" disabled={busy} onClick={() => void apply(pending)}>Подтвердить</button>
          <button className="refresh-button" disabled={busy} onClick={() => setPending(null)}>Отмена</button>
        </div>
      </div> : <div className="admin-actions">
        <button className="refresh-button" onClick={() => setPending(profile.disabled_at ? "unblock" : "block")}>{actions[profile.disabled_at ? "unblock" : "block"].label}</button>
        <button className="refresh-button" disabled={profile.active_sessions === 0} onClick={() => setPending("revoke_sessions")}>{actions.revoke_sessions.label}</button>
      </div>)}
    </> : !error && <p role="status">Загружаем профиль…</p>}
  </aside>;
}
