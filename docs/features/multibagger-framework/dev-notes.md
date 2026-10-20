# Dev notes — multibagger-framework

## Files
Backend
- `app/services/multibagger_rules.py` — pure rules: `lin`, kill-switches, 5 layer metric sets, tiers, timing flag, thesis check, explain, `evaluate_stock`.
- `app/services/multibagger_data.py` — deep fetch (5 Yahoo calls), deterministic demo data, `mb_fin:{SYMBOL}` cache (7-day TTL), failures → `{"error"}`.
- `app/services/multibagger_backtest.py` — price-proxy backtest (L4 + L5 without VCP), monthly cohorts, criteria/verdict, hindsight.
- `app/services/multibagger.py` — `prefilter()` (pure), `MultibaggerService` (run / stock / backtest jobs, one at a time; ledger snapshot, grading, summary, decision).
- `app/services/db.py` — `multibagger_snapshots` table. `app/config.py` — 5 settings.
- `app/api.py` — 8 endpoints under `/api/multibagger…`.
- `app/jobs/run_multibagger.py` (CLI) and a hook in `app/jobs/send_daily_scan_report.py`.
- Tests: `tests/test_multibagger_rules.py` (AC 1–9), `tests/test_multibagger_service.py` (AC 10, 12–14), `tests/test_api.py` (AC 11).

Frontend
- `types.ts`, `lib/api.ts`, `lib/store.ts` (3 new localStorage hooks), `lib/multibagger.ts` (checklist, `adjustedScore`, `sizing`).
- `components/multibagger-shell.tsx`, `app/multibagger/page.tsx`, nav link, `app/globals.css` (`mb-` classes).
- `tests/multibagger.test.ts` (AC 15).

## Decisions / deviations from the spec
1. **Monthly snapshot is wired into the existing daily cron** (user-approved): `send_daily_scan_report` calls `run_monthly()`, which runs the Smallcap 250 screen only if this month has no snapshot. No new paid cron. The spec's "out of scope: cron" is superseded.
2. Snapshot month comes from the data's `as_of` (last trading day), so on the 1st of a month before any bar exists the run records nothing new and the job retries the next day.
3. Funnel `eliminated_by` counts only the first failing kill-switch per stock so counts add up; all failures are still shown in the excluded row's detail.
4. Timing flag treats "no 200-day average yet" as `downtrend` (cannot confirm the trend).
5. Backtest: cost is applied to the cohort only; the equal-weight universe benchmark carries no cost (conservative). Excluded LARGE names are removed for every universe (spec: nifty500 minus LARGE; other universes have ≤ 1 LARGE).
6. Hindsight runs on whichever universe is backtested (spec said the nifty500 panel); names not in that universe show "Not in this universe".
7. Single-stock endpoint: peer rank is always neutral (no peers for one stock) and stated in `notes`.
8. Backtest memory (review SF8): Nifty 100 names are dropped before loading and every universe is evenly sampled to `MULTIBAGGER_BACKTEST_MAX_SYMBOLS` (300), always keeping the hindsight names. So the Nifty 500 backtest tests ~300 of its ~400 non-large names (spec said all); Smallcap 250 is unaffected. Peak ≈ 251 MB in demo.
9. Review fixes: quarter YoY matched by date; ledger grading only when the next horizon is due and never inside a request; snapshot skipped for demo, custom exclusions, empty runs and > 20 % fetch failures; thread-safe status; tier-D-twice warning (`LEDGER` row in the thesis check).
10. Secondary universes use criteria 1–2 only, with the relaxed thresholds (> 0 pp, > 50 %), per spec.

## Verified
- `python -m pytest -q`: 84 passed (offline, demo). `npm run typecheck`, `npm test` (19 passed), `next build` OK.
- Browser (Playwright, demo data): run → progress → 83 scored rows; tier/timing filters; row expand; checklist persists across reload; Check-any-stock; price backtest renders criteria/horizons/quintiles/hindsight; ledger shows PENDING with the 2026-10 snapshot; no horizontal page scroll at 390 px.
- Demo backtest on random-walk data returns FAIL (no spurious edge), as it should. Peak memory of a run + backtest ≈ 210 MB.
- AC17 grep: the only "buy" is "buy-and-hold" in a code docstring.

## Not done
- A ledger row whose target date never gets a close (suspended/delisted stock) is re-tried on each hourly/daily grading. Harmless but wasteful; mark such rows after ~60 days in a follow-up.
- Real-data run (sandbox cannot reach Yahoo). First production run will take ~3–4 min; watch Render memory on `mid_small_2000_plus`.
