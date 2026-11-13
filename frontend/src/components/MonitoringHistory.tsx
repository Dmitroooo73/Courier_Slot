import { useEffect, useState } from "react";
import { getMonitoringDecisions } from "../api/dashboard";
import type { MonitoringDecision } from "../api/dashboard";
import { formatDate, formatDateTime, number } from "../format";
import { decisionAdvice, monitoringMessages } from "../monitoring";

export function MonitoringHistory({ zoneId }: { zoneId: string }) {
  const [records, setRecords] = useState<MonitoringDecision[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void getMonitoringDecisions(zoneId, cursor, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      setRecords(previous => cursor ? [...previous, ...page.decisions] : page.decisions);
      setNext(page.next_before);
    }).catch(() => {
      if (!controller.signal.aborted) setError("Не удалось загрузить историю проверок.");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [zoneId, cursor, revision]);

  return <section className="panel monitor-history decision-history" aria-label="История решений мониторинга">
    <p className="eyebrow">СОХРАНЕНО НА СЕРВЕРЕ</p>
    <h2>Что решила система</h2>
    <p className="panel-copy">После каждого закрытого дня сохраняем вывод по выбранной зоне. Нажмите на дату, чтобы увидеть причину.
      История общая для устройств, подключённых к этому пульту. Учебный сценарий её не меняет.</p>
    {!records.length && !loading && !error && <p className="empty-panel">Сохранённых проверок пока нет. Первая появится после загрузки итогов закрытого дня.</p>}
    <ul className="run-list">
      {records.map(record => <li key={record.rule_version + record.end_date}>
        <details className="decision-entry">
          <summary aria-label={"Проверка за " + formatDate(record.end_date, true) + " — " + monitoringMessages[record.status]}>
            <time dateTime={record.end_date}>{formatDate(record.end_date, true)}</time>
            <span className="run-body"><strong>{monitoringMessages[record.status]}</strong><span>Дней в проверке: {record.points.length}</span></span>
            <span className={"decision-status decision-" + record.status}>{record.status === "pending" ? "Ждём данные" : record.status === "normal" ? "В норме" : record.status === "warning" ? "Внимание" : "Обновить модель"}</span>
            <span className="run-expand" aria-hidden="true">⌄</span>
          </summary>
          <div className="decision-detail">
            <p>{decisionAdvice(record)}</p>
            <div className="monitor-weeks">{record.weeks.map(week => <article className={"week-card" + (week.aboveLimit ? " week-alert" : "")} key={week.start}>
              <strong>{formatDate(week.start, true)} — {formatDate(week.end, true)}</strong>
              <span>Промах {number(week.error)}</span>
              <small>Допустимо до {number(week.limit)} заказа в день.</small>
              <small>{week.complete ? week.aboveLimit ? "Порог превышен" : "В пределах порога" : `Только ${week.days} из ${record.rule.week_days} дней — недельного вывода ещё нет`}</small>
            </article>)}</div>
            <p className="panel-copy">Сохранено {formatDateTime(record.saved_at_utc)} мск.
              {record.orders_sources.some((source) => source.startsWith("synthetic")) && " Использованы синтетические заказы."}</p>
            <details className="run-technical"><summary>Правило проверки и данные для отчёта</summary>
              <p>Берём до {record.rule.window_days} последних дней подряд. Для каждой полной недели допустимый промах —
                обычная ошибка сохранённых прогнозов × {number(record.rule.limit_factor)}.
                Пропуск дня начинает отсчёт заново. При восстановлении истории время сохранения может быть позже проверенной даты.</p>
              <p>Версия правила: {record.rule_version}. Данные проверки:</p><code>{record.source_sha256}</code>
            </details>
          </div>
        </details>
      </li>)}
    </ul>
    {loading && <p className="panel-copy" role="status">Загружаем проверки…</p>}
    {error && <div role="alert"><p className="panel-copy">{error}</p><button className="refresh-button" onClick={() => setRevision(value => value + 1)}>Повторить загрузку</button></div>}
    {next && !error && <button className="refresh-button" disabled={loading} onClick={() => setCursor(next)}>Показать более ранние проверки</button>}
  </section>;
}
