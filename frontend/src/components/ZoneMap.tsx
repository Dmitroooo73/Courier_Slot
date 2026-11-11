import type { ZoneForecast } from "../api/dashboard";
import { number } from "../format";

const shapes = [
  {
    id: "north",
    path: "M226 125 165 63 201 45 236 48 270 30 327 42 354 58 393 78 314 111Z",
    label: [275, 68],
  },
  {
    id: "east",
    path: "M314 111 393 78 431 105 443 146 476 161 464 203 425 227 414 264 322 239 358 175Z",
    label: [406, 161],
  },
  {
    id: "south",
    path: "M322 239 414 264 391 296 349 306 305 335 278 319 242 334 217 311 170 300 228 232Z",
    label: [294, 275],
  },
  {
    id: "west",
    path: "M228 232 170 300 139 270 105 249 94 213 64 192 88 162 187 182Z",
    label: [145, 212],
  },
  {
    id: "northwest",
    path: "M187 182 88 162 91 135 108 122 113 96 145 84 165 63 226 125Z",
    label: [156, 125],
  },
  {
    id: "center",
    path: "M226 125 314 111 358 175 322 239 228 232 187 182Z",
    label: [276, 166],
  },
] as const;

interface Props {
  zones: ZoneForecast[];
  active: string;
  onSelect: (id: string) => void;
}

export function ZoneMap({ zones, active, onSelect }: Props) {
  return (
    <>
      <div className="map-canvas">
        <svg viewBox="0 0 540 365" role="group" aria-label="Карта условных зон доставки Москвы">
          <defs>
            <pattern id="map-grid" width="20" height="20" patternUnits="userSpaceOnUse">
              <path d="M20 0H0V20" fill="none" stroke="#dce5de" strokeWidth="1" />
            </pattern>
          </defs>
          <path d="M0 0H540V365H0Z" fill="url(#map-grid)" />
          <path className="map-ring" d="M41 194C51 98 101 37 209 24C351 2 462 60 489 135C533 253 435 335 304 350C179 368 46 304 41 194Z" />
          <g className="map-compass" aria-hidden="true">
            <text x="496" y="39">С</text>
            <path d="M496 45 491 57 496 54 501 57Z" />
          </g>
          <text className="map-caption" x="20" y="346" aria-hidden="true">МОСКВА / СХЕМА</text>
          {shapes.map(({ id, path }) => {
            const zone = zones.find((entry) => entry.zone_id === id);
            if (!zone) return null;
            const pressed = active === id;
            return (
              <g
                key={id}
                role="button"
                tabIndex={0}
                aria-label={zone.zone_name}
                aria-pressed={pressed}
                className={"map-zone" + (zone.orders_over_capacity ? " map-zone-risk" : "")}
                onClick={() => onSelect(id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(id);
                  }
                }}
              >
                <title>{zone.zone_name} · {number(zone.forecast_orders)} из {number(zone.capacity_orders)}</title>
                <path d={path} />
              </g>
            );
          })}
          <path className="map-river" d="M33 165C78 132 102 156 125 172C148 189 181 206 196 177S232 148 253 178S289 204 315 197S332 237 364 251S423 267 475 240S500 224 526 238" aria-hidden="true" />
          <g className="map-labels" aria-hidden="true">
            {shapes.map(({ id, label: [x, y] }) => {
              const zone = zones.find((entry) => entry.zone_id === id);
              if (!zone) return null;
              return (
                <text key={id} x={x} y={y} className={active === id ? "map-label-active" : ""}>
                  <tspan x={x}>{zone.zone_name}</tspan>
                  <tspan className="map-number" x={x} dy="19">{number(zone.forecast_orders)}</tspan>
                </text>
              );
            })}
          </g>
        </svg>
      </div>
      <div className="map-footer">
        <div className="legend">
          <span><i className="legend-chip safe" />В пределах плана</span>
          <span><i className="legend-chip risk" />Сверх мощности</span>
        </div>
        <span>Условные границы</span>
      </div>
      <div className="zone-chips" aria-label="Список зон">
        {zones.map((zone) => (
          <button key={zone.zone_id} aria-pressed={active === zone.zone_id} onClick={() => onSelect(zone.zone_id)}>
            {zone.zone_name}
          </button>
        ))}
      </div>
    </>
  );
}
