import { test } from "node:test";
import assert from "node:assert/strict";

import { computeExitAdvice } from "../lib/exits";
import { candles, trade } from "./helpers";

const flat = (n: number, v = 100) => Array(n).fill(v);

test("stop hit → exit", () => {
  assert.equal(computeExitAdvice(trade(), candles([100, 98, 94.5]))?.rule, "stop");
});

test("target reached → exit", () => {
  assert.equal(computeExitAdvice(trade(), candles([100, 108, 114], [100.5, 108.5, 115.2]))?.rule, "target");
});

test("+2R with no partial → book half", () => {
  const a = computeExitAdvice(trade(), candles([100, 105, 110.5]));
  assert.equal(a?.action, "book_partial");
  assert.equal(a?.partialQty, 50);
  assert.equal(a?.partialPrice, 110);
});

test("time stop after 10 flat sessions", () => {
  assert.equal(computeExitAdvice(trade(), candles(flat(12)))?.rule, "time_stop");
});

test("R is measured against the initial stop, not a trailed one", () => {
  const a = computeExitAdvice(trade({ stop_loss: 99, initial_stop: 95 }), candles([100, 102.5]));
  assert.equal(a?.rNow, 0.5);
});

test("curated: +1.5R books half, then moves stop to breakeven", () => {
  const t = trade({ exit_profile: "curated" });
  assert.equal(computeExitAdvice(t, candles([100, 104, 107.6]))?.action, "book_partial");
  const after = trade({ exit_profile: "curated", qty: 50, partials: [{ qty: 50, price: 107.5, date: "2026-01-03" }] });
  const a = computeExitAdvice(after, candles([100, 104, 107.6]));
  assert.equal(a?.action, "trail");
  assert.equal(a?.suggestedStop, 100);
});

test("curated: time stop at 10 sessions", () => {
  assert.equal(computeExitAdvice(trade({ exit_profile: "curated" }), candles(flat(10)))?.rule, "time_stop");
});

test("invalid risk returns null", () => {
  assert.equal(computeExitAdvice(trade({ stop_loss: 101, initial_stop: 101 }), candles([100])), null);
});
