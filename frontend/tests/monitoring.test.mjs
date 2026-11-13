import assert from "node:assert/strict";
import test from "node:test";
import { monitoringPeriods, monitoringSnapshot } from "../src/monitoring.ts";

const day = (index) => new Date(Date.UTC(2026, 9, index + 1)).toISOString().slice(0, 10);
const points = Array.from({ length: 28 }, (_, index) => ({ date: day(index), actual: 100, forecast: 102, baseline: 99, zone_id: "east" }));

test("demo needs a whole week for warning and two high weeks for retraining", () => {
  const periods = monitoringPeriods(points);
  assert.equal(monitoringSnapshot(periods, 0, 35).status, "pending");
  assert.equal(monitoringSnapshot(periods, 6, 35).status, "pending");
  assert.equal(monitoringSnapshot(periods, 7, 35).status, "warning");
  assert.equal(monitoringSnapshot(periods, 14, 35).status, "alert");
  assert.equal(monitoringSnapshot(periods, 14, 0).status, "normal");
  assert.equal(monitoringSnapshot(periods, 14, 0).currentMae, 2);
});

test("a missing day prevents pretending there are two consecutive weeks", () => {
  assert.equal(monitoringPeriods(points.filter((_, index) => index !== 10)), null);
});

test("the teaching scenario leaves original observations unchanged", () => {
  const original = structuredClone(points);
  monitoringSnapshot(monitoringPeriods(points), 14, 60);
  assert.deepEqual(points, original);
});
