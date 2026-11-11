import type { ZoneForecast } from "../api/dashboard";
import { featureLabels, formatDate, number } from "../format";
const weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const units: Record<string, string> = {
  mean_temperature_c: " °C",
  precipitation_mm: " мм",
  hours_with_precipitation: " ч",
};
const signed = new Intl.NumberFormat("ru-RU", { signDisplay: "exceptZero", maximumFractionDigits: 2 });

function displayValue(feature: string, value: string | number | null, zone: ZoneForecast): string {
  if (value === null) return "Нет данных";
  if (feature === "zone_id") return zone.zone_name;
  if (feature === "weekday") return weekdays[Number(value)] ?? String(value);
  return typeof value === "number" ? number(value) + (units[feature] ?? "") : value;
}

export function ForecastExplanation({ zone }: { zone: ZoneForecast }) {
  const explanation = zone.explanation;
  const contributions = [...(explanation?.contributions ?? [])]
    .sort((left, right) => Math.abs(right.impact_orders) - Math.abs(left.impact_orders));
  const scale = Math.max(1, ...contributions.map((row) => Math.abs(row.impact_orders)));

  return (
    <details className="explanation">
      <summary>Почему такой прогноз?</summary>
      {!explanation ? (
        <p className="explanation-note">Объяснение появится после повторного расчёта этой даты.</p>
      ) : explanation.method === "seasonal_naive_7d" ? (
        <p className="explanation-note">
          Сезонная база берёт факт за тот же день прошлой недели
          {explanation.reference_date ? " (" + formatDate(explanation.reference_date, true) + ")" : ""}.
          {" В этот день было "}<strong>{number(explanation.base_orders)}</strong> заказов.
        </p>
      ) : (
        <div className="explanation-body">
          <p className="explanation-note">Плюс добавляет заказы к отправной точке, минус уменьшает прогноз. Это вклад фактора в расчёт модели.</p>
          <p className="explanation-base">
            <span>{explanation.method === "ridge" ? "Средний прогноз модели" : "Отправная точка модели"}</span>
            <strong>{number(explanation.base_orders)}</strong>
          </p>
          <ul className="factor-list" aria-label="Вклады факторов в заказах">
            {contributions.map(({ feature, value, impact_orders }) => {
              const barWidth = 50 * Math.abs(impact_orders) / scale;
              return (
                <li key={feature} className={impact_orders < 0 ? "factor-negative" : "factor-positive"}>
                  <span className="factor-name">
                    {featureLabels[feature] ?? feature}
                    <small>{displayValue(feature, value, zone)}</small>
                  </span>
                  <strong className="factor-amount">{signed.format(impact_orders)}</strong>
                  <span className="factor-track" aria-hidden="true">
                    <i style={{ width: barWidth + "%", left: (impact_orders < 0 ? 50 - barWidth : 50) + "%" }} />
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="explanation-total">
            <span>После округления</span>
            <strong>{number(explanation.raw_orders)} → {number(zone.forecast_orders)}</strong>
          </p>
        </div>
      )}
    </details>
  );
}
