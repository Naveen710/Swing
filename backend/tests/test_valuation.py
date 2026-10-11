import pytest

from app.services.valuation import cagr, implied_growth, resolve_shares, run_dcf, verdict


def test_zero_growth_dcf_matches_closed_form():
    # Flat cash flows forever = perpetuity CF / r
    r = run_dcf(base_cf=100.0, stage1=0.0, terminal=0.0, discount=0.10, cash=0, debt=0, shares=1)
    assert r["enterprise_value"] == pytest.approx(1000.0, rel=1e-9)


def test_cash_and_debt_flow_to_equity():
    a = run_dcf(100, 0.1, 0.04, 0.12, cash=0, debt=0, shares=10)
    b = run_dcf(100, 0.1, 0.04, 0.12, cash=500, debt=200, shares=10)
    assert b["per_share"] - a["per_share"] == pytest.approx(30.0)


def test_discount_must_exceed_terminal():
    with pytest.raises(ValueError):
        run_dcf(100, 0.1, 0.12, 0.12, 0, 0, 1)


def test_reverse_dcf_round_trips():
    price = run_dcf(100, 0.18, 0.05, 0.125, 50, 20, 10)["per_share"]
    assert implied_growth(price, 100, 0.05, 0.125, 50, 20, 10) == pytest.approx(0.18, abs=1e-6)


def test_reverse_dcf_rejects_negative_cash_flow():
    assert implied_growth(100, -5, 0.05, 0.12, 0, 0, 10) is None


@pytest.mark.parametrize("price,label", [(80, "Undervalued"), (100, "Fairly valued"), (114, "Fairly valued"), (120, "Overvalued")])
def test_verdict_band(price, label):
    assert verdict(price, 100)[0] == label


def test_cagr():
    assert cagr(100, 200, 5) == pytest.approx(2 ** 0.2 - 1)
    assert cagr(0, 200, 5) is None


def test_shares_fallback_from_market_cap():
    shares, source = resolve_shares({"marketCap": 1_000_000, "currentPrice": 100})
    assert shares == pytest.approx(10_000) and source
