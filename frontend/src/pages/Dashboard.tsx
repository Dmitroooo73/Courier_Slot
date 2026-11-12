import { useEffect, useState } from "react";
import { getDashboard } from "../api/dashboard";
import { FeatureImportance } from "../components/FeatureImportance";
import type { DashboardData, Forecast, ZoneForecast } from "../api/dashboard";
import { ForecastExplanation } from "../components/ForecastExplanation";
import { HistoryChart } from "../components/HistoryChart";
import { LiveMonitoring } from "../components/LiveMonitoring";
import { ModelTable } from "../components/ModelTable";
import { Monitoring } from "../components/Monitoring";
import { ZoneMap } from "../components/ZoneMap";
import { ZonePicker } from "../components/ZonePicker";
import { WhatIf } from "../components/WhatIf";
import { RunHistory } from "../components/RunHistory";
import { formatDate, formatDateTime, formatTime, modelName, number } from "../format";
import { useAuth } from "../useAuth";
import { AccountPanel } from "./Account";
import { AdminPanel } from "./Admin";
import { CourierPanel } from "./Courier";

const refreshInterval = 60_000;
const views = [
  { id: "courier", label: "Курьер", heading: "Остаться или сменить район?" },
  { id: "forecast", label: "Прогноз", heading: "Спрос по зонам" },
  { id: "quality", label: "Качество", heading: "Качество прогноза" },
  { id: "monitoring", label: "Мониторинг", heading: "Мониторинг" },
  { id: "runs", label: "Журнал", heading: "Журнал загрузок" },
  { id: "account", label: "Аккаунт", heading: "Аккаунт" },
  { id: "admin", label: "Админка", heading: "Админка" },
] as const;

function currentView() {
  return views.find((view) => "#" + view.id === window.location.hash) ?? views[0];
}

function ZoneDetails({ zone, forecast }: { zone: ZoneForecast; forecast: Forecast }) {
  const gap = zone.capacity_orders - zone.forecast_orders;
  const load = 100 * zone.forecast_orders / zone.capacity_orders;
  const weather = forecast.weather;

  return (
    <section id="zone-forecast" className="panel zone-detail" aria-label="Прогноз выбранной зоны">
      <div className="eyebrow">ВЫБРАННАЯ ЗОНА</div>
      <div className="zone-title">
        <h2>{zone.zone_name}</h2>
        <span className={"pill" + (gap < 0 ? " pill-risk" : "")}>
          {gap < 0 ? "Перегруз" : "В пределах плана"}
        </span>
      </div>
      <div className="zone-value">
        <strong>{number(zone.forecast_orders)}</strong>
        <span>заказов ожидается</span>
      </div>
      <div className={"zone-advice" + (gap < 0 ? " zone-advice-risk" : "")}>
        <strong>{gap < 0 ? "Добавьте людей в эту зону" : gap === 0 ? "Держите резерв наготове" : "Работайте по обычному плану"}</strong>
        <p>{gap < 0 ? `При текущем плане не хватит мощности на ${number(-gap)} заказов.` : gap === 0
          ? "Прогноз равен мощности зоны. Всплеск спроса создаст очередь."
          : `По прогнозу останется запас мощности на ${number(gap)} заказов.`}</p>
      </div>
      <div className="capacity">
        <div className="capacity-label">
          <span>Плановая мощность</span>
          <strong>{number(zone.capacity_orders)}</strong>
        </div>
        <div className={"capacity-track" + (gap < 0 ? " capacity-risk" : "")} aria-hidden="true">
          <div style={{ width: Math.min(load, 100) + "%" }} />
        </div>
        <div className="capacity-label">
          <span>{gap < 0 ? "Сверх плана" : "Резерв до лимита"}</span>
          <strong className={gap < 0 ? "text-risk" : ""}>{gap < 0 ? "+" : ""}{number(Math.abs(gap))}</strong>
        </div>
      </div>
      <dl className="zone-facts">
        <div><dt>Модель</dt><dd>{modelName(forecast.model)}{forecast.uses_weather ? " · с погодой" : ""}</dd></div>
        <div>
          <dt>Погода в Москве</dt>
          <dd>
            {weather.mean_temperature_c === null ? "—" : number(weather.mean_temperature_c) + " °C"}
            {" · "}
            {weather.precipitation_mm === null ? "—" : number(weather.precipitation_mm) + " мм"}
          </dd>
        </div>
      </dl>
      <ForecastExplanation zone={zone} />
      <a className="zone-quality-link" href="#quality">График факта и прогноза <span aria-hidden="true">→</span></a>
      <p className="zone-note">Отсечка данных {formatDateTime(forecast.forecast_cutoff)} мск</p>
    </section>
  );
}

