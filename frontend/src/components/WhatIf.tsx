import { useState } from "react";
import type { ReactNode } from "react";
import type { ScenarioCurve } from "../api/dashboard";
import { number } from "../format";

const inputs = {
  mean_temperature_c: { title: "Температура", unit: " °C", initial: "Исходная температура", changed: "Если температура" },
  orders_lag_7: { title: "Заказы неделю назад", unit: " заказов", initial: "Было неделю назад", changed: "Если неделю назад было" },
};

export function WhatIf({ curves, zonePicker }: { curves: ScenarioCurve[]; zonePicker: ReactNode }) {
  const [feature, setFeature] = useState<ScenarioCurve["feature"]>("mean_temperature_c");
  const curve = curves.find((entry) => entry.feature === feature) ?? curves[0];

  return <section className="panel what-if-panel" aria-labelledby="what-if-title">
    <div className="panel-heading">
      <div><p className="eyebrow">ПРОВЕРЬТЕ СЦЕНАРИЙ</p><h2 id="what-if-title">Что изменится, если…</h2></div>
      {zonePicker}
    </div>
    {curve ? <>
      <p className="panel-copy">Меняем одно условие в прогнозе для выбранной зоны. Остальные условия остаются такими же.</p>
      <label className="date-control scenario-feature"><span>Что меняем</span>
        <select value={curve.feature} onChange={(event) => setFeature(event.target.value as ScenarioCurve["feature"])}>
          {curves.map((entry) => <option key={entry.feature} value={entry.feature}>{inputs[entry.feature].title}</option>)}
        </select>
      </label>
      <Scenario key={curve.feature} curve={curve} />
    </> : <p className="empty-panel">Для этого прогноза сценарий пока недоступен. Попробуйте более свежую дату.</p>}
  </section>;
}

function Scenario({ curve }: { curve: ScenarioCurve }) {
  const original = curve.points.findIndex((point) => point.value === curve.base_value);
  const initial = original < 0 ? Math.floor(curve.points.length / 2) : original;
  const [step, setStep] = useState(initial);
  const currentStep = Math.min(step, curve.points.length - 1);
  const point = curve.points[currentStep];
  const input = inputs[curve.feature];
  const value = (amount: number) => number(amount) + input.unit;
  const difference = point.orders - curve.base_orders;
  const comparison = difference > 0 ? `Больше на ${number(difference)}`
    : difference < 0 ? `Меньше на ${number(-difference)}` : "Столько же заказов";

  return <div className="scenario-layout">
    <div className="scenario-controls">
      <p className="panel-copy">{input.initial} {curve.base_value === null ? "нет данных" : value(curve.base_value)}</p>
      <label className="scenario-slider">
        <span>{input.changed} <strong>{value(point.value)}</strong></span>
        <input type="range" min={0} max={curve.points.length - 1} step={1} value={currentStep}
          disabled={curve.points.length < 2} aria-valuetext={value(point.value)}
          onChange={(event) => setStep(Number(event.target.value))} />
      </label>
      <button className="refresh-button" type="button" disabled={currentStep === initial} onClick={() => setStep(initial)}>{original < 0 ? "К середине диапазона" : "Сбросить"}</button>
      <div className="scenario-result" aria-live="polite">
        <span>Заказов в прогнозе</span><strong>{number(curve.base_orders)} → {number(point.orders)}</strong>
        <small>{comparison}</small>
      </div>
      {!curve.used && <p className="panel-copy">Выбранная модель работает без погоды. Попробуйте изменить число заказов неделю назад.</p>}
      {curve.used && curve.base_value === null && <p className="panel-copy">В исходном прогнозе модель обработала пропуск. Сценарий подставляет выбранное значение.</p>}
    </div>
    <ScenarioChart curve={curve} point={point} formatValue={value} />
  </div>;
}

function ScenarioChart({ curve, point, formatValue }: {
  curve: ScenarioCurve; point: ScenarioCurve["points"][number]; formatValue: (value: number) => string;
}) {
  const left = 54, right = 585, top = 23, bottom = 155;
  const minValue = curve.points[0].value;
  const maxValue = curve.points.at(-1)!.value;
  const orders = [curve.base_orders, ...curve.points.map((entry) => entry.orders)];
  const low = Math.min(...orders), high = Math.max(...orders);
  const minOrders = Math.max(0, low - 1), maxOrders = high + 1;
  const ticks = Array.from(new Set([minOrders, Math.round((minOrders + maxOrders) / 2), maxOrders]));
  const x = (value: number) => maxValue === minValue ? (left + right) / 2 : left + (value - minValue) / (maxValue - minValue) * (right - left);
  const y = (count: number) => bottom - (count - minOrders) / (maxOrders - minOrders) * (bottom - top);
  const path = curve.points.map((entry) => `${x(entry.value)},${y(entry.orders)}`).join(" ");

  return <figure className="scenario-chart">
    <svg viewBox="0 0 610 185" role="img" aria-label={`При ${formatValue(point.value)} модель прогнозирует ${number(point.orders)} заказов`}>
      {ticks.map((tick) => <g key={tick}>
        <line className="scenario-gridline" x1={left} y1={y(tick)} x2={right} y2={y(tick)} />
        <text x={left - 8} y={y(tick) + 4} textAnchor="end">{number(tick)}</text>
      </g>)}
      <line className="scenario-baseline" x1={left} y1={y(curve.base_orders)} x2={right} y2={y(curve.base_orders)} />
      <polyline className="scenario-line" points={path} />
      <circle className="scenario-point" cx={x(point.value)} cy={y(point.orders)} r={6} />
      <text x={left} y={175}>{formatValue(minValue)}</text>
      {maxValue !== minValue && <text x={right} y={175} textAnchor="end">{formatValue(maxValue)}</text>}
    </svg>
    <figcaption>По вертикали число заказов. Пунктир показывает исходный прогноз.</figcaption>
  </figure>;
}
