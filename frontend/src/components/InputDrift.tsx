import type { ReactNode } from "react";
import type { DriftSeries, InputDrift as DriftReport } from "../api/dashboard";
import { featureLabels, formatDate, formatDateTime, number } from "../format";

const units: Record<string, string> = {
  mean_temperature_c: "°C",
  precipitation_mm: "мм",
  hours_with_precipitation: "ч",
};

function DriftGroup({ title, rows, referenceDays, currentDays }: {
  title: string;
  rows: DriftSeries[];
  referenceDays: number;
  currentDays: number;
}) {
  return <section className="drift-group" aria-label={title}>
    <h3>{title}</h3>
    <div className="drift-features">
      {rows.map(row => {
        const unit = units[row.feature] ?? "заказов";
        const median = (value: number | null) => value === null ? "—" : `${number(value)} ${unit}`;
        return <article className="week-card drift-feature" key={row.feature}>
          <div className="drift-feature-heading">
            <strong>{featureLabels[row.feature] ?? row.feature}</strong>
            <small className={"decision-status " + (row.status === "shift" ? "decision-warning" : "decision-pending")}>
              {row.status === "shift" ? "Есть сдвиг" : row.status === "steady" ? "В пределах порога" : "Ждём данные"}
            </small>
          </div>
          <dl className="drift-values">
            <div><dt>Опорное значение</dt><dd>{median(row.reference_median)}</dd><small>{row.reference_count} из {referenceDays} дней</small></div>
            <div><dt>В свежих данных</dt><dd>{median(row.current_median)}</dd><small>{row.current_count} из {currentDays} дней</small></div>
          </dl>
        </article>;
      })}
    </div>
  </section>;
}

export function InputDrift({ report, zoneId, zonePicker, loading }: {
  report?: DriftReport | null;
  zoneId: string;
  zonePicker: ReactNode;
  loading: boolean;
}) {
  if (!report) return <section className="panel monitor-signal">
    <h2>{loading ? "Загружаем проверку данных" : "Первая проверка ещё не сохранена"}</h2>
    <p className="panel-copy">Планировщик сравнивает данные при запуске и после закрытия суток.</p>
  </section>;

  const orders = report.orders[zoneId] ?? [];
  const rows = [...orders, ...report.weather];
  const changes = rows.filter(row => row.status === "shift");
  const missing = rows.filter(row => row.status === "insufficient").length;
  const summary = changes.length ? "Часть входных данных изменилась" : missing ? "Для полного вывода мало данных" : "Заметных изменений нет";
  const labels = changes.map(row => featureLabels[row.feature] ?? row.feature).join(", ");
  const period = (start: string, end: string) => `${formatDate(start, start.slice(0, 4) === end.slice(0, 4))} — ${formatDate(end)}`;

  return <section className="panel monitor-signal drift-panel">
    <div className="panel-heading"><h2>Как изменились входные данные</h2>{zonePicker}</div>
    <div className={"monitor-message " + (changes.length ? "monitor-warning" : missing ? "monitor-pending" : "monitor-normal")} aria-live="polite">
      <h3>{summary}</h3>
      {changes.length > 0 && <p>Сигнал по признакам — {labels}. Точность прогноза можно проверить в режиме «По заказам».</p>}
      {missing > 0 && <p>{report.reference_fixed ? "По части признаков пока не хватает свежих значений." : "Опорный период зафиксируется, когда наберётся достаточно значений."}</p>}
      {!changes.length && !missing && <p>История заказов в выбранной зоне и погода по Москве укладываются в порог проверки.</p>}
    </div>
    <dl className="drift-periods">
      <div><dt>{report.reference_fixed ? "Зафиксированная опора" : "Период для опоры"}</dt><dd>{period(report.reference.start_date, report.reference.end_date)}</dd></div>
      <div><dt>Свежие закрытые даты</dt><dd>{period(report.current.start_date, report.current.end_date)}</dd></div>
    </dl>
    <p className="panel-copy">В карточках показаны медианы и число дней с данными. Для заказов учитываем день недели и выбранную зону. Сезонная смена погоды тоже может дать сигнал.</p>
    <DriftGroup title="История заказов для выбранной зоны" rows={orders} referenceDays={report.reference.days} currentDays={report.current.days} />
    <DriftGroup title="Погодные признаки по Москве" rows={report.weather} referenceDays={report.reference.days} currentDays={report.current.days} />
    <details className="monitor-help">
      <summary>Как считаем сдвиг</summary>
      <p>Сравниваем, какая доля значений лежит ниже одной и той же границы в каждом периоде. Если на какой-либо границе разница достигает {number(report.rule.distance_threshold * 100)} процентных пунктов, появляется сигнал. Медианы при этом могут быть близкими.</p>
      <p>Для расчёта нужно хотя бы {report.rule.reference_min} дней в опоре и {report.rule.current_min} в свежем периоде. Для заказов нужна история по каждому дню недели. Проверка сохранена {formatDateTime(report.saved_at_utc)} мск.</p>
      <ul>{rows.filter(row => row.distance !== null).map(row => <li key={row.feature}>{featureLabels[row.feature] ?? row.feature} — разрыв {number(row.distance! * 100)} п. п.</li>)}</ul>
      <p>Версия правила — {report.rule_version}.</p>
    </details>
  </section>;
}