export function Dashboard() {
  const auth = useAuth();
  const [view, setView] = useState(currentView);
  const [monitorMode, setMonitorMode] = useState<"live" | "demo">("live");
  const [data, setData] = useState<DashboardData | null>(null);
  const [zoneId, setZoneId] = useState("east");
  const [date, setDate] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [error, setError] = useState("");
  const accountView = view.id === "account" || view.id === "admin";
  const preferredZone = auth.user?.preferred_zone;
  const userId = auth.user?.id;

  useEffect(() => {
    if (userId) setZoneId(preferredZone ?? "east");
  }, [preferredZone, userId]);

  useEffect(() => {
    function navigate() {
      setView(currentView());
      window.scrollTo(0, 0);
    }
    window.addEventListener("hashchange", navigate);
    return () => window.removeEventListener("hashchange", navigate);
  }, []);

  useEffect(() => {
    let pending: AbortController | undefined;

    async function refresh() {
      pending?.abort();
      const controller = new AbortController();
      pending = controller;
      setLoading(true);
      try {
        const next = await getDashboard(date, controller.signal);
        if (controller.signal.aborted) return;
        setData(next);
        setUpdatedAt(new Date());
        setError("");
      } catch (failure) {
        if (!controller.signal.aborted) {
          setError(failure instanceof Error ? failure.message : "Не удалось обновить пульт.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void refresh();
    const timer = window.setInterval(refresh, refreshInterval);
    return () => {
      pending?.abort();
      window.clearInterval(timer);
    };
  }, [date, revision]);

  const forecast = data?.forecast;
  const metrics = data?.metrics;
  const zones = forecast?.forecasts ?? [];
  const selected = zones.find((zone) => zone.zone_id === zoneId) ?? zones[0];
  const zonePicker = selected && <ZonePicker zones={zones} active={selected.zone_id} onSelect={setZoneId} />;
  const history = metrics?.holdout.predictions.filter((point) => point.zone_id === selected?.zone_id) ?? [];
  const selectionKey = forecast?.target_date + "/" + selected?.zone_id;
  const validation = metrics?.validation.find(
    (row) => row.model === metrics.selected.model && row.uses_weather === metrics.selected.uses_weather,
  );
  const validationMae = selected ? validation?.by_zone?.[selected.zone_id]?.mae_orders : undefined;
  const orders = zones.reduce((sum, zone) => sum + zone.forecast_orders, 0);
  const overloaded = zones.filter((zone) => zone.orders_over_capacity > 0);
  const mostOverloaded = overloaded.reduce<ZoneForecast | undefined>((worst, zone) =>
    !worst || zone.orders_over_capacity > worst.orders_over_capacity ? zone : worst, undefined);
  const improvement = metrics && metrics.holdout.baseline.mae_orders > 0
    ? 100 * (1 - metrics.holdout.selected.mae_orders / metrics.holdout.baseline.mae_orders)
    : null;

  return (
    <>
      <a className="skip-link" href="#main" onClick={(event) => {
        event.preventDefault();
        document.getElementById("main")?.focus();
      }}>К содержимому</a>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#courier">
            <span className="brand-icon">
              <svg viewBox="0 0 28 28" fill="none" aria-hidden="true">
                <path d="M14 4.5 23 9.2v9.6l-9 4.7-9-4.7V9.2L14 4.5Z" />
                <path d="m5 9.2 9 4.8 9-4.8M14 14v9.5M9.5 6.9l9 4.7" />
              </svg>
            </span>
            <span>Пульт доставки</span>
          </a>
          <nav aria-label="Разделы пульта">
            {views.filter((entry) => entry.id !== "admin" || auth.user?.role === "admin").map((entry) => (
              <a key={entry.id} href={"#" + entry.id} aria-current={view.id === entry.id ? "page" : undefined}>
                {entry.label}
              </a>
            ))}
          </nav>
          <span className="mode"><i />УЧЕБНОЕ ДЕМО</span>
        </div>
      </header>

      <main id="main" className="workspace" tabIndex={-1}>
        <section className="hero" aria-labelledby="page-title">
          <div>
            <p className="eyebrow hero-kicker">МОСКВА <span /> ПЛАНИРОВАНИЕ ДОСТАВКИ</p>
            <h1 id="page-title">{view.heading}</h1>
            <p>{view.id === "account" ? "Регистрация, вход и управление сессией" :
              view.id === "courier" ? "Понятный выбор перед переездом" :
              view.id === "admin" ? "Пользователи и роли" :
              view.id === "runs" ? "История обновления данных и публикации прогнозов" :
              view.id === "monitoring" ? monitorMode === "live" ? "Сверка прогнозов с заказами после закрытия суток" : "Учебная проверка на 14 прошлых днях" :
              forecast ? "Прогноз на " + formatDate(forecast.target_date) : "Суточный прогноз заказов готовой еды"}</p>
          </div>
          {!accountView && view.id !== "courier" && <div className="hero-actions">
            {view.id !== "runs" && (view.id !== "monitoring" || monitorMode === "demo") && (
              <label className="date-control">
                <span>Дата прогноза</span>
                <select value={date} onChange={(event) => setDate(event.target.value)}>
                  <option value="">Последний прогноз</option>
                  {(data?.dates ?? []).map((day) => <option key={day} value={day}>{formatDate(day)}</option>)}
                </select>
              </label>
            )}
            <button className="refresh-button" onClick={() => setRevision((value) => value + 1)} disabled={loading}>
              <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M16.6 7.4a6.7 6.7 0 1 0 .1 5.2M16.7 2.8v4.8h-4.8" /></svg>
              {loading ? "Обновляем" : "Обновить"}
            </button>
          </div>}
        </section>

        {!accountView && view.id !== "courier" && <div className="status-bar">
          <span><i />Синтетические заказы</span>
          <span aria-live="polite">
            {updatedAt ? "Данные получены в " + formatTime(updatedAt) + " мск" : "Подключение к локальному API"}
          </span>
        </div>}
        {!accountView && error && <p className="notice" role="alert">{error}</p>}
        {view.id === "account" && <AccountPanel auth={auth} zones={zones} />}
        {view.id === "admin" && <AdminPanel auth={auth} zones={zones} />}
        {view.id === "courier" && <CourierPanel forecast={forecast} selected={selected} onSelect={setZoneId} />}

        {view.id === "forecast" && (forecast && selected ? (
          <>
            <section className="stats" aria-label="Сводка прогноза">
              <article className="stat">
                <div className="stat-label">Заказов в прогнозе</div>
                <strong>{number(orders)}</strong>
                <small>По всем зонам доставки</small>
              </article>
              <article className={"stat" + (overloaded.length ? " stat-risk" : "")}>
                <div className="stat-label">Зон сверх мощности</div>
                <strong>{overloaded.length}<em> / {zones.length}</em></strong>
                <small>Прогноз выше плана зоны</small>
              </article>
              <article className="stat">
                <div className="stat-label">На сколько модель ошибалась</div>
                <strong>{metrics ? number(metrics.holdout.selected.mae_orders) : "—"}</strong>
                <small>{metrics ? "Заказа на зону за день в среднем" : "Доступно после сравнения моделей"}</small>
              </article>
            </section>

            <section className={"forecast-advice" + (mostOverloaded ? " forecast-advice-risk" : "")} aria-label="Куда направить помощь">
              <div>
                <strong>{mostOverloaded ? `Первая зона для усиления — ${mostOverloaded.zone_name}` : "По прогнозу все зоны справятся по плану"}</strong>
                <p>{mostOverloaded ? `В этой зоне самый большой ожидаемый перегруз — ${number(mostOverloaded.orders_over_capacity)} заказов сверх плана.` :
                  "Мощность каждой зоны покрывает ожидаемое число заказов."}</p>
              </div>
              {mostOverloaded && <a className="refresh-button action-link" href="#zone-forecast" onClick={(event) => {
                event.preventDefault();
                setZoneId(mostOverloaded.zone_id);
                document.getElementById("zone-forecast")?.scrollIntoView({ block: "nearest" });
              }}>Показать зону</a>}
            </section>

            <div className="forecast-grid">
              <section className="panel map-panel">
                <div className="panel-heading">
                  <div><p className="eyebrow">01 / НАГРУЗКА</p><h2>Карта зон</h2></div>
                  <span className="panel-note">Выбери зону</span>
                </div>
                <ZoneMap zones={zones} active={selected.zone_id} onSelect={setZoneId} />
              </section>
              <ZoneDetails key={selectionKey} zone={selected} forecast={forecast} />
            </div>
          </>
        ) : (
          <section className="panel empty-state" aria-live="polite">
            <h2>{loading ? "Загружаем прогноз…" : "Прогноза пока нет"}</h2>
            {!loading && <p>Из папки проекта запусти <code>python -X utf8 -m backend.pipeline</code> и обнови пульт.</p>}
          </section>
        ))}

        {view.id === "quality" && <div className="quality-grid">
          {metrics && <section className="panel quality-summary" aria-label="Главный результат проверки">
            <div>
              <p className="eyebrow">ГЛАВНЫЙ РЕЗУЛЬТАТ</p>
              <h2>Средний промах — {number(metrics.holdout.selected.mae_orders)} заказа</h2>
              <p className="panel-copy">Столько заказов в среднем составляла разница между прогнозом и фактом в одной зоне за день.</p>
            </div>
            <dl>
              <div><dt>Модель в работе</dt><dd>{modelName(metrics.selected.model)}{metrics.selected.uses_weather ? " · с погодой" : ""}</dd></div>
              <div><dt>Проверили на отдельных датах</dt><dd>{formatDate(metrics.holdout.start, true)} — {formatDate(metrics.holdout.end, true)}</dd></div>
              <div><dt>Промах прогноза по прошлой неделе</dt><dd>{number(metrics.holdout.baseline.mae_orders)} заказа</dd></div>
            </dl>
            {metrics.data_quality && <details className="monitor-help quality-data-check">
              <summary>Проверка исходных данных</summary>
              <p>Проверено {number(metrics.data_quality.checked_orders)} заказов за {metrics.data_quality.checked_days} дней.
                Необычных значений спроса: {metrics.data_quality.anomaly_count}. Они отмечаются для проверки и сохраняются.</p>
              <p>Неполных дней погоды: {metrics.data_quality.missing_weather_days}. Пропуски обрабатывает модель;
                ошибки в ID, зоне и времени заказа останавливают публикацию.</p>
            </details>}
          </section>}
          <section className="panel history-panel">
            <div className="panel-heading">
              <div><p className="eyebrow">02 / ПРОВЕРКА</p><h2>Факт и прогноз</h2></div>
              {zonePicker}
            </div>
            {metrics ? (
              <HistoryChart key={selectionKey} points={history} />
            ) : <p className="empty-panel">График появится после расчёта качества моделей.</p>}
          </section>
          <section className="panel models-panel">
            <div className="panel-heading">
                  <div><p className="eyebrow">03 / МОДЕЛИ</p><h2>Какой прогноз точнее</h2></div>
            </div>
            {metrics ? (
              <>
                <ModelTable metrics={metrics} />
                <p className="models-note">
                  На отдельном тесте за {formatDate(metrics.holdout.start, true)} — {formatDate(metrics.holdout.end, true)} выбранная модель ошибалась в среднем на{" "}
                  <strong>{number(metrics.holdout.selected.mae_orders)} заказа за день в одной зоне</strong>.
                  {improvement !== null && improvement > 0 && " Это на " + number(Math.round(improvement)) + " % меньше ошибки прогноза по прошлой неделе."}
                </p>
              </>
            ) : <p className="empty-panel">Запусти <code>python -X utf8 -m backend.modeling</code> из окружения проекта.</p>}
          </section>
          <FeatureImportance key={forecast?.target_date} windows={metrics?.feature_importance ?? []} />
          <WhatIf key={selectionKey} curves={metrics?.scenarios?.[selected?.zone_id ?? ""] ?? []} zonePicker={zonePicker} />
        </div>}

        {view.id === "monitoring" && (
          <>
            <div className="monitor-modes" aria-label="Режим мониторинга">
              <button className="refresh-button" aria-pressed={monitorMode === "live"} onClick={() => setMonitorMode("live")}>По заказам</button>
              <button className="refresh-button" aria-pressed={monitorMode === "demo"} onClick={() => setMonitorMode("demo")}>Учебный сценарий</button>
            </div>
            <p className="monitor-mode-description">{monitorMode === "live"
              ? "Сравниваем заранее сохранённые прогнозы с заказами после завершения каждого дня."
              : "Проигрываем 14 прошлых дней, чтобы показать, как система замечает рост ошибки."}</p>
            {monitorMode === "live" ? (
              <LiveMonitoring observations={data?.observations ?? []} zoneId={selected?.zone_id ?? zoneId} zonePicker={zonePicker} />
            ) : <Monitoring key={selectionKey} points={history} zonePicker={zonePicker} validationMae={validationMae} />}
          </>
        )}

        {view.id === "runs" && <section className="panel runs-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">04 / ДАННЫЕ</p><h2>Последние запуски</h2></div>
          </div>
          <RunHistory runs={data?.runs ?? []} onOpen={(target) => { setDate(target); window.location.hash = "forecast"; }} />
        </section>}
        <footer className="footer">
          <span>Пульт доставки · Проектный практикум</span>
          <span>Проверка новых результатов каждую минуту</span>
        </footer>
      </main>
    </>
  );
}
