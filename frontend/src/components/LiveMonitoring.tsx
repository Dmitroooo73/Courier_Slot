import type { ReactNode } from "react";
import type { MonitoringDecision, Observation } from "../api/dashboard";
import { formatDate, formatDateTime, number } from "../format";
import { decisionAdvice, monitoringMessages } from "../monitoring";
import { HistoryChart } from "./HistoryChart";
import { MonitoringHistory } from "./MonitoringHistory";

export function LiveMonitoring({ observations, decisions, zoneId, zonePicker }: {
  observations: Observation[];
  decisions: MonitoringDecision[];
  zoneId: string;
  zonePicker: ReactNode;
}) {
  const result = decisions.find(decision => decision.zone_id === zoneId);
  const last = result?.points.at(-1);
  const newestClosed = observations.filter(record => record.actuals?.[zoneId] !== undefined)
    .map(record => record.target_date).sort().at(-1);
  const pending = [...observations].filter((record) => record.actuals === null && record.forecasts[zoneId] !== undefined)
    .sort((left, right) => left.target_date.localeCompare(right.target_date))[0];
  const closesAt = pending ? new Date(Date.parse(pending.target_date + "T00:00:00+03:00") + 86_400_000) : null;
  const dayFinished = closesAt !== null && Date.now() >= closesAt.getTime();

  return (
    <div className="monitoring-view">
      <section className="panel monitor-signal">
        <div className="panel-heading">
          <div><p className="eyebrow">ЕЖЕДНЕВНАЯ ПРОВЕРКА</p><h2>Сбывается ли прогноз</h2></div>
          {zonePicker}
        </div>
        {pending && closesAt && <div className="monitor-waiting" role="status">
          <div><strong>{dayFinished ? "День завершён, ждём загрузку заказов" : "Ждём завершения дня"}</strong>
            <p>Прогноз на {formatDate(pending.target_date, true)} уже сохранён.</p>
          </div>
          <dl className="monitor-metrics">
            <div><dt>Ожидаем в этой зоне</dt><dd>{number(pending.forecasts[zoneId])}<small>заказов</small></dd></div>
            <div><dt>Сверка после</dt><dd className="waiting-date">{formatDateTime(closesAt.toISOString())}<small>мск</small></dd></div>
          </dl>
          <p className="panel-copy">{dayFinished ? "Планировщик добавит итог дня при следующей успешной загрузке."
            : "После полуночи посчитаем заказы за весь день и покажем разницу с прогнозом."}</p>
        </div>}
        {newestClosed && (!result || newestClosed > result.end_date) && <p className="notice" role="status">
          Итоги новых дней уже загружены. Ждём сохранения вывода планировщиком; ниже — последняя сохранённая проверка, если она есть.
        </p>}
        {!last && !pending && !newestClosed && <div className="monitor-waiting">
          <strong>Готовимся к первой проверке</strong>
          <p>В 20:00 мск планировщик сохраняет прогноз на завтра. Когда завтрашний день завершится, здесь появятся число заказов и ошибка прогноза.</p>
        </div>}
        {last && result && (
          <>
            <div className={"monitor-message monitor-" + result.status} aria-live="polite">
              <h2>{monitoringMessages[result.status]}</h2>
              <p>Дней подряд: {result.points.length} · Последний: {formatDate(last.date, true)}</p>
              <p>{decisionAdvice(result)}</p>
            </div>
            <dl className="monitor-metrics">
              <div><dt>Ожидали заказов</dt><dd>{number(last.forecast)}</dd></div>
              <div><dt>Получили заказов</dt><dd>{number(last.actual)}</dd></div>
              <div><dt>Разница</dt><dd>{number(Math.abs(last.actual - last.forecast))}<small>заказов</small></dd></div>
            </dl>
            <div className="monitor-weeks">
              {result.weeks.map((week) => (
                <article className={week.aboveLimit ? "week-card week-alert" : "week-card"} key={week.start}>
                  <strong>{formatDate(week.start, true)} — {formatDate(week.end, true)}</strong>
                  <span>Промах {number(week.error ?? 0)}</span>
                  <small>Для этой проверки предел {number(week.limit)} заказа в день.</small>
                  <small>{week.complete ? week.aboveLimit ? "Промах больше допустимого" : "Укладываемся в допустимый промах" : "Проверено " + week.days + " из " + result.rule.week_days + " дней"}</small>
                </article>
              ))}
            </div>
          </>
        )}
        <details className="monitor-help">
          <summary>Как считаем ошибку и когда обновлять модель</summary>
          <p>Если ожидали 100 заказов, а получили 120, промах равен 20 заказам. За неделю считаем средний размер такого промаха.</p>
          <p>Для этой проверки допускаем рост обычного промаха на 30%. Одна полная неделя выше этого уровня даёт предупреждение, две подряд — рекомендацию переобучить модель.</p>
        </details>
      </section>
      {last && result && <section className="panel monitor-history">
        <h2>Что ожидали и что получили</h2>
        <HistoryChart key={zoneId} points={result.points} />
      </section>}
      <MonitoringHistory key={zoneId + "/" + (result?.saved_at_utc ?? "")} zoneId={zoneId} />
    </div>
  );
}
