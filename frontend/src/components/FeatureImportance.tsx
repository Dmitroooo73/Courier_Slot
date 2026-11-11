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
  const strongest = rows.find((row) => row.permutation_mae_delta > 0);

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
          <p className="monitor-example">{strongest
            ? <>Самая полезная подсказка модели — <strong>{(featureLabels[strongest.feature] ?? strongest.feature).toLowerCase()}</strong>.
              {" Если перемешать эти данные, средний промах вырастет на "}<strong>{number(strongest.permutation_mae_delta)} заказа</strong>.</>
            : "В этом периоде перемешивание отдельных признаков не увеличило средний промах."}</p>
          <div className="table-scroll">
            <table>
              <thead><tr><th scope="col">Что учитываем</th><th scope="col">Сдвиг прогноза, заказы</th><th scope="col">Изменение промаха, заказы</th></tr></thead>
              <tbody>{rows.map((row) => (
                <tr key={row.feature}>
                  <th scope="row">{featureLabels[row.feature] ?? row.feature}</th>
                  <td>{number(row.mean_abs_orders)}</td>
                  <td>{Math.abs(row.permutation_mae_delta) < 0.005 ? "≈0" :
                    (row.permutation_mae_delta > 0 ? "+" : "") + number(row.permutation_mae_delta)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p className="models-note">Чем больше положительное число справа, тем полезнее признак для точности. Ноль или минус — заметной пользы в этой проверке нет.</p>
          <details className="monitor-help"><summary>Как считаем важность: два способа</summary>
            <p>Сдвиг прогноза — средний модуль вклада: насколько сильно признак меняет ответ модели, независимо от знака.</p>
            <p>Изменение промаха — перестановочная важность: перемешиваем один столбец пять раз и измеряем изменение средней абсолютной ошибки (MAE). Остальные данные остаются прежними.</p>
            <p>Оба числа — заказы на зону в сутки. Это объяснение работы модели; связанные между собой признаки могут делить влияние.</p>
          </details>
        </>
      )}
    </section>
  );
}
