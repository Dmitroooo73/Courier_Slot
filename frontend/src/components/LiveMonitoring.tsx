import type { ReactNode } from "react";
import type { Observation } from "../api/dashboard";
import { formatDate, formatDateTime, number } from "../format";
import { observedMonitoring, WEEK_DAYS } from "../monitoring";
import { HistoryChart } from "./HistoryChart";

const messages = {
  pending: "Собираем первую неделю",
  normal: "Точность на обычном уровне",
  warning: "Модель стала чаще ошибаться",
  alert: "Прогноз пора обновить",
};

export function LiveMonitoring({ observations, zoneId, zonePicker }: {
  observations: Observation[];
  zoneId: string;
  zonePicker: ReactNode;
}) {
  const result = observedMonitoring(observations, zoneId);
  const last = result.points.at(-1);
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
        {!last && !pending && <div className="monitor-waiting">
          <strong>Готовимся к первой проверке</strong>
          <p>В 20:00 мск планировщик сохраняет прогноз на завтра. Когда завтрашний день завершится, здесь появятся число заказов и ошибка прогноза.</p>
        </div>}
        {last && (
          <>
            <div className={"monitor-message monitor-" + result.status} aria-live="polite">
              <h2>{messages[result.status]}</h2>
              <p>Проверено {result.points.length} дней подряд. Последний — {formatDate(last.date, true)}.</p>
              {result.status === "pending" && <p>Для первого недельного вывода нужно {WEEK_DAYS} дней.</p>}
              {result.status === "warning" && <p>За одну полную неделю средний промах вырос. Проверим следующую неделю.</p>}
              {result.status === "alert" && <p>Средний промах слишком большой две недели подряд. Модель стоит заново обучить на свежих данных.</p>}
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
                  <small>{week.complete ? week.aboveLimit ? "Промах больше допустимого" : "Укладываемся в допустимый промах" : "Проверено " + week.days + " из " + WEEK_DAYS + " дней"}</small>
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
      {last && <section className="panel monitor-history">
        <h2>Что ожидали и что получили</h2>
        <HistoryChart key={zoneId} points={result.points} />
      </section>}
    </div>
  );
}
