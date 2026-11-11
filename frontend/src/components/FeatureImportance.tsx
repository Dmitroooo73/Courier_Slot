import { useState } from "react";
import type { ImportanceWindow } from "../api/dashboard";
import { featureLabels, formatDate, number } from "../format";

function averageFeatures(windows: ImportanceWindow[]): ImportanceWindow["features"] {
  const totals = new Map<string, { contribution: number; delta: number; count: number }>();
  for (const window of windows) {
    for (const row of window.features) {
      const total = totals.get(row.feature) ?? { contribution: 0, delta: 0, count: 0 };
      total.contribution += row.mean_abs_orders;
      total.delta += row.permutation_mae_delta;
      total.count++;
      totals.set(row.feature, total);
    }
  }
  return Array.from(totals, ([feature, total]) => ({
    feature,
    mean_abs_orders: total.contribution / total.count,
    permutation_mae_delta: total.delta / total.count,
  }));
}

export function FeatureImportance({ windows }: { windows: ImportanceWindow[] }) {
  const [period, setPeriod] = useState("");
  const selected = windows.find((window) => window.start === period);
  const rows = [...(selected?.features ?? averageFeatures(windows))]
    .sort((left, right) => right.permutation_mae_delta - left.permutation_mae_delta);

  return (
    <section className="panel importance-panel">
      <div className="panel-heading">
        <div><p className="eyebrow">ОБЪЯСНЕНИЕ МОДЕЛИ</p><h2>Важность признаков</h2></div>
        {windows.length > 0 && (
          <label className="date-control">
            <span>Период проверки</span>
            <select value={selected?.start ?? ""} onChange={(event) => setPeriod(event.target.value)}>
              <option value="">Все периоды</option>
              {windows.map((window) => (
                <option key={window.start} value={window.start}>
                  {formatDate(window.start, true)} — {formatDate(window.end, true)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {rows.length === 0 ? <p className="empty-panel">Рейтинг появится после пересчёта отчёта.</p> : (
        <>
          <p className="monitor-copy">Средний модуль вклада показывает, насколько признак сдвигает прогноз. Изменение MAE показывает, насколько меняется ошибка после перемешивания значений признака.</p>
          <div className="table-scroll">
            <table>
              <thead><tr><th scope="col">Признак</th><th scope="col">Средний модуль вклада</th><th scope="col">Изменение MAE</th></tr></thead>
              <tbody>{rows.map((row) => (
                <tr key={row.feature}>
                  <th scope="row">{featureLabels[row.feature] ?? row.feature}</th>
                  <td>{number(row.mean_abs_orders)}</td>
                  <td>{row.permutation_mae_delta > 0 ? "+" : ""}{number(row.permutation_mae_delta)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p className="models-note">Оба показателя выражены в заказах на зону в сутки.</p>
        </>
      )}
    </section>
  );
}
