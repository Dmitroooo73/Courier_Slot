/** A teaching example, independent of the daily forecasting model. */
export interface CourierExample {
  currentOrders: number;
  currentCouriers: number;
  otherOrders: number;
  otherCouriers: number;
  travelMinutes: number;
  ageMinutes: number;
}

export function compareCourierExample(input: CourierExample) {
  const values = Object.values(input);
  if (values.some(value => !Number.isFinite(value) || value < 0)
      || input.currentCouriers === 0 || input.otherCouriers === 0 || input.ageMinutes > 15) {
    return { decision: "unknown" as const, stay: null, move: null };
  }
  const stay = input.currentOrders / input.currentCouriers;
  const move = input.otherOrders / input.otherCouriers * Math.max(0, 60 - input.travelMinutes) / 60;
  // A small difference is insufficient to justify moving in this toy rule.
  return { decision: move > stay * 1.2 ? "consider" as const : "stay" as const, stay, move };
}
