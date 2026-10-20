# Test report — multibagger-framework

| AC | Result | Evidence |
|---|---|---|
| 1 `lin` known answers | PASS | `test_lin_known_answers` |
| 2 Kill-switches K1–K6 pass/fail, K0 → insufficient | PASS | `test_k1…test_k6`, `test_k0_routes_to_insufficient_not_excluded` |
| 3 Missing-data policy | PASS | `test_missing_cfo_is_penalised_and_missing_quarters_neutral`, `test_missing_inputs_never_score_zero` |
| 4 Layer/system score by hand, weights = 100 | PASS | `test_layer_and_system_score_by_hand` |
| 5 Tier bands + weak-layer cap | PASS | `test_weak_layer_caps_tier` |
| 6 Timing flag (4 states) | PASS | `test_timing_flags` |
| 7 Coverage gate | PASS | `test_low_growth_coverage_goes_to_insufficient` |
| 8 Demo deep data deterministic, offline | PASS | `test_demo_deep_financials_deterministic` |
| 9 Cache reuse / refresh | PASS | `test_cache_reused_until_refresh` |
| 10 Pre-filter rules counted, deep cap | PASS | `test_prefilter_rules_each_counted`, `test_multibagger_run_end_to_end` (deep_pass ≤ 120) |
| 11 API smoke | PASS | `tests/test_api.py::test_multibagger_*` |
| 12 Backtest: planted winner, T+1 entry, exact costs, data breaks, no look-ahead | PASS | `test_always_up_stock_in_top_cohort`, `test_entry_is_the_session_after_the_signal`, `test_costs_reduce_returns_by_exactly_the_setting`, `test_data_break_dropped_and_counted`, `test_scores_ignore_the_future` |
| 13 Backtest contract + INCONCLUSIVE | PASS | `test_result_contract`, `test_too_few_cohorts_is_inconclusive` |
| 14 Ledger idempotent, first close after date, unreached horizons null | PASS | `test_snapshot_idempotent`, `test_ledger_grades_from_first_close_after_snapshot` |
| 15 Frontend adjusted score + sizing | PASS | `frontend/tests/multibagger.test.ts` (8 tests) |
| 16 Page renders with all sections; build | PASS | Playwright run on demo data at 1400 px and 390 px; `next build` OK |
| 17 No "buy" imperative in user-facing strings | PASS | grep: only "buy-and-hold" in a code docstring |
| 18 Backend suite offline | PASS | 84 passed |

## Validation numbers
Price backtest on **demo (random-walk) data**, Smallcap 250, 2015→: verdict **FAIL** (median 24-month excess +1.3 pp, IC 0.022). This is the expected answer on random data and shows the test doesn't invent an edge. **The real verdict needs a production run** (press "Run price backtest" on the page); record those numbers here when available.
Forward ledger: PENDING — starts with the first production run.
