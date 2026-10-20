# Multibagger framework (`/multibagger`)

Slug: `multibagger-framework` · Author: planner · Date: 2026-10-11
Status: ready for developer. Educational tool, not investment advice (CLAUDE.md rule 7).

---

## Thesis

Swing setups find 2–4 % moves. Multibaggers come from a different place: a small, clean business at an
earnings inflection, compounding capital at high returns for years, while the market still prices it
for modest growth. Price only confirms this late. This page runs a scored funnel. Kill-switches remove
names that are too small, illiquid, loss-making, over-levered, cash-burning or diluting. The remaining
names get a 0–100 score from five layers: growth, quality, valuation, tailwind and price. A separate
timing flag says whether the chart is in an entry zone or still a "watch". The fundamental half of the
score cannot be backtested honestly (we have no point-in-time fundamentals). So the weights are fixed
priors, the price half is proxy-backtested from 2015, and the whole list goes into a monthly forward
ledger that is graded at 6/12/24/36 months. The real verdict comes from that ledger, not from the backtest.

This is a 3–5 year holding framework. It is kept apart from swing trading: it has its own capital bucket,
sizing and sell rules.

---

## Rules

### 0. Notation

- `lin(x, lo, hi)` maps a value to a 0–100 score: 0 at `lo`, 100 at `hi`, clipped, linear in between
  (`lo > hi` means lower is better). Every metric uses this, which avoids cliff thresholds.
- "3-year CAGR" uses `valuation.cagr(first, last, years)` over the oldest to newest annual points
  (Yahoo usually gives 4 FYs, so this is a 3-year span). If only 3 FYs exist, a 2-year span is used and
  data gap `short_history` is added. `cagr` returns `None` when the first or last value ≤ 0.
- FY = annual statement column year; TTM = Yahoo `info` trailing figures.
- All money is in ₹. Market cap in ₹ Cr = rupees / 1e7.

### 1. Pre-filter (price only, whole universe, before any fundamentals call)

Run on the last row of the OHLCV panel (520-session lookback). A stock must pass all of these:

| Code | Rule | Notes |
|---|---|---|
| `large_cap` | `listing.market_cap_bucket != LARGE` (Nifty 100 names excluded) | Cheap proxy for the size band. The real band is checked in K1. |
| `penny` | last close ≥ ₹20 | |
| `illiquid` | 60-session median of Close×Volume ≥ ₹1 Cr | A full position (~₹45 k) is then < 1 % of a day's value. |
| `short_history` | ≥ 500 non-NaN closes | Needed for 3-year CAGRs and the 52-week/200-DMA checks. Listings < 2 years old are excluded and say so. |
| `circuit` | ≤ 2 circuit-pinned days in the last 60 sessions | Same pinned definition as `curated_algo.compute_layers` (|ret| ≥ 4.9 % and close within 0.05 % of the high or low). |
| `asm_gsm` | not in `curated_algo._fetch_surveillance()` set, and not in the user's `exclude` list | If the lists are not configured, this rule is skipped and a note is shown (same as Algo). |

Pre-rank of the survivors: `prerank = 0.5 × RS_pctl + 0.5 × (1 − pct_rank(dist_52w_high))`. RS is defined in layer 4.
The top `MULTIBAGGER_DEEP_CAP` (default **120**) go to the deep fundamental pass. Ties are broken by symbol A→Z.
The rest are counted as `not_deep_passed`, and the funnel shows that count. Known bias: a base-building stock far
below its high may never reach the deep pass. The single-stock endpoint (§UX) is the escape hatch for that.

### 2. Kill-switches (deep pass; any failure → `excluded`, with code and detail shown)

