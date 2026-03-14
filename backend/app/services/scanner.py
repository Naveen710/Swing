from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
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


@dataclass(frozen=True)
class TradeCandidate:
    listing: StockListing
    match: PatternMatch
    current_price: float
    entry_price: float
    stop_loss: float
    target_price: float
    risk_reward_ratio: float
    probability_score: float
    ranking_score: float
    expected_profit_amount: float
    expected_return_pct: float
    indicators: IndicatorSnapshot
    relative_strength: RelativeStrengthSnapshot
    setup_state: str


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

        if self._should_run_async(listings, request):
            refresh_started = self._start_background_scan(request, listings)
            generated_at, universe_size, scanned_symbols, cached_results = signal_store.snapshot(
                request.max_results
            )
            return ScanResponse(
                generated_at=generated_at or datetime.now(UTC),
                universe_size=universe_size or len(listings),
                scanned_symbols=scanned_symbols,
                results=cached_results,
                from_cache=bool(cached_results),
                refresh_started=refresh_started,
                scan_in_progress=True,
            )

        return self._execute_scan(listings, request)

    def _execute_scan(
        self,
        listings: list[StockListing],
        request: ScanRequest,
    ) -> ScanResponse:
        benchmark_context = self._load_benchmark_context(request.lookback_days)
        candidates = self._collect_candidates(listings, request, benchmark_context)
        candidates.sort(
            key=lambda candidate: (
                candidate.ranking_score,
                candidate.expected_profit_amount,
            ),
            reverse=True,
        )
        limited = [
            self._finalize_trade_setup(candidate, benchmark_context, request.lookback_days)
            for candidate in candidates[: request.max_results]
        ]
        generated_at = datetime.now(UTC)
        signal_store.replace(
            limited,
            generated_at=generated_at,
            universe_size=len(listings),
            scanned_symbols=len(listings),
        )

        return ScanResponse(
            generated_at=generated_at,
            universe_size=len(listings),
            scanned_symbols=len(listings),
            results=limited,
        )

    def latest_signals(self) -> list[TradeSetup]:
        return signal_store.all()

    def scan_status(self):
        (
            scan_in_progress,
            generated_at,
            universe_size,
            scanned_symbols,
            latest_results_count,
        ) = signal_store.status()
        return {
            "scan_in_progress": scan_in_progress,
            "latest_generated_at": generated_at,
            "universe_size": universe_size,
            "scanned_symbols": scanned_symbols,
            "latest_results_count": latest_results_count,
        }

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
    ) -> TradeCandidate | None:
        try:
            history = self.market_data.get_history(
                listing=listing,
                lookback_days=request.lookback_days,
            )
            enriched = apply_indicators(history)
            relative_strength = build_relative_strength_snapshot(enriched, benchmark_context)
            match = detect_best_pattern(enriched, relative_strength)
            if match is None:
                return None

            candidate = self._build_trade_candidate(
                listing=listing,
                frame=enriched,
                match=match,
                investment_amount=request.investment_amount,
                relative_strength=relative_strength,
            )
            if candidate.probability_score < request.min_probability:
                return None
            if candidate.risk_reward_ratio < request.min_risk_reward:
                return None

            return candidate
        except MarketDataError as exc:
            logger.warning("Skipping %s because history loading failed. %s", listing.symbol, exc)
            return None
        except Exception as exc:
            logger.warning(
                "Skipping %s because pattern evaluation failed. %s",
                listing.symbol,
                exc,
            )
            return None

    def _build_trade_candidate(
        self,
        listing: StockListing,
        frame,
        match: PatternMatch,
        investment_amount: int,
        relative_strength: RelativeStrengthSnapshot,
    ) -> TradeCandidate:
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

        setup_state = (
            f" Entry trigger sits {(entry / current_price - 1) * 100:.1f}% above the current price."
            if entry > current_price * 1.001
            else " Setup is already near the trigger zone."
        )
        return TradeCandidate(
            listing=listing,
            match=match,
            current_price=round(current_price, 2),
            entry_price=round(entry, 2),
            stop_loss=stop_loss,
            target_price=target_price,
            risk_reward_ratio=risk_reward,
            probability_score=probability,
            ranking_score=ranking_score,
            expected_profit_amount=expected_profit_amount,
            expected_return_pct=expected_return_pct,
            indicators=indicators,
            relative_strength=relative_strength,
            setup_state=setup_state,
        )

    def _finalize_trade_setup(
        self,
        candidate: TradeCandidate,
        benchmark_context: RelativeStrengthContext | None,
        lookback_days: int,
    ) -> TradeSetup:
        try:
            history = self.market_data.get_history(candidate.listing, lookback_days)
            enriched = apply_indicators(history)
            backtest = backtest_pattern(enriched, candidate.match.pattern, benchmark_context)
        except Exception as exc:
            logger.warning(
                "Unable to load finalized history for %s while building scan output. %s",
                candidate.listing.symbol,
                exc,
            )
            backtest = BacktestStats(
                pattern=candidate.match.pattern,
                total_trades=0,
                win_rate=0.0,
                average_return_pct=0.0,
                max_drawdown_pct=0.0,
                profit_factor=0.0,
            )

        rs_note = (
            f" Relative strength vs {candidate.relative_strength.benchmark_name}: "
            f"{candidate.relative_strength.excess_return_50d_pct:+.1f}% over 50 sessions "
            f"and {candidate.relative_strength.excess_return_120d_pct:+.1f}% over 120 sessions."
        )
        reason = (
            f"{candidate.match.explanation} Backtest win rate: {backtest.win_rate:.0%} across "
            f"{backtest.total_trades} historical occurrences.{candidate.setup_state}{rs_note}"
            if backtest.total_trades
            else (
                f"{candidate.match.explanation} Historical sample is still sparse."
                f"{candidate.setup_state}{rs_note}"
            )
        )

        return TradeSetup(
            symbol=candidate.listing.symbol,
            company_name=candidate.listing.company_name,
            sector=candidate.listing.sector,
            market_cap_bucket=candidate.listing.market_cap_bucket,
            pattern=candidate.match.pattern,
            current_price=candidate.current_price,
            entry_price=candidate.entry_price,
            stop_loss=candidate.stop_loss,
            target_price=candidate.target_price,
            risk_reward_ratio=candidate.risk_reward_ratio,
            probability_score=candidate.probability_score,
            ranking_score=candidate.ranking_score,
            expected_profit_amount=candidate.expected_profit_amount,
            expected_return_pct=candidate.expected_return_pct,
            confidence_reason=reason,
            indicators=candidate.indicators,
            relative_strength=candidate.relative_strength,
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

    def _collect_candidates(
        self,
        listings: list[StockListing],
        request: ScanRequest,
        benchmark_context: RelativeStrengthContext | None,
    ) -> list[TradeCandidate]:
        if len(listings) < settings.async_scan_universe_threshold:
            self._prefetch_histories(listings, request.lookback_days)
            with ThreadPoolExecutor(max_workers=settings.scan_workers) as executor:
                results = list(
                    executor.map(
                        lambda listing: self._scan_listing(listing, request, benchmark_context),
                        listings,
                    )
                )
            return [candidate for candidate in results if candidate is not None]

        candidates: list[TradeCandidate] = []
        scanned_symbols = 0
        chunk_size = max(5, min(settings.yahoo_batch_size, 25))

        for chunk in self._chunk_listings(listings, chunk_size):
            self._prefetch_histories(chunk, request.lookback_days)
            with ThreadPoolExecutor(
                max_workers=max(1, min(settings.scan_workers, len(chunk)))
            ) as executor:
                results = list(
                    executor.map(
                        lambda listing: self._scan_listing(listing, request, benchmark_context),
                        chunk,
                    )
                )

            candidates.extend(candidate for candidate in results if candidate is not None)
            scanned_symbols += len(chunk)
            signal_store.update_progress(
                scanned_symbols=scanned_symbols,
                universe_size=len(listings),
            )

        return candidates

    def _prefetch_histories(
        self,
        listings: list[StockListing],
        lookback_days: int,
    ) -> None:
        try:
            self.market_data.prefetch_histories(listings, lookback_days)
        except AttributeError:
            logger.info("Market data provider does not support batch prefetch.")
        except Exception as exc:
            logger.warning(
                "Batch market-data prefetch failed. Continuing with on-demand loads. %s",
                exc,
            )

    def _should_run_async(
        self,
        listings: list[StockListing],
        request: ScanRequest,
    ) -> bool:
        return (
            len(listings) >= settings.async_scan_universe_threshold
            and request.symbols is None
            and request.sectors is None
            and request.market_caps is None
        )

    def _start_background_scan(
        self,
        request: ScanRequest,
        listings: list[StockListing],
    ) -> bool:
        if not signal_store.begin_scan(universe_size=len(listings)):
            return False

        thread = threading.Thread(
            target=self._background_scan,
            args=(request, listings),
            daemon=True,
        )
        thread.start()
        return True

    def _background_scan(
        self,
        request: ScanRequest,
        listings: list[StockListing],
    ) -> None:
        try:
            if listings:
                self._execute_scan(listings, request)
            else:
                signal_store.finish_scan()
        except Exception as exc:
            logger.exception("Background scan failed. %s", exc)
            signal_store.finish_scan()

    def _chunk_listings(
        self,
        listings: list[StockListing],
        chunk_size: int,
    ) -> list[list[StockListing]]:
        return [
            listings[index : index + chunk_size]
            for index in range(0, len(listings), chunk_size)
        ]


scanner_service = ScannerService()
