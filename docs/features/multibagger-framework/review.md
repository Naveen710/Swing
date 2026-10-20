# Review: multibagger-framework

Reviewer: reviewer agent · Date: 2026-10-11 · Scope: uncommitted diff plus untracked files (backend services, API, jobs, DB, frontend page, types, tests).
Inputs: `spec.md` and `dev-notes.md`. There is no `test-report.md` yet, so the price-backtest numbers in the spec's validation plan have not been recorded by the tester.

## What I verified (not just read)

- `python -m pytest -q`: 79 passed. `npx tsc --noEmit` is clean. `npm test`: 19 passed.
- **Look-ahead in `price_scores`: none.** I wrote a probe (`/tmp/claude-0/review/lookahead.py`) that multiplies every bar after signal row T by random factors (×0.3 to ×3, applied to close, high, low, volume and the index). It then recomputes the price score at T, at three dates from 2015 to 2020. The maximum absolute difference was 0.0. Every rolling window, the 12-1 momentum (`shift(21)/shift(252)`), the cross-sectional ranks, the eligibility mask and the data-break window `(entry, exit]` use only data up to T. Entry is at `pos + 1` (T+1). Horizons whose exit falls past the last bar are `None` and are left out of that horizon's statistics.
- **Live and backtest price metrics match.** I compared 120 demo stocks at the last bar. The backtest's vectorised Stage-2 count and `near_high` score match `vcp_scanner.trend_template` and `price_metrics` exactly (0 mismatches).
- **Scoring constants match the spec.** Every `lin()` band, every metric weight, the layer weights (30/25/15/15/15), the neutral (50) and penalised (25) fills, the tier bands, the weak-layer cap, the 60 % coverage gate, the timing order (downtrend → extended → entry_zone → watch) and kill-switches K0–K6 all match the spec.
- **Memory (demo data, single process, app import baseline ≈ 107 MB):**
  - Backtest on `nifty500`: peak RSS 348 MB, 33 s.
  - Backtest on `nifty_smallcap_250`: 227 MB, 18 s.
  - Run on `mid_small_2000_plus`: 245 MB, 35 s.
- **Ledger grading is correct.** Entry is the first close strictly after `snapshot_date`. Each horizon uses the first close on or after `snapshot_date + N months`. The benchmark is graded the same way. Horizons not yet reached stay null. The snapshot write is idempotent: a check-then-insert plus `uq_mb_snapshot`.

## Blocker

None.

## Should fix

1. **`backend/app/services/multibagger.py:423-428` and `:369-421`: ledger grading runs synchronously inside `GET /multibagger/ledger`, and it refetches rows that cannot be graded yet.**
   - Why: `evaluate_ledger` selects every row with `ret_36m IS NULL` once its snapshot is more than 6 months old (`:374`). That includes rows whose next horizon (12m, 24m, 36m) is still months away. It then fetches price history for each symbol, one by one, on the request thread.
   - From about 2027-04, one snapshot means about 120 symbols (scored plus the X control group). Once the disk cache is stale, every page open (lazily, once an hour, and on every cold start) pays for about 120 Yahoo downloads. The Yahoo provider serialises these behind `_download_lock`. That is well over the 30 s limit in the checklist.
   - Fix:
     - Only treat a row as due when its next ungraded horizon's target date is on or before today.
     - Remove the lazy grading from GET. Grading already runs from the daily cron (`run_monthly` calls `evaluate_ledger` every day). Alternatively, start grading in the existing background-thread pattern and return the stored summary straight away.

2. **`backend/app/services/multibagger.py:335-363`: a month's snapshot can be used up by a bad run.**
   - Why: `record_snapshot` has no data-quality gate. If Yahoo throttles or blocks the 4-worker fundamentals fetch, every deep-pass name becomes "Fetch failed" (`multibagger_rules.py:641-642`). The snapshot is then recorded as about 120 tier-X rows and 0 scored rows.
   - Idempotency then stops any retry that month, so a month of the forward test (the "real test") is lost. When `results` is empty, it returns `recorded: True` with 0 rows, and `has_snapshot` then makes the cron rerun every day.
   - Fix: skip recording, and return `recorded: False` with a reason, when `tiered == 0` or when fetch failures are more than about 20 % of `deep_pass`. Do the same in demo mode (`is_demo()`), so demo fundamentals never enter a real ledger.

