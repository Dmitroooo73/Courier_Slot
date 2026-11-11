import { useState } from "react";
import type { ForecastPoint } from "../api/dashboard";
import { formatDate, number } from "../format";

const width = 760;
const height = 220;
const plot = { left: 43, right: 731, top: 18, bottom: 184 };

export function HistoryChart({ points, actualLabel = "Факт" }: { points: ForecastPoint[]; actualLabel?: string }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  if (points.length === 0) return <p className="empty-panel">За этот период нет проверочных прогнозов.</p>;

  const values = points.flatMap((point) => [point.actual, point.forecast]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const step = Math.max(5, Math.ceil((max - min) / 25) * 5);
  const low = Math.max(0, Math.floor((min - 3) / step) * step);
  const high = Math.ceil((max + 3) / step) * step;
  const last = points.length - 1;
  const current = Math.min(activeIndex ?? last, last);
  const point = points[current];
  const x = (index: number) => plot.left + index / Math.max(last, 1) * (plot.right - plot.left);
  const y = (value: number) => plot.bottom - (value - low) / (high - low) * (plot.bottom - plot.top);
  const line = (kind: "actual" | "forecast") => points.map((row, index) => [x(index), y(row[kind])].join(",")).join(" ");
  const ticks = Array.from({ length: (high - low) / step + 1 }, (_, index) => low + index * step);
  const labels = Array.from(new Set([0, Math.round(last / 3), Math.round(2 * last / 3), last]));

  return (
    <div className="history">
      <div className="chart-caption">
        <div className="legend">
          <span><i className="legend-line actual" />{actualLabel}</span>
          <span><i className="legend-line forecast" />Прогноз</span>
        </div>
        <div className="chart-point">
          <span>{formatDate(point.date, true)}</span>
          <strong>{actualLabel}: {number(point.actual)} · прогноз: {number(point.forecast)}</strong>
        </div>
      </div>
      <svg
        className="history-plot"
        viewBox={"0 0 " + width + " " + height}
        role="img"
        tabIndex={0}
        aria-label={actualLabel + " и прогноз по дням. Стрелки влево и вправо меняют выбранный день."}
        onPointerMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const viewX = (event.clientX - box.left) / box.width * width;
          const index = Math.round((viewX - plot.left) / (plot.right - plot.left) * last);
          setActiveIndex(Math.max(0, Math.min(last, index)));
        }}
        onKeyDown={(event) => {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          event.preventDefault();
          const next = current + (event.key === "ArrowLeft" ? -1 : 1);
          setActiveIndex(Math.max(0, Math.min(last, next)));
        }}
      >
        {ticks.map((value) => (
          <g key={value} className="chart-grid">
            <line x1={plot.left} x2={plot.right} y1={y(value)} y2={y(value)} />
            <text x={plot.left - 9} y={y(value)} dy="0.34em" textAnchor="end">{value}</text>
          </g>
        ))}
        {labels.map((index) => (
          <text key={index} className="chart-date" x={x(index)} y={207} textAnchor="middle">
            {formatDate(points[index].date, true)}
          </text>
        ))}
        <line className="chart-cursor" x1={x(current)} x2={x(current)} y1={plot.top} y2={plot.bottom} />
        <polyline className="chart-series actual" points={line("actual")} />
        <polyline className="chart-series forecast" points={line("forecast")} />
        <circle className="chart-dot actual" cx={x(current)} cy={y(point.actual)} r={4} />
        <circle className="chart-dot forecast" cx={x(current)} cy={y(point.forecast)} r={4} />
      </svg>
    </div>
  );
}
