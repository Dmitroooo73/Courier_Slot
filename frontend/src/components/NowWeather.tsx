import { useEffect, useState } from "react";
import { getWeatherNow } from "../api/dashboard";
import type { WeatherNow } from "../api/dashboard";
import { formatDateTime, formatTime, number } from "../format";

const refreshInterval = 10 * 60_000;

export function NowWeather() {
  const [weather, setWeather] = useState<WeatherNow | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let controller = new AbortController();
    function load() {
      controller.abort();
      controller = new AbortController();
      const signal = controller.signal;
      getWeatherNow(signal).then((next) => { setWeather(next); setFailed(false); })
        .catch(() => { if (!signal.aborted) setFailed(true); });
    }
    load();
    const timer = window.setInterval(load, refreshInterval);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);

  if (!weather) return <span className="now-weather">{failed ? "Погода сейчас недоступна" : "Погода в Москве…"}</span>;
  // A failed refresh in the browser or on the server, or an old provider timestamp, is said out loud.
  const old = weather.stale || failed;
  const observed = new Date(weather.observed_at);
  const updated = (weather.age_minutes ?? 0) > 12 * 60 ? formatDateTime(weather.observed_at) : formatTime(observed);
  return <span className={"now-weather" + (old ? " now-weather-stale" : "")} title={`Open-Meteo · ощущается как ${weather.apparent_c === null ? "—" : number(weather.apparent_c) + " °C"} · ветер ${weather.wind_ms === null ? "—" : number(weather.wind_ms) + " м/с"}. Модель использует архивный прогноз погоды, а не эти данные.`}>
    Сейчас в Москве {weather.temperature_c > 0 ? "+" : ""}{number(weather.temperature_c)} °C, {weather.description}
    <small>{old ? ` · данные на ${updated}, обновить не удалось` : ` · на ${updated}`}</small>
  </span>;
}
