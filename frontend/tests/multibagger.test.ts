import { test } from "node:test";
import assert from "node:assert/strict";

import { CHECKLIST, THESIS_BROKEN, adjustedScore, sizing, starterAllowed } from "../lib/multibagger";

test("checklist has the 11 spec items", () => {
  assert.equal(CHECKLIST.length, 11);
});

test("positives add up but are capped at +10", () => {
  const all = Object.fromEntries(CHECKLIST.filter((c) => c.kind === "positive").map((c) => [c.id, "yes" as const]));
  const a = adjustedScore(60, all);
  assert.equal(a.positives, 12);
  assert.equal(a.score, 70);
  assert.equal(a.tier, "B");
});

test("red flags subtract 5 each and unknown counts 0", () => {
  const a = adjustedScore(62, { rpt: "yes", promoter_dilution: "yes", audit_contingent: "unknown", runway: "no" });
  assert.equal(a.score, 52);
  assert.equal(a.tier, "C");
  assert.equal(a.answered, 3);
});

test("hard stop → thesis broken regardless of score", () => {
  const a = adjustedScore(90, { pledge_high: "yes" });
  assert.equal(a.label, THESIS_BROKEN);
  assert.equal(a.hardStop, true);
});

test("adjusted score is clamped to 0–100 and respects the weak-layer cap", () => {
  assert.equal(adjustedScore(98, { promoter_stable: "yes", pledge_low: "yes" }).score, 100);
  assert.equal(adjustedScore(3, { rpt: "yes" }).score, 0);
  assert.equal(adjustedScore(80, {}, true).tier, "C");
});

test("sizing: ₹18L, 25%, 10 ideas", () => {
  const s = sizing(1_800_000, 25, 10);
  assert.equal(s.bucket, 450_000);
  assert.equal(s.fullPosition, 45_000);
  assert.equal(s.starter, 15_000);
  assert.equal(s.maxAtCost, 54_000);
});

test("ideas are clamped to 8–15", () => {
  assert.equal(sizing(1_800_000, 25, 3).fullPosition, Math.round(450_000 / 8));
  assert.equal(sizing(1_800_000, 25, 40).fullPosition, 30_000);
});

test("starter only for tier A/B in entry zone or watch", () => {
  assert.equal(starterAllowed("B", "entry_zone"), true);
  assert.equal(starterAllowed("C", "entry_zone"), false);
  assert.equal(starterAllowed("A", "extended"), false);
});
