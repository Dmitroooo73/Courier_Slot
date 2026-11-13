import { useEffect, useState } from "react";
import { getLifecycleDemo } from "../api/dashboard";
import type { CandidateJob, LifecycleDemoSummary } from "../api/dashboard";
import { formatDate, number } from "../format";
import { versionLabel } from "./ModelLifecycle";

const width = 760;
const height = 250;
const plot = { left: 43, right: 731, top: 34, bottom: 204 };

interface Step { date: string; title: string; text: string; tone?: "good" | "risk" }

function mae(rows: { actual: number; forecast: number }[]): number | null {
  return rows.length ? rows.reduce((sum, row) => sum + Math.abs(row.actual - row.forecast), 0) / rows.length : null;
}

function steps(demo: LifecycleDemoSummary, zoneNames: Record<string, string>): Step[] {
  const zone = zoneNames[demo.shift.zone_id] ?? demo.shift.zone_id;
  const result: Step[] = [{
    date: demo.shift.start, title: "Спрос изменился",
    text: `В учебных данных на ${zone === "Восток" ? "Востоке" : zone} добавлено ${number(Math.round((demo.shift.factor - 1) * 100))}% заказов. Модель об этом не знает.`, tone: "risk",
  }];
  for (const event of demo.events) {
    if (event.kind === "initialize" && event.version_id) {
      const version = demo.versions[event.version_id];
      result.push({ date: event.date, title: "Первая рабочая версия",
        text: `${versionLabel(version)}. Обучена на данных по ${formatDate(version.training.end, true)} и дальше каждый вечер только прогнозирует.` });
    } else if (event.kind === "monitoring") {
      const recovered = event.previous === "alert" || (event.previous === "warning" && event.status === "normal");
      result.push({ date: event.date,
        title: event.status === "alert" ? "Сигнал: две недели выше порога" : recovered ? "Промах снова в норме" : event.status === "warning" ? "Предупреждение мониторинга" : "Мониторинг: норма",
        text: event.status === "alert" ? `На ${zone === "Восток" ? "Востоке" : zone} промах две недели подряд выше порога — правило рекомендует новую версию.`
          : recovered ? "Последняя полная неделя уложилась в порог." : event.status === "warning" ? "Неделя с промахом выше порога. Ждём вторую неделю." : "Промах в пределах порога.",
        tone: recovered ? "good" : event.status === "normal" ? undefined : "risk" });
    } else if (event.kind === "candidate" && event.job) {
      result.push(candidateStep(event.date, event.job, demo, zoneNames));
    } else if (event.kind === "rollback") {
      result.push({ date: event.date, title: "Откат администратором",
        text: `Рабочей снова стала первая версия. Прогнозы, которые уже выпустила новая версия, остались без изменений${demo.checks.published_forecasts_unchanged_after_rollback ? " — проверено" : ""}.` });
    }
  }
  return result.sort((a, b) => a.date.localeCompare(b.date));
}

function candidateStep(date: string, job: Pick<CandidateJob, "trigger" | "status" | "decision" | "control">,
  demo: LifecycleDemoSummary, zoneNames: Record<string, string>): Step {
  const why = job.trigger.kind === "manual" ? "Администратор запросил проверку без повода."
    : job.trigger.zones.includes(demo.shift.zone_id) ? "Планировщик поставил задание по сигналу."
      : `Сигнал в зоне ${job.trigger.zones.map(zone => zoneNames[zone] ?? zone).join(", ")}, где спрос не менялся — случайный шум.`;
  const control = job.control ? ` Контрольная неделя: ${formatDate(job.control.start, true)} — ${formatDate(job.control.end, true)}` : "";
  return { date, title: job.status === "activated" ? "Новая версия в работе" : "Новая версия отклонена",
    text: `${why} ${job.decision?.summary ?? ""}${control}`, tone: job.status === "activated" ? "good" : undefined };
}

