import type { Metrics } from "../api/dashboard";
import { modelName, number } from "../format";

const descriptions: Record<string, string> = {
  seasonal_naive_7d: "Берёт число заказов в этой зоне неделю назад.",
  ridge: "Складывает взвешенные поправки за историю заказов, зону, день недели и погоду, если она учтена.",
  catboost: "Комбинирует много коротких правил. Например, спрос в пятницу может меняться по-разному в разных зонах.",
};

export function ModelTable({ metrics }: { metrics: Metrics }) {
  return (
    <>
    <p className="panel-copy">Выбираем вариант, который в среднем промахивается на меньшее число заказов. Сравниваем все модели на одних и тех же датах.</p>
    <div className="table-scroll">
      <table>
        <thead>
          <tr><th scope="col">Модель</th><th scope="col">Погода</th><th scope="col">Средний промах</th><th scope="col">Ошибка, %</th></tr>
        </thead>
        <tbody>
          {[...metrics.validation].sort((left, right) => left.mae_orders - right.mae_orders).map((row) => {
            const selected = row.model === metrics.selected.model && row.uses_weather === metrics.selected.uses_weather;
            return (
              <tr className={selected ? "model-selected" : ""} key={row.model + String(row.uses_weather)}>
                <th scope="row">{modelName(row.model)}{selected && <span className="model-tag">Выбрана</span>}</th>
                <td>{row.uses_weather ? "Да" : "Нет"}</td>
                <td>{number(row.mae_orders)}</td>
                <td>{row.wape_percent === null ? "—" : number(row.wape_percent) + " %"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
    <details className="explanation model-guide">
      <summary>Что означают модели и цифры?</summary>
      <dl>
        {Object.entries(descriptions).map(([model, description]) => <div key={model}><dt>{modelName(model)}</dt><dd>{description}</dd></div>)}
        <div><dt>Средний промах (MAE)</dt><dd>Если ждали 100 заказов, а пришло 110, промах составил 10. Здесь показано среднее по зонам и дням.</dd></div>
        <div><dt>Ошибка в процентах (WAPE)</dt><dd>Складываем все промахи и делим на общее число фактических заказов. Меньше — точнее.</dd></div>
      </dl>
    </details>
    </>
  );
}
