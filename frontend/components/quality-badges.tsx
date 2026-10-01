import { TradeSetup } from "../types";

/* Compact positive/negative badges for the overlay checks on a setup. */
export function QualityBadges({ setup }: { setup: TradeSetup }) {
  const badges: { label: string; tone: "ok" | "bad" | "info" }[] = [];
  const pl = setup.price_levels;
  if (pl?.price_discovery) badges.push({ label: "New 52W high", tone: "ok" });
  else if (pl?.near_52w_high) badges.push({ label: `${pl.distance_from_52w_high_pct.toFixed(1)}% off 52W high`, tone: "ok" });

  const wk = setup.weekly_trend;
  if (wk) badges.push({ label: wk.aligned ? `Weekly ✓ ${wk.checks_passed}/3` : `Weekly ✗ ${wk.checks_passed}/3`, tone: wk.aligned ? "ok" : "bad" });

  const peer = setup.peer_rank;
  if (peer?.sector_leader) badges.push({ label: `Sector leader #${peer.rank}/${peer.peer_count}`, tone: "ok" });
  else if (peer?.sector_laggard) badges.push({ label: `Laggard #${peer.rank}/${peer.peer_count}`, tone: "bad" });

  const f = setup.fundamentals;
  if (f && f.checks_available > 0) {
    badges.push({
      label: `Fundamentals ${f.quality_score}/${f.checks_available}`,
      tone: f.passes === false ? "bad" : f.passes ? "ok" : "info",
    });
  }

  const rot = setup.sector_rotation;
  if (rot?.leading) badges.push({ label: `Top sector #${rot.rank}`, tone: "ok" });
  else if (rot?.lagging) badges.push({ label: `Lagging sector #${rot.rank}/${rot.sector_count}`, tone: "bad" });

  if (setup.rs_line?.leads_price) badges.push({ label: "RS line leads price", tone: "ok" });
  if (setup.pattern === "gap_momentum") badges.push({ label: "Results/news gap", tone: "info" });

  const sm = setup.smart_money;
  if (sm?.delivery_spike) badges.push({ label: `Delivery ${sm.delivery_ratio?.toFixed(1)}× avg`, tone: "ok" });
  if (sm && sm.bulk_deal_buys > 0 && sm.bulk_deal_net_qty > 0) badges.push({ label: "Bulk buying", tone: "ok" });
  else if (sm && sm.bulk_deal_sells > 0 && sm.bulk_deal_net_qty < 0) badges.push({ label: "Bulk selling", tone: "bad" });

  const ev = setup.event_risk;
  if (ev.blackout) badges.push({ label: ev.days_to_earnings !== null && ev.days_to_earnings >= 0 ? `Results in ${ev.days_to_earnings}d` : "Post-results", tone: "bad" });
  else if (ev.days_to_ex_dividend !== null && ev.days_to_ex_dividend !== undefined) badges.push({ label: `Ex-div in ${ev.days_to_ex_dividend}d`, tone: "info" });

  if (!badges.length) return null;
  return (
    <div className="q-badges">
      {badges.map((b) => <span key={b.label} className={`q-badge q-badge--${b.tone}`}>{b.label}</span>)}
    </div>
  );
}

export type GateReason = "blackout" | "weekly" | "fundamentals" | "sector_full" | "held";

export const GATE_LABEL: Record<GateReason, string> = {
  blackout: "earnings blackout",
  weekly: "weekly trend not aligned",
  fundamentals: "weak fundamentals",
  sector_full: "sector already at 2 open positions",
  held: "already holding",
};

/* Hard gates used by the trading system. Returns the reasons a setup fails. */
export function gateFailures(
  setup: TradeSetup,
  heldSymbols: Set<string>,
  sectorCounts: Map<string, number>,
  maxPerSector: number
): GateReason[] {
  const reasons: GateReason[] = [];
  if (setup.event_risk.blackout) reasons.push("blackout");
  if (setup.weekly_trend && !setup.weekly_trend.aligned) reasons.push("weekly");
  if (setup.fundamentals?.passes === false) reasons.push("fundamentals");
  if (heldSymbols.has(setup.symbol)) reasons.push("held");
  else if ((sectorCounts.get(setup.sector) ?? 0) >= maxPerSector) reasons.push("sector_full");
  return reasons;
}
