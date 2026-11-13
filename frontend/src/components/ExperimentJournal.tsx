import { useEffect, useState } from "react";
import { getExperiments } from "../api/dashboard";
import type { Experiment, ExperimentTrial } from "../api/dashboard";
import { formatDate, formatDateTime, modelName, number } from "../format";

function params(trial: ExperimentTrial): string {
  return Object.entries(trial.params).map(([key, value]) => `${key} ${number(value)}`).join(", ");
}

function label(trial: ExperimentTrial): string {
  return modelName(trial.model) + (trial.uses_weather ? " с погодой" : " без погоды") + ` (${params(trial)})`;
}

export function ExperimentJournal() {
  const [experiments, setExperiments] = useState<Experiment[] | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void getExperiments(controller.signal).then(setExperiments).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, []);

  if (failed) return null;
  const experiment = experiments?.find((item) => item.experiment_id === selectedId) ?? experiments?.[0];
  if (!experiment) {
    return <section className="panel experiments-panel" aria-labelledby="experiments-title">
      <p className="eyebrow">ЖУРНАЛ ЭКСПЕРИМЕНТОВ</p>
      <h2 id="experiments-title">Подбор параметров</h2>
      <p className="panel-copy">{experiments === null ? "Загружаем журнал…" : <>Экспериментов пока нет. Запуск:
        <code> docker compose exec worker python -m backend.modeling.tuning</code></>}</p>
    </section>;
  }
  const trials = [...experiment.trials].sort((a, b) => a.mae_orders - b.mae_orders);
  const best = experiment.trials.find((trial) => trial.trial === experiment.best_trial)!;
  const { holdout } = experiment;
  const gain = 100 * (1 - holdout.best.mae_orders / holdout.default.mae_orders);

  return <section className="panel experiments-panel" aria-labelledby="experiments-title">
    <div className="panel-heading">
      <div>
        <p className="eyebrow">ЖУРНАЛ ЭКСПЕРИМЕНТОВ</p>
        <h2 id="experiments-title">Подбор параметров: лучший — {label(best)}</h2>
      </div>
      {experiments && experiments.length > 1 && <label className="date-control">
        <span>Эксперимент</span>
        <select value={experiment.experiment_id} onChange={(event) => setSelectedId(event.target.value)}>
          {experiments.map((item) => <option key={item.experiment_id} value={item.experiment_id}>{formatDateTime(item.created_at_utc)}</option>)}
        </select>
      </label>}
    </div>
    <p className="panel-copy experiments-result">
      {experiment.best_trial === experiment.default_trial
        ? "Лучшим остался вариант по умолчанию — подбор его подтвердил. "
        : `Найденный вариант ${gain >= 0 ? "лучше" : "хуже"} параметров по умолчанию на ${number(Math.abs(Math.round(gain * 10) / 10))}%. `}
      На отложенных днях {formatDate(holdout.start, true)} — {formatDate(holdout.end, true)}, которые в подборе не участвовали,
      его промах <strong>{number(holdout.best.mae_orders)}</strong> заказа
      {experiment.best_trial === experiment.default_trial ? "" : ` (по умолчанию — ${number(holdout.default.mae_orders)})`} против{" "}
      {number(holdout.baseline.mae_orders)} у прогноза по прошлой неделе.
    </p>
    <dl className="run-facts experiments-facts">
      <div><dt>Бюджет</dt><dd>{experiment.trials.length} из {experiment.budget.max_trials} испытаний · {number(experiment.spent_seconds)} из {number(experiment.budget.max_seconds)} с</dd></div>
      <div><dt>Данные и seed</dt><dd>{formatDate(experiment.data.start, true)} — {formatDate(experiment.data.end, true)}, {number(experiment.data.rows)} строк · seed {experiment.seed}</dd></div>
    </dl>
    <div className="table-scroll">
      <table>
        <thead><tr><th>№</th><th>Вариант</th><th>Средний промах</th><th>Ошибка, %</th><th>Время, с</th></tr></thead>
        <tbody>{trials.map((trial) => <tr key={trial.trial} className={trial.trial === experiment.best_trial ? "model-selected" : ""}>
          <td>{trial.trial}</td>
          <td>{label(trial)}{trial.origin === "default" && <span className="model-tag">по умолчанию</span>}{trial.trial === experiment.best_trial && <span className="model-tag">лучший</span>}</td>
          <td>{number(trial.mae_orders)}</td>
          <td>{trial.wape_percent === null ? "—" : number(trial.wape_percent) + " %"}</td>
          <td>{number(trial.seconds)}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <details className="monitor-help">
      <summary>Как устроен подбор и как его повторить</summary>
      <p>Сначала проверяются параметры по умолчанию, затем случайные варианты Ridge и CatBoost (с погодой и без) — пока не кончится
        бюджет испытаний или времени. Каждый вариант оценивается на тех же трёх окнах по 28 дней, что и выбор модели.
        Последние 28 дней в подборе не участвуют: на них один раз проверяется лучший вариант.</p>
      <p>В журнале сохранены конфигурации, версия данных (SHA-256 {experiment.data.dataset_sha256.slice(0, 12)}…), seed и метрики.
        Повтор по идентификатору заново считает все испытания и сверяет результат:</p>
      <code>docker compose exec worker python -m backend.modeling.tuning --reproduce {experiment.experiment_id}</code>
    </details>
  </section>;
}