| Code | Rule | Missing data |
|---|---|---|
| K0 `insufficient_data` | needs ≥ 3 annual points of both revenue and net income | **Not excluded.** The stock goes to the separate "Insufficient data" table and is not scored or tiered. |
| K1 `size_band` | market cap ₹500 – ₹20,000 Cr. Source: `info.marketCap`, else price × `resolve_shares()` | No market cap → **excluded** (`size_unknown`). Size is core to the thesis. |
| K2 `financial` | `valuation.is_financial()` on the Yahoo sector/industry, or listing sector "Financial Services" | n/a. Banks, NBFCs and insurers are out of scope for v1 (ROCE, CFO and D/E don't apply to lenders). |
| K3 `loss_making` | TTM net income (`info.netIncomeToCommon`) ≤ 0, **or** latest FY net income ≤ 0 | Uses whichever exists. If both are missing it is already K0. |
| K4 `high_debt` | D/E > 1.0. D/E = `info.debtToEquity / 100`, else latest FY Total Debt / Stockholders Equity | Missing → **not excluded**. Q4 is penalised and gap `debt_unknown` is added. |
| K5 `negative_cfo` | CFO < 0 in ≥ 2 of the last 3 FYs, **or** the sum of the last 3 FYs of CFO ≤ 0 | Fewer than 2 CFO years → **not excluded**. Q3 is penalised and gap `cfo_unknown` is added. |
| K6 `dilution` | share count up > 25 % across the statement span. Uses `Ordinary Shares Number` (balance sheet), else `Diluted Average Shares` (income statement) | Fewer than 2 points → **not excluded**. Gap `dilution_unknown` is added. |

### 3. Layer scores (each 0–100; system score = Σ weight × layer / 100)

Weights are fixed priors. They are **not** tuned on any backtest (see Risks).

| Layer | Weight |
|---|---|
| Growth & inflection | **30** |
| Quality | **25** |
| Valuation sanity | **15** |
| Tailwind & leadership | **15** |
| Price confirmation | **15** |

Inside a layer: `layer = Σ wᵢ·sᵢ / Σ wᵢ` over **all** metrics. Missing metrics are filled per the
"If missing" column: `neutral` = 50, `penalised` = 25. A missing metric is never silently 0.
`coverage_pct` for a layer = the weight share of metrics computed from real data.

**L1 Growth & inflection (30)**

| Key | Metric | Score | w | If missing |
|---|---|---|---|---|
| `rev_cagr` | 3y revenue CAGR | lin(5 %, 25 %) | 25 | neutral |
| `pat_cagr` | 3y net income CAGR | lin(5 %, 30 %) | 25 | neutral. If the base year PAT ≤ 0 (turnaround), use neutral and add gap `turnaround_base` |
| `accel` | latest-quarter revenue YoY − 3y revenue CAGR, in pp. Quarter YoY = q[-1]/q[-5]−1 from `quarterly_income_stmt` "Total Revenue" (needs 5 quarters), else `info.revenueGrowth` | lin(−10, +10) | 20 | neutral + gap `quarterly_unknown` |
| `margin_exp` | EBIT margin latest FY − EBIT margin oldest FY, in pp. EBIT = "EBIT", else "Operating Income" | lin(−3, +5) | 15 | neutral |
| `op_leverage` | pat_cagr − rev_cagr, in pp | lin(−5, +10) | 15 | neutral |

**L2 Quality (25)**

| Key | Metric | Score | w | If missing |
|---|---|---|---|---|
| `roce` | latest FY EBIT / capital employed. CE = Total Assets − Current Liabilities, else Stockholders Equity + Total Debt (gap `ce_proxy`) | lin(10 %, 25 %) | 30 | neutral |
| `roce_trend` | ROCE latest − ROCE two FYs earlier, in pp | lin(−5, +5) | 15 | neutral |
| `cfo_pat` | Σ CFO / Σ net income over the last 3 FYs (only where both exist) | lin(0.5, 1.0) | 25 | **penalised** (unverified cash conversion is a risk) |
| `de` | D/E as in K4 | lin(1.0, 0.0) | 15 | **penalised** |
| `int_cover` | EBIT / "Interest Expense", latest FY. Debt ≈ 0 (< ₹1 Cr) or no interest line with D/E < 0.1 → 100 | lin(2, 10) | 10 | **penalised** if D/E ≥ 0.1 and no interest line; else 100 |
| `asset_turn_trend` | Revenue/Total Assets latest − two FYs earlier | lin(−0.2, +0.2) | 5 | neutral |

**L3 Valuation sanity (15)** reuses `valuation.assemble` + `default_assumptions` + `implied_growth`. Terminal growth is 5 %, the discount rate is the module default and the method is the module default.

| Key | Metric | Score | w | If missing |
|---|---|---|---|---|
| `rdcf_gap` | delivered growth − implied growth, in pp. Delivered = `default_assumptions()["historical_growth_pct"]` (the lower of revenue and profit CAGR) | lin(−10, +10). `implied_growth` returns None (needs > 150 %/yr) → score 0 + red flag | 50 | neutral (e.g. no shares count) |
| `peg` | trailing P/E ÷ pat_cagr(%). P/E = `info.trailingPE`, else mcap / TTM NI | lin(2.5, 0.8). pat_cagr ≤ 0 → score 0 | 30 | neutral |
| `ey_spread` | 100/PE − 7.0 (`valuation.RISK_FREE`), in pp | lin(−5, +2) | 20 | neutral |

**L4 Tailwind & leadership (15)** is computed over all pre-filter survivors (cross-sectional).

| Key | Metric | Score | w | If missing |
|---|---|---|---|---|
| `sector_rank` | `trend_overlays.build_sector_rotation` on 1m/3m returns vs Nifty 500 (`trailing_returns`) | 100 × (1 − (rank−1)/(count−1)) | 40 | neutral if sector is "Unknown" or has < 3 members (common in `mid_small_2000_plus`: 1,366 of 2,038 are Unknown) |
| `rs_pctl` | RS raw = 0.5 × (126-session return − index) + 0.5 × (12-1 month return − index), percentile among survivors | pctl × 100 | 40 | n/a (price always present) |
| `peer_rank` | `trend_overlays.build_peer_ranks` percentile on RS raw | pctl × 100 | 20 | neutral if Unknown sector |

**L5 Price confirmation (15)** is computed on the deep-pass stock's daily frame.

| Key | Metric | Score | w |
|---|---|---|---|
| `stage2` | `vcp_scanner.trend_template()` checks passed / 6 | ×100 | 35 |
| `near_high` | distance below the 52-week high, % | lin(40, 10) | 25 |
| `rs_line` | `trend_overlays.build_rs_line` vs Nifty 500 | 100 if `rs_line_new_high`, else lin(20, 0) on `distance_from_rs_high_pct` | 20 |
| `vcp` | `vcp_scanner.scan_stock()` | valid VCP → 100. Template pass but no valid VCP → 40. Template fail → 0 | 20 |

### 4. Score, tier, tie-breaks

- `score` = Σ (layer weight × layer score)/100, rounded to 1 dp.
- Tiering is only done when **Growth coverage ≥ 60 % and Quality coverage ≥ 60 %**. Otherwise the stock goes to the "Insufficient data" table.
- Tiers: **A** ≥ 75 "High-conviction candidate" · **B** 60–74.9 "Candidate" · **C** 45–59.9 "Watchlist" · **D** < 45 "Not now".
- Weak-link cap: if any layer score < 30, the tier is capped at C (`capped_by_weak_layer=true`, and the layer is named).
- Sort order: score desc → quality layer desc → `near_high` distance asc → symbol asc.

### 5. Timing flag (separate from the score; evaluated in this order)

| Value | Label | Rule |
|---|---|---|
| `downtrend` | "Downtrend — wait" | close < SMA200, or SMA200 today ≤ SMA200 21 sessions ago |
| `extended` | "Extended — wait for a base" | close > 1.25 × SMA50, or VCP status `extended` |
| `entry_zone` | "Entry zone" | trend template passes **and** (VCP status ∈ {near pivot, breakout, breakout today} **or** (close ≤ 1.10 × SMA50 **and** ≤ 15 % below the 52-week high)) |
| `watch` | "Watch" | anything else |

Decision: the brief's "Buy zone" is labelled **"Entry zone"** to keep buy language off the page (CLAUDE.md rule 7).

### 6. "Why it qualifies" / "Red flags" / "Data gaps" (per stock, max 6 each)

- Qualifies: every metric with score ≥ 75. The text is a template from the metric, e.g. "Revenue CAGR 3y 28.4 % (≥ 25 % scores full)", "ROCE 27 % and rising +6 pp", "Market prices 9 % growth vs 22 % delivered".
- Red flags: every metric with score ≤ 25 from real data. Also kill-switch near-misses: D/E 0.7–1.0, dilution 15–25 %, mcap > ₹15,000 Cr ("late — already mid-cap"), `implied_growth` None, CFO negative in 1 of 3 FYs.
- Data gaps: each code above, in plain English, e.g. "No quarterly results — acceleration scored neutral (50)".

### 7. Manual checklist (frontend, adjusts a separate **Adjusted score**; the system score is never changed)

Stored in localStorage `swing.multibagger.checklist.v1` as `{ [symbol]: { items: { [id]: "yes"|"no"|"unknown" }, updated: ISO } }`.
"Unknown" (the default) = 0 points.

| id | Item | "yes" effect |
|---|---|---|
| `promoter_stable` | Promoter holding stable or rising over the last 4 quarters | +2 |
| `pledge_low` | Promoter pledge < 5 % and not rising | +2 |
| `mgmt_track` | Management met past guidance; no unrelated diversification | +2 |
| `runway` | Visible runway (order book, capacity, new products ≥ 2× current revenue opportunity) | +2 |
| `discovery` | Institutional holding rising but still < 15 % (under-owned) | +2 |
| `tailwind_named` | You can name the structural tailwind (PLI, import substitution, formalisation, capex, consumption) | +2 |
| `rpt` | Material related-party transactions or loans to promoter entities | −5 |
| `promoter_dilution` | Repeated preferential issues or warrants to promoters at a discount | −5 |
| `audit_contingent` | Qualified audit opinion, or contingent liabilities > 20 % of net worth | −5 |
| `pledge_high` | Pledge > 25 % or rising over the last 2 quarters | **Hard stop** |
| `governance_event` | Auditor resignation, CFO exit < 12 m, or SEBI/forensic action | **Hard stop** |

`adjusted = clamp(score + min(Σ positives, 10) − Σ red flags, 0, 100)`. The adjusted tier uses the same bands.
Any hard stop sets the adjusted tier to "Thesis broken (manual)".
Helper text names where to look: NSE → Corporate filings → Shareholding pattern / Annual report.

### 8. Position sizing for a long-term bucket (frontend helper, guidance only)

Inputs: capital (from `useTradingSettings`, default ₹18 L), bucket % (default **25 %**, editable, stored in
`swing.multibagger.settings.v1`) and target ideas (default 10, range 8–15).

- Bucket = capital × bucket %. Full position = bucket / target ideas (₹18 L × 25 % / 10 = ₹45,000).
- **Starter** = ⅓ of a full position. Only when tier ≥ B and timing ∈ {entry_zone, watch}.
- **Add ⅓** after the next quarterly result confirms (revenue and PAT YoY ≥ 15 %) and the price is above SMA200.
- **Add the final ⅓** on a new entry-zone signal (base breakout) while the tier is still ≥ B.
- Caps: ≤ 12 % of the bucket at cost per idea. Trim back toward 15 % when market value > 20 % of the bucket. ≤ 3 ideas per sector. ≤ 15 ideas.
- Starter stop: −30 % from starter cost before any add → the framework treats the thesis as unproven and exits the starter.

### 9. Thesis-break rules (computed per stock as `thesis_check`; shown as text rules on the page)

| Code | Rule | Status |
|---|---|---|
| TB1 | Quarterly revenue YoY < 0 in each of the last 2 quarters (needs 6 quarters) | break |
| TB2 | Latest quarter PAT YoY < −20 % (break if the prior quarter was also < −20 %) | warning / break |
| TB3 | ROCE < 12 % while ROCE two FYs ago was ≥ 18 % | break |
| TB4 | Latest FY CFO < 0 | warning |
| TB5 | D/E > 1.0 (kill-switch K4) | break |
| TB6 | Close below SMA200 for the last 4 weekly closes **and** RS line at its 52-week low | warning: "review; framework cuts to starter size" |
| TB7 | implied growth − delivered growth > +15 pp | warning: "priced for perfection; framework trims to half" |
| TB8 | Manual hard stop ticked | break (frontend) |
| — | Tier D in 2 consecutive monthly snapshots | warning (from the ledger) |

Any rule whose input is missing → status `na` with the reason (never a silent "ok").

---

## Data

| Input | Source in codebase | Gaps → handling |
|---|---|---|
| OHLCV panel (520 sessions) | `curated_algo.load_panel(scanner, universe, rows=520)` (wide float32 + Nifty 500 index via `_index_frame`) | Prices are split-adjusted but not dividend-adjusted (`auto_adjust=False`), so returns are price-only (stated). |
| Universe, sector, size bucket | `universe.load_universe`, `StockListing.market_cap_bucket`, `.sector` | `mid_small_2000_plus`: 67 % of sectors are "Unknown" → L4 sector metrics neutral. Yahoo `sector` is shown for display only. |
| ASM/GSM | `curated_algo._fetch_surveillance()` (env `ASM_GSM_URLS`) | Not configured → skipped. Note + user exclusion box. |
| Market cap, TTM NI, PE, D/E, revenueGrowth, sector/industry, shares | `YahooFundamentalsProvider.get_info()` keys: `marketCap`, `netIncomeToCommon`, `trailingPE`, `debtToEquity`, `revenueGrowth`, `sector`, `industry`, `sharesOutstanding`, `currentPrice` | Often missing for small caps. Per-metric policy as in Rules. |
| Annual income statement | `yf.Ticker.income_stmt` rows: Total Revenue / Operating Revenue, EBIT / Operating Income, Net Income, Interest Expense, Diluted Average Shares | ~4 FYs, patchy. Row lookup via `valuation._row`. |
| Annual balance sheet | `yf.Ticker.balance_sheet` rows: Total Assets, Current Liabilities, Total Debt, Stockholders Equity / Common Stock Equity, Ordinary Shares Number | Current Liabilities is often missing → CE proxy (gap `ce_proxy`). |
| Annual cash flow | `yf.Ticker.cashflow` rows: Operating Cash Flow, Capital Expenditure, Free Cash Flow | Feeds `valuation.assemble` unchanged. |
| Quarterly income | `yf.Ticker.quarterly_income_stmt` Total Revenue, Net Income (pattern of `curated_algo._quarterly_growth`) | Often 4–5 quarters → YoY only when ≥ 5 quarters. TB1/TB2 need 6 → `na`. |
| Reverse DCF | `valuation.assemble`, `default_assumptions`, `implied_growth`, `is_financial`, `resolve_shares`, `RISK_FREE` | Base cash flow ≤ 0 or no shares → neutral. |
| Trend/RS/sector | `trend_overlays.build_sector_rotation`, `build_peer_ranks`, `build_rs_line`, `trailing_returns` | — |
| Stage 2 / VCP | `vcp_scanner.trend_template`, `scan_stock` (needs ≥ 260 rows, tz-naive index as in `VcpScannerService._scan`) | — |
| Promoter holding/pledge, MF/FII trend, management, order book, RPTs, auditor events | **Not available for free.** | Manual checklist only (§7). |
| Historical fundamentals | **Not available.** | Fundamental layers are not backtested (§Validation). |

**Deep-pass fetch and caching.** The new `multibagger_data.fetch_deep_financials(symbol)` makes 5 Yahoo calls
(info, income_stmt, balance_sheet, cashflow, quarterly_income_stmt). It returns a JSON-safe dict:
`{source, info_subset, annual: [{year, revenue, ebit, net_income, interest_expense, total_assets, current_liabilities, total_debt, equity, cfo, capex, fcf, shares}], quarterly: [{period, revenue, net_income}], valuation_data}`,
where `valuation_data` is the output of `valuation.assemble(...)`. It is cached with `db.save_artifact("mb_fin:{SYMBOL}", {...,"fetched_at"})`
and reused for `MULTIBAGGER_FUND_TTL_DAYS` (default 7). `refresh_fundamentals=true` bypasses the cache.
There are `MULTIBAGGER_FUND_WORKERS` workers (default 4), and each symbol is wrapped in try/except, so a failed symbol becomes K0 with reason "fetch failed".
Demo mode (`market_data_provider == "demo"` or overlays off): `demo_deep_financials(symbol)` is deterministic,
seeded by crc32 like `valuation.demo_financials`. Its mcap is inside the band for most symbols, and demo results are labelled "demo".

Render budget: 120 symbols × ~7 s / 4 workers ≈ 3–4 min on the first run, and seconds when cached.
A 2,038-symbol panel is ~25 MB float32 (the VCP scan already loads this universe).

---

## Validation plan

Fixed before any result is seen. The tester records numbers in `test-report.md`; no threshold is changed afterwards.

### (a) Price-proxy backtest of layers 4+5 (`multibagger_backtest.py`)

- **What**: the price half of the score (L4 + L5 without `vcp`, whose weight is redistributed pro rata within L5), vectorised on a panel.
  Same pre-filter point-in-time (price ≥ ₹20, 60d median value ≥ ₹1 Cr, ≥ 252 sessions of history at that date).
- **When**: signal on the last session of each month from 2015-01 to (today − 12 months). Entry = close of the **next**
  session (T+1, rule 1). Exits at T+1 + 252 / 504 / 756 sessions (≈ 1/2/3 y), or the last available close (marked partial and
  excluded from that horizon's stats).
- **Portfolio**: top 20 by price score, ≤ 3 per sector (`quant_screen.pick_top`), equal weight, buy and hold per cohort.
  Costs: `settings.trade_round_trip_cost_pct` (0.25 %) per cohort, plus a 1.0 % sensitivity line for small-cap impact.
  This is a cohort forward-return study with no stops or targets, so `trade_sim.simulate_trade` does not apply. Only its cost setting is reused (note for the reviewer).
- **Data hygiene**: a member with any one-day |return| > 40 % (`curated_algo.DATA_BREAK`) inside its window is dropped from that
  cohort and counted (`data_breaks_dropped`).
- **Universes**: primary `nifty_smallcap_250` (all 250). Secondary `nifty500` minus LARGE. `mid_small_2000_plus` is sampled
  evenly to `MULTIBAGGER_BACKTEST_MAX_SYMBOLS` (300).
- **Benchmarks**: (1) the equal-weight average of all eligible stocks that month (survivorship-matched; **primary**), and (2) Nifty 500 (`^CRSLDX`).
- **Outputs**: per horizon, the cohort mean/median return, excess vs EW universe and vs Nifty 500, % of cohorts beating EW, the "≥ 2× in 3 y"
  hit rate vs the universe base rate, monthly rank IC (price score vs 12-month forward return), quintile mean returns (Q1–Q5),
  2018-H1 and 2020-H1 cohort excess, and the count of cohorts.
- **PASS (primary universe, all required)**:
  1. Median 24-month excess of the cohort vs the EW universe, after 0.25 % costs, ≥ **+8 pp**.
  2. The cohort beats the EW universe at 24 months in ≥ **60 %** of monthly cohorts.
  3. Mean monthly rank IC (12-month forward) ≥ **0.03**, and IC > 0 in ≥ **55 %** of months.
  4. The 2× in 3 y hit rate is ≥ **1.5×** the universe base rate.
  5. The 2018-H1 cohorts' mean 24-month excess is ≥ **−15 pp** (does not blow up in a small-cap crash).
  The secondary universe must show criteria 1 and 2 with excess > 0 and > 50 %.
- **INCONCLUSIVE** if fewer than 60 monthly cohorts have a 24-month outcome.
- **If FAIL**: the price layers stay on the page, but in a follow-up their combined weight drops from 30 to 10 (Growth +10, Quality +10), and the
  page states "price layers did not validate". The timing flag stays as information only.
- **Significance caveat**: monthly cohorts overlap. The t-stat of excess is computed on **non-overlapping January cohorts only**
  (≈ 9–10 points), which is honestly small, so it is reported but is not a pass criterion.

Biases and handling:
- *Survivorship*: the universe is today's constituents, and today's Smallcap 250 partly exists because these names went up.
  Absolute returns and the 2× base rate are inflated. The primary comparison is against the same-survivorship EW universe, and every result prints the warning.
- *Look-ahead*: T+1 entry. Sector labels are today's (minor). No fundamentals are used.
- *Small sample*: ~10 independent years, handled by the overlap caveat above.
- *Costs/liquidity*: point-in-time liquidity filter, plus the 1 % cost sensitivity.
- *Not tested*: L1–L3 (≈ 70 % of the score). Stated on the page.

### (b) Forward ledger (the real test)

- New table `multibagger_snapshots`. On the first completed run in each calendar month (per universe), every **scored** stock
  is written (A–D tiers, plus the deep-pass but excluded/insufficient names with `tier = "X"` as a control group).
  Idempotent on `(snapshot_month, universe, symbol)`.
- Grading (`POST /api/multibagger/ledger/evaluate`, also run lazily on GET): the return from the first close **after** `snapshot_date`
  to the first close ≥ snapshot_date + 6/12/24/36 months, and the same for Nifty 500.
- Summary per snapshot and horizon: the median return of A+B, C, D and X, and the rank IC of score vs return.
- **Early read at 12 months / decision at 24 months.** PASS at 24 months = across ≥ 12 graded monthly snapshots, the median A+B
  excess vs the X+D control is ≥ **+10 pp**, and the mean score-vs-24-month-return rank IC is ≥ **0.05**. FAIL → the fundamental weights get
  revisited with the forward data (one change, documented). Until then the page shows "Not yet validated — ledger started YYYY-MM".

### (c) Hindsight sanity check (qualitative, explicitly flagged)

`MULTIBAGGER_HINDSIGHT_SYMBOLS` (default `DIXON.NS,DEEPAKNTR.NS,APARINDS.NS,KPITTECH.NS,CGPOWER.NS,TANLA.NS,VAKRANGEE.NS,PCJEWELLER.NS`)
are scored on the price layers against the `nifty500` backtest panel. For each, the backtest reports the first month it entered the top 20,
its price score in the 12 months before its largest 3-year run, and whether a top-20 entry was followed by a > 50 % drawdown.
The UI label is: "Hindsight check — these names were chosen *because* we know what happened. Informational only, not evidence."
There are no pass/fail thresholds.

---

## UX

New route `frontend/app/multibagger/page.tsx` → `components/multibagger-shell.tsx`. Nav link "Multibagger" goes after "VCP" in `app-nav.tsx`.

1. **Header**: title "Multibagger framework — 3–5 year ideas". A permanent banner: "Educational research tool, not investment
   advice. A high score means a stock matches these rules today, not that it will rise. Do your own research." Universe
   select (default **Smallcap 250**), "Run screen" button, "Refresh fundamentals" checkbox, ASM/GSM exclusion textarea
   (localStorage `swing.multibagger.exclude.v1`). While running: a progress bar with `stage` and `progress` (poll status every 3 s).
   The last result loads on open (`GET /api/multibagger`). A 404 shows "No run yet for this universe — press Run screen (≈ 3–4 min first time)".
2. **Funnel strip**: universe → after size bucket → after pre-filter → deep pass (cap 120) → passed kill-switches → tiered,
   plus `eliminated_by` chips with counts.
3. **Filters**: tier chips (A/B/C/D/All), timing chips, sector select, "Hide rows with data gaps".
4. **Main table** columns: Rank · Stock (symbol + name, links to `/analyze/[symbol]`) · Sector · Mcap ₹Cr · Score · Adj. score ·
   Tier · Timing · Growth · Quality · Valuation · Tailwind · Price (layer mini-bars 0–100) · Rev CAGR 3y · PAT CAGR 3y · ROCE ·
   D/E · Implied vs delivered growth · % below 52w high · Coverage %. A missing value shows "—". A coverage below 100 % shows a grey dot with the tooltip
   listing the gaps.
5. **Row expand**: per-layer metric table (metric, value, score, status chip: `ok` / `neutral — no data (50)` /
   `penalised — no data (25)`), Why it qualifies, Red flags, Data gaps, Thesis check, the **Manual checklist** (11 tri-state
   items, live adjusted score/tier), and links "Open DCF" (`/valuation/[symbol]`) and "Full analysis".
6. **Check any stock** box: symbol → `GET /api/multibagger/stock/{symbol}` → the same expanded card (runs even for stocks outside
   the deep-pass cap. Kill-switch failures are shown, not hidden).
7. **Collapsed tables**: "Excluded by kill-switch" (symbol, code, detail) and "Insufficient data" (symbol, reason, gaps).
8. **Side panels**: "How the score works" (weights, kill-switches, missing-data policy, from `rules`), "Position sizing"
   calculator (§8), "Thesis-break rules" (§9).
9. **Validation panel**: "Run price backtest" (POST, poll status) → PASS/FAIL/INCONCLUSIVE badge per criterion with numbers,
   a horizon table, quintile bars, survivorship warning and hindsight table (with its label). Forward ledger table:
   snapshot month × horizon → median A+B / C / D / X and IC. Before 12 months it shows "Pending".
10. Demo mode: a "(demo data)" pill next to the title.

---

## API (backend/app/api.py, prefix /api)

| Method | Path | Behaviour |
|---|---|---|
| POST | `/multibagger/run?universe=nifty_smallcap_250&exclude=A,B&refresh_fundamentals=false` | Starts a background thread. Returns `{started, state}`. `started=false` + `state.reason` if any multibagger job (run or backtest) is running. |
| GET | `/multibagger/status` | `{running, job: "run"|"backtest"|null, stage, progress 0–100, universe, started_at, finished_at?, error?}` |
| GET | `/multibagger?universe=` | Latest result artifact `multibagger:{universe}`, or 404. |
| GET | `/multibagger/stock/{symbol}` | Synchronous single-stock evaluation. L4 is mapped through `rs_breakpoints` and `sectors` of the latest `nifty500` (else the latest any-universe) result. If no result exists → L4 neutral + gap `no_reference_run`. 404 if there is no price data. |
| POST | `/multibagger/backtest?universe=&start=2015-01-01` | Async. Result in artifact `multibagger_bt:{universe}`. |
| GET | `/multibagger/backtest?universe=` | Result or 404. |
| GET | `/multibagger/ledger?universe=` | Graded summary (evaluates lazily, at most once per hour). |
| POST | `/multibagger/ledger/evaluate` | Grades now. Returns `{checked, updated}`. |

Result JSON (`GET /multibagger`):
```
{ available, universe, as_of, generated_at, demo,
  funnel: {universe_count, after_size_bucket, after_prefilter, deep_pass, not_deep_passed, passed_kill_switches, tiered, insufficient},
  eliminated_by: {code: count}, weights: {growth:30, quality:25, valuation:15, tailwind:15, price:15},
  rules: [str], notes: [str], sectors: [{sector, rank, score}], rs_breakpoints: [21 floats],
  results: [Row], excluded: [{symbol, company_name, code, detail}], insufficient: [{symbol, company_name, reason, data_gaps}],
  snapshot: {month, recorded} }
Row = { rank, symbol, company_name, sector, industry, price, market_cap_cr, score, tier, tier_label,
  capped_by_weak_layer, weak_layer, timing, timing_label, timing_reason, coverage_pct,
  layers: {growth|quality|valuation|tailwind|price: {score, weight, coverage_pct,
           metrics: [{key, label, value, display, score, status: "ok"|"neutral"|"penalised", note}]}},
  qualifies: [str], red_flags: [str], data_gaps: [str],
  vcp_status, pivot, distance_to_52w_high_pct, reverse_dcf: {implied_growth_pct, delivered_growth_pct},
  thesis_check: [{code, status: "ok"|"warning"|"break"|"na", text}] }
```

DB table `multibagger_snapshots` (in `db.py`): `id` PK, `snapshot_month` String(7), `snapshot_date` Date, `universe` String(40),
`symbol` String(40), `company_name` String(200), `sector` String(120), `rank` Integer, `score` Float, `tier` String(2),
`timing` String(20), `price` Float, `layer_growth`, `layer_quality`, `layer_valuation`, `layer_tailwind`, `layer_price` Float,
`coverage_pct` Float, `entry_date` Date, `entry_price` Float, `ret_6m`, `ret_12m`, `ret_24m`, `ret_36m` Float,
`bench_6m`, `bench_12m`, `bench_24m`, `bench_36m` Float, `recorded_at`, `evaluated_at` DateTime(tz).
`UniqueConstraint(snapshot_month, universe, symbol, name="uq_mb_snapshot")`.

Config (`config.py`): `multibagger_deep_cap` (env `MULTIBAGGER_DEEP_CAP`, 120), `multibagger_fund_workers` (4),
`multibagger_fund_ttl_days` (7), `multibagger_backtest_max_symbols` (300), `multibagger_hindsight_symbols` (CSV above).

---

## Acceptance criteria

1. `lin()` known answers: `lin(15,5,25)=50`, `lin(0,5,25)=0`, `lin(40,5,25)=100`, `lin(0.5,1.0,0.0)=50`.
2. Each kill-switch K1–K6 has a unit test with a passing and a failing fixture. K0 routes to `insufficient`, not `excluded`.
3. Missing-data policy test: a fixture with no CFO rows gives `cfo_pat.score == 25` and `status == "penalised"`, a fixture with no quarterly data gives `accel.score == 50` and `status == "neutral"`, and both codes appear in `data_gaps`. No metric with missing input ever has score 0 unless the rule says so (`implied_growth` None, pat_cagr ≤ 0).
4. Layer and system score test: a hand-computed fixture reproduces `score` to 0.1. Weights sum to 100.
5. Tier bands and the weak-link cap: a fixture with score 80 and price layer 20 → tier C, `capped_by_weak_layer=true`, `weak_layer="price"`.
6. Timing flag: four fixtures (built with `conftest.make_bars`) produce `downtrend`, `extended`, `entry_zone` and `watch` respectively.
7. Coverage gate: Growth coverage < 60 % → the stock is in `insufficient`, not in `results`.
8. `fetch_deep_financials` in demo mode is deterministic (same symbol → identical dict) and makes no network calls.
9. A cached `mb_fin:{SYMBOL}` artifact younger than the TTL is reused (the fetch is mocked and asserted not called). `refresh_fundamentals=true` refetches.
10. Pre-filter excludes LARGE-bucket, < ₹20, < ₹1 Cr median value, < 500 sessions and > 2 circuit days, each counted in `eliminated_by`. No more than `MULTIBAGGER_DEEP_CAP` symbols reach the deep pass.
11. API smoke tests (`tests/test_api.py`, demo): POST run → `started` true; polling status reaches `stage == "done"` within the test timeout; GET returns `available: true` with all `funnel` keys; GET `/multibagger/stock/RELIANCE` returns 200 with K1 or tier info; a second POST while running → `started: false`; GET backtest before any run → 404.
12. Backtest test on a synthetic panel: a planted "always-up" stock enters the top cohort, and entry is the close **after** the signal date (look-ahead test). Costs reduce returns by exactly the configured %. A > 40 % one-day jump is dropped and counted.
13. Backtest result contains `criteria: [{name, threshold, value, pass}]`, `verdict ∈ {PASS, FAIL, INCONCLUSIVE}` and `survivorship_warning`.
14. Ledger: snapshot is idempotent (running twice in a month inserts once). Grading uses the first close after `snapshot_date`. Horizons not yet reached stay null.
15. Frontend unit tests (`frontend/tests/multibagger.test.ts`): `adjustedScore` (positives capped at +10, red flags −5, hard stop → "Thesis broken (manual)"), and `sizing` (₹18 L, 25 %, 10 ideas → full ₹45,000, starter ₹15,000, max at cost ₹54,000).
16. The page renders with demo data and shows the disclaimer banner, funnel, table, expandable row, checklist that persists across reload, sizing panel and validation panel. `npm run typecheck`, `npm test` and `npm run build` pass.
17. A grep across new backend and frontend files finds no "buy " imperative in user-facing strings (the allowed phrase is "entry zone").
18. `cd backend && python -m pytest -q` passes offline.

---

## Out of scope

- Point-in-time fundamental backtest, and scraping promoter/pledge/MF data (screener.in, NSE XBRL).
- Banks, NBFCs and insurers (need P/B and ROE-based layers; later spec).
- A holdings tracker for multibagger positions (v1 uses the single-stock check plus thesis rules). Including the new localStorage keys in the Journal backup/restore.
- Adding a monthly cron to `render.yaml` (the job module is provided. Wiring it in costs a paid cron, so the user decides).
- Excel export, email alerts, weight auto-tuning.

## Risks

- **Overfitting**: about 25 thresholds. Mitigation: linear bands instead of cliffs, priors written before testing, no tuning on the
  proxy backtest, and only one documented weight change allowed after the 24-month ledger read. The page says "not yet validated".
- **Yahoo data quality**: consolidated/standalone mixing, missing rows, restatements, and occasional unit errors. Mitigation: explicit gaps,
  coverage gate and the "Open DCF" link for inspection. There is also rate-limiting/blocking risk on Render, which the deep cap, 4 workers and 7-day cache mitigate.
- **Momentum pre-rank bias**: early, base-building names can miss the deep pass. Mitigation: the funnel count and the single-stock endpoint.
- **Survivorship** inflates every historical number (stated everywhere).
- **Behavioural**: a 3–5 year bucket conflicts with a 2–4 %/month swing mindset. Sizing defaults keep it at 25 % of capital, with ⅓ starters.
- **Small-cap liquidity/impact** in a 2018-style crash: the 1 % cost sensitivity and the 2018 criterion cover it.
- **Render memory/CPU**: one multibagger job at a time. The deep pass is capped. The `mid_small_2000_plus` backtest is sampled.

---

## Plan (ordered, each step one reviewable PR-sized change)

1. **Config + DB**: add the five settings to `backend/app/config.py`, and the `multibagger_snapshots` table to `backend/app/services/db.py`.
2. **Pure rules**: `backend/app/services/multibagger_rules.py` holds constants and weights, `lin`, metric functions (`growth_metrics(annual, quarterly, info)`,
   `quality_metrics`, `valuation_metrics(valuation_data, price, info)`, `tailwind_metrics`, `price_metrics(frame, bench_close)`),
   `layer_score`, `kill_switches(...) -> list[(code, detail)]`, `tier_for`, `timing_flag(frame, vcp_result)`, `thesis_check`,
   and `explain(...)` (qualifies/red_flags/gaps). Tests: `backend/tests/test_multibagger_rules.py` (AC 1–7).
3. **Data**: `backend/app/services/multibagger_data.py` holds `fetch_deep_financials`, `demo_deep_financials` and artifact caching. It reuses
   `valuation._row`, `assemble`, `resolve_shares`. Tests: AC 8–9.
4. **Service**: `backend/app/services/multibagger.py` holds `MultibaggerService` (state dict + lock + daemon thread like `CuratedAlgoService`).
   It runs `_run(universe, exclude, refresh)` through the stages: loading prices → pre-filter → ranking → fundamentals (progress per symbol) → scoring → saving.
   It also has `latest(universe)`, `stock(symbol)`, and `snapshot_if_new_month(result)`. Tests: AC 10.
5. **Ledger**: `record_snapshot`, `evaluate`, `summary` in `multibagger.py` (or `multibagger_ledger.py`), following `ledger.py`. Tests: AC 14.
6. **Backtest**: `backend/app/services/multibagger_backtest.py` holds the vectorised price score on `curated_algo.Panel`, monthly cohorts,
   horizons, criteria/verdict and hindsight. It is launched through the same service state. Tests: AC 12–13 (`backend/tests/test_multibagger_backtest.py`).
7. **API**: endpoints in `backend/app/api.py` plus smoke tests in `backend/tests/test_api.py` (AC 11). Job module
   `backend/app/jobs/run_multibagger.py` (run → snapshot → evaluate, CLI `--universe`).
8. **Frontend types/client**: `frontend/types.ts` (`MultibaggerResult`, `MultibaggerRow`, `MultibaggerLayer`, `MultibaggerMetric`,
   `MultibaggerStatus`, `MultibaggerBacktest`, `MultibaggerLedger`). `frontend/lib/api.ts` gets `startMultibagger`, `getMultibaggerStatus`,
   `getMultibagger`, `getMultibaggerStock`, `startMultibaggerBacktest`, `getMultibaggerBacktest`, `getMultibaggerLedger`.
9. **Frontend logic**: `frontend/lib/multibagger.ts` holds `CHECKLIST` items, `adjustedScore`, `adjustedTier` and `sizing`. `frontend/lib/store.ts`
   gets `useMultibaggerChecklist` and `useMultibaggerSettings` (keys added to `KEYS`). Tests: `frontend/tests/multibagger.test.ts` (AC 15).
10. **Page**: `frontend/app/multibagger/page.tsx`, `frontend/components/multibagger-shell.tsx` (sections 1–10 of UX), the nav link in
    `frontend/components/app-nav.tsx`, and styles in `frontend/app/globals.css`. Verify AC 16–17.
11. **Dev notes**: `docs/features/multibagger-framework/dev-notes.md` lists any deviation from this spec with the reason.