3. **`backend/app/api.py:240-241`: the status endpoint returns the live shared `state` dict.**
   - Why: FastAPI serialises it by iterating over it while the worker thread runs `state.update(...)`. That update adds keys (`detail`, `finished_at`, `error`) under `_lock`, but the reader does not take the lock. A poll that lands at that moment can fail with "dictionary changed size during iteration". `api.py` also copies the dict at `/run` and `/backtest` without the lock.
   - Fix: add `MultibaggerService.status()`, which returns `dict(self.state)` while holding `self._lock`, and use it in all three routes.

4. **`backend/app/services/multibagger_rules.py:148-157`: `quarter_yoy` compares the wrong quarters when data is missing.**
   - Why: it drops `None` values and then indexes the shortened list as `[-1]` against `[-5]`. `quarterly_from_statement` keeps quarters that have net income but no revenue, and Yahoo often skips quarter columns altogether. In both cases `q[-1]` vs `q[-5]` is no longer year-on-year.
   - This feeds `accel` (20 % of Growth) and the break rules TB1 and TB2.
   - Fix: match by period. For each quarter, find the quarter whose `period` is 350–380 days earlier. Return `None` when there is no such quarter. Add a known-answer test with one missing quarter.

5. **`backend/app/services/multibagger_backtest.py:115-118`: the backtest's peer rank differs from the live rule.**
   - Why: live code puts a sector into peer ranking only if that sector is in the rotation table, which needs at least 3 members (`multibagger.py:192`). Otherwise the stock is scored neutral (50).
   - The backtest ranks every sector that is not Unknown, including 1- and 2-member sectors. A single-member sector gets 100, and a two-member sector gets 0 or 100. So the price score being validated is not the one used live (checklist: "live signal rules == backtest rules").
   - Fix: set `peer_metric` to 50 where the sector is not in `valid`, or better, make one shared helper that both paths call.

6. **`backend/app/services/multibagger_backtest.py:321-325`: "Fell > 50 % after entry" is measured from before the entry.**
   - Why: `months[0]` is the YYYY-MM of the month-end signal, and `first` is the 1st of that month. That is about 20 sessions before the signal and about 21 before the T+1 entry. So the drawdown base is a price the framework never paid.
   - Fix: store each cohort's `entry_date` per member in `member_months`, and measure `after = s[s.index >= entry_date].head(757)`.

7. **`backend/app/services/multibagger_rules.py:498-573` and `frontend/components/multibagger-shell.tsx:72`: a rule is shown that does not exist.**
   - Why: the page lists "Tier D in two consecutive monthly snapshots → warning" as a thesis-break rule, but no code computes it. `dev-notes.md` does not list this as a deviation from the spec. A user will assume it is being checked.
   - Fix: either compute it from `multibagger_snapshots` (the last two months, per symbol and universe) and add it to `thesis_check`, or remove the line and record the deviation in the dev notes.

8. **`backend/app/services/multibagger.py:316-319` and `multibagger_backtest.py:57-121`: Render memory for the `nifty500` backtest.**
   - Why: I measured a 348 MB peak in a bare process. On Render it shares 512 MB with uvicorn, the scanner's in-memory caches and yfinance batch downloads of about 3,350 rows × 100 symbols. Nothing stops an Algo, VCP or portfolio backtest from running at the same time (the lock only covers multibagger jobs). It is likely to hit the out-of-memory limit.
   - Fix (any of these):
     - Cap `nifty500` at `multibagger_backtest_max_symbols` the way `mid_small_2000_plus` is capped.
     - Drop the LARGE names before `load_panel` instead of after.
     - Compute rolling frames as float32 and `del` them after use.

9. **`backend/tests/test_multibagger_service.py:81-86`: the look-ahead test checks only the entry date.**
   - Why: this is the most important property, and the test would not catch a future bar leaking into the score.
   - Fix: add the future-scramble assertion from my probe. Multiply every row after T by random factors and assert the scores at T do not change.

## Nit