function DemoChart({ demo, zone }: { demo: LifecycleDemoSummary; zone: string }) {
  const rows = demo.timeline.map(row => ({ date: row.date, actual: row.actual[zone], forecast: row.forecast[zone], version: row.version_id }));
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  if (!rows.length) return null;
  const values = rows.flatMap(row => [row.actual, row.forecast]);
  const low = Math.floor((Math.min(...values) - 5) / 20) * 20;
  const high = Math.ceil((Math.max(...values) + 5) / 20) * 20;
  const last = rows.length - 1;
  const current = Math.min(activeIndex ?? last, last);
  const x = (index: number) => plot.left + index / Math.max(last, 1) * (plot.right - plot.left);
  const y = (value: number) => plot.bottom - (value - low) / (high - low) * (plot.bottom - plot.top);
  const line = (kind: "actual" | "forecast") => rows.map((row, index) => [x(index), y(row[kind])].join(",")).join(" ");
  const ticks = Array.from({ length: (high - low) / 20 + 1 }, (_, index) => low + index * 20);
  const shiftIndex = rows.findIndex(row => row.date >= demo.shift.start);
  const newIndex = rows.findIndex(row => row.version !== demo.first_version_id);
  const point = rows[current];
  const markers = [
    shiftIndex >= 0 && { index: shiftIndex, label: "сдвиг спроса" },
    newIndex >= 0 && { index: newIndex, label: "новая версия" },
  ].filter(Boolean) as { index: number; label: string }[];
  const labels = Array.from(new Set([0, Math.round(last / 2), last]));

  return <div className="history">
    <div className="chart-caption">
      <div className="legend">
        <span><i className="legend-line actual" />Факт</span>
        <span><i className="legend-line forecast" />Прогноз рабочей версии</span>
        {newIndex >= 0 && <span><i className="legend-band" />Прогнозирует новая версия</span>}
      </div>
      <div className="chart-point">
        <span>{formatDate(point.date, true)} · {point.version === demo.first_version_id ? "первая версия" : "новая версия"}</span>
        <strong>Факт: {number(point.actual)} · прогноз: {number(point.forecast)}</strong>
      </div>
    </div>
    <svg className="history-plot" viewBox={`0 0 ${width} ${height}`} role="img" tabIndex={0}
      aria-label="Факт и прогноз по дням для зоны со сдвигом спроса. Стрелки влево и вправо меняют выбранный день."
      onPointerMove={(event) => {
        const box = event.currentTarget.getBoundingClientRect();
        const viewX = (event.clientX - box.left) / box.width * width;
        setActiveIndex(Math.max(0, Math.min(last, Math.round((viewX - plot.left) / (plot.right - plot.left) * last))));
      }}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        setActiveIndex(Math.max(0, Math.min(last, current + (event.key === "ArrowLeft" ? -1 : 1))));
      }}>
      {newIndex >= 0 && <rect className="chart-band" x={x(newIndex) - (x(1) - x(0)) / 2} y={plot.top}
        width={plot.right - x(newIndex) + (x(1) - x(0)) / 2} height={plot.bottom - plot.top} />}
      {ticks.map(value => <g key={value} className="chart-grid">
        <line x1={plot.left} x2={plot.right} y1={y(value)} y2={y(value)} />
        <text x={plot.left - 9} y={y(value)} dy="0.34em" textAnchor="end">{value}</text>
      </g>)}
      {labels.map(index => <text key={index} className="chart-date" x={x(index)} y={228}
        textAnchor={index === 0 ? "start" : index === last ? "end" : "middle"}>{formatDate(rows[index].date, true)}</text>)}
      {markers.map(marker => <g key={marker.label} className="chart-event">
        <line x1={x(marker.index)} x2={x(marker.index)} y1={plot.top - 8} y2={plot.bottom} />
        <text x={x(marker.index) + 5} y={plot.top - 12}>{marker.label}</text>
      </g>)}
      <line className="chart-cursor" x1={x(current)} x2={x(current)} y1={plot.top} y2={plot.bottom} />
      <polyline className="chart-series actual" points={line("actual")} />
      <polyline className="chart-series forecast" points={line("forecast")} />
      <circle className="chart-dot actual" cx={x(current)} cy={y(point.actual)} r={4} />
      <circle className="chart-dot forecast" cx={x(current)} cy={y(point.forecast)} r={4} />
    </svg>
  </div>;
}

