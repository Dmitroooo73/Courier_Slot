import type { ZoneForecast } from "../api/dashboard";

interface Props {
  zones: ZoneForecast[];
  active: string;
  onSelect: (zoneId: string) => void;
}

export function ZonePicker({ zones, active, onSelect }: Props) {
  return (
    <label className="date-control zone-picker">
      <span>Зона доставки</span>
      <select value={active} onChange={(event) => onSelect(event.target.value)}>
        {zones.map((zone) => <option key={zone.zone_id} value={zone.zone_id}>{zone.zone_name}</option>)}
      </select>
    </label>
  );
}
