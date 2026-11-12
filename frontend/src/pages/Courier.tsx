import { useState } from "react";
import type { Forecast, ZoneForecast } from "../api/dashboard";
import { compareCourierExample } from "../courier";
import { formatDate, number } from "../format";

export function CourierPanel({ forecast, selected, onSelect }: {
  forecast?: Forecast | null; selected?: ZoneForecast; onSelect: (id: string) => void;
}) {
  const [example, setExample] = useState<"nearby" | "far" | "stale" | null>(null);
  const current = selected?.zone_name ?? "Ваш район";
  const other = [...(forecast?.forecasts ?? [])]
    .filter(zone => zone.zone_id !== selected?.zone_id)
    .sort((a, b) => b.forecast_orders - a.forecast_orders)[0];
  const destination = other?.zone_name ?? "Другой район";
  const input = { currentOrders: 24, currentCouriers: 6, otherOrders: 48, otherCouriers: 6,
    travelMinutes: example === "far" ? 45 : 15, ageMinutes: example === "stale" ? 30 : 5 };
  const result = compareCourierExample(input);

  return <div className="courier-view">
    <div className="courier-location">
      <label className="date-control"><span>Где вы сейчас</span>
        <select value={selected?.zone_id ?? ""} onChange={event => onSelect(event.target.value)} disabled={!selected}>
          {!selected && <option value="">Районы загружаются</option>}
          {forecast?.forecasts.map(zone => <option key={zone.zone_id} value={zone.zone_id}>{zone.zone_name}</option>)}
        </select>
      </label>
      <span className="panel-copy">Москва · условные зоны доставки</span>
    </div>

    {!example ? <section className="panel courier-answer" aria-label="Совет для курьера">
      <span className="courier-state">Нужны свежие данные</span>
      <h2>Совета о переезде пока нет</h2>
      <p>Чтобы сравнить районы, нужны заказы на ближайший час, число свободных курьеров и время дороги.
        Сейчас у нас только учебный прогноз на весь день.</p>
      <div className="courier-missing">
        <span>Заказы сейчас <strong>Нет источника</strong></span>
        <span>Курьеры рядом <strong>Неизвестно</strong></span>
        <span>Время переезда <strong>Не рассчитано</strong></span>
      </div>
      <button className="refresh-button primary-action" onClick={() => setExample("nearby")}>Посмотреть учебный пример <span aria-hidden="true">→</span></button>
      <small>Покажем, как дорога и конкуренция меняют решение.</small>
    </section> : <section className="panel courier-answer" aria-label="Учебный совет для курьера">
      <div className="courier-example-heading"><span className="courier-state">Вымышленный пример · ближайший час</span>
        <button className="text-button" onClick={() => setExample(null)}>Закрыть пример</button>
      </div>
      <div className="monitor-modes courier-cases" aria-label="Условия примера">
        <button className="refresh-button" aria-pressed={example === "nearby"} onClick={() => setExample("nearby")}>Рядом больше заказов</button>
        <button className="refresh-button" aria-pressed={example === "far"} onClick={() => setExample("far")}>Долгая дорога</button>
        <button className="refresh-button" aria-pressed={example === "stale"} onClick={() => setExample("stale")}>Данные устарели</button>
      </div>
      <div aria-live="polite">
        <h2>{result.decision === "consider" ? `В примере: рассмотрите район «${destination}»`
          : result.decision === "stay" ? `В примере: оставайтесь в районе «${current}»`
          : "Свежих данных не хватает для совета"}</h2>
        <p>{result.decision === "consider"
          ? `При одинаковом числе курьеров в другом районе больше заказов. Даже с дорогой ${input.travelMinutes} минут условный расчёт лучше.`
          : result.decision === "stay" ? "Переезд займёт большую часть часа. За оставшееся время преимущество другого района исчезнет."
          : "Вымышленному снимку уже 30 минут. Очередь заказов и число курьеров могли измениться — сначала нужно обновление."}</p>
      </div>
      {result.stay !== null && result.move !== null && <div className="courier-comparison">
        <article><span>Остаться · {current}</span><strong>≈{number(result.stay)}</strong><small>условных заказов за час</small></article>
        <article className={result.decision === "consider" ? "courier-choice" : ""}>
          <span>Переехать · {destination}</span><strong>≈{number(result.move)}</strong><small>с учётом {input.travelMinutes} минут дороги</small>
        </article>
      </div>}
      <p className="courier-demo-note">Все числа в этом примере вымышлены. Это демонстрация решения, а не обещание заказов или заработка.</p>
      <details className="monitor-help"><summary>Откуда вывод в примере</summary>
        <p>В вашей зоне: 24 заказа и 6 курьеров. В другой: 48 заказов и 6 курьеров. Условно делим заказы поровну.
          После переезда учитываем только оставшиеся минуты из часа.</p>
        <p>Переезд рассматриваем, если условный результат больше хотя бы на 20%. Это учебное правило, не проверенная рекомендация для реальных смен.</p>
        <p>В примере дождь в обеих зонах. Дорога {input.travelMinutes} минут задана вручную;
          маршруты и районную погоду приложение пока не получает. Возраст снимка — {input.ageMinutes} минут, допустимо до 15.</p>
      </details>
    </section>}

    <details className="panel courier-daily">
      <summary>Что известно по суточному прогнозу</summary>
      {forecast && selected ? <>
        <p className="panel-copy">Учебный прогноз на {formatDate(forecast.target_date)}. Это общее число заказов за весь день,
          а не очередь сейчас и не личные заказы курьера.</p>
        <div className="courier-daily-values">
          <div><span>{current} · ваш район</span><strong>{number(selected.forecast_orders)} заказов</strong></div>
          {other && <div><span>{destination} · для сравнения</span><strong>{number(other.forecast_orders)} заказов</strong></div>}
        </div>
        <p className="panel-copy">Погода — один прогноз для Москвы: {forecast.weather.mean_temperature_c === null ? "температура неизвестна" : number(forecast.weather.mean_temperature_c) + " °C"},
          {" "}{forecast.weather.precipitation_mm === null ? "осадки неизвестны" : number(forecast.weather.precipitation_mm) + " мм осадков за день"}.
          Отличия между районами не измеряются.</p>
        <a className="zone-quality-link" href="#forecast">Карта и подробный прогноз →</a>
      </> : <p className="panel-copy">Суточный прогноз пока не загружен. Учебный пример доступен независимо от него.</p>}
    </details>
  </div>;
}
