import assert from "node:assert/strict";
import test from "node:test";
import { compareCourierExample } from "../src/courier.ts";

const example = { currentOrders: 24, currentCouriers: 6, otherOrders: 48, otherCouriers: 6, travelMinutes: 15, ageMinutes: 5 };

test("a road delay or more couriers can remove a destination's demand advantage", () => {
  assert.deepEqual(compareCourierExample(example), { decision: "consider", stay: 4, move: 6 });
  assert.deepEqual(compareCourierExample({ ...example, travelMinutes: 45 }), { decision: "stay", stay: 4, move: 2 });
  assert.equal(compareCourierExample({ ...example, otherCouriers: 12 }).decision, "stay");
  assert.equal(compareCourierExample({ ...example, travelMinutes: 60 }).move, 0);
});

test("stale or incomplete inputs must never produce a relocation recommendation", () => {
  for (const change of [{ ageMinutes: 30 }, { currentCouriers: 0 }, { otherCouriers: 0 }, { travelMinutes: -1 }, { otherOrders: NaN }]) {
    assert.deepEqual(compareCourierExample({ ...example, ...change }), { decision: "unknown", stay: null, move: null });
  }
});
