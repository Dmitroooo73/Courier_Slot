import { useEffect, useState } from "react";
import { getWeatherNow } from "../api/dashboard";
import type { WeatherNow } from "../api/dashboard";
import { formatTime, number } from "../format";

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
  const updated = formatTime(new Date(weather.observed_at));
  return <span className="now-weather" title={`Open-Meteo · ощущается как ${weather.apparent_c === null ? "—" : number(weather.apparent_c) + " °C"} · ветер ${weather.wind_ms === null ? "—" : number(weather.wind_ms) + " м/с"}. Модель использует архивный прогноз погоды, а не эти данные.`}>
    Сейчас в Москве {weather.temperature_c > 0 ? "+" : ""}{number(weather.temperature_c)} °C, {weather.description}
    <small>{weather.stale ? ` · не обновлялось с ${updated}` : ` · на ${updated}`}</small>
  </span>;
}