export function LifecycleDemo({ zoneNames }: { zoneNames: Record<string, string> }) {
  const [demo, setDemo] = useState<LifecycleDemoSummary | null | undefined>(undefined);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void getLifecycleDemo(controller.signal).then(setDemo).catch(() => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, []);

  if (error) return <section className="panel empty-state"><h2>Не удалось загрузить учебный цикл</h2><p>Обновите страницу.</p></section>;
  if (demo === undefined) return <section className="panel empty-state"><h2>Загружаем учебный цикл…</h2></section>;
  if (demo === null) {
    return <section className="panel empty-state">
      <h2>Учебный цикл ещё готовится</h2>
      <p>Планировщик рассчитывает его после запуска, обычно за одну-две минуты. Обновите страницу позже.</p>
    </section>;
  }

  const zone = demo.shift.zone_id;
  const shifted = demo.timeline.filter(row => row.date >= demo.shift.start)
    .map(row => ({ actual: row.actual[zone], forecast: row.forecast[zone], version: row.version_id }));
  const before = mae(shifted.filter(row => row.version === demo.first_version_id));
  const after = mae(shifted.filter(row => row.version !== demo.first_version_id));
  const activated = demo.events.find(event => event.kind === "candidate" && event.job?.status === "activated")?.job;
  const zoneName = zoneNames[zone] ?? zone;

  return <div className="lifecycle-demo">
    <section className="panel demo-summary" aria-labelledby="demo-title">
      <p className="eyebrow">УЧЕБНЫЕ ДАННЫЕ · ОТДЕЛЬНЫЙ КАТАЛОГ</p>
      <h2 id="demo-title">{activated ? "Система сама заменила модель, когда спрос изменился" : "Как система заменяет модель"}</h2>
      <p className="panel-copy">Проигрываем {formatDate(demo.period.start, true)} — {formatDate(demo.period.end)} тем же кодом, что и в работе:
        вечерний прогноз, сверка после полуночи, мониторинг, проверка новой версии и откат. Рабочие прогнозы, версии и история пульта не меняются.</p>
      <div className="demo-stats">
        <article className="stat stat-risk"><div className="stat-label">Промах на {zoneName === "Восток" ? "Востоке" : zoneName} до замены</div>
          <strong>{before === null ? "—" : number(Math.round(before * 10) / 10)}</strong><small>заказа в день после сдвига спроса</small></article>
        <article className="stat"><div className="stat-label">После замены</div>
          <strong>{after === null ? "—" : number(Math.round(after * 10) / 10)}</strong><small>заказа в день, прогнозирует новая версия</small></article>
        <article className="stat"><div className="stat-label">Проверок новой версии</div>
          <strong>{demo.candidates}</strong><small>{activated ? "одна принята, остальные отклонены" : "все отклонены"}</small></article>
      </div>
      <DemoChart demo={demo} zone={zone} />
    </section>
    <section className="panel demo-steps" aria-labelledby="demo-steps-title">
      <h2 id="demo-steps-title">Что происходило</h2>
      <ol>{steps(demo, zoneNames).map((step, index) => <li key={index} className={step.tone ? "demo-step-" + step.tone : ""}>
        <time>{formatDate(step.date, true)}</time>
        <div><strong>{step.title}</strong><p>{step.text}</p></div>
      </li>)}</ol>
      <ul className="lifecycle-checks demo-checks">
        <li className={demo.checks.repeated_signal_creates_job === false ? "check-pass" : "check-fail"}>
          <span aria-hidden="true">{demo.checks.repeated_signal_creates_job === false ? "✓" : "✗"}</span> Повторный сигнал не создал второе задание.</li>
        {"published_forecasts_unchanged_after_rollback" in demo.checks && <li className={demo.checks.published_forecasts_unchanged_after_rollback ? "check-pass" : "check-fail"}>
          <span aria-hidden="true">{demo.checks.published_forecasts_unchanged_after_rollback ? "✓" : "✗"}</span> После отката опубликованные прогнозы не изменились.</li>}
      </ul>
      <details className="monitor-help">
        <summary>Как пересчитать демонстрацию</summary>
        <p>Планировщик строит её один раз во временном каталоге и сохраняет только итог. Пересчитать вручную:
          <code> docker compose exec worker python -m backend.modeling.demo_cycle --force</code>. Сдвиг и даты заданы в
          <code> backend/modeling/demo_cycle.py</code>; правила — {demo.rules.retrain}, {demo.rules.candidate}, {demo.rules.promotion}.</p>
      </details>
    </section>
  </div>;
}
