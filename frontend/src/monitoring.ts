import type { ForecastPoint, Observation, Prediction } from "./api/dashboard";

export const WEEK_DAYS = 7;
export const REPLAY_DAYS = 2 * WEEK_DAYS;
export const ERROR_LIMIT_INCREASE = 30;

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

export function observedMonitoring(observations: Observation[], zone: string) {
  const closed = observations.flatMap((record) => {
    const actual = record.actuals?.[zone];
    const forecast = record.forecasts[zone];
    const reference = record.validation_mae[zone];
    if (actual === undefined || forecast === undefined || reference === undefined) return [];
    return [{ date: record.target_date, actual, forecast, reference }];
  }).sort((left, right) => right.date.localeCompare(left.date));

  const points: typeof closed = [];
  for (const point of closed.slice(0, REPLAY_DAYS)) {
    const previous = points.at(-1);
    if (previous && Date.parse(previous.date) - Date.parse(point.date) !== 86_400_000) break;
    points.push(point);
  }
  points.reverse();
  const blocks = [points.slice(0, -WEEK_DAYS), points.slice(-WEEK_DAYS)].filter((block) => block.length);
  const weeks = blocks.map((block) => {
    const limit = mean(block, (point) => point.reference) * (1 + ERROR_LIMIT_INCREASE / 100);
    return { ...assessWeek(block, limit), limit, start: block[0].date, end: block.at(-1)!.date };
  });
  return { points, weeks, status: signal(weeks) };
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
