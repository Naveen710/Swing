import { test } from "node:test";
import assert from "node:assert/strict";

import { DIM_SHORT, STATUS_LABEL, evidenceHref, fmtCr, fmtMetric, fmtPrice } from "../lib/research";

test("fmtMetric formats each unit and never invents a value", () => {
  assert.equal(fmtMetric(null), "—");
  assert.equal(fmtMetric({ value: null, unit: "pct" }), "—");
  assert.equal(fmtMetric({ value: 18.26, unit: "pct" }), "18.3%");
  assert.equal(fmtMetric({ value: -2.04, unit: "pp" }), "-2.0 pp");
  assert.equal(fmtMetric({ value: 3.5, unit: "pp" }), "+3.5 pp");
  assert.equal(fmtMetric({ value: 0.456, unit: "x" }), "0.46×");
  assert.equal(fmtMetric({ value: 61.4, unit: "days" }), "61 days");
});

test("rupee amounts are shown in crore", () => {
  assert.equal(fmtMetric({ value: 2.5e9, unit: "INR" }), "₹250 cr");
  assert.equal(fmtCr(undefined), "—");
  assert.equal(fmtPrice(null), "—");
  assert.ok(fmtPrice(1234.5).startsWith("₹"));
});

test("evidence links encode the reference and carry the snapshot", () => {
  assert.equal(evidenceHref("fact:12"), "/research/evidence/fact%3A12");
  assert.equal(evidenceHref("chunk:3/4", 7), "/research/evidence/chunk%3A3%2F4?snapshot=7");
});

test("all seven dimensions and three statuses have labels", () => {
  assert.equal(Object.keys(DIM_SHORT).length, 7);
  assert.deepEqual(Object.keys(STATUS_LABEL).sort(), ["BLOCKED", "ELIGIBLE_FOR_RESEARCH", "REVIEW_REQUIRED"]);
});
