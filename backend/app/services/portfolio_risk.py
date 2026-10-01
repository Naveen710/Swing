"""Beta and correlation of holdings and candidates, from daily returns."""
from __future__ import annotations

import logging
from itertools import combinations

import numpy as np
import pandas as pd

from app.config import settings
from app.schemas import (
    CorrelationPair,
    MarketCapBucket,
    PortfolioRiskRequest,
    PortfolioRiskResponse,
    SymbolRisk,
)
from app.services.universe import StockListing, find_listing

logger = logging.getLogger(__name__)
HIGH_CORRELATION = 0.7


def _normalise(symbol: str) -> str:
    s = symbol.strip().upper()
    return s if "." in s else f"{s}.NS"


def compute_portfolio_risk(market_data, benchmark_frame: pd.DataFrame | None, request: PortfolioRiskRequest) -> PortfolioRiskResponse:
    sessions = request.lookback_sessions
    holdings = {}
    for h in request.holdings:
        sym = _normalise(h.symbol)
        holdings[sym] = holdings.get(sym, 0.0) + h.value
    candidates = [c for c in dict.fromkeys(_normalise(c) for c in request.candidates) if c not in holdings]

    returns: dict[str, pd.Series] = {}
    unavailable: list[str] = []
    for sym in list(holdings) + candidates:
        listing = find_listing(sym) or StockListing(sym, sym, "Unknown", MarketCapBucket.SMALL)
        try:
            closes = market_data.get_history(listing, lookback_days=sessions + 30)["Close"]
            returns[sym] = closes.pct_change().dropna().tail(sessions)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Risk: no data for %s. %s", sym, exc)
            unavailable.append(sym)

    bench = None
    if benchmark_frame is not None and not benchmark_frame.empty:
        bench = benchmark_frame["Close"].pct_change().dropna().tail(sessions)

    def beta_and_vol(series: pd.Series) -> tuple[float | None, float | None]:
        vol = float(series.std() * np.sqrt(252) * 100) if len(series) > 20 else None
        if bench is None:
            return None, vol
        joined = pd.concat([series, bench], axis=1, join="inner").dropna()
        if len(joined) < 30 or joined.iloc[:, 1].var() == 0:
            return None, vol
        beta = float(joined.cov().iloc[0, 1] / joined.iloc[:, 1].var())
        return round(beta, 2), (round(vol, 1) if vol is not None else None)

    frame = pd.DataFrame(returns)
    corr = frame.corr(min_periods=30) if not frame.empty else pd.DataFrame()

    def corr_of(a: str, b: str) -> float | None:
        if a not in corr.index or b not in corr.columns:
            return None
        v = corr.loc[a, b]
        return None if pd.isna(v) else float(v)

    held = [s for s in holdings if s in returns]

    def risk_row(sym: str, others: list[str]) -> SymbolRisk:
        beta, vol = beta_and_vol(returns[sym])
        best, best_sym = None, None
        for other in others:
            if other == sym:
                continue
            c = corr_of(sym, other)
            if c is not None and (best is None or c > best):
                best, best_sym = c, other
        return SymbolRisk(
            symbol=sym, beta=beta, volatility_pct=vol,
            max_correlation=round(best, 2) if best is not None else None,
            most_correlated_with=best_sym,
            high_correlation=best is not None and best >= HIGH_CORRELATION,
        )

    holding_rows = [risk_row(s, held) for s in held]
    candidate_rows = [risk_row(s, held) for s in candidates if s in returns]

    total_value = sum(holdings[s] for s in held)
    betas = [(holdings[r.symbol], r.beta) for r in holding_rows if r.beta is not None]
    portfolio_beta = round(sum(v * b for v, b in betas) / sum(v for v, _ in betas), 2) if betas else None
    capital_beta = None
    if portfolio_beta is not None and request.capital:
        capital_beta = round(portfolio_beta * min(total_value / request.capital, 1.0), 2)

    pairs = []
    pair_values = []
    for a, b in combinations(held, 2):
        c = corr_of(a, b)
        if c is None:
            continue
        pair_values.append(c)
        if c >= HIGH_CORRELATION:
            pairs.append(CorrelationPair(a=a, b=b, correlation=round(c, 2)))

    return PortfolioRiskResponse(
        sessions=int(len(frame)) if not frame.empty else 0,
        benchmark_name=settings.benchmark_name,
        portfolio_beta=portfolio_beta,
        capital_weighted_beta=capital_beta,
        average_pairwise_correlation=round(float(np.mean(pair_values)), 2) if pair_values else None,
        holdings=holding_rows,
        candidates=candidate_rows,
        high_correlation_pairs=sorted(pairs, key=lambda p: p.correlation, reverse=True),
        correlation_threshold=HIGH_CORRELATION,
        unavailable=unavailable,
    )
