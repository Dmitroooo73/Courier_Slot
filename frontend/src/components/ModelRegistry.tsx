import { useEffect, useState } from "react";
import { getModelVersions } from "../api/dashboard";
import type { Forecast, ModelVersion } from "../api/dashboard";
import { featureLabels, formatDate, formatDateTime, modelName, number } from "../format";

function ModelEntry({ version, linked }: { version: ModelVersion; linked: boolean }) {
  const { holdout, training } = version;
  const label = modelName(version.model) + (version.uses_weather ? " · с погодой" : " · без погоды");
  return <details className="registry-entry">
    <summary>
      <span><strong>{label}</strong><small>Расчёт на {formatDate(version.target_date)} · сохранено {formatDateTime(version.created_at_utc)} мск</small></span>
      <span className="decision-status decision-pending">{linked ? "Версия выбранного прогноза" : version.status === "published" ? "Прогноз опубликован" : "Сохранена для проверки"}</span>
    </summary>
    <div className="registry-detail">
      <dl className="run-facts">
        <div><dt>Данные итогового расчёта</dt><dd>{formatDate(training.start)} — {formatDate(training.end)} · строк {number(training.rows)}</dd></div>
        <div><dt>Средний промах при выборе метода</dt><dd>{number(version.validation.mae_orders)} заказа на зону в день</dd></div>
        <div><dt>Отдельная проверка метода</dt><dd>{formatDate(holdout.start)} — {formatDate(holdout.end)} Для проверки обучали по {formatDate(holdout.train_through)} включительно.</dd></div>
        <div><dt>Результат отдельной проверки</dt><dd>{number(holdout.selected.mae_orders)} заказа на зону в день · WAPE {holdout.selected.wape_percent === null ? "—" : number(holdout.selected.wape_percent) + "%"}</dd></div>
      </dl>
      <p className="panel-copy">Признаки — {version.features.map(feature => featureLabels[feature] ?? feature).join(", ")}.</p>
      <details className="run-technical">
        <summary>Параметры и данные для отчёта</summary>
        <p>Идентификатор версии</p><code>{version.version_id}</code>
        <p>Контрольная сумма обучающих данных</p><code>{version.dataset_sha256}</code>
        <p>Контрольная сумма модели</p><code>{version.artifact.sha256}</code>
        <p>Источник заказов — {version.orders_source === "synthetic" ? "синтетический" : version.orders_source}. Размер модели — {number(version.artifact.bytes)} Б.</p>
        <p>Параметры</p><code>{JSON.stringify(version.parameters)}</code>
        <p>Версия признаков</p><code>{version.feature_schema_version}</code>
        <p>Окружение</p><code>{Object.entries(version.versions).map(([name, value]) => `${name} ${value}`).join(" · ")}</code>
      </details>
    </div>
  </details>;
}

export function ModelRegistry({ forecast, selected }: { forecast?: Forecast | null; selected?: ModelVersion | null }) {
  const [records, setRecords] = useState<ModelVersion[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    void getModelVersions(cursor, controller.signal).then(page => {
      if (controller.signal.aborted) return;
      setRecords(previous => cursor ? [...previous, ...page.versions] : page.versions);
      setNext(page.next_before);
    }).catch(() => {
      if (!controller.signal.aborted) setError(true);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [cursor, attempt]);

  return <section className="panel model-registry" aria-labelledby="registry-title">
    <h2 id="registry-title">Реестр моделей</h2>
    <p className="panel-copy">Откройте версию, чтобы посмотреть, на каких данных её подготовили и как проверили точность.</p>
    {forecast && !selected && <p className="panel-copy">{forecast.model_version_id ? "Паспорт выбранного прогноза недоступен." : "Выбранный прогноз сохранён до появления реестра."}</p>}
    {selected && <ModelEntry version={selected} linked />}
    {records.filter(version => version.version_id !== selected?.version_id).map(version => <ModelEntry key={version.version_id} version={version} linked={false} />)}
    {loading && <p role="status" className="panel-copy">Загружаем версии…</p>}
    {!loading && !error && !records.length && !selected && <p className="empty-panel">Сохранённых версий пока нет. Первая появится после обучения модели.</p>}
    {error && <p role="alert" className="panel-copy">Не удалось загрузить реестр. <button className="refresh-button" onClick={() => setAttempt(value => value + 1)}>Повторить</button></p>}
    {next && !error && <button className="refresh-button" disabled={loading} onClick={() => setCursor(next)}>Показать более ранние версии</button>}
  </section>;
}