- `backend/app/jobs/run_multibagger.py:17`: the job checks `date.today()`'s month, but the snapshot month comes from `as_of`. On the first days of a month, before a new bar exists, it reruns the full screen every day and records nothing (as the dev notes say). Use the last bar's month instead, e.g. from `_index_frame`.
- `backend/app/services/multibagger.py:248`: the funnel's `passed_kill_switches` includes K0 and coverage-gate "insufficient" names. K0 is checked before K1–K6, so a bank with too little data is counted as having passed. Count only `results` plus coverage-gate cases, or rename the label.
- `backend/app/services/multibagger_rules.py:250`: the PAT CAGR label uses `rev_years`. Use the span of the net-income series.
- `backend/app/api.py:253`: `symbol` is not validated. Restrict it to `^[A-Za-z0-9&^.\-]{1,30}$` and return 400 otherwise.
- `backend/app/services/multibagger.py:290`: when there is no `nifty500` result, every universe artifact is loaded and parsed on every single-stock call. Cache the reference result or stop once one is found (already lazy, but each miss still costs a DB read).
- The first run of a month is recorded in the ledger with whatever `exclude` list and `refresh` setting the user chose. Consider recording only from the cron's default run.
- `frontend/components/multibagger-shell.tsx:295`: every failure of the single-stock check is shown as "No price data". A 5xx or a timeout should show a different message.
- `frontend/components/multibagger-shell.tsx:534`: "Starter-eligible right now: N stocks" is close to advice language. Consider "N stocks meet the starter rule (tier A/B, Entry zone or Watch) — rules, not recommendations".
- `backend/app/services/multibagger_backtest.py:168`: `pick_top` treats "Unknown" as a sector, so at most 3 Unknown names go into each cohort. This is the same as in `quant_screen`, but it should be mentioned in the notes for `mid_small_2000_plus`, where about 67 % of sectors are Unknown.
- Speculative, not verified: `multibagger_data.fetch_deep_financials` calls `yfinance.Ticker` from 4 threads, while price downloads are serialised behind a lock. yfinance has had cookie/crumb race bugs under concurrency. Watch the first production logs.

VERDICT: CHANGES REQUESTED

---

## Re-review (after the developer's fixes)

Checks run: `python -m pytest -q` → 84 passed. `npx tsc --noEmit` is clean and `npm test` → 19 passed. The demo `nifty500` backtest now peaks at 251 MB RSS (was 348 MB) and runs in 20 s on 300 symbols.

| # | Status | Evidence |
|---|---|---|
| 1 | Fixed | `evaluate_ledger.next_due` treats a row as due only when its first ungraded horizon has arrived. `ledger()` calls `_evaluate_in_background()`, which is guarded by `_lock` and `_grading` and runs at most once an hour. GET no longer blocks. |
| 2 | Fixed | `run()` does not record a snapshot when the data is demo, when the user supplied an exclusion list, when nothing was scored, or when fetch failures are more than 20 % of the deep pass. The reason is returned in `snapshot.reason`. |
| 3 | Fixed | `_update` replaces the dict instead of changing it. `get_state()` returns a copy taken under the lock. All three routes use it. |
| 4 | Fixed | `quarter_yoy` now matches quarters by date (350–380 days apart), and `back` indexes the sorted dated points. A known-answer test was added. |
| 5 | Fixed | Sector and peer rank are 50 unless the sector has at least 3 eligible known members, which is the same rule as the live screen. A test was added. |
| 6 | Fixed | The hindsight drawdown is now measured from the stored entry bar (`member_months` holds `(month, entry_pos)`). |
| 7 | Fixed | `previous_tiers` plus a `LEDGER` warning in `thesis_check` when a stock is tier D in this run and in the previous snapshot. The test covers `previous_tiers` only, not the warning being added in `run()` (nit). |
| 8 | Fixed | Nifty 100 names are dropped before `load_panel(listings=...)`, and every universe is capped at `MULTIBAGGER_BACKTEST_MAX_SYMBOLS`. The new memory figure is measured above. |
| 9 | Fixed | `test_scores_ignore_the_future` scrambles every bar after the signal and asserts the scores at the signal date are unchanged. |

Nits taken (verified by reading): the cron job's month now comes from the last bar; `passed_kill_switches` now counts only scored plus coverage-gate names; the PAT label uses the net-income span; the symbol regex returns 400; the reference run is cached; snapshots are recorded only from the default run; the single-stock error messages are split; the starter wording is softened; the backtest notes mention the Unknown-sector cap.

New nits (not blocking):
- `multibagger.py:352-358`: Nifty 500 is now *sampled* to 300 names. That departs from the spec ("nifty500 minus LARGE"), and `dev-notes.md` does not record it. Sampling can also drop a hindsight name, which is then reported as "Not in this universe". Record the deviation in the dev notes and always include the hindsight symbols in the sampled list.
- `evaluate_ledger`: a row whose due horizon never gets a close (for example, a suspended stock) stays due and is refetched every hour. Mark it graded-as-null after about 30 days past the target date.
- Insufficient rows now carry a `stage` key that is not in `MultibaggerInsufficient` in `types.ts`. This is harmless.

VERDICT: APPROVE
