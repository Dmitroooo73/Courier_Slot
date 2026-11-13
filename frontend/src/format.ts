const locale = "ru-RU";
const timeZone = "Europe/Moscow";
const longDate = new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric", timeZone });
const shortDate = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone });
const dateTime = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone });
const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone });
const numberFormat = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });

export function formatDate(day: string, short = false): string {
  return (short ? shortDate : longDate).format(new Date(day + "T12:00:00+03:00"));
}

export function formatDateTime(timestamp: string): string {
  return dateTime.format(new Date(timestamp));
}

export function formatTime(timestamp: Date): string {
  return time.format(timestamp);
}

export function number(value: number): string {
  return numberFormat.format(value);
}

export function modelName(model: string): string {
  if (model === "seasonal_naive_7d") return "Сезонная база";
  if (model === "ridge") return "Ridge";
  if (model === "catboost") return "CatBoost";
  return model;
}

export const featureLabels: Record<string, string> = {
  zone_id: "Зона доставки",
  weekday: "День недели",
  orders_lag_2: "Заказы два дня назад",
  orders_lag_7: "Заказы неделю назад",
  orders_lag_14: "Заказы две недели назад",
  mean_closed_week: "Среднее за 7 завершённых дней",
  mean_temperature_c: "Температура",
  precipitation_mm: "Осадки",
  hours_with_precipitation: "Часы с осадками",
};

export function ordersSourceLabel(source: string | undefined): string {
  if (source === "synthetic") return "синтетические заказы, архивная версия без погоды";
  if (source === "synthetic-weather-v2") return "синтетические заказы, спрос зависит от погоды";
  return source ?? "неизвестный источник";
}
