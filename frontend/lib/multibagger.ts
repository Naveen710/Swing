/* Multibagger framework — pure frontend logic (manual checklist, adjusted score, sizing).
   Spec §7 and §8: docs/features/multibagger-framework/spec.md */

import type { ChecklistAnswer } from "./store";

export interface ChecklistItem {
  id: string;
  text: string;
  effect: number | "hard_stop";
  kind: "positive" | "red_flag" | "hard_stop";
}

export const CHECKLIST: ChecklistItem[] = [
  { id: "promoter_stable", text: "Promoter holding stable or rising over the last 4 quarters", effect: 2, kind: "positive" },
  { id: "pledge_low", text: "Promoter pledge < 5% and not rising", effect: 2, kind: "positive" },
  { id: "mgmt_track", text: "Management met past guidance; no unrelated diversification", effect: 2, kind: "positive" },
  { id: "runway", text: "Visible runway (order book, capacity, new products ≥ 2× current revenue opportunity)", effect: 2, kind: "positive" },
  { id: "discovery", text: "Institutional holding rising but still < 15% (under-owned)", effect: 2, kind: "positive" },
  { id: "tailwind_named", text: "You can name the structural tailwind (PLI, import substitution, formalisation, capex, consumption)", effect: 2, kind: "positive" },
  { id: "rpt", text: "Material related-party transactions or loans to promoter entities", effect: -5, kind: "red_flag" },
  { id: "promoter_dilution", text: "Repeated preferential issues or warrants to promoters at a discount", effect: -5, kind: "red_flag" },
  { id: "audit_contingent", text: "Qualified audit opinion, or contingent liabilities > 20% of net worth", effect: -5, kind: "red_flag" },
  { id: "pledge_high", text: "Pledge > 25% or rising over the last 2 quarters", effect: "hard_stop", kind: "hard_stop" },
  { id: "governance_event", text: "Auditor resignation, CFO exit < 12 months, or SEBI/forensic action", effect: "hard_stop", kind: "hard_stop" },
];

export const POSITIVE_CAP = 10;
export const THESIS_BROKEN = "Thesis broken (manual)";

export const TIER_BANDS: [number, string, string][] = [
  [75, "A", "High-conviction candidate"],
  [60, "B", "Candidate"],
  [45, "C", "Watchlist"],
  [0, "D", "Not now"],
];

export function tierFor(score: number): { tier: string; label: string } {
  const band = TIER_BANDS.find(([cut]) => score >= cut) ?? TIER_BANDS[TIER_BANDS.length - 1];
  return { tier: band[1], label: band[2] };
}

export interface AdjustedScore {
  score: number;
  tier: string;
  label: string;
  positives: number;
  redFlags: number;
  hardStop: boolean;
  answered: number;
}

/** adjusted = clamp(score + min(Σ positives, 10) − Σ red flags, 0, 100); any hard stop → "Thesis broken (manual)". */
export function adjustedScore(score: number, items: Record<string, ChecklistAnswer> = {}, weakCapped = false): AdjustedScore {
  let positives = 0;
  let redFlags = 0;
  let hardStop = false;
  let answered = 0;
  for (const item of CHECKLIST) {
    const a = items[item.id];
    if (a && a !== "unknown") answered += 1;
    if (a !== "yes") continue;
    if (item.effect === "hard_stop") hardStop = true;
    else if (item.effect > 0) positives += item.effect;
    else redFlags += -item.effect;
  }
  const adj = Math.min(100, Math.max(0, score + Math.min(positives, POSITIVE_CAP) - redFlags));
  const rounded = Math.round(adj * 10) / 10;
  if (hardStop) return { score: rounded, tier: "✕", label: THESIS_BROKEN, positives, redFlags, hardStop, answered };
  let { tier, label } = tierFor(rounded);
  if (weakCapped && (tier === "A" || tier === "B")) { tier = "C"; label = "Watchlist"; }
  return { score: rounded, tier, label, positives, redFlags, hardStop, answered };
}

export interface Sizing {
  bucket: number;
  fullPosition: number;
  starter: number;
  addOn: number;
  maxAtCost: number;
  trimAbove: number;
  trimTo: number;
}

/** Long-term bucket: full position = bucket / ideas; starter ⅓; ≤ 12% of bucket at cost; trim > 20% back toward 15%. */
export function sizing(capital: number, bucketPct: number, ideas: number): Sizing {
  const n = Math.min(15, Math.max(8, Math.round(ideas)));
  const bucket = Math.max(0, capital) * Math.min(100, Math.max(0, bucketPct)) / 100;
  const full = bucket / n;
  return {
    bucket: Math.round(bucket),
    fullPosition: Math.round(full),
    starter: Math.round(full / 3),
    addOn: Math.round(full / 3),
    maxAtCost: Math.round(bucket * 0.12),
    trimAbove: Math.round(bucket * 0.2),
    trimTo: Math.round(bucket * 0.15),
  };
}

export function starterAllowed(tier: string, timing: string): boolean {
  return (tier === "A" || tier === "B") && (timing === "entry_zone" || timing === "watch");
}
