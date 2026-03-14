from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from app.config import settings
from app.schemas import (
    BacktestStats,
    IndicatorSnapshot,
    RelativeStrengthSnapshot,
    ScanRequest,
    ScanResponse,
    StockDetailResponse,
    TradeSetup,
)
from app.services.backtest import backtest_pattern
from app.services.indicators import apply_indicators
from app.services.market_data import MarketDataError, create_market_data_provider
from app.services.patterns import PatternMatch, detect_best_pattern
from app.services.relative_strength import (
    RelativeStrengthContext,
    build_relative_strength_snapshot,
)
from app.services.store import signal_store
from app.services.universe import StockListing, get_benchmark_listing, load_universe

logger = logging.getLogger(__name__)


class ScannerService:
    def __init__(self) -> None:
        self.market_data = create_market_data_provider()

    def list_stocks(self) -> list[dict[str, str]]:
        return [listing.to_summary().model_dump() for listing in load_universe()]

    def run_scan(self, request: ScanRequest) -> ScanResponse:
        listings = load_universe(request.symbols, request.sectors, request.market_caps)
        if not listings:
            return ScanResponse(
                generated_at=datetime.now(UTC),
                universe_size=0,
                scanned_symbols=0,
                results=[],
            )

        benchmark_context = self._load_benchmark_context(request.lookback_days)
        with ThreadPoolExecutor(max_workers=settings.scan_workers) as executor:
            results = list(
                executor.map(
                    lambda listing: self._scan_listing(listing, request, benchmark_context),
                    listings,
                )
            )

        setups = [setup for setup in results if setup is not None]
        setups.sort(
            key=lambda setup: (
                setup.ranking_score,
                setup.expected_profit_amount,
            ),
            reverse=True,
        )
        limited = setups[: request.max_results]
        signal_store.replace(limited)

        return ScanResponse(
            generated_at=datetime.now(UTC),
            universe_size=len(listings),
            scanned_symbols=len(listings),
            results=limited,
        )

    def latest_signals(self) -> list[TradeSetup]:
        return signal_store.all()

    def get_stock_detail(self, symbol: str) -> StockDetailResponse | None:
        listings = load_universe(symbols=[symbol])
        if not listings:
            return None

        listing = listings[0]
        try:
            history = self.market_data.get_history(listing)
        except MarketDataError as exc:
            logger.warning("Unable to build stock detail for %s. %s", symbol, exc)
            return None

        candles = [
            {
                "date": index.to_pydatetime(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            }
            for index, row in history.tail(90).iterrows()
        ]
        return StockDetailResponse(
            stock=listing.to_summary(),
            latest_signal=signal_store.find(symbol),
            candles=candles,
        )

    def get_backtest(self, symbol: str) -> BacktestStats | None:
        listings = load_universe(symbols=[symbol])
        if not listings:
            return None

        try:
            history = self.market_data.get_history(listings[0])
        except MarketDataError as exc:
            logger.warning("Unable to build backtest for %s. %s", symbol, exc)
            return None

        enriched = apply_indicators(history)
        benchmark_context = self._load_benchmark_context(settings.default_scan_lookback)
        relative_strength = build_relative_strength_snapshot(enriched, benchmark_context)
        match = detect_best_pattern(enriched, relative_strength)
        if match is None:
            return None

        return backtest_pattern(enriched, match.pattern, benchmark_context)

    def _scan_listing(
        self,
        listing: StockListing,
        request: ScanRequest,
        benchmark_context: RelativeStrengthContext | None,
    ) -> TradeSetup | None:
        try:
            history = self.market_data.get_history(
                listing=listing,
                lookback_days=request.lookback_days,
            )
        except MarketDataError as exc:
            logger.warning("Skipping %s because history loading failed. %s", listing.symbol, exc)
            return None

        enriched = apply_indicators(history)
        relative_strength = build_relative_strength_snapshot(enriched, benchmark_context)
        match = detect_best_pattern(enriched, relative_strength)
        if match is None:
            return None

        trade_setup = self._build_trade_setup(
            listing=listing,
            frame=enriched,
            match=match,
            investment_amount=request.investment_amount,
            relative_strength=relative_strength,
            benchmark_context=benchmark_context,
        )
        if trade_setup.probability_score < request.min_probability:
            return None
        if trade_setup.risk_reward_ratio < request.min_risk_reward:
            return None

        return trade_setup

    def _build_trade_setup(
        self,
        listing: StockListing,
        frame,
        match: PatternMatch,
        investment_amount: int,
        relative_strength: RelativeStrengthSnapshot,
        benchmark_context: RelativeStrengthContext | None,
    ) -> TradeSetup:
        latest = frame.iloc[-1]
        current_price = float(latest["Close"])
        entry = round(max(current_price, match.trigger_price), 2)
        atr = float(latest["atr14"])
        technical_stop = min(match.support_price * 0.995, entry - atr * 0.8)
        risk = max(entry - technical_stop, atr * 1.1, entry * 0.022)
        stop_loss = round(entry - risk, 2)
        target_price = round(entry + risk * match.reward_multiple, 2)
        risk_reward = round((target_price - entry) / (entry - stop_loss), 2)
        probability = self._score_probability(latest, match)
        trigger_gap = max(0.0, (entry / current_price) - 1)
        probability = max(0.45, round(probability - min(0.14, trigger_gap * 0.7), 3))
        probability = min(
            0.92,
            round(probability + max(-0.04, (relative_strength.score - 0.5) * 0.18), 3),
        )
        expected_return_pct = round(((target_price / entry) - 1) * 100, 2)
        expected_profit_amount = round(
            investment_amount * (expected_return_pct / 100) * probability,
            2,
        )
        ranking_score = round(
            probability * 0.5
            + min(risk_reward / 3.0, 1.0) * 0.18
            + relative_strength.score * 0.32,
            3,
        )

        indicators = IndicatorSnapshot(
            ema20=round(float(latest["ema20"]), 2),
            ema50=round(float(latest["ema50"]), 2),
            ema200=round(float(latest["ema200"]), 2),
            rsi14=round(float(latest["rsi14"]), 2),
            atr14=round(float(atr), 2),
            volume_ratio=round(float(latest["volume_ratio"]), 2),
            price_vs_ema20_pct=round(
                ((entry / float(latest["ema20"])) - 1) * 100,
                2,
            ),
        )

        backtest = backtest_pattern(frame, match.pattern, benchmark_context)
        setup_state = (
            f" Entry trigger sits {(entry / current_price - 1) * 100:.1f}% above the current price."
            if entry > current_price * 1.001
            else " Setup is already near the trigger zone."
        )
        rs_note = (
            f" Relative strength vs {relative_strength.benchmark_name}: "
            f"{relative_strength.excess_return_50d_pct:+.1f}% over 50 sessions "
            f"and {relative_strength.excess_return_120d_pct:+.1f}% over 120 sessions."
        )
        reason = (
            f"{match.explanation} Backtest win rate: {backtest.win_rate:.0%} across "
            f"{backtest.total_trades} historical occurrences.{setup_state}{rs_note}"
            if backtest.total_trades
            else f"{match.explanation} Historical sample is still sparse.{setup_state}{rs_note}"
        )

        return TradeSetup(
            symbol=listing.symbol,
            company_name=listing.company_name,
            sector=listing.sector,
            market_cap_bucket=listing.market_cap_bucket,
            pattern=match.pattern,
            current_price=round(current_price, 2),
            entry_price=round(entry, 2),
            stop_loss=stop_loss,
            target_price=target_price,
            risk_reward_ratio=risk_reward,
            probability_score=probability,
            ranking_score=ranking_score,
            expected_profit_amount=expected_profit_amount,
            expected_return_pct=expected_return_pct,
            confidence_reason=reason,
            indicators=indicators,
            relative_strength=relative_strength,
            backtest=backtest,
        )

    def _score_probability(self, latest, match: PatternMatch) -> float:
        score = match.strength
        score += min(0.05, max(0.0, latest["volume_ratio"] - 1.0) * 0.03)
        if latest["ema20"] > latest["ema50"] > latest["ema200"]:
            score += 0.04
        if 48 <= latest["rsi14"] <= 72:
            score += 0.02
        if match.pattern.value == "support_bounce" and 22 <= latest["rsi14"] <= 48:
            score += 0.03
        if (
            match.pattern.value == "relative_strength_breakout"
            and latest["ema20"] > latest["ema50"]
            and latest["rsi14"] >= 48
        ):
            score += 0.04
        return round(min(score, 0.88), 3)

    def _load_benchmark_context(
        self,
        lookback_days: int,
    ) -> RelativeStrengthContext | None:
        benchmark_listing = get_benchmark_listing()
        try:
            benchmark_frame = self.market_data.get_history(
                benchmark_listing,
                lookback_days=lookback_days,
            )
        except MarketDataError as exc:
            logger.warning(
                "Benchmark data unavailable for %s. Relative strength will be neutral. %s",
                benchmark_listing.symbol,
                exc,
            )
            return None

        return RelativeStrengthContext(
            benchmark_listing=benchmark_listing,
            benchmark_frame=benchmark_frame,
        )


scanner_service = ScannerService()
