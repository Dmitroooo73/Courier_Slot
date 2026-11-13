import type { ForecastPoint, MonitoringDecision, Prediction } from "./api/dashboard";

export const WEEK_DAYS = 7;
export const REPLAY_DAYS = 2 * WEEK_DAYS;
export const ERROR_LIMIT_INCREASE = 30;

export const monitoringMessages = {
  pending: "Собираем первую неделю",
  normal: "Точность на обычном уровне",
  warning: "Модель стала чаще ошибаться",
  alert: "Прогноз пора обновить",
};

export function decisionAdvice(decision: MonitoringDecision): string {
  if (decision.status === "pending") return `Для первого недельного вывода нужно ${decision.rule.week_days} дней подряд. Пока проверено: ${decision.points.length}. Продолжаем собирать итоги дней.`;
  if (decision.status === "normal") return "В полных неделях промах не превысил допустимый уровень. Продолжаем наблюдение.";
  if (decision.status === "warning") return "В одной полной неделе промах выше допустимого. Проверим следующую неделю; пока модель не меняем.";
  return "Две недели подряд промах выше допустимого. Рекомендуем обучить и проверить новую модель. Автоматической замены пока нет.";
}

interface Periods {
  reference: Prediction[];
  replay: Prediction[];
}

export function monitoringPeriods(points: Prediction[]): Periods | null {
  const days = [...points].sort((left, right) => left.date.localeCompare(right.date)).slice(-2 * REPLAY_DAYS);
  if (days.length !== 2 * REPLAY_DAYS) return null;
  const consecutive = days.slice(1).every(
    (point, index) => Date.parse(point.date) - Date.parse(days[index].date) === 86_400_000,
  );
  if (!consecutive) return null;
  return { reference: days.slice(0, REPLAY_DAYS), replay: days.slice(REPLAY_DAYS) };
}

function mean<T>(points: T[], value: (point: T) => number): number {
  return points.reduce((total, point) => total + value(point), 0) / points.length;
}

function mae(points: ForecastPoint[]): number {
  return mean(points, (point) => Math.abs(point.actual - point.forecast));
}

function assessWeek(points: ForecastPoint[], limit: number) {
  const error = points.length ? mae(points) : null;
  const complete = points.length === WEEK_DAYS;
  return { days: points.length, error, complete, aboveLimit: complete && error !== null && error > limit };
}

function signal(weeks: ReturnType<typeof assessWeek>[]): "pending" | "normal" | "warning" | "alert" {
  const exceeded = weeks.filter((week) => week.aboveLimit).length;
  if (exceeded === 2) return "alert";
  if (exceeded === 1) return "warning";
  return weeks.some((week) => week.complete) ? "normal" : "pending";
}

export function monitoringSnapshot(periods: Periods, played: number, increase: number, validationMae?: number) {
  const referenceMae = validationMae ?? mae(periods.reference);
  const limit = referenceMae * (1 + ERROR_LIMIT_INCREASE / 100);
  const points = periods.replay.slice(0, played).map((point) => ({
    ...point,
    actual: Math.round(point.actual * (1 + increase / 100)),
  }));
  const weeks = [0, WEEK_DAYS].map((start) => assessWeek(points.slice(start, start + WEEK_DAYS), limit));
  return {
    points,
    referenceMae,
    limit,
    weeks,
    status: signal(weeks),
    currentMae: points.length ? mae(points) : null,
    referenceDemand: mean(periods.reference, (point) => point.actual),
    currentDemand: points.length ? mean(points, (point) => point.actual) : null,
  };
}
