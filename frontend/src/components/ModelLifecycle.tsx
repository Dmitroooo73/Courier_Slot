import { useState } from "react";
import { authMessage, jsonRequest } from "../api/auth";
import type { CandidateJob, ComparedVersion, ModelLifecycleInfo, ModelVersion } from "../api/dashboard";
import { formatDate, formatDateTime, modelName, number } from "../format";
import type { AuthState } from "../useAuth";

const jobStatus: Record<CandidateJob["status"], string> = {
  queued: "В очереди",
  training: "Идёт проверка",
  activated: "Переведена в работу",
  rejected: "Не прошла проверку",
  failed: "Проверка прервана",
};
const actions = { initialize: "Первая рабочая версия", activate: "Новая версия в работе", rollback: "Возврат версии" };
const compared: { key: ComparedVersion; label: string }[] = [
  { key: "active", label: "Прежняя рабочая версия" },
  { key: "candidate", label: "Новая версия" },
  { key: "baseline", label: "Прогноз по прошлой неделе" },
];

export function versionLabel(version: { model: string; uses_weather: boolean }): string {
  return modelName(version.model) + (version.uses_weather ? " · с погодой" : " · без погоды");
}

function actor(value: string): string {
  if (value.startsWith("admin:")) return "администратор " + value.slice(6);
  return value === "worker" ? "планировщик" : value === "command" ? "команда настройки" : value;
}

function reason(job: CandidateJob, zoneNames: Record<string, string>): string {
  if (job.trigger.kind === "manual") return `Запрос: ${actor(job.trigger.requested_by)}.`;
  const zones = job.trigger.zones.map(zone => zoneNames[zone] ?? zone).join(", ");
  return `Повод: две недели подряд промах выше порога (${zones}).`;
}

