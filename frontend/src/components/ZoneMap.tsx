import type { ZoneForecast } from "../api/dashboard";
import { number } from "../format";
import { MAP_VIEWBOX, MOSCOW_RIVER, MOSCOW_ZONES } from "../geo/moscowZones";

interface Props {
  zones: ZoneForecast[];
  active: string;
  onSelect: (id: string) => void;
}

export function ZoneMap({ zones, active, onSelect }: Props) {
  const shapes = Object.entries(MOSCOW_ZONES);
  return (
    <>
      <div className="map-canvas">
        <svg viewBox={MAP_VIEWBOX} role="group" aria-label="Карта зон доставки Москвы по административным округам">
          {shapes.map(([id, shape]) => {
            const zone = zones.find((entry) => entry.zone_id === id);
            if (!zone) return null;
            const pressed = active === id;
            return (
              <g
                key={id}
                role="button"
                tabIndex={0}
                aria-label={`${zone.zone_name}: ${number(zone.forecast_orders)} заказов при мощности ${number(zone.capacity_orders)}`}
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
                <title>{zone.zone_name} ({shape.okrugs.join(" + ")}) · {number(zone.forecast_orders)} из {number(zone.capacity_orders)}</title>
                <path d={shape.path} />
              </g>
            );
          })}
          <path className="map-river" d={MOSCOW_RIVER} aria-hidden="true" />
          {/* The selected zone is drawn again on top so its outline is not hidden by neighbours. */}
          {MOSCOW_ZONES[active] && <path className="map-outline" d={MOSCOW_ZONES[active].path} aria-hidden="true" />}
          <g className="map-labels" aria-hidden="true">
            {shapes.map(([id, { label: [x, y] }]) => {
              const zone = zones.find((entry) => entry.zone_id === id);
              if (!zone) return null;
              return (
                <text key={id} x={x} y={y - 6} className={active === id ? "map-label-active" : ""}>
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
        <span>Зоны — объединения округов · © участники OpenStreetMap</span>
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
