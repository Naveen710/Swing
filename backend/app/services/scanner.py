from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

from app.config import settings
from app.schemas import (
    AccumulationSnapshot,
    BacktestStats,
    EventRiskSnapshot,
    FundamentalSnapshot,
    IndicatorSnapshot,
    LiquiditySnapshot,
    MarketRegimeSnapshot,
    PeerRankSnapshot,
    PriceLevelSnapshot,
    RelativeStrengthSnapshot,
    RsLineSnapshot,
    SectorRotationSnapshot,
    SmartMoneySnapshot,
    ScanRequest,
    ScanResponse,
    ScanUniverse,
    SectorStrengthSnapshot,
    StockDetailResponse,
    TradeSetup,
    WeeklyTrendSnapshot,
)
from app.services.backtest import backtest_pattern
from app.services.delivery_data import DeliveryTrend, NseDeliveryDataProvider
from app.services.event_risk import YahooEventRiskProvider
from app.services.fundamentals import YahooFundamentalsProvider, unknown_fundamentals
from app.services.market_regime import MarketRegimeService
from app.services.bulk_deals import BulkDealProvider
from app.services.trend_overlays import (
    build_peer_ranks,
    build_price_levels,
    build_rs_line,
    build_sector_rotation,
    build_weekly_trend,
    trailing_returns,
)
from app.services.indicators import apply_indicators
from app.services.market_data import MarketDataError, create_market_data_provider
from app.services.patterns import PatternMatch, detect_best_pattern
from app.services.relative_strength import (
    RelativeStrengthContext,
    build_relative_strength_snapshot,
)
from app.services.selection_overlays import (
    build_accumulation_snapshot,
    build_liquidity_snapshot,
    build_sector_observation,
    build_sector_strength_map,
)
from app.services.store import signal_store
from app.services.trade_sim import TradePlan, plan_is_tradeable, plan_trade
from app.services.data_quality import data_quality_issue
from app.services.calibration import Calibrator
from app.services.ledger import SignalLedger
from app.services.universe import (
    StockListing,
    get_benchmark_candidates,
    find_listing,
    load_universe,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeCandidate:
    listing: StockListing
    match: PatternMatch
    reference_date: date
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
    liquidity: LiquiditySnapshot
    accumulation: AccumulationSnapshot
    sector_strength: SectorStrengthSnapshot
    event_risk: EventRiskSnapshot
    setup_state: str
    price_levels: PriceLevelSnapshot | None = None
    weekly_trend: WeeklyTrendSnapshot | None = None
    peer_rank: PeerRankSnapshot | None = None
    fundamentals: FundamentalSnapshot | None = None
    rs_line: RsLineSnapshot | None = None
    sector_rotation: SectorRotationSnapshot | None = None
    smart_money: SmartMoneySnapshot | None = None


@dataclass
class ScanSink:
    """Collects universe-wide facts while scanning (peer RS scores and market breadth)."""

    scores: dict[str, tuple[str, float]] = field(default_factory=dict)
    returns: dict[str, tuple[str, float, float]] = field(default_factory=dict)
    benchmark_returns: tuple[float, float] | None = None
    above_ema50: int = 0
    total: int = 0
    rejected: dict[str, str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reject(self, symbol: str, reason: str) -> None:
        with self.lock:
            self.rejected[symbol] = reason

    def record(
        self,
        symbol: str,
        sector: str,
        rs_score: float,
        above_ema50: bool,
        trailing: tuple[float, float] | None = None,
    ) -> None:
        with self.lock:
            self.scores[symbol] = (sector, rs_score)
            if trailing is not None:
                self.returns[symbol] = (sector, trailing[0], trailing[1])
            self.total += 1
            if above_ema50:
                self.above_ema50 += 1


@dataclass
class ActiveScanState:
    request: ScanRequest
    listings: list[StockListing]
    worker_count: int
    benchmark_context: RelativeStrengthContext | None = None
    delivery_trends: dict[str, DeliveryTrend] = field(default_factory=dict)
    candidates: list[TradeCandidate] = field(default_factory=list)
    cursor: int = 0
    sink: ScanSink = field(default_factory=ScanSink)


class ScannerService:
    def __init__(self) -> None:
        self.market_data = create_market_data_provider()
        self.delivery_data = NseDeliveryDataProvider()
        self.event_risk = YahooEventRiskProvider()
        self.fundamentals = YahooFundamentalsProvider()
        self.regime = MarketRegimeService(self.market_data)
        self.bulk_deals = BulkDealProvider()
        self._sector_rotation: dict[str, tuple[datetime, list[SectorRotationSnapshot]]] = {}
        self.last_rejected: dict[str, str] = {}
        self.ledger = SignalLedger(self.market_data)
        self.calibration = Calibrator()
        self._active_scan: ActiveScanState | None = None
        self._active_scan_lock = threading.Lock()

    def list_stocks(self, universe: ScanUniverse = ScanUniverse.NIFTY500) -> list[dict[str, str]]:
        return [
            listing.to_summary().model_dump()
            for listing in load_universe(universe=universe)
        ]

    def run_scan(self, request: ScanRequest) -> ScanResponse:
        listings = load_universe(
            universe=request.universe,
            symbols=request.symbols,
            sectors=request.sectors,
            market_caps=request.market_caps,
        )
        if not listings:
            return ScanResponse(
                universe=request.universe,
                generated_at=datetime.now(UTC),
                universe_size=0,
                scanned_symbols=0,
                results=[],
            )

        if self._should_run_async(listings, request):
            refresh_started = self._start_incremental_scan(request, listings)
            (
                generated_at,
                cached_universe_size,
                scanned_symbols,
                cached_results,
            ) = signal_store.snapshot(
                request.universe,
                request.max_results
            )
            status_universe, _, _, status_universe_size, status_scanned_symbols, _ = (
                signal_store.status()
            )
            if status_universe != request.universe:
                scanned_symbols = 0
                universe_size = len(listings)
            else:
                scanned_symbols = status_scanned_symbols
                universe_size = (
                    status_universe_size
                    or cached_universe_size
                    or len(listings)
                )
            return ScanResponse(
                universe=request.universe,
                generated_at=generated_at or datetime.now(UTC),
                universe_size=universe_size,
                scanned_symbols=scanned_symbols,
                results=cached_results,
                from_cache=bool(cached_results),
                refresh_started=refresh_started,
                scan_in_progress=True,
            )

        return self._execute_scan(listings, request)

    def run_scan_sync(self, request: ScanRequest) -> ScanResponse:
        listings = load_universe(
            universe=request.universe,
            symbols=request.symbols,
            sectors=request.sectors,
            market_caps=request.market_caps,
        )
        if not listings:
            return ScanResponse(
                universe=request.universe,
                generated_at=datetime.now(UTC),
                universe_size=0,
                scanned_symbols=0,
                results=[],
            )
        return self._execute_scan(listings, request)

    def _execute_scan(
        self,
        listings: list[StockListing],
        request: ScanRequest,
    ) -> ScanResponse:
        benchmark_context = self._load_benchmark_context(request.lookback_days)
        delivery_trends = self._load_delivery_trends(benchmark_context)
        sink = ScanSink(benchmark_returns=self._benchmark_returns(benchmark_context))
        candidates = self._collect_candidates(
            listings,
            request,
            benchmark_context,
            delivery_trends,
            sink,
        )
        self.regime.record_scan_breadth(sink.above_ema50, sink.total)
        self.last_rejected = dict(sink.rejected)
        candidates = self._apply_candidate_overlays(candidates, request, sink)
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
            universe=request.universe,
            generated_at=generated_at,
            universe_size=len(listings),
            scanned_symbols=len(listings),
        )
        self._record_to_ledger(limited, request.universe)

        return ScanResponse(
            universe=request.universe,
            generated_at=generated_at,
            universe_size=len(listings),
            scanned_symbols=len(listings),
            results=limited,
        )

    def latest_signals(
        self,
        universe: ScanUniverse | None = None,
    ) -> list[TradeSetup]:
        return signal_store.all(universe=universe)

    def market_regime(self, force: bool = False) -> MarketRegimeSnapshot:
        return self.regime.get_snapshot(force=force)

    def scan_status(self):
        (
            universe,
            scan_in_progress,
            generated_at,
            universe_size,
            scanned_symbols,
            latest_results_count,
        ) = signal_store.status()
        return {
            "universe": universe,
            "scan_in_progress": scan_in_progress,
            "latest_generated_at": generated_at,
            "universe_size": universe_size,
            "scanned_symbols": scanned_symbols,
            "latest_results_count": latest_results_count,
            "data_rejected": len(self.last_rejected),
            "data_rejected_examples": [
                f"{symbol}: {reason}" for symbol, reason in list(self.last_rejected.items())[:8]
            ],
        }

    def get_stock_detail(self, symbol: str) -> StockDetailResponse | None:
        listing = find_listing(symbol)
        if listing is None:
            return None

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
        listing = find_listing(symbol)
        if listing is None:
            return None

        try:
            history = self.market_data.get_history(listing)
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
        delivery_trends: dict[str, DeliveryTrend],
        sink: ScanSink | None = None,
    ) -> TradeCandidate | None:
        try:
            history = self.market_data.get_history(
                listing=listing,
                lookback_days=request.lookback_days,
            )
            issue = data_quality_issue(history, self._market_reference_date(benchmark_context))
            if issue is not None:
                if sink is not None:
                    sink.reject(listing.symbol, issue)
                logger.info("Skipping %s: %s", listing.symbol, issue)
                return None
            enriched = apply_indicators(history)
            relative_strength = build_relative_strength_snapshot(enriched, benchmark_context)
            if sink is not None and len(enriched) >= 60:
                latest_row = enriched.iloc[-1]
                sink.record(
                    listing.symbol,
                    listing.sector,
                    relative_strength.score,
                    bool(latest_row["Close"] > latest_row["ema50"]),
                    trailing_returns(enriched["Close"]),
                )
            match = detect_best_pattern(enriched, relative_strength)
            if match is None:
                return None

            candidate = self._build_trade_candidate(
                listing=listing,
                frame=enriched,
                match=match,
                investment_amount=request.investment_amount,
                relative_strength=relative_strength,
                delivery_trends=delivery_trends,
                benchmark_close=(
                    benchmark_context.benchmark_frame["Close"] if benchmark_context is not None else None
                ),
            )
            if not plan_is_tradeable(
                TradePlan(candidate.entry_price, candidate.stop_loss, candidate.target_price)
            ):
                return None
            if (
                request.universe == ScanUniverse.MID_SMALL_2000_PLUS
                and not candidate.liquidity.passes_filter
            ):
                return None
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
        delivery_trends: dict[str, DeliveryTrend],
        benchmark_close=None,
    ) -> TradeCandidate:
        latest = frame.iloc[-1]
        liquidity = build_liquidity_snapshot(frame)
        accumulation = build_accumulation_snapshot(
            frame,
            delivery_trend=delivery_trends.get(listing.symbol.upper()),
        )
        current_price = float(latest["Close"])
        atr = float(latest["atr14"])
        plan = plan_trade(latest, match.trigger_price, match.support_price, match.reward_multiple)
        entry, stop_loss, target_price = plan.entry, plan.stop, plan.target
        risk_reward = round((target_price - entry) / (entry - stop_loss), 2)
        probability = self._score_probability(latest, match)
        trigger_gap = max(0.0, (entry / current_price) - 1)
        probability = max(0.45, round(probability - min(0.14, trigger_gap * 0.7), 3))
        probability = min(
            0.92,
            round(probability + max(-0.04, (relative_strength.score - 0.5) * 0.18), 3),
        )
        probability = min(
            0.95,
            round(
                probability
                + max(-0.03, (accumulation.score - 0.5) * 0.08)
                + max(-0.02, (liquidity.score - 0.5) * 0.04),
                3,
            ),
        )
        price_levels = build_price_levels(frame)
        weekly_trend = build_weekly_trend(frame)
        overlay_prob, overlay_rank = self._trend_overlay_adjustments(
            price_levels, weekly_trend, float(latest["volume_ratio"])
        )
        rs_line = build_rs_line(frame, benchmark_close)
        if rs_line is not None and rs_line.leads_price:
            overlay_prob += 0.03
            overlay_rank += 0.04
        elif rs_line is not None and rs_line.rs_line_new_high:
            overlay_prob += 0.01
            overlay_rank += 0.01
        smart_money = self._delivery_signal(frame, delivery_trends.get(listing.symbol.upper()))
        if smart_money.delivery_spike and smart_money.breakout:
            overlay_prob += 0.04
            overlay_rank += 0.05
        elif smart_money.delivery_spike:
            overlay_prob += 0.01
            overlay_rank += 0.01
        probability = round(min(0.95, max(0.35, probability + overlay_prob)), 3)
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
        ranking_score = round(
            ranking_score
            + accumulation.score * 0.08
            + liquidity.score * 0.05
            + overlay_rank,
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
            reference_date=frame.index[-1].date(),
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
            liquidity=liquidity,
            accumulation=accumulation,
            sector_strength=self._neutral_sector_strength(listing.sector),
            event_risk=self._neutral_event_risk(),
            setup_state=setup_state,
            price_levels=price_levels,
            weekly_trend=weekly_trend,
            rs_line=rs_line,
            smart_money=smart_money,
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
                target_hit_rate=0.0,
                average_holding_sessions=0.0,
                average_target_sessions=None,
            )

        estimated_target_sessions = self._estimate_target_sessions(candidate, backtest)
        estimated_target_date = self._project_target_date(
            candidate.reference_date,
            estimated_target_sessions,
        )

        rs_note = (
            f" Relative strength vs {candidate.relative_strength.benchmark_name}: "
            f"{candidate.relative_strength.excess_return_50d_pct:+.1f}% over 50 sessions "
            f"and {candidate.relative_strength.excess_return_120d_pct:+.1f}% over 120 sessions."
        )
        liquidity_note = (
            f" Liquidity: average traded value is "
            f"{candidate.liquidity.average_traded_value_20d_cr:.1f} Cr over 20 sessions."
        )
        sector_note = (
            f" Sector rank: {candidate.sector_strength.rank}/{candidate.sector_strength.sector_count} "
            f"with sector strength score {candidate.sector_strength.score:.2f}."
        )
        accumulation_note = self._build_accumulation_note(candidate.accumulation)
        event_note = (
            f" Earnings risk: {candidate.event_risk.risk_level}"
            + (
                f", {candidate.event_risk.days_to_earnings} days to results."
                if candidate.event_risk.days_to_earnings is not None
                else ", upcoming results date unavailable."
            )
        )
        timing_note = (
            f" Estimated target window: about {estimated_target_sessions} trading sessions, "
            f"which points to {estimated_target_date.strftime('%d %b %Y')} if the setup follows "
            f"its recent pace."
        )
        reason = (
            f"{candidate.match.explanation} Backtest win rate: {backtest.win_rate:.0%} across "
            f"{backtest.total_trades} historical occurrences.{candidate.setup_state}"
            f"{timing_note}{rs_note}{sector_note}{liquidity_note}{accumulation_note}{event_note}"
            if backtest.total_trades
            else (
                f"{candidate.match.explanation} Historical sample is still sparse."
                f"{candidate.setup_state}{timing_note}{rs_note}{sector_note}"
                f"{liquidity_note}{accumulation_note}{event_note}"
            )
        )

        quality_flags = self._build_quality_flags(candidate, estimated_target_sessions)
        historical_win_rate, calibration_samples = self.calibration.predict(candidate.ranking_score)
        reason += self._build_overlay_note(candidate)

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
            estimated_target_sessions=estimated_target_sessions,
            estimated_target_date=estimated_target_date,
            confidence_reason=reason,
            indicators=candidate.indicators,
            relative_strength=candidate.relative_strength,
            liquidity=candidate.liquidity,
            accumulation=candidate.accumulation,
            sector_strength=candidate.sector_strength,
            event_risk=candidate.event_risk,
            backtest=backtest,
            price_levels=candidate.price_levels,
            weekly_trend=candidate.weekly_trend,
            peer_rank=candidate.peer_rank,
            fundamentals=candidate.fundamentals,
            quality_flags=quality_flags,
            rs_line=candidate.rs_line,
            sector_rotation=candidate.sector_rotation,
            smart_money=candidate.smart_money,
            signal_date=candidate.reference_date,
            historical_win_rate=historical_win_rate,
            calibration_samples=calibration_samples,
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

    def _apply_candidate_overlays(
        self,
        candidates: list[TradeCandidate],
        request: ScanRequest,
        sink: ScanSink | None = None,
    ) -> list[TradeCandidate]:
        if not candidates:
            return []

        sector_map = build_sector_strength_map(
            [
                build_sector_observation(
                    candidate.listing.sector,
                    candidate.relative_strength,
                    candidate.liquidity,
                    candidate.accumulation,
                )
                for candidate in candidates
            ]
        )

        enriched = [
            replace(
                candidate,
                sector_strength=sector_map.get(
                    candidate.listing.sector,
                    self._neutral_sector_strength(candidate.listing.sector),
                ),
            )
            for candidate in candidates
        ]

        if sink is not None and sink.scores:
            enriched = self._apply_peer_rank_overlay(enriched, sink)
        if sink is not None and sink.returns and sink.benchmark_returns is not None:
            enriched = self._apply_sector_rotation_overlay(enriched, sink, request.universe)

        if request.universe == ScanUniverse.MID_SMALL_2000_PLUS:
            enriched = [self._apply_advanced_discovery_score(candidate) for candidate in enriched]

        if self._external_overlays_enabled():
            enriched = self._apply_event_risk_overlay(enriched)
            enriched = self._apply_fundamentals_overlay(enriched)
            enriched = self._apply_bulk_deal_overlay(enriched)

        return [
            candidate
            for candidate in enriched
            if candidate.probability_score >= request.min_probability
        ]

    def _record_to_ledger(self, setups: list[TradeSetup], universe: ScanUniverse) -> None:
        """Every published pick goes into the ledger so its real outcome can be graded later."""
        try:
            regime = self.regime.get_snapshot().regime
        except Exception:  # noqa: BLE001
            regime = None
        try:
            self.ledger.record(setups, universe.value, regime)
            self.ledger.evaluate_in_background()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write signals to the ledger. %s", exc)

    def _market_reference_date(self, benchmark_context: RelativeStrengthContext | None) -> date:
        """Latest session date: the benchmark's last bar, or today if the benchmark is unavailable."""
        if benchmark_context is not None and not benchmark_context.benchmark_frame.empty:
            return benchmark_context.benchmark_frame.index[-1].date()
        return datetime.now(UTC).date()

    def _benchmark_returns(self, benchmark_context) -> tuple[float, float] | None:
        if benchmark_context is None:
            return None
        return trailing_returns(benchmark_context.benchmark_frame["Close"])

    def sector_rotation(self, universe: ScanUniverse) -> tuple[datetime | None, list[SectorRotationSnapshot]]:
        return self._sector_rotation.get(universe.value, (None, []))

    def _apply_sector_rotation_overlay(
        self,
        candidates: list[TradeCandidate],
        sink: ScanSink,
        universe: ScanUniverse,
    ) -> list[TradeCandidate]:
        with sink.lock:
            returns = dict(sink.returns)
        rotation = build_sector_rotation(returns, sink.benchmark_returns)
        self._sector_rotation[universe.value] = (
            datetime.now(UTC),
            sorted(rotation.values(), key=lambda r: r.rank),
        )
        adjusted: list[TradeCandidate] = []
        for candidate in candidates:
            snap = rotation.get(candidate.listing.sector)
            if snap is None:
                adjusted.append(candidate)
                continue
            adj = 0.03 if snap.leading else -0.03 if snap.lagging else 0.0
            adjusted.append(
                replace(
                    candidate,
                    sector_rotation=snap,
                    probability_score=round(min(0.95, max(0.35, candidate.probability_score + adj)), 3),
                    ranking_score=round(candidate.ranking_score + adj * 1.3, 3),
                )
            )
        return adjusted

    def _delivery_signal(self, frame, trend: DeliveryTrend | None) -> SmartMoneySnapshot:
        """Delivery spike: today's delivery % at least 1.5x its 10-day average (and >= 40%)."""
        prior_high = float(frame["High"].iloc[-21:-1].max()) if len(frame) > 21 else float("inf")
        breakout = bool(float(frame["Close"].iloc[-1]) > prior_high)
        if trend is None or trend.average_delivery_pct_10d <= 0:
            return SmartMoneySnapshot(breakout=breakout)
        ratio = trend.latest_delivery_pct / trend.average_delivery_pct_10d
        return SmartMoneySnapshot(
            delivery_spike=bool(trend.latest_delivery_pct >= 40 and ratio >= 1.5),
            delivery_ratio=round(ratio, 2),
            latest_delivery_pct=round(trend.latest_delivery_pct, 1),
            breakout=breakout,
        )

    def _apply_bulk_deal_overlay(self, candidates: list[TradeCandidate]) -> list[TradeCandidate]:
        try:
            self.bulk_deals.refresh()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bulk deal refresh failed. %s", exc)
        if not self.bulk_deals.available:
            return candidates
        adjusted: list[TradeCandidate] = []
        for candidate in candidates:
            deals = self.bulk_deals.summary_for(candidate.listing.symbol)
            base = candidate.smart_money or SmartMoneySnapshot()
            smart = base.model_copy(update={
                "bulk_deal_buys": deals["buys"], "bulk_deal_sells": deals["sells"],
                "bulk_deal_net_qty": deals["net_qty"], "bulk_deal_source": deals["source"],
            })
            adj = 0.0
            if deals["buys"] and deals["net_qty"] > 0:
                adj = 0.02
            elif deals["sells"] and deals["net_qty"] < 0:
                adj = -0.03
            adjusted.append(
                replace(
                    candidate,
                    smart_money=smart,
                    probability_score=round(min(0.95, max(0.35, candidate.probability_score + adj)), 3),
                    ranking_score=round(candidate.ranking_score + adj, 3),
                )
            )
        return adjusted

    def _external_overlays_enabled(self) -> bool:
        return settings.enable_external_overlays and settings.market_data_provider != "demo"

    def _trend_overlay_adjustments(
        self,
        price_levels: PriceLevelSnapshot | None,
        weekly_trend: WeeklyTrendSnapshot | None,
        volume_ratio: float,
    ) -> tuple[float, float]:
        prob = rank = 0.0
        if price_levels is not None:
            if price_levels.near_52w_high:
                bump = 0.05 if volume_ratio >= 1.2 else 0.02
                prob += bump
                rank += bump
            if price_levels.price_discovery:
                prob += 0.03
                rank += 0.04
        if weekly_trend is not None:
            if weekly_trend.aligned:
                prob += 0.02
                rank += 0.02
            else:
                prob -= 0.06
                rank -= 0.06
        return prob, rank

    def _apply_peer_rank_overlay(
        self,
        candidates: list[TradeCandidate],
        sink: ScanSink,
    ) -> list[TradeCandidate]:
        with sink.lock:
            scores = dict(sink.scores)
        ranks = build_peer_ranks(scores, [c.listing.symbol for c in candidates])
        adjusted: list[TradeCandidate] = []
        for candidate in candidates:
            peer = ranks.get(candidate.listing.symbol)
            if peer is None:
                adjusted.append(candidate)
                continue
            prob_adj = 0.02 if peer.sector_leader else -0.03 if peer.sector_laggard else 0.0
            rank_adj = 0.03 if peer.sector_leader else -0.03 if peer.sector_laggard else 0.0
            adjusted.append(
                replace(
                    candidate,
                    peer_rank=peer,
                    probability_score=round(
                        min(0.95, max(0.35, candidate.probability_score + prob_adj)), 3
                    ),
                    ranking_score=round(candidate.ranking_score + rank_adj, 3),
                )
            )
        return adjusted

    def _apply_fundamentals_overlay(
        self,
        candidates: list[TradeCandidate],
    ) -> list[TradeCandidate]:
        ordered = sorted(candidates, key=lambda c: c.ranking_score, reverse=True)
        review = ordered[: max(0, settings.fundamentals_review_limit)]
        if not review:
            return candidates
        with ThreadPoolExecutor(max_workers=max(1, settings.overlay_workers)) as executor:
            snapshots = dict(
                zip(
                    [c.listing.symbol for c in review],
                    executor.map(lambda c: self.fundamentals.get_snapshot(c.listing.symbol), review),
                )
            )
        adjusted: list[TradeCandidate] = []
        for candidate in candidates:
            snapshot = snapshots.get(candidate.listing.symbol)
            if snapshot is None:
                adjusted.append(replace(candidate, fundamentals=unknown_fundamentals("not_reviewed")))
                continue
            prob_adj = rank_adj = 0.0
            if snapshot.passes is False:
                prob_adj, rank_adj = -0.06, -0.06
            elif snapshot.passes and snapshot.quality_score >= 3:
                prob_adj, rank_adj = 0.01, 0.02
            adjusted.append(
                replace(
                    candidate,
                    fundamentals=snapshot,
                    probability_score=round(
                        min(0.95, max(0.35, candidate.probability_score + prob_adj)), 3
                    ),
                    ranking_score=round(candidate.ranking_score + rank_adj, 3),
                )
            )
        return adjusted

    def _build_quality_flags(
        self,
        candidate: TradeCandidate,
        estimated_target_sessions: int,
    ) -> list[str]:
        flags: list[str] = []
        event = candidate.event_risk
        if event.blackout and event.days_to_earnings is not None:
            if event.days_to_earnings >= 0:
                flags.append(f"Earnings in {event.days_to_earnings} days — blackout")
            else:
                flags.append("Results just announced — cooling off")
        if (
            event.days_to_ex_dividend is not None
            and event.days_to_ex_dividend <= round(estimated_target_sessions * 1.45)
        ):
            flags.append(
                f"Ex-dividend {event.ex_dividend_date.strftime('%d %b')} inside the trade window"
            )
        if candidate.weekly_trend is not None and not candidate.weekly_trend.aligned:
            flags.append("Weekly trend not aligned")
        if candidate.peer_rank is not None and candidate.peer_rank.sector_laggard:
            peers = ", ".join(candidate.peer_rank.top_peers) or "sector leaders"
            flags.append(f"Sector laggard — stronger peers: {peers}")
        rotation = candidate.sector_rotation
        if rotation is not None and rotation.lagging:
            flags.append(f"{rotation.sector} is lagging the market (sector rank {rotation.rank}/{rotation.sector_count})")
        smart = candidate.smart_money
        if smart is not None and smart.bulk_deal_sells and smart.bulk_deal_net_qty < 0:
            flags.append("Net bulk/block selling in the last 10 sessions")
        fundamentals = candidate.fundamentals
        if fundamentals is not None and fundamentals.passes is False:
            flags.append(
                f"Weak fundamentals ({fundamentals.quality_score}/{fundamentals.checks_available})"
            )
        return flags

    def _build_overlay_note(self, candidate: TradeCandidate) -> str:
        parts: list[str] = []
        levels = candidate.price_levels
        if levels is not None:
            if levels.price_discovery:
                parts.append("Price discovery: fresh 52-week high within the last 5 sessions.")
            elif levels.near_52w_high:
                parts.append(
                    f"Trading {levels.distance_from_52w_high_pct:.1f}% below its 52-week high."
                )
        weekly = candidate.weekly_trend
        if weekly is not None:
            parts.append(
                f"Weekly chart {'confirms' if weekly.aligned else 'does not confirm'} "
                f"({weekly.checks_passed}/3 checks, weekly RSI {weekly.weekly_rsi14:.0f})."
            )
        peer = candidate.peer_rank
        if peer is not None:
            parts.append(f"Sector RS rank {peer.rank}/{peer.peer_count} in {peer.sector}.")
        rotation = candidate.sector_rotation
        if rotation is not None and rotation.leading:
            parts.append(f"{rotation.sector} ranks #{rotation.rank} of {rotation.sector_count} sectors on 1- and 3-month relative strength.")
        if candidate.rs_line is not None and candidate.rs_line.leads_price:
            parts.append("Relative-strength line is at a new high while price is not yet — a leadership tell.")
        smart = candidate.smart_money
        if smart is not None and smart.delivery_spike:
            parts.append(f"Delivery spiked to {smart.latest_delivery_pct:.0f}% ({smart.delivery_ratio:.1f}x its 10-day average).")
        if smart is not None and smart.bulk_deal_buys and smart.bulk_deal_net_qty > 0:
            parts.append("Net bulk/block buying in the last 10 sessions.")
        return (" " + " ".join(parts)) if parts else ""

    def _apply_advanced_discovery_score(
        self,
        candidate: TradeCandidate,
    ) -> TradeCandidate:
        probability_score = round(
            min(
                0.95,
                max(
                    0.4,
                    candidate.probability_score
                    + (candidate.sector_strength.score - 0.5) * 0.12
                    + (candidate.accumulation.score - 0.5) * 0.08
                    + (candidate.liquidity.score - 0.5) * 0.05,
                ),
            ),
            3,
        )
        ranking_score = round(
            candidate.ranking_score
            + (candidate.sector_strength.score - 0.5) * 0.28
            + (candidate.accumulation.score - 0.5) * 0.16
            + (candidate.liquidity.score - 0.5) * 0.08,
            3,
        )
        expected_profit_amount = round(
            candidate.expected_profit_amount
            * (probability_score / max(candidate.probability_score, 0.01)),
            2,
        )
        return replace(
            candidate,
            probability_score=probability_score,
            ranking_score=ranking_score,
            expected_profit_amount=expected_profit_amount,
        )

    def _apply_event_risk_overlay(
        self,
        candidates: list[TradeCandidate],
    ) -> list[TradeCandidate]:
        if not candidates:
            return []

        sorted_candidates = sorted(
            candidates,
            key=lambda candidate: (
                candidate.ranking_score,
                candidate.expected_profit_amount,
            ),
            reverse=True,
        )
        review_limit = min(len(sorted_candidates), settings.event_risk_review_limit)
        reviewed: dict[str, TradeCandidate] = {}

        to_review = sorted_candidates[:review_limit]
        with ThreadPoolExecutor(max_workers=max(1, settings.overlay_workers)) as executor:
            event_snapshots = list(
                executor.map(
                    lambda c: self.event_risk.get_snapshot(c.listing.symbol, c.reference_date),
                    to_review,
                )
            )

        for candidate, event_risk in zip(to_review, event_snapshots):
            probability_score = round(
                max(0.35, candidate.probability_score - event_risk.ranking_penalty * 0.35),
                3,
            )
            ranking_score = round(
                candidate.ranking_score - event_risk.ranking_penalty,
                3,
            )
            expected_profit_amount = round(
                candidate.expected_profit_amount
                * (probability_score / max(candidate.probability_score, 0.01)),
                2,
            )
            reviewed[candidate.listing.symbol.upper()] = replace(
                candidate,
                probability_score=probability_score,
                ranking_score=ranking_score,
                expected_profit_amount=expected_profit_amount,
                event_risk=event_risk,
            )

        return [
            reviewed.get(candidate.listing.symbol.upper(), candidate)
            for candidate in candidates
        ]

    def _neutral_sector_strength(self, sector: str) -> SectorStrengthSnapshot:
        return SectorStrengthSnapshot(
            sector=sector,
            score=0.5,
            rank=1,
            sector_count=1,
            average_relative_strength_score=0.5,
            average_excess_return_50d_pct=0.0,
            average_excess_return_120d_pct=0.0,
        )

    def _neutral_event_risk(self) -> EventRiskSnapshot:
        return EventRiskSnapshot(
            earnings_date=None,
            days_to_earnings=None,
            risk_level="unknown",
            ranking_penalty=0.0,
        )

    def _load_benchmark_context(
        self,
        lookback_days: int,
    ) -> RelativeStrengthContext | None:
        failures: list[str] = []
        for benchmark_listing in get_benchmark_candidates():
            try:
                benchmark_frame = self.market_data.get_history(
                    benchmark_listing,
                    lookback_days=lookback_days,
                )
                return RelativeStrengthContext(
                    benchmark_listing=benchmark_listing,
                    benchmark_frame=benchmark_frame,
                )
            except MarketDataError as exc:
                failures.append(f"{benchmark_listing.symbol}: {exc}")

        logger.warning(
            "Benchmark data unavailable for all configured symbols. Relative strength will be neutral. %s",
            " | ".join(failures),
        )
        return None

    def _load_delivery_trends(
        self,
        benchmark_context: RelativeStrengthContext | None,
    ) -> dict[str, DeliveryTrend]:
        reference_date = self._resolve_market_reference_date(benchmark_context)
        try:
            return self.delivery_data.get_recent_delivery_trends(reference_date)
        except Exception as exc:
            logger.warning(
                "Unable to load recent NSE delivery data for %s. Falling back to proxy accumulation. %s",
                reference_date,
                exc,
            )
            return {}

    def _resolve_market_reference_date(
        self,
        benchmark_context: RelativeStrengthContext | None,
    ) -> date:
        if benchmark_context is not None and not benchmark_context.benchmark_frame.empty:
            return benchmark_context.benchmark_frame.index[-1].date()
        return datetime.now(UTC).date()

    def _collect_candidates(
        self,
        listings: list[StockListing],
        request: ScanRequest,
        benchmark_context: RelativeStrengthContext | None,
        delivery_trends: dict[str, DeliveryTrend],
        sink: ScanSink | None = None,
    ) -> list[TradeCandidate]:
        return self._scan_chunk(
            listings,
            request,
            benchmark_context,
            delivery_trends,
            worker_count=self._resolve_worker_count(request, len(listings)),
            sink=sink,
        )

    def _scan_chunk(
        self,
        listings: list[StockListing],
        request: ScanRequest,
        benchmark_context: RelativeStrengthContext | None,
        delivery_trends: dict[str, DeliveryTrend],
        worker_count: int | None = None,
        sink: ScanSink | None = None,
    ) -> list[TradeCandidate]:
        self._prefetch_histories(listings, request.lookback_days)
        with ThreadPoolExecutor(
            max_workers=max(
                1,
                min(worker_count or settings.scan_workers, len(listings)),
            )
        ) as executor:
            results = list(
                executor.map(
                    lambda listing: self._scan_listing(
                        listing,
                        request,
                        benchmark_context,
                        delivery_trends,
                        sink,
                    ),
                    listings,
                )
            )
        return [candidate for candidate in results if candidate is not None]

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

    def _estimate_target_sessions(
        self,
        candidate: TradeCandidate,
        backtest: BacktestStats,
    ) -> int:
        per_session_move = max(
            candidate.indicators.atr14 * 0.85,
            candidate.entry_price * 0.008,
        )
        price_distance = max(
            candidate.target_price - candidate.entry_price,
            candidate.indicators.atr14,
        )
        atr_sessions = max(3, round(price_distance / per_session_move))
        trigger_gap_sessions = 0
        if candidate.entry_price > candidate.current_price * 1.001:
            trigger_gap_sessions = max(
                0,
                round((candidate.entry_price - candidate.current_price) / per_session_move),
            )

        if backtest.average_target_sessions:
            historical_component = backtest.average_target_sessions
            estimated = round(historical_component * 0.7 + atr_sessions * 0.3)
        elif backtest.average_holding_sessions:
            estimated = round(backtest.average_holding_sessions * 0.55 + atr_sessions * 0.45)
        else:
            estimated = atr_sessions

        return max(3, min(60, estimated + trigger_gap_sessions))

    def _project_target_date(
        self,
        reference_date: date,
        trading_sessions: int,
    ) -> date:
        projected = reference_date
        remaining = max(1, trading_sessions)
        while remaining > 0:
            projected += timedelta(days=1)
            if projected.weekday() < 5:
                remaining -= 1
        return projected

    def _start_incremental_scan(
        self,
        request: ScanRequest,
        listings: list[StockListing],
    ) -> bool:
        with self._active_scan_lock:
            if self._active_scan is not None:
                return False
            if not signal_store.begin_scan(
                universe=request.universe,
                universe_size=len(listings),
            ):
                return False

            self._active_scan = ActiveScanState(
                request=request,
                listings=listings,
                worker_count=self._resolve_worker_count(request, len(listings)),
            )
            threading.Thread(
                target=self._run_async_scan,
                args=(self._active_scan,),
                daemon=True,
                name=f"scan-{request.universe.value}",
            ).start()
            return True

    def _run_async_scan(self, state: ActiveScanState) -> None:
        try:
            state.benchmark_context = self._load_benchmark_context(
                state.request.lookback_days
            )
            state.sink.benchmark_returns = self._benchmark_returns(state.benchmark_context)
            state.delivery_trends = self._load_delivery_trends(
                state.benchmark_context
            )
            state.candidates = self._scan_with_parallel_workers(state)
            state.cursor = len(state.listings)
            self._finish_incremental_scan(state)
        except Exception as exc:
            logger.exception("Background async scan failed. %s", exc)
            signal_store.finish_scan()
        finally:
            with self._active_scan_lock:
                if self._active_scan is state:
                    self._active_scan = None

    def _scan_with_parallel_workers(self, state: ActiveScanState) -> list[TradeCandidate]:
        listings = state.listings
        if not listings:
            return []

        worker_count = max(1, min(state.worker_count, len(listings)))
        if worker_count == 1:
            return self._scan_partition(
                listings,
                state.request,
                state.benchmark_context,
                state.delivery_trends,
                total_listings=len(listings),
                sink=state.sink,
            )

        partitions = self._partition_listings(listings, worker_count)
        candidates: list[TradeCandidate] = []
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    self._scan_partition,
                    partition,
                    state.request,
                    state.benchmark_context,
                    state.delivery_trends,
                    len(listings),
                    state.sink,
                )
                for partition in partitions
                if partition
            ]
            for future in as_completed(futures):
                candidates.extend(future.result())
        return candidates

    def _scan_partition(
        self,
        listings: list[StockListing],
        request: ScanRequest,
        benchmark_context: RelativeStrengthContext | None,
        delivery_trends: dict[str, DeliveryTrend],
        total_listings: int,
        sink: ScanSink | None = None,
    ) -> list[TradeCandidate]:
        self._prefetch_histories(listings, request.lookback_days)
        local_candidates: list[TradeCandidate] = []
        for listing in listings:
            candidate = self._scan_listing(
                listing,
                request,
                benchmark_context,
                delivery_trends,
                sink,
            )
            if candidate is not None:
                local_candidates.append(candidate)
            with self._active_scan_lock:
                active_state = self._active_scan
                if active_state is not None:
                    active_state.cursor += 1
                    scanned_symbols = active_state.cursor
                else:
                    scanned_symbols = 0
            signal_store.update_progress(
                scanned_symbols=scanned_symbols,
                universe_size=total_listings,
            )
        return local_candidates

    def _partition_listings(
        self,
        listings: list[StockListing],
        worker_count: int,
    ) -> list[list[StockListing]]:
        partitions: list[list[StockListing]] = []
        start = 0
        for worker_index in range(worker_count):
            remaining = len(listings) - start
            workers_left = worker_count - worker_index
            current_size = max(1, remaining // workers_left)
            partitions.append(listings[start : start + current_size])
            start += current_size
        return partitions

    def _resolve_worker_count(
        self,
        request: ScanRequest,
        listing_count: int,
    ) -> int:
        base_workers = settings.scan_workers
        if request.universe == ScanUniverse.MID_SMALL_2000_PLUS:
            base_workers = settings.mid_small_parallel_workers
        return max(1, min(base_workers, listing_count))

    def _finish_incremental_scan(self, state: ActiveScanState) -> None:
        self.regime.record_scan_breadth(state.sink.above_ema50, state.sink.total)
        self.last_rejected = dict(state.sink.rejected)
        state.candidates = self._apply_candidate_overlays(
            state.candidates, state.request, state.sink
        )
        state.candidates.sort(
            key=lambda candidate: (
                candidate.ranking_score,
                candidate.expected_profit_amount,
            ),
            reverse=True,
        )
        limited = [
            self._finalize_trade_setup(
                candidate,
                state.benchmark_context,
                state.request.lookback_days,
            )
            for candidate in state.candidates[: state.request.max_results]
        ]
        generated_at = datetime.now(UTC)
        signal_store.replace(
            limited,
            universe=state.request.universe,
            generated_at=generated_at,
            universe_size=len(state.listings),
            scanned_symbols=len(state.listings),
        )
        self._record_to_ledger(limited, state.request.universe)

    def _build_accumulation_note(
        self,
        accumulation: AccumulationSnapshot,
    ) -> str:
        base_note = (
            f" Accumulation score: {accumulation.score:.2f} with "
            f"{accumulation.closes_near_high_10d} strong closes near highs in the last 10 sessions."
        )
        if (
            accumulation.source != "nse_delivery"
            or accumulation.average_delivery_pct_10d is None
            or accumulation.latest_delivery_pct is None
        ):
            return base_note + " Delivery quality is using the internal volume proxy."

        return (
            base_note
            + f" NSE delivery confirms the move with a 10-session average of "
            + f"{accumulation.average_delivery_pct_10d:.1f}%, latest delivery at "
            + f"{accumulation.latest_delivery_pct:.1f}%, and "
            + f"{accumulation.rising_delivery_days_10d} rising delivery sessions."
        )

scanner_service = ScannerService()