function Candidate({ job, zoneNames }: { job?: CandidateJob; zoneNames: Record<string, string> }) {
  if (!job) {
    return <div className="lifecycle-result">
      <strong>Новую версию пока не проверяли</strong>
      <p>Проверка запустится сама, если в какой-то зоне две недели подряд промах выше порога мониторинга.</p>
    </div>;
  }
  const open = job.status === "queued" || job.status === "training";
  const scores = job.comparison?.scores;
  const control = job.control ?? { start: "", end: job.trigger.end_date };
  return <div className={"lifecycle-result" + (job.status === "activated" ? " lifecycle-good" : job.status === "rejected" || job.status === "failed" ? " lifecycle-muted" : "")}>
    <div className="lifecycle-result-head">
      <strong>{open ? "Проверяем новую версию" : job.decision?.summary ?? (job.error ? "Проверка прервалась" : jobStatus[job.status])}</strong>
      <span className={"decision-status " + (job.status === "activated" ? "" : "decision-pending")}>{jobStatus[job.status]}</span>
    </div>
    <p>{reason(job, zoneNames)} {open
      ? "Обычно это занимает одну-две минуты. Страница обновится сама."
      : `Проверено ${formatDateTime(job.finished_at_utc ?? job.created_at_utc)} мск.`}</p>
    {job.error && <p role="alert">Причина: {job.error}</p>}
    {scores && <>
      <p>Контрольная неделя {formatDate(control.start, true)} — {formatDate(control.end, true)}: обе версии прогнозируют одни и те же дни,
        которых не было в их обучении.</p>
      <div className="table-scroll">
        <table className="lifecycle-table">
          <thead><tr><th>Чем прогнозировали</th><th>Средний промах</th><th>Ошибка, %</th></tr></thead>
          <tbody>{compared.map(({ key, label }) => <tr key={key} className={key === "candidate" ? "model-selected" : ""}>
            <td>{label}</td>
            <td>{number(scores[key].mae_orders)} заказа</td>
            <td>{scores[key].wape_percent === null ? "—" : number(scores[key].wape_percent) + " %"}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <ul className="lifecycle-checks">{job.decision?.checks.map(check => <li key={check.id} className={check.passed ? "check-pass" : "check-fail"}>
        <span aria-hidden="true">{check.passed ? "✓" : "✗"}</span> {check.text}</li>)}</ul>
    </>}
  </div>;
}

export function ModelLifecycle({ lifecycle, active, auth, zoneNames, onChange }: {
  lifecycle: ModelLifecycleInfo; active: ModelVersion | null; auth: AuthState;
  zoneNames: Record<string, string>; onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState("");
  const last = lifecycle.candidates[0];
  const openJob = last && (last.status === "queued" || last.status === "training");
  const admin = auth.user?.role === "admin";
  const rules = lifecycle.rules;

  async function act(path: string, body: object, done: string) {
    setBusy(true);
    setMessage("");
    try {
      await auth.request(path, jsonRequest("POST", body));
      setMessage(done);
      setConfirming(false);
      onChange();
    } catch (error) {
      setMessage(authMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel lifecycle-panel" aria-labelledby="lifecycle-title">
    <p className="eyebrow">ОБНОВЛЕНИЕ МОДЕЛИ</p>
    <h2 id="lifecycle-title">{active ? `В работе — ${versionLabel(active)}` : "Рабочей модели пока нет"}</h2>
    <p className="panel-copy">{active
      ? `Обучена на данных по ${formatDate(active.training.end)}${lifecycle.active_since ? ` · прогнозирует с ${formatDateTime(lifecycle.active_since)} мск` : ""}. Ежедневный прогноз считает эта версия, без нового обучения.`
      : "Она появится после первого вечернего расчёта."}</p>
    <Candidate job={last} zoneNames={zoneNames} />

    {admin && active && <div className="lifecycle-actions">
      <div>
        <button className="refresh-button" disabled={busy || openJob || Boolean(lifecycle.manual_check.issue)}
          onClick={() => void act("/admin/models/candidates", {}, "Задание отправлено. Проверка начнётся в течение минуты.")}>
          Проверить новую версию
        </button>
        <small>{lifecycle.manual_check.issue ?? (openJob ? "Дождитесь окончания текущей проверки."
          : `Контрольная неделя — 7 дней по ${formatDate(lifecycle.manual_check.control_end, true)}.`)}</small>
      </div>
      {lifecycle.rollback_to && (confirming ? <div className="lifecycle-confirm" role="group" aria-label="Подтверждение возврата">
        <p>Со следующего вечернего расчёта прогноз будет считать {versionLabel(lifecycle.rollback_to)} (обучена по {formatDate(lifecycle.rollback_to.training.end, true)}).
          Уже опубликованные прогнозы и решения мониторинга не изменятся.</p>
        <button className="refresh-button risk-button" disabled={busy}
          onClick={() => void act("/admin/models/rollback", { expected_version_id: active.version_id }, "Предыдущая версия снова в работе.")}>Вернуть</button>
        <button className="refresh-button" disabled={busy} onClick={() => setConfirming(false)}>Отмена</button>
      </div> : <div>
        <button className="refresh-button" disabled={busy} onClick={() => setConfirming(true)}>Вернуть предыдущую версию</button>
        <small>{versionLabel(lifecycle.rollback_to)}, обучена по {formatDate(lifecycle.rollback_to.training.end, true)}.</small>
      </div>)}
      {message && <p role="status" className="panel-copy">{message}</p>}
    </div>}

    <details className="monitor-help">
      <summary>Когда система меняет модель</summary>
      <p><strong>Повод.</strong> Если в зоне две недели подряд средний промах выше порога мониторинга, планировщик
        один раз ставит задание на новую версию ({rules.retrain}). Повторный сигнал в течение недели нового задания не создаёт.
        Администратор может запустить проверку вручную.</p>
      <p><strong>Новая версия.</strong> Обучается тем же кодом признаков на днях до контрольной недели. Свежие дни весят больше:
        вес дня уменьшается вдвое каждые {rules.half_life_days} дней ({rules.candidate}).</p>
      <p><strong>Честное сравнение.</strong> Последние {rules.control_days} закрытых дней не участвуют в обучении ни одной из версий.
        Обе прогнозируют эти дни по данным, доступным накануне в 20:00.</p>
      <p><strong>Перевод в работу.</strong> Новая версия заменяет рабочую, если её средний промах меньше хотя бы на {rules.min_improvement_percent}%
        и не хуже прогноза по прошлой неделе ({rules.promotion}). Иначе она остаётся в реестре с причиной отказа.</p>
      <p><strong>Возврат.</strong> Администратор может вернуть предыдущую версию одной кнопкой, например если после замены промах
        в мониторинге вырос. Опубликованные прогнозы, факты и решения не переписываются.</p>
    </details>
    {lifecycle.activations.length > 0 && <details className="monitor-help">
      <summary>Журнал переключений</summary>
      <ol className="lifecycle-journal">{lifecycle.activations.map(item => <li key={item.activation_id}>
        <time>{formatDateTime(item.at_utc)} мск</time>
        <span><strong>{actions[item.action]}</strong> {item.reason} Выполнил: {actor(item.actor)}.</span>
        <code>{item.version_id}</code>
      </li>)}</ol>
    </details>}
  </section>;
}
