import { test } from "node:test";
import assert from "node:assert/strict";

import { closedMetrics, journalSummary, lossStreak, positionQty } from "../lib/store";
import { trade } from "./helpers";

const settings = { capital: 1_800_000, riskPct: 1 } as any;

test("position size = 1% risk, capped at 20% of capital", () => {
  assert.equal(positionQty(100, 95, settings), 3600);          // 18,000 / 5 = 3,600 = 3.6L (20% cap)
  assert.equal(positionQty(100, 90, settings), 1800);          // 18,000 / 10
  assert.equal(positionQty(100, 98, settings), 3600);          // risk says 9,000, cap says 3,600
  assert.equal(positionQty(100, 101, settings), 0);
});

test("closed R includes partials and uses the initial stop", () => {
  const t = trade({ status: "closed", qty: 50, exit_price: 100, stop_loss: 100,
    partials: [{ qty: 50, price: 110, date: "2026-01-05" }], exit_date: "2026-01-10" });
  const m = closedMetrics(t);
  assert.equal(m.pnl, 500);
  assert.equal(m.r, 1);   // 500 / (5 × 100)
});

test("summary and loss streak", () => {
  const loss = (d: string) => trade({ status: "closed", exit_price: 95, exit_date: d });
  const win = trade({ status: "closed", exit_price: 110, exit_date: "2026-01-01" });
  const s = journalSummary([win, loss("2026-01-02"), loss("2026-01-03")], 1_800_000);
  assert.equal(s.closedCount, 3);
  assert.ok(Math.abs((s.winRate ?? 0) - 1 / 3) < 1e-9);
  assert.equal(lossStreak([win, loss("2026-01-02"), loss("2026-01-03")]).streak, 2);
});
