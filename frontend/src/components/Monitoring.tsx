import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Prediction } from "../api/dashboard";
import { formatDate, number } from "../format";
import { ERROR_LIMIT_INCREASE, monitoringPeriods, monitoringSnapshot, REPLAY_DAYS, WEEK_DAYS } from "../monitoring";
import { HistoryChart } from "./HistoryChart";

const messages = {
  pending: ["Проверяем первые дни", "Первый вывод сделаем после семи дней."],
  normal: ["Модель справляется", "За завершённые недели промах укладывается в допустимый уровень."],
  warning: ["Модель стала чаще ошибаться", "За одну неделю средний промах слишком вырос. Посмотрим ещё одну неделю."],
  alert: ["Прогноз пора обновить", "Две недели подряд промах слишком большой. Рекомендуется заново обучить модель на свежих данных."],
};

interface Props {
  points: Prediction[];
  zonePicker: ReactNode;
  validationMae?: number;
}

export function Monitoring({ points, zonePicker, validationMae }: Props) {
  const [shock, setShock] = useState(true);
  const [increase, setIncrease] = useState(35);
  const [played, setPlayed] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (!playing || played === REPLAY_DAYS) return;
    const timer = window.setTimeout(() => {
      const next = played + 1;
      setPlayed(next);
      if (next === REPLAY_DAYS) setPlaying(false);
    }, 450);
    return () => window.clearTimeout(timer);
  }, [played, playing]);

  const periods = monitoringPeriods(points);
  if (!periods) {
    return (
      <section className="panel empty-state">
        <h2>Нужна история прогнозов</h2>
        <p>Для сценария нужны 28 последовательных дней из проверки моделей.</p>
      </section>
    );
  }
  const appliedIncrease = shock ? increase : 0;
  const result = monitoringSnapshot(periods, played, appliedIncrease, validationMae);
  const [status, detail] = played === 0
    ? ["Проверка готова к запуску", "Нажмите «Запустить проверку», чтобы увидеть результат по дням."]
    : messages[result.status];
  const firstDay = periods.replay[0];
  const firstScenarioOrders = Math.round(firstDay.actual * (1 + appliedIncrease / 100));

  function chooseScenario(enabled: boolean) {
    setShock(enabled);
    setPlayed(0);
    setPlaying(false);
  }

  return (
    <div className="monitoring-view">
      <div className="monitor-grid">
        <section className="panel monitor-settings">
          <div className="panel-heading">
            <div><p className="eyebrow">УЧЕБНЫЙ ПРИМЕР</p><h2>Если заказов стало больше</h2></div>
            {zonePicker}
          </div>
          <p className="panel-copy">Возьмём 14 прошлых дней. В варианте с ростом увеличим число заказов и сравним его с сохранённым прогнозом.</p>
          <div className="monitor-modes" aria-label="Условие учебного примера">
            <button className="refresh-button" aria-pressed={!shock} onClick={() => chooseScenario(false)}>Обычный спрос</button>
            <button className="refresh-button" aria-pressed={shock} onClick={() => chooseScenario(true)}>Заказов больше</button>
          </div>
          <label className="monitor-control">
            <span>На сколько больше заказов <strong>+{appliedIncrease} %</strong></span>
            <input type="range" min={shock ? 10 : 0} max={60} step={5} value={appliedIncrease} disabled={!shock}
              onChange={(event) => { setIncrease(Number(event.target.value)); setPlayed(0); setPlaying(false); }} />
          </label>
          <p className="monitor-example">Например, {formatDate(firstDay.date, true)} было {number(firstDay.actual)} заказов.
            {shock ? ` В сценарии будет ${number(firstScenarioOrders)} — добавили ${increase}%.` : " Проверим, как модель справлялась с этим спросом."}</p>
          <label className="monitor-control">
            <span>Проверено дней <strong>{played} из {REPLAY_DAYS}</strong></span>
            <input type="range" min={0} max={REPLAY_DAYS} value={played}
              onChange={(event) => { setPlaying(false); setPlayed(Number(event.target.value)); }} />
          </label>
          <button className="refresh-button primary-action replay-button" onClick={() => {
            if (playing) setPlaying(false);
            else {
              if (played === REPLAY_DAYS) setPlayed(0);
              setPlaying(true);
            }
          }}>
            {playing ? "Пауза" : played > 0 && played < REPLAY_DAYS ? "Продолжить" : played === REPLAY_DAYS ? "Повторить проверку" : "Запустить проверку"}
          </button>
        </section>
        <section className="panel monitor-signal">
          <p className="eyebrow">ЧТО УВИДЕЛА СИСТЕМА</p>
          <div className={"monitor-message monitor-" + result.status} aria-live="polite">
            <h2>{status}</h2>
            <p>{detail}</p>
          </div>
          <dl className="monitor-metrics">
            <div><dt>Обычный промах</dt><dd>{number(result.referenceMae)}<small>заказов в день</small></dd></div>
            <div><dt>В этой проверке допускаем до</dt><dd>{number(result.limit)}<small>заказов в день</small></dd></div>
            <div><dt>Получилось в сценарии</dt><dd>{result.currentMae === null ? "—" : number(result.currentMae)}<small>заказов в день</small></dd></div>
          </dl>
          <p className="monitor-copy">
            Промах — разница между ожидаемым и полученным числом заказов. Здесь показано среднее по проверенным дням.
          </p>
          <div className="monitor-weeks">
            {result.weeks.map((week, index) => (
              <article className={week.aboveLimit ? "week-card week-alert" : "week-card"} key={index}>
                <strong>Неделя {index + 1}</strong>
                <span>{week.error === null ? "—" : number(week.error)}</span>
                <small>{week.complete ? week.aboveLimit ? "Промах слишком большой" : "Укладываемся в допустимый промах" : "Проверено " + week.days + " из " + WEEK_DAYS + " дней"}</small>
              </article>
            ))}
          </div>
          <details className="monitor-help">
            <summary>Почему система предлагает обновить модель</summary>
            <p>Для этой проверки берём обычный промах {number(result.referenceMae)} и добавляем запас {ERROR_LIMIT_INCREASE}%. Получается {number(result.limit)} заказа в день.</p>
            <p>Если средний промах за полную неделю больше этого числа, показываем предупреждение. Если это повторилось две недели подряд, рекомендуем переобучение.</p>
            <p>Обычный промах рассчитан {validationMae === undefined ? "по 14 дням перед сценарием." : "на 84 днях проверки модели для выбранной зоны."}</p>
          </details>
        </section>
      </div>
      <section className="panel monitor-history">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">СРАВНЕНИЕ ПО ДНЯМ</p>
            <h2>Сколько ждали и сколько получили в примере</h2>
          </div>
          <span className="panel-note">{formatDate(periods.replay[0].date, true)} — {formatDate(periods.replay[REPLAY_DAYS - 1].date, true)}</span>
        </div>
        {played ? <HistoryChart points={result.points} actualLabel={shock ? "Заказы в сценарии" : "Заказы"} />
          : <p className="empty-panel">Нажмите «Запустить проверку». Здесь по очереди появятся 14 дней.</p>}
      </section>
    </div>
  );
}
