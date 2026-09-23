"""Paper signal generation: live snapshots and no-look-ahead replays.

The strategy (``src/main.py``) remains the only source of trading decisions.
This module wraps its emissions into classified ``PaperSignal`` records:

  actionable_paper - a long entry the paper simulator may fill
  blocked          - refused by a safety/applicability gate (funding,
                     session, parabolic SAR), instrument status, a data
                     problem, or the circuit breaker
  informational    - the strategy evaluated the symbol and chose to watch
                     on ordinary entry conditions (trend, pullback,
                     volume, RSI)

Replays step through historical 4h bar closes. At each decision time the
harness serves only bars closed at or before that instant and the strategy
clock reads the simulated time, so decisions never see the future. Symbols
without enough closed history are skipped with an explicit
``insufficient_history`` record - performance is never manufactured.
"""
from __future__ import annotations

import time
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from .config import (
    PaperConfig,
    strategy_config as manifest_strategy_config,
    strategy_version,
    trading_symbols,
)
from .harness import MarketDataProvider, PaperHarness
from .market_data import (
    INTERVAL_MS,
    MIN_BARS,
    InsufficientDataError,
    KlineBar,
    MarketDataError,
    assert_fresh,
    build_source,
    save_bars,
)

DECISION_INTERVAL = "4h"
DAILY_INTERVAL = "1d"
STRATEGY_LIMITS = {"1d": 260, "4h": 120}
LIVE_MODE = "live_snapshot"
REPLAY_MODE = "replay"

ACTIONABLE = "actionable_paper"
BLOCKED = "blocked"
INFORMATIONAL = "informational"

# Applicability/safety gates. A watch caused by one of these is "blocked";
# ordinary entry-condition misses are "informational".
HARD_GATE_CHECKS = (
    "btc_funding_filter",
    "gold_london_or_ny_session",
    "oil_parabolic_sar_confirmation",
)
BLOCKED_STATUSES = (
    "unknown_symbol",
    "instrument_disabled",
    "data_or_signal_build_failed",
    "instrument_registry_invalid",
)
DAY_MS = 86_400_000


