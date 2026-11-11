import type { Run } from "../api/dashboard";
import { formatDate, formatDateTime, number } from "../format";

const weatherOrigins: Record<string, string> = {
  api: "получена с погодного сервиса",
  cache: "взята из сохранённой истории",
  snapshot: "взята из учебного архива",
};
const runSteps: Record<string, string> = {
  prepare: "Подготовка", orders: "Проверка заказов", weather: "Загрузка погоды",
  model: "Обучение модели", publish: "Сохранение прогноза",
};

export function RunHistory({ runs, onOpen }: { runs: Run[]; onOpen: (date: string) => void }) {
  if (runs.length === 0) return <p className="empty-panel">Когда появится первый расчёт, здесь сохранится его результат.</p>;

  return <div className="run-history">
    <p className="panel-copy">Нажмите на запись, чтобы посмотреть результат расчёта. История хранится на сервере и доступна с устройств, подключённых к этому пульту.</p>
    <ul className="run-list">
      {runs.map((run) => {
        const seconds = run.started_at_utc
          ? Math.max(0, (Date.parse(run.finished_at_utc) - Date.parse(run.started_at_utc)) / 1000) : null;
        return <li key={run.run_id}>
          <details className="run-entry">
            <summary>
              <span className={"pill" + (run.status === "success" ? "" : " pill-risk")}>{run.status === "success" ? "Готово" : "Сбой"}</span>
              <span className="run-body"><strong>Прогноз на {formatDate(run.target_date, true)}</strong>
                <span>{run.status === "success" ? "Расчёт завершён, прогноз сохранён" : "Расчёт остановился · " + (runSteps[run.step] ?? "обработка данных")}</span>
              </span>
              <time dateTime={run.finished_at_utc}>{formatDateTime(run.finished_at_utc)} мск</time>
              <span className="run-expand" aria-hidden="true">⌄</span>
            </summary>
            <div className="run-detail">
              <dl className="run-facts">
                {run.started_at_utc && <div><dt>Начали расчёт</dt><dd>{formatDateTime(run.started_at_utc)} мск</dd></div>}
                <div><dt>Завершили</dt><dd>{formatDateTime(run.finished_at_utc)} мск</dd></div>
                {seconds !== null && <div><dt>Затрачено времени</dt><dd>{number(seconds)} с</dd></div>}
                {run.orders && <div><dt>История заказов</dt><dd>Новых дней — {number(run.orders.generated_days)}, ранее сохранённых — {number(run.orders.reused_days)}</dd></div>}
                {run.weather && <div><dt>Погода</dt><dd>{weatherOrigins[run.weather.origin] ?? "данные сохранены"}</dd></div>}
              </dl>
              {run.status === "failed" && <p className="notice" role="status">{run.error || "Подробная причина в этой записи не указана."}</p>}
              {run.status === "success" && <button className="refresh-button" onClick={() => onOpen(run.target_date)}>Открыть прогноз на {formatDate(run.target_date, true)}</button>}
              <details className="run-technical"><summary>Данные запуска для отчёта</summary><p>ID запуска</p><code>{run.run_id}</code></details>
            </div>
          </details>
        </li>;
      })}
    </ul>
  </div>;
}