def iso_utc(time_ms: int) -> str:
    return (
        datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _optional_str(value) -> Optional[str]:
    return None if value is None else str(value)


@dataclass(frozen=True)
class PaperSignal:
    timestamp_ms: int
    symbol: str
    timeframe: str
    direction: str
    classification: str
    confidence: float
    entry_reference: Optional[str]
    stop_loss: Optional[str]
    stop_distance: Optional[str]
    target_2r: Optional[str]
    target_4r: Optional[str]
    strategy_version: str
    reason_codes: Tuple[str, ...]
    checks: Dict[str, bool]
    data_source: str
    mode: str
    risk_plan: Dict = field(default_factory=dict)
    status: Optional[str] = None
    error: Optional[str] = None

    @property
    def key(self) -> Tuple[str, int]:
        return (self.symbol, self.timestamp_ms)

    @property
    def timestamp(self) -> str:
        return iso_utc(self.timestamp_ms)

    def to_json(self) -> dict:
        return {
            "timestamp_ms": self.timestamp_ms,
            "timestamp": iso_utc(self.timestamp_ms),
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "classification": self.classification,
            "confidence": self.confidence,
            "entry_reference": self.entry_reference,
            "stop_loss": self.stop_loss,
            "stop_distance": self.stop_distance,
            "target_2r": self.target_2r,
            "target_4r": self.target_4r,
            "strategy_version": self.strategy_version,
            "reason_codes": list(self.reason_codes),
            "checks": dict(self.checks),
            "data_source": self.data_source,
            "mode": self.mode,
            "risk_plan": dict(self.risk_plan),
            "status": self.status,
            "error": self.error,
        }


def classify_emitted(emitted: dict) -> Tuple[str, Tuple[str, ...]]:
    action = emitted.get("action")
    meta = emitted.get("meta") or {}
    if meta.get("circuit_breaker"):
        return BLOCKED, ("circuit_breaker_lock",)
    if action == "long":
        return ACTIONABLE, ()
    status = meta.get("status")
    if status in BLOCKED_STATUSES:
        reasons = [status]
        if meta.get("error"):
            reasons.append("strategy_error")
        return BLOCKED, tuple(reasons)
    checks = meta.get("getclaw_checks") or {}
    failed_hard = tuple(name for name in HARD_GATE_CHECKS if checks.get(name) is False)
    if failed_hard:
        return BLOCKED, failed_hard
    failed_entry = tuple(name for name, ok in sorted(checks.items()) if ok is False)
    return INFORMATIONAL, failed_entry or ("watch",)


def is_portfolio_summary(emitted: dict) -> bool:
    meta = emitted.get("meta") or {}
    return emitted.get("symbol") == "PORTFOLIO" and "signals" in meta


def build_signal(emitted: dict, timestamp_ms: int, version: str,
                 data_source: str, mode: str) -> PaperSignal:
    meta = emitted.get("meta") or {}
    plan = meta.get("risk_plan") or {}
    classification, reasons = classify_emitted(emitted)
    checks = {name: bool(ok) for name, ok in (meta.get("getclaw_checks") or {}).items()}
    return PaperSignal(
        timestamp_ms=timestamp_ms,
        symbol=str(emitted.get("symbol")),
        timeframe=DECISION_INTERVAL,
        direction=str(emitted.get("action")),
        classification=classification,
        confidence=float(emitted.get("confidence") or 0.0),
        entry_reference=_optional_str(plan.get("entry_price")),
        stop_loss=_optional_str(plan.get("stop_loss")),
        stop_distance=_optional_str(plan.get("stop_distance")),
        target_2r=_optional_str(plan.get("take_profit_50pct_at_2r")),
        target_4r=_optional_str(plan.get("runner_target_4r_plus")),
        strategy_version=version,
        reason_codes=reasons,
        checks=checks,
        data_source=data_source,
        mode=mode,
        risk_plan=dict(plan),
        status=meta.get("status"),
        error=meta.get("error"),
    )


def blocked_signal(symbol: str, timestamp_ms: int, reason: str, version: str,
                   data_source: str, mode: str, error: Optional[str] = None) -> PaperSignal:
    """Locally produced blocked record - the strategy is NOT run for these."""
    return PaperSignal(
        timestamp_ms=timestamp_ms,
        symbol=str(symbol),
        timeframe=DECISION_INTERVAL,
        direction="watch",
        classification=BLOCKED,
        confidence=0.0,
        entry_reference=None,
        stop_loss=None,
        stop_distance=None,
        target_2r=None,
        target_4r=None,
        strategy_version=version,
        reason_codes=(reason,),
        checks={},
        data_source=data_source,
        mode=mode,
        risk_plan={},
        status=reason,
        error=error,
    )


def coverage_ok(bars_by_interval: Dict[str, Sequence[KlineBar]]) -> Tuple[bool, str]:
    daily = len(bars_by_interval.get(DAILY_INTERVAL) or ())
    four = len(bars_by_interval.get(DECISION_INTERVAL) or ())
    if daily < MIN_BARS[DAILY_INTERVAL]:
        return False, f"need >= {MIN_BARS[DAILY_INTERVAL]} closed daily bars, have {daily}"
    if four < MIN_BARS[DECISION_INTERVAL]:
        return False, f"need >= {MIN_BARS[DECISION_INTERVAL]} closed 4h bars, have {four}"
    return True, ""


# ---------------------------------------------------------------------------
# Live snapshot
# ---------------------------------------------------------------------------
@dataclass
class SnapshotResult:
    generated_at_ms: int
    data_source: str
    signals: List[PaperSignal]
    coverage: Dict[str, dict]
    errors: List[str]


def generate_now(config: PaperConfig, manifest: dict,
                 now_ms: Optional[int] = None) -> SnapshotResult:
    """One live snapshot: fetch fresh public data, run the strategy once."""
    if config.source == "cache":
        raise ValueError("the live snapshot command does not read the cache; use rest or synthetic")
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    base_config = manifest_strategy_config(manifest)
    version = strategy_version(manifest)
    symbols = list(config.symbols) or list(trading_symbols(manifest))
    source = build_source(config.source, config.output_dir / "data", seed=config.seed)

    provider = MarketDataProvider(source=source, replay_funding=config.replay_funding)
    signals: List[PaperSignal] = []
    coverage: Dict[str, dict] = {}
    errors: List[str] = []
    runnable: List[str] = []

    for symbol in symbols:
        try:
            daily = source.fetch(symbol, DAILY_INTERVAL, end_ms=now,
                                 bars_needed=STRATEGY_LIMITS[DAILY_INTERVAL])
            four_hour = source.fetch(symbol, DECISION_INTERVAL, end_ms=now,
                                     bars_needed=STRATEGY_LIMITS[DECISION_INTERVAL])
            assert_fresh(daily, DAILY_INTERVAL, now_ms=now)
            assert_fresh(four_hour, DECISION_INTERVAL, now_ms=now)
        except InsufficientDataError as exc:
            reason = "stale_feed" if str(exc).startswith("stale") else "insufficient_history"
            signals.append(blocked_signal(symbol, now, reason, version, source.name,
                                          LIVE_MODE, error=str(exc)))
            errors.append(f"{symbol}: {exc}")
            coverage[symbol] = {"status": reason}
            continue
        except MarketDataError as exc:
            signals.append(blocked_signal(symbol, now, "market_data_unavailable", version,
                                          source.name, LIVE_MODE, error=str(exc)))
            errors.append(f"{symbol}: {exc}")
            coverage[symbol] = {"status": "market_data_unavailable"}
            continue
        ok, why = coverage_ok({DAILY_INTERVAL: daily, DECISION_INTERVAL: four_hour})
        if not ok:
            signals.append(blocked_signal(symbol, now, "insufficient_history", version,
                                          source.name, LIVE_MODE, error=why))
            errors.append(f"{symbol}: {why}")
            coverage[symbol] = {"status": "insufficient_history",
                                "daily_bars": len(daily), "four_hour_bars": len(four_hour)}
            continue
        provider.set_bars(symbol, DAILY_INTERVAL, daily)
        provider.set_bars(symbol, DECISION_INTERVAL, four_hour)
        coverage[symbol] = {"status": "ok", "daily_bars": len(daily),
                            "four_hour_bars": len(four_hour)}
        runnable.append(symbol)

    if runnable:
        harness = PaperHarness(provider)
        # margin_budget is the paper account size; this is an account input,
        # not a strategy rule. reported_daily_pnl_pct keeps its manifest value.
        paper_config = {**base_config, "margin_budget": str(config.initial_balance),
                        "trading_symbols": list(runnable)}
        emitted = harness.run_decision(paper_config, runnable, sim_now_ms=None, clock_ms=now)
        for item in emitted:
            if is_portfolio_summary(item):
                continue
            signals.append(build_signal(item, now, version, source.name, LIVE_MODE))

    return SnapshotResult(generated_at_ms=now, data_source=source.name, signals=signals,
                          coverage=coverage, errors=errors)


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------
@dataclass
class ReplayDataset:
    source_name: str
    bars: Dict[str, Dict[str, List[KlineBar]]]
    decision_times: List[int]
    window_start_ms: int
    window_end_ms: int


def fetch_replay_dataset(config: PaperConfig, symbols: Sequence[str],
                         now_ms: Optional[int] = None) -> ReplayDataset:
    """Fetch warmup + replay-window bars once, so every step slices locally."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cache_root = config.output_dir / "data"
    if config.source == "cache":
        source = build_source("cache", cache_root / "rest")
    else:
        source = build_source(config.source, cache_root, seed=config.seed)
    # +2 buffer bars: the newest bar of a synthetic series can close in the
    # future, which would otherwise cost the first replay step its warmup.
    needed = {
        DAILY_INTERVAL: MIN_BARS[DAILY_INTERVAL] + config.days + 2,
        DECISION_INTERVAL: MIN_BARS[DECISION_INTERVAL] + config.days * 6 + 2,
    }
    bars: Dict[str, Dict[str, List[KlineBar]]] = {}
    for symbol in symbols:
        bars[symbol] = {}
        for interval in (DAILY_INTERVAL, DECISION_INTERVAL):
            fetched = source.fetch(symbol, interval, end_ms=now, bars_needed=needed[interval])
            if not fetched:
                raise MarketDataError(f"no {interval} bars returned for {symbol}")
            bars[symbol][interval] = fetched
            if config.source == "rest":
                save_bars(cache_root, "rest", symbol, interval, fetched)

    step = INTERVAL_MS[DECISION_INTERVAL]
    window_start = now - config.days * DAY_MS
    closes = sorted({
        bar.time_ms + step
        for symbol in bars
        for bar in bars[symbol][DECISION_INTERVAL]
    })
    decision_times = [value for value in closes if window_start <= value <= now]
    if not decision_times:
        raise MarketDataError("no 4h decision times inside the replay window")
    return ReplayDataset(source.name, bars, decision_times, decision_times[0], decision_times[-1])


def make_psar_fn(harness: PaperHarness, dataset: ReplayDataset):
    """Parabolic SAR over bars closed at or before the query time.

    Uses the strategy's own ``_parabolic_sar`` on the closed-bar prefix, so
    runner exits trail exactly like the documented plan - with no look-ahead.
    """
    import pandas as pd

    bars_by_symbol = {
        symbol: dataset.bars[symbol][DECISION_INTERVAL] for symbol in dataset.bars
    }
    closes_by_symbol = {
        symbol: [bar.time_ms + INTERVAL_MS[DECISION_INTERVAL] for bar in bars]
        for symbol, bars in bars_by_symbol.items()
    }
    cache: Dict[Tuple[str, int], Optional[Decimal]] = {}

    def psar_at(symbol: str, bar_close_ms: int) -> Optional[Decimal]:
        bars = bars_by_symbol.get(symbol)
        if not bars:
            return None
        key = (symbol, bar_close_ms)
        if key in cache:
            return cache[key]
        idx = bisect_right(closes_by_symbol[symbol], bar_close_ms)
        frame = pd.DataFrame([bar.to_row() for bar in bars[:idx]])
        series = harness.strategy._parabolic_sar(frame)
        value = series.iloc[-1] if len(series) else None
        result = None if value is None or pd.isna(value) else Decimal(str(float(value)))
        cache[key] = result
        return result

    return psar_at


@dataclass
class ReplayOutcome:
    signals: List[PaperSignal]
    coverage: Dict[str, dict]
    steps: int


def run_replay(config: PaperConfig, manifest: dict, dataset: ReplayDataset,
               simulator, harness: PaperHarness, signal_filter=None) -> ReplayOutcome:
    """Step through 4h closes; run the strategy per step; feed the simulator.

    ``signal_filter`` is an optional veto hook, called as
    ``signal_filter(decision_ms, actionable_signals, closed_bars)`` and expected
    to return the subset of the actionable signals that may be simulated. It can
    only REMOVE signals: ``all_signals`` - and therefore coverage and every
    signal count reported downstream - is recorded before the hook runs, so a
    veto never hides what the deterministic strategy actually produced. With the
    default ``None`` the behavior is identical to the unfiltered replay.
    """
    base_config = manifest_strategy_config(manifest)
    version = strategy_version(manifest)
    symbols = list(dataset.bars)
    closes = {
        (symbol, interval): [
            bar.time_ms + INTERVAL_MS[interval]
            for bar in dataset.bars[symbol][interval]
        ]
        for symbol in symbols
        for interval in (DAILY_INTERVAL, DECISION_INTERVAL)
    }
    all_signals: List[PaperSignal] = []
    coverage: Dict[str, dict] = {
        symbol: {
            "daily_bars": len(dataset.bars[symbol][DAILY_INTERVAL]),
            "four_hour_bars": len(dataset.bars[symbol][DECISION_INTERVAL]),
            "steps_with_data": 0,
        }
        for symbol in symbols
    }
    reported_insufficient = set()

    for decision_ms in dataset.decision_times:
        closed_bars: Dict[str, KlineBar] = {}
        runnable: List[str] = []
        for symbol in symbols:
            four_closes = closes[(symbol, DECISION_INTERVAL)]
            idx = bisect_right(four_closes, decision_ms) - 1
            if idx < 0 or four_closes[idx] != decision_ms:
                continue  # this symbol printed no bar closing at this decision time
            daily_closed = bisect_right(closes[(symbol, DAILY_INTERVAL)], decision_ms)
            if daily_closed < MIN_BARS[DAILY_INTERVAL] or idx + 1 < MIN_BARS[DECISION_INTERVAL]:
                if symbol not in reported_insufficient:
                    reported_insufficient.add(symbol)
                    all_signals.append(blocked_signal(
                        symbol, decision_ms, "insufficient_history", version,
                        dataset.source_name, REPLAY_MODE,
                        error=f"daily={daily_closed} four_hour={idx + 1}",
                    ))
                continue
            closed_bars[symbol] = dataset.bars[symbol][DECISION_INTERVAL][idx]
            runnable.append(symbol)
            coverage[symbol]["steps_with_data"] += 1

        step_signals: List[PaperSignal] = []
        if runnable:
            daily_pnl = simulator.reported_daily_pnl_pct(decision_ms)
            paper_config = {
                **base_config,
                "margin_budget": str(config.initial_balance),
                "trading_symbols": list(runnable),
                "reported_daily_pnl_pct": float(daily_pnl),
            }
            emitted = harness.run_decision(paper_config, runnable, sim_now_ms=decision_ms)
            for item in emitted:
                if is_portfolio_summary(item):
                    continue
                signal = build_signal(item, decision_ms, version, dataset.source_name, REPLAY_MODE)
                step_signals.append(signal)
                all_signals.append(signal)

        actionable = [signal for signal in step_signals if signal.classification == ACTIONABLE]
        if signal_filter is not None and actionable:
            # Veto-only hook: it may return fewer actionable signals, never more.
            actionable = list(signal_filter(decision_ms, actionable, closed_bars))
        simulator.process_step(decision_ms, closed_bars, actionable)

    return ReplayOutcome(all_signals, coverage, len(dataset.decision_times))
