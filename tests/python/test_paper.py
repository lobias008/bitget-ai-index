"""Tests for the Milestone 2 paper-trading workflow (the paper/ package).

Guarantees covered here:
  * sizing mirrors the strategy's _position_plan exactly (re-derived in
    Decimal; display rounding never feeds arithmetic);
  * stops, partial take-profits, breakeven moves, runner targets, SAR-flip
    exits and the daily circuit breaker behave per the documented risk rules;
  * duplicate signals and entries while a position is open are rejected;
  * missing or invalid market data produces honest blocked records - never
    fabricated bars, signals or fills;
  * replays never look ahead: providers serve closed bars only, session
    gates read the simulated clock, and decisions are prefix-invariant;
  * runs are deterministic for a fixed seed and timestamp;
  * the package can never place a live order: GET-only allowlisted public
    endpoints, no auth headers, no private-API surface, no secrets in output.

Offline and deterministic: the network is never touched (urlopen is
monkeypatched where request behavior is asserted).
"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from getagent_stub import load_strategy  # noqa: E402  (same-dir test helper)

from paper import market_data as md  # noqa: E402
from paper import signals as signals_module  # noqa: E402
from paper import storage  # noqa: E402
from paper.config import (  # noqa: E402
    DASHBOARD_EXPORT_PATH,
    OUTPUT_DIR,
    PAPER_LABELS,
    PaperConfig,
    load_manifest,
    strategy_config as manifest_strategy_config,
    strategy_version,
    trading_symbols,
)
from paper.harness import MarketDataProvider, PaperHarness  # noqa: E402
from paper.market_data import (  # noqa: E402
    INTERVAL_MS,
    MIN_BARS,
    CacheSource,
    InsufficientDataError,
    KlineBar,
    MarketDataError,
    SyntheticSource,
    assert_fresh,
    parse_candle_row,
    save_bars,
)
from paper.signals import (  # noqa: E402
    ACTIONABLE,
    BLOCKED,
    INFORMATIONAL,
    PaperSignal,
    ReplayDataset,
    build_signal,
    classify_emitted,
    coverage_ok,
    fetch_replay_dataset,
    generate_now,
    is_portfolio_summary,
    make_psar_fn,
    run_replay,
)
from paper.simulator import PaperSimulator  # noqa: E402

# Reference copy of the strategy (bound to the classic test stub) used only
# for _position_plan parity checks. Pure function - no data, no emissions.
strategy_ref, _data_ref, _runtime_ref = load_strategy()

STEP = INTERVAL_MS["4h"]
DAY = 86_400_000
T0 = 1_758_067_200_000  # 2025-09-17T00:00:00Z, 4h-grid aligned
SYMBOL = "SP500USDT"


def make_bar(close, open_time_ms, open_=None, high=None, low=None, volume=100.0):
    reference = float(close)
    return KlineBar(
        open_time_ms,
        float(reference if open_ is None else open_),
        float(reference if high is None else high),
        float(reference if low is None else low),
        reference,
        float(volume),
    )


def make_plan(entry=100.0, stop_distance=3.0, quantity=None):
    if quantity is None:
        # 10 USDT of risk over the stop distance. A zero stop is left for
        # size_entry to reject, so never divide by zero while building it.
        quantity = 10.0 / stop_distance if stop_distance else 10.0
    return {
        "symbol": SYMBOL,
        "entry_price": entry,
        "stop_loss": entry - stop_distance,
        "stop_distance": stop_distance,
        "risk_usdt": 10.0,
        "quantity": quantity,
        "notional_usdt": min(quantity * entry, 3000.0),
        "take_profit_50pct_at_2r": entry + 2 * stop_distance,
        "move_stop_to_breakeven_after_2r": entry,
        "runner_target_4r_plus": entry + 4 * stop_distance,
        "runner_trailing_stop": "parabolic_sar",
        "state_after_partial": "Free Trade",
    }


def make_signal(symbol=SYMBOL, ts=T0, classification=ACTIONABLE, plan=None, direction="long"):
    plan = make_plan() if plan is None else plan
    return PaperSignal(
        timestamp_ms=ts,
        symbol=symbol,
        timeframe="4h",
        direction=direction,
        classification=classification,
        confidence=0.82 if classification == ACTIONABLE else 0.0,
        entry_reference=None if not plan else str(plan.get("entry_price")),
        stop_loss=None if not plan else str(plan.get("stop_loss")),
        stop_distance=None if not plan else str(plan.get("stop_distance")),
        target_2r=None if not plan else str(plan.get("take_profit_50pct_at_2r")),
        target_4r=None if not plan else str(plan.get("runner_target_4r_plus")),
        strategy_version="test@0",
        reason_codes=(),
        checks={},
        data_source="synthetic",
        mode="replay",
        risk_plan=dict(plan),
    )


def make_sim(initial="1000", fee="0.06", slip="0.02", psar_fn=None, **param_overrides):
    config = PaperConfig(
        initial_balance=Decimal(initial),
        fee_pct=Decimal(fee),
        slippage_pct=Decimal(slip),
        source="synthetic",
    )
    params = {
        "margin_budget": initial,
        "leverage": 3,
        "risk_per_trade_pct": 1.0,
        "atr_stop_multiple": 1.5,
        "circuit_breaker_daily_loss_pct": -2.0,
        "partial_take_profit_pct": 50.0,
    }
    params.update(param_overrides)
    return PaperSimulator(config, params, psar_fn=psar_fn)


def enter(sim, symbol=SYMBOL, ts=T0, close=100.0):
    """Open one position through the public process_step API."""
    sim.process_step(ts, {symbol: make_bar(close, ts - STEP)}, [make_signal(symbol, ts)])
    return sim.positions[symbol]


MANIFEST = load_manifest()
BASE_CONFIG = manifest_strategy_config(MANIFEST)
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# ---------------------------------------------------------------------------
# Sizing parity with the strategy's own risk plan
# ---------------------------------------------------------------------------
class TestPositionSizing(unittest.TestCase):
    def test_sizing_mirrors_strategy_position_plan(self):
        sim = make_sim(initial="1000")
        plan_params = {
            "margin_budget": "1000",
            "risk_per_trade_pct": 1.0,
            "atr_stop_multiple": 1.5,
            "leverage": 3,
        }
        reference = strategy_ref._position_plan(SYMBOL, 100.0, 2.0, plan_params)
        sized = sim.size_entry(SYMBOL, make_plan(entry=100.0, stop_distance=3.0))
        self.assertIsNotNone(sized)
        quantity, notional = sized
        self.assertIsInstance(quantity, Decimal)
        self.assertAlmostEqual(float(quantity), reference["quantity"], places=6)
        self.assertAlmostEqual(float(notional), reference["notional_usdt"], places=4)

    def test_quantity_is_exact_decimal_not_rounded_display(self):
        sim = make_sim(initial="1000")
        quantity, _ = sim.size_entry(SYMBOL, make_plan(stop_distance=3.0))
        self.assertTrue(str(quantity).startswith("3.3333333333"))

    def test_notional_cap_scales_quantity_down(self):
        sim = make_sim(initial="1000")
        plan = make_plan(entry=100000.0, stop_distance=0.0001)
        quantity, notional = sim.size_entry(SYMBOL, plan)
        self.assertEqual(notional, Decimal("3000"))
        self.assertEqual(quantity, Decimal("0.03"))

    def test_zero_stop_distance_is_rejected(self):
        sim = make_sim(initial="1000")
        self.assertIsNone(sim.size_entry(SYMBOL, make_plan(stop_distance=0.0)))

    def test_stop_above_entry_is_rejected(self):
        sim = make_sim(initial="1000")
        plan = make_plan()
        plan["stop_loss"] = plan["entry_price"] + 1
        self.assertIsNone(sim.size_entry(SYMBOL, plan))


# ---------------------------------------------------------------------------
# Entries, fills, duplicates and guards
# ---------------------------------------------------------------------------
class TestEntries(unittest.TestCase):
    def test_entry_fill_applies_slippage_and_fee(self):
        sim = make_sim()
        enter(sim)
        position = sim.positions[SYMBOL]
        expected_fill = Decimal("100") * (Decimal(1) + sim.slip_rate)
        self.assertEqual(position.entry_price, expected_fill)
        expected_notional = position.quantity * expected_fill
        expected_fee = expected_notional * sim.fee_rate
        expected_margin = expected_notional / Decimal(3)
        self.assertEqual(position.entry_fee, expected_fee)
        self.assertEqual(sim.balance, Decimal("1000") - expected_margin - expected_fee)
        self.assertEqual(len(sim.fills), 1)
        self.assertEqual(sim.fills[0]["side"], "entry")
        self.assertTrue(any(e["type"] == "entry_filled" for e in sim.events))

    def test_fill_uses_actual_bar_close(self):
        sim = make_sim()
        sim.process_step(T0, {SYMBOL: make_bar(101.0, T0 - STEP)}, [make_signal()])
        position = sim.positions[SYMBOL]
        self.assertEqual(position.entry_price, Decimal("101") * (Decimal(1) + sim.slip_rate))

    def test_blocked_signal_never_fills(self):
        sim = make_sim()
        sim.process_step(T0, {SYMBOL: make_bar(100.0, T0 - STEP)},
                         [make_signal(classification=BLOCKED)])
        self.assertEqual(sim.positions, {})
        self.assertEqual(sim.fills, [])

    def test_informational_signal_never_fills(self):
        sim = make_sim()
        sim.process_step(T0, {SYMBOL: make_bar(100.0, T0 - STEP)},
                         [make_signal(classification=INFORMATIONAL, direction="watch")])
        self.assertEqual(sim.positions, {})
        self.assertEqual(sim.fills, [])

    def test_duplicate_signal_in_same_step_rejected(self):
        sim = make_sim()
        signal = make_signal()
        sim.process_step(T0, {SYMBOL: make_bar(100.0, T0 - STEP)}, [signal, signal])
        self.assertEqual(len(sim.fills), 1)
        self.assertTrue(any(e["type"] == "duplicate_signal_rejected" for e in sim.events))

    def test_duplicate_signal_in_later_step_rejected(self):
        sim = make_sim()
        signal = make_signal()
        sim.process_step(T0, {SYMBOL: make_bar(100.0, T0 - STEP)}, [signal])
        sim.process_step(T0 + STEP, {SYMBOL: make_bar(100.0, T0)}, [signal])
        self.assertEqual(len(sim.fills), 1)
        self.assertTrue(any(e["type"] == "duplicate_signal_rejected" for e in sim.events))

    def test_entry_ignored_while_position_open(self):
        sim = make_sim()
        enter(sim)
        quantity_before = sim.positions[SYMBOL].quantity
        sim.process_step(T0 + STEP, {SYMBOL: make_bar(101.0, T0)},
                         [make_signal(ts=T0 + STEP)])
        self.assertEqual(sim.positions[SYMBOL].quantity, quantity_before)
        self.assertTrue(any(e["type"] == "entry_ignored_position_open" for e in sim.events))

    def test_entry_without_market_data_is_rejected(self):
        sim = make_sim()
        sim.process_step(T0, {}, [make_signal()])
        self.assertEqual(sim.positions, {})
        self.assertTrue(any(e["type"] == "entry_missing_market_data" for e in sim.events))

    def test_invalid_plan_is_rejected(self):
        sim = make_sim()
        empty = make_signal(plan={})
        sim.process_step(T0, {SYMBOL: make_bar(100.0, T0 - STEP)}, [empty])
        self.assertEqual(sim.positions, {})
        self.assertTrue(any(e["type"] == "invalid_plan_rejected" for e in sim.events))

    def test_margin_guard_blocks_second_position(self):
        sim = make_sim(leverage=0.4)  # account cap = 1000 * 0.4 = 400
        sim.process_step(T0, {"AAAUSDT": make_bar(100.0, T0 - STEP)},
                         [make_signal("AAAUSDT", T0)])
        self.assertIn("AAAUSDT", sim.positions)
        sim.process_step(T0 + STEP, {"BBBUSDT": make_bar(100.0, T0)},
                         [make_signal("BBBUSDT", T0 + STEP)])
        self.assertNotIn("BBBUSDT", sim.positions)
        self.assertTrue(any(e["type"] == "margin_guard_rejected" for e in sim.events))

    def test_insufficient_margin_blocks_entry(self):
        sim = make_sim(initial="10", margin_budget="1000")
        sim.process_step(T0, {SYMBOL: make_bar(100.0, T0 - STEP)}, [make_signal()])
        self.assertEqual(sim.positions, {})
        self.assertTrue(any(e["type"] == "insufficient_margin_rejected" for e in sim.events))


# ---------------------------------------------------------------------------
# Exits: stop, partial TP, breakeven, runner, SAR flip
# ---------------------------------------------------------------------------
class TestExits(unittest.TestCase):
    def test_stop_loss_exit(self):
        sim = make_sim()
        position = enter(sim)
        quantity = position.quantity
        entry = position.entry_price
        bar = make_bar(96.5, T0, open_=99.0, high=99.5, low=96.0)
        sim.process_step(T0 + STEP, {SYMBOL: bar}, [])
        self.assertEqual(sim.positions, {})
        self.assertEqual(len(sim.closed_trades), 1)
        trade = sim.closed_trades[0]
        self.assertEqual(trade["exit_reasons"], ["stop_loss"])
        expected_fill = Decimal("97") * (Decimal(1) - sim.slip_rate)
        self.assertEqual(Decimal(trade["entry_price"]), entry)
        realized = (expected_fill - entry) * quantity - Decimal(trade["fees"])
        self.assertAlmostEqual(float(Decimal(trade["realized_pnl"])), float(realized), places=6)
        self.assertLess(Decimal(trade["realized_pnl"]), 0)

    def test_gap_open_fills_at_worse_open(self):
        sim = make_sim()
        enter(sim)
        bar = make_bar(94.0, T0, open_=95.0, high=95.5, low=93.0)
        sim.process_step(T0 + STEP, {SYMBOL: bar}, [])
        exit_leg = [f for f in sim.fills if f["side"] == "exit"][0]
        expected = Decimal("95") * (Decimal(1) - sim.slip_rate)
        self.assertEqual(Decimal(exit_leg["fill_price"]), expected)

    def test_no_exit_on_entry_bar(self):
        sim = make_sim()
        wild = make_bar(100.0, T0 - STEP, open_=100.0, high=150.0, low=50.0)
        sim.process_step(T0, {SYMBOL: wild}, [make_signal()])
        self.assertIn(SYMBOL, sim.positions)  # same-bar exit is not simulated
        self.assertEqual(sim.fills[-1]["side"], "entry")

    def test_partial_take_profit_at_2r(self):
        sim = make_sim()
        position = enter(sim)
        initial_qty = position.quantity
        bar = make_bar(106.0, T0, open_=101.0, high=106.5, low=100.5)
        sim.process_step(T0 + STEP, {SYMBOL: bar}, [])
        position = sim.positions[SYMBOL]
        self.assertEqual(position.state, "free_trade")
        # The runner keeps the remainder after the 50% partial, which can
        # differ from an exact half by one unit in the last Decimal place.
        self.assertAlmostEqual(float(position.quantity), float(initial_qty) / 2, places=10)
        self.assertEqual(position.stop_loss, position.breakeven)
        leg = [f for f in sim.fills if f["side"] == "exit"][0]
        self.assertEqual(leg["reason"], "partial_take_profit_2r")
        # No quantity may leak: the partial fill plus the runner must equal
        # the original entry size exactly.
        self.assertEqual(Decimal(leg["quantity"]) + position.quantity, initial_qty)
        expected_fill = Decimal("106") * (Decimal(1) - sim.slip_rate)
        self.assertEqual(Decimal(leg["fill_price"]), expected_fill)
        self.assertEqual(len(sim.closed_trades), 0)  # lifecycle still open

    def test_same_bar_2r_then_4r_closes_lifecycle(self):
        sim = make_sim()
        enter(sim)
        bar = make_bar(112.0, T0, open_=101.0, high=112.5, low=100.5)
        sim.process_step(T0 + STEP, {SYMBOL: bar}, [])
        self.assertEqual(sim.positions, {})
        trade = sim.closed_trades[0]
        self.assertEqual(trade["exit_reasons"], ["partial_take_profit_2r", "runner_target_4r"])
        self.assertEqual(trade["legs"], 2)
        self.assertGreater(Decimal(trade["realized_pnl"]), 0)

    def test_stop_checked_before_take_profit_same_bar(self):
        sim = make_sim()
        enter(sim)
        bar = make_bar(100.0, T0, open_=100.0, high=107.0, low=96.0)
        sim.process_step(T0 + STEP, {SYMBOL: bar}, [])
        self.assertEqual(sim.closed_trades[0]["exit_reasons"], ["stop_loss"])
        self.assertEqual(len(sim.fills), 2)  # entry + stop exit only

    def test_breakeven_stop_after_partial(self):
        sim = make_sim()
        enter(sim)
        sim.process_step(T0 + STEP,
                         {SYMBOL: make_bar(106.0, T0, open_=101.0, high=106.5, low=100.5)}, [])
        bar = make_bar(100.2, T0 + STEP, open_=100.5, high=101.0, low=99.9)
        sim.process_step(T0 + 2 * STEP, {SYMBOL: bar}, [])
        self.assertEqual(sim.positions, {})
        trade = sim.closed_trades[0]
        self.assertEqual(trade["exit_reasons"], ["partial_take_profit_2r", "breakeven_stop"])

    def test_runner_exits_at_4r_on_later_bar(self):
        sim = make_sim()
        enter(sim)
        sim.process_step(T0 + STEP,
                         {SYMBOL: make_bar(106.0, T0, open_=101.0, high=106.5, low=100.5)}, [])
        bar = make_bar(112.0, T0 + STEP, open_=108.0, high=112.2, low=107.0)
        sim.process_step(T0 + 2 * STEP, {SYMBOL: bar}, [])
        self.assertEqual(sim.closed_trades[0]["exit_reasons"],
                         ["partial_take_profit_2r", "runner_target_4r"])

    def test_sar_flip_exits_runner(self):
        def psar_fn(symbol, time_ms):
            return Decimal("101")

        sim = make_sim(psar_fn=psar_fn)
        enter(sim)
        sim.process_step(T0 + STEP,
                         {SYMBOL: make_bar(106.0, T0, open_=101.0, high=106.5, low=100.5)}, [])
        bar = make_bar(100.5, T0 + STEP, open_=100.8, high=101.5, low=100.2)
        sim.process_step(T0 + 2 * STEP, {SYMBOL: bar}, [])
        self.assertEqual(sim.positions, {})
        self.assertEqual(sim.closed_trades[0]["exit_reasons"],
                         ["partial_take_profit_2r", "sar_flip"])

    def test_no_sar_exit_without_psar_fn(self):
        sim = make_sim()  # psar_fn None
        enter(sim)
        sim.process_step(T0 + STEP,
                         {SYMBOL: make_bar(106.0, T0, open_=101.0, high=106.5, low=100.5)}, [])
        bar = make_bar(100.5, T0 + STEP, open_=100.8, high=101.5, low=100.2)
        sim.process_step(T0 + 2 * STEP, {SYMBOL: bar}, [])
        self.assertIn(SYMBOL, sim.positions)

    def test_fees_charged_on_exit_leg(self):
        sim = make_sim()
        position = enter(sim)
        quantity = position.quantity
        bar = make_bar(96.5, T0, open_=99.0, high=99.5, low=96.0)
        sim.process_step(T0 + STEP, {SYMBOL: bar}, [])
        leg = [f for f in sim.fills if f["side"] == "exit"][0]
        expected_fee = Decimal(leg["fill_price"]) * quantity * sim.fee_rate
        self.assertEqual(Decimal(leg["fee"]), expected_fee)

# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------
class TestCircuitBreaker(unittest.TestCase):
    def _double_stop_out(self, sim):
        sim.process_step(T0, {"AAAUSDT": make_bar(100.0, T0 - STEP)},
                         [make_signal("AAAUSDT", T0)])
        sim.process_step(T0, {"BBBUSDT": make_bar(100.0, T0 - STEP)},
                         [make_signal("BBBUSDT", T0)])
        crash = make_bar(92.0, T0, open_=92.0, high=93.0, low=90.0)
        sim.process_step(T0 + STEP, {"AAAUSDT": crash, "BBBUSDT": crash}, [])

    def test_breaker_flattens_and_locks_for_24h(self):
        sim = make_sim()
        self._double_stop_out(sim)
        self.assertEqual(sim.positions, {})
        self.assertEqual(sim.breaker_trips, 1)
        self.assertEqual(sim.locked_until_ms, T0 + STEP + DAY)
        event = [e for e in sim.events if e["type"] == "circuit_breaker"][0]
        self.assertIn("flatten_all_positions_and_block_new_entries_24h", event["action"])

        # during the lock, actionable signals are refused and recorded
        t2 = T0 + 2 * STEP
        sim.process_step(t2, {"CCCUSDT": make_bar(100.0, t2 - STEP)},
                         [make_signal("CCCUSDT", t2)])
        self.assertEqual(sim.positions, {})
        self.assertTrue(any(e["type"] == "entry_blocked_circuit_breaker" for e in sim.events))

        # after the lock (and a new UTC day), entries are allowed again
        t3 = T0 + STEP + DAY + STEP
        sim.process_step(t3, {"DDDUSDT": make_bar(100.0, t3 - STEP)},
                         [make_signal("DDDUSDT", t3)])
        self.assertIn("DDDUSDT", sim.positions)

    def test_reported_daily_pnl_feeds_strategy_input(self):
        sim = make_sim()
        self.assertEqual(sim.reported_daily_pnl_pct(T0), 0.0)
        self._double_stop_out(sim)
        reported = sim.reported_daily_pnl_pct(T0 + 2 * STEP)
        self.assertLessEqual(reported, -2.0)
        next_day = T0 + STEP + DAY + STEP
        self.assertEqual(sim.reported_daily_pnl_pct(next_day), 0.0)

    def test_small_daily_loss_does_not_trip(self):
        sim = make_sim()
        sim.process_step(T0, {SYMBOL: make_bar(100.0, T0 - STEP)}, [make_signal()])
        bar = make_bar(96.5, T0, open_=99.0, high=99.5, low=96.0)  # ~ -1.1% of equity
        sim.process_step(T0 + STEP, {SYMBOL: bar}, [])
        self.assertEqual(sim.breaker_trips, 0)
        self.assertIsNone(sim.locked_until_ms)

    def test_custom_threshold_trips(self):
        sim = make_sim(circuit_breaker_daily_loss_pct=-0.5)
        sim.process_step(T0, {SYMBOL: make_bar(100.0, T0 - STEP)}, [make_signal()])
        bar = make_bar(96.5, T0, open_=99.0, high=99.5, low=96.0)
        sim.process_step(T0 + STEP, {SYMBOL: bar}, [])
        self.assertEqual(sim.breaker_trips, 1)


# ---------------------------------------------------------------------------
# Harness + classification (the strategy runs unmodified)
# ---------------------------------------------------------------------------
SPIKE_VOLUMES = [100.0] * 27 + [500.0, 500.0, 500.0]
FLAT_VOLUMES = [100.0] * 30


def frame_with_volumes(volumes, close=80.0):
    rows = [
        {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close,
         "volume": volume}
        for volume in volumes
    ]
    return pd.DataFrame(rows)


def constant_series(value, length=30):
    return pd.Series([float(value)] * length)


def ema_lookup(mapping):
    def _ema(frame, period):
        return pd.Series([float(mapping[period])] * len(frame), index=frame.index)
    return _ema


ALL_PASS_PATCHES = dict(
    _fetch_frame=lambda symbol, interval, limit: frame_with_volumes(SPIKE_VOLUMES),
    _ema=ema_lookup({20: 110.0, 50: 100.0, 200: 90.0}),
    _rsi=lambda frame, period=14: constant_series(50.0, len(frame)),
    _atr=lambda frame, period=14: constant_series(2.0, len(frame)),
    _parabolic_sar=lambda frame, acceleration=0.02, maximum=0.2: constant_series(70.0, len(frame)),
)


@contextmanager
def patch_strategy(strategy_module, **overrides):
    originals = {}
    for name, value in overrides.items():
        originals[name] = getattr(strategy_module, name)
        setattr(strategy_module, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(strategy_module, name, value)


class TestHarnessClassification(unittest.TestCase):
    def decisions(self, symbols, sim_now_ms=None, funding="block", **patches):
        provider = MarketDataProvider(replay_funding=funding)
        source = SyntheticSource(seed=7)
        for interval, count in (("1d", 220), ("4h", 150)):
            for symbol in symbols:
                provider.set_bars(
                    symbol, interval,
                    source.fetch(symbol, interval, end_ms=T0, bars_needed=count),
                )
        harness = PaperHarness(provider)
        config = {**copy.deepcopy(BASE_CONFIG), "margin_budget": "1000",
                  "trading_symbols": list(symbols)}
        with patch_strategy(harness.strategy, **(patches or ALL_PASS_PATCHES)):
            emitted = harness.run_decision(config, symbols, sim_now_ms=sim_now_ms)
        return harness, provider, emitted

    def test_long_is_actionable_paper_with_plan_levels(self):
        _h, _p, emitted = self.decisions([SYMBOL])
        instrument_signals = [item for item in emitted if not is_portfolio_summary(item)]
        self.assertEqual(len(instrument_signals), 1)
        signal = build_signal(instrument_signals[0], T0, "test@0", "synthetic", "replay")
        self.assertEqual(signal.classification, ACTIONABLE)
        self.assertEqual(signal.direction, "long")
        self.assertEqual(signal.reason_codes, ())
        self.assertEqual(signal.entry_reference, "80.0")
        self.assertEqual(signal.stop_loss, "77.0")
        self.assertEqual(signal.target_2r, "86.0")
        self.assertEqual(signal.target_4r, "92.0")
        self.assertEqual(signal.risk_plan["runner_trailing_stop"], "parabolic_sar")

    def test_portfolio_summary_is_skipped(self):
        _h, _p, emitted = self.decisions([SYMBOL])
        summaries = [item for item in emitted if is_portfolio_summary(item)]
        self.assertEqual(len(summaries), 1)

    def test_entry_condition_miss_is_informational(self):
        patches = {**ALL_PASS_PATCHES,
                   "_fetch_frame": lambda symbol, interval, limit: frame_with_volumes(FLAT_VOLUMES)}
        _h, _p, emitted = self.decisions([SYMBOL], **patches)
        signal = build_signal(emitted[0], T0, "test@0", "synthetic", "replay")
        self.assertEqual(signal.classification, INFORMATIONAL)
        self.assertIn("three_candle_volume_spike", signal.reason_codes)

    def test_session_gate_blocks_gold_off_hours_and_clock_is_simulated(self):
        two_am = T0 + 2 * 3_600_000
        _h, _p, emitted = self.decisions(["XAUUSDT"], sim_now_ms=two_am)
        signal = build_signal(emitted[0], two_am, "test@0", "synthetic", "replay")
        self.assertEqual(signal.classification, BLOCKED)
        self.assertEqual(signal.reason_codes, ("gold_london_or_ny_session",))

        eight_am = T0 + 8 * 3_600_000
        _h2, _p2, emitted2 = self.decisions(["XAUUSDT"], sim_now_ms=eight_am)
        signal2 = build_signal(emitted2[0], eight_am, "test@0", "synthetic", "replay")
        self.assertEqual(signal2.classification, ACTIONABLE)

    def test_funding_block_mode_fails_closed_for_btc(self):
        _h, _p, emitted = self.decisions(["BTCUSDT"], sim_now_ms=T0, funding="block")
        signal = build_signal(emitted[0], T0, "test@0", "synthetic", "replay")
        self.assertEqual(signal.classification, BLOCKED)
        self.assertEqual(signal.reason_codes, ("btc_funding_filter",))

    def test_funding_neutral_mode_lets_btc_pass(self):
        _h, _p, emitted = self.decisions(["BTCUSDT"], sim_now_ms=T0, funding="neutral")
        signal = build_signal(emitted[0], T0, "test@0", "synthetic", "replay")
        self.assertEqual(signal.classification, ACTIONABLE)
        self.assertTrue(signal.checks["btc_funding_filter"])

    def test_unknown_symbol_is_blocked(self):
        _h, _p, emitted = self.decisions(["FOOUSD"])
        signal = build_signal(emitted[0], T0, "test@0", "synthetic", "replay")
        self.assertEqual(signal.classification, BLOCKED)
        self.assertEqual(signal.reason_codes, ("unknown_symbol",))

    def test_disabled_instrument_is_blocked(self):
        harness = PaperHarness(MarketDataProvider())
        config = copy.deepcopy(BASE_CONFIG)
        config["symbol_roles"]["SP500USDT"]["strategy_eligibility"] = "disabled"
        config["trading_symbols"] = ["SP500USDT"]
        emitted = harness.run_decision(config, ["SP500USDT"], sim_now_ms=T0)
        signal = build_signal(emitted[0], T0, "test@0", "synthetic", "replay")
        self.assertEqual(signal.classification, BLOCKED)
        self.assertEqual(signal.reason_codes, ("instrument_disabled",))

    def test_missing_data_is_blocked_not_fabricated(self):
        provider = MarketDataProvider()  # no bars loaded at all
        harness = PaperHarness(provider)
        config = {**copy.deepcopy(BASE_CONFIG), "trading_symbols": [SYMBOL]}
        emitted = harness.run_decision(config, [SYMBOL], sim_now_ms=T0)
        signal = build_signal(emitted[0], T0, "test@0", "synthetic", "replay")
        self.assertEqual(signal.classification, BLOCKED)
        self.assertIn("data_or_signal_build_failed", signal.reason_codes)
        self.assertIn("missing required columns", signal.error)
        kline_calls = [call for call in provider.calls if call["kind"] == "kline"]
        self.assertTrue(kline_calls)
        self.assertTrue(all(call["returned"] == 0 for call in kline_calls))

    def test_clock_shim_follows_simulated_time(self):
        harness = PaperHarness(MarketDataProvider())
        harness.clock.set_sim_time(T0 + 8 * 3_600_000)
        self.assertEqual(harness.clock.now(timezone.utc).hour, 8)
        harness.clock.set_sim_time(None)
        self.assertGreater(harness.clock.now(timezone.utc).year, 2020)

    def test_classify_circuit_breaker_hold(self):
        emitted = {"action": "hold", "symbol": "PORTFOLIO", "confidence": 1.0,
                   "meta": {"circuit_breaker": "LOCKED_24H"}}
        classification, reasons = classify_emitted(emitted)
        self.assertEqual(classification, BLOCKED)
        self.assertEqual(reasons, ("circuit_breaker_lock",))

# ---------------------------------------------------------------------------
# No look-ahead guarantees
# ---------------------------------------------------------------------------
class TestNoLookAhead(unittest.TestCase):
    def test_provider_serves_closed_bars_only(self):
        source = SyntheticSource(seed=3)
        bars = source.fetch(SYMBOL, "4h", end_ms=T0, bars_needed=40)
        provider = MarketDataProvider()
        provider.set_bars(SYMBOL, "4h", bars)
        cut = bars[20].time_ms + STEP  # close time of bar 20
        provider.set_sim_time(cut)
        rows = provider.kline(symbol=SYMBOL, interval="4h", exchange="bitget", limit=120)
        self.assertEqual(len(rows), 21)
        newest_close = rows[-1]["time"] + STEP
        self.assertLessEqual(newest_close, cut)
        call = provider.calls[-1]
        self.assertLessEqual(call["newest_close_ms"], cut)

    def test_provider_honors_limit_with_newest_bars(self):
        source = SyntheticSource(seed=3)
        bars = source.fetch(SYMBOL, "4h", end_ms=T0, bars_needed=40)
        provider = MarketDataProvider()
        provider.set_bars(SYMBOL, "4h", bars)
        provider.set_sim_time(T0 + STEP)
        rows = provider.kline(symbol=SYMBOL, interval="4h", exchange="bitget", limit=5)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1]["time"], bars[-1].time_ms)

    def test_decisions_are_prefix_invariant(self):
        source = SyntheticSource(seed=11)
        daily = source.fetch(SYMBOL, "1d", end_ms=T0, bars_needed=220)
        four = source.fetch(SYMBOL, "4h", end_ms=T0, bars_needed=150)
        cut_index = 140
        cut = four[cut_index].time_ms + STEP

        def decide(bars_4h):
            provider = MarketDataProvider()
            provider.set_bars(SYMBOL, "1d", daily)
            provider.set_bars(SYMBOL, "4h", bars_4h)
            harness = PaperHarness(provider)
            config = {**copy.deepcopy(BASE_CONFIG), "margin_budget": "1000",
                      "trading_symbols": [SYMBOL]}
            return harness.run_decision(config, [SYMBOL], sim_now_ms=cut)

        full = decide(four)
        truncated = decide([bar for bar in four if bar.time_ms + STEP <= cut])
        strip = lambda items: [
            dict(item) for item in items if not is_portfolio_summary(item)
        ]
        self.assertEqual(strip(full), strip(truncated))

    def test_replay_never_queries_future_bars(self):
        config = dataclasses.replace(
            PaperConfig(source="synthetic", days=3, symbols=(SYMBOL,)),
            output_dir=Path(tempfile.mkdtemp()),
        )
        dataset = fetch_replay_dataset(config, [SYMBOL], now_ms=T0)
        provider = MarketDataProvider(replay_funding=config.replay_funding)
        for symbol, by_interval in dataset.bars.items():
            for interval, bars in by_interval.items():
                provider.set_bars(symbol, interval, bars)
        harness = PaperHarness(provider)
        simulator = make_sim()
        run_replay(config, MANIFEST, dataset, simulator, harness)
        kline_calls = [call for call in provider.calls if call["kind"] == "kline"]
        self.assertTrue(kline_calls)
        for call in kline_calls:
            self.assertIsNotNone(call["sim_now_ms"])
            if call["newest_close_ms"] is not None:
                self.assertLessEqual(call["newest_close_ms"], call["sim_now_ms"])
            self.assertLessEqual(call["returned"], call["limit"])


# ---------------------------------------------------------------------------
# Replay pipeline
# ---------------------------------------------------------------------------
class TestReplay(unittest.TestCase):
    def _small_run(self, seed=42, days=2, symbols=(SYMBOL,)):
        config = dataclasses.replace(
            PaperConfig(source="synthetic", days=days, symbols=symbols, seed=seed),
            output_dir=Path(tempfile.mkdtemp()),
        )
        dataset = fetch_replay_dataset(config, list(symbols), now_ms=T0)
        provider = MarketDataProvider(replay_funding=config.replay_funding)
        for symbol, by_interval in dataset.bars.items():
            for interval, bars in by_interval.items():
                provider.set_bars(symbol, interval, bars)
        harness = PaperHarness(provider)
        strategy_params = {**copy.deepcopy(BASE_CONFIG),
                           "margin_budget": str(config.initial_balance)}
        simulator = PaperSimulator(config, strategy_params,
                                   psar_fn=make_psar_fn(harness, dataset))
        outcome = run_replay(config, MANIFEST, dataset, simulator, harness)
        return config, dataset, outcome, simulator

    def test_dataset_shape_and_window(self):
        config = dataclasses.replace(
            PaperConfig(source="synthetic", days=3), output_dir=Path(tempfile.mkdtemp()))
        dataset = fetch_replay_dataset(config, [SYMBOL], now_ms=T0)
        # The replay window is inclusive at both ends, so a 3-day window at
        # 4h resolution yields days * 6 + 1 decision times.
        self.assertEqual(len(dataset.decision_times), config.days * 6 + 1)
        self.assertEqual(len(dataset.bars[SYMBOL]["1d"]), MIN_BARS["1d"] + 3 + 2)
        self.assertEqual(len(dataset.bars[SYMBOL]["4h"]), MIN_BARS["4h"] + 18 + 2)
        self.assertLessEqual(dataset.window_end_ms, T0)
        self.assertEqual(dataset.decision_times[-1], T0)

    def test_replay_is_deterministic(self):
        _c1, _d1, first, sim1 = self._small_run()
        _c2, _d2, second, sim2 = self._small_run()

        def canon(outcome, sim):
            return json.dumps({
                "signals": [s.to_json() for s in outcome.signals],
                "equity": sim.equity_history,
                "fills": sim.fills,
                "closed": sim.closed_trades,
                "events": sim.events,
            }, sort_keys=True, default=str)

        self.assertEqual(canon(first, sim1), canon(second, sim2))

    def test_replay_step_count_and_coverage(self):
        _config, dataset, outcome, simulator = self._small_run()
        self.assertEqual(outcome.steps, len(dataset.decision_times))
        self.assertEqual(len(simulator.equity_history), outcome.steps)
        self.assertEqual(outcome.coverage[SYMBOL]["steps_with_data"], outcome.steps)
        self.assertTrue(all(s.symbol == SYMBOL for s in outcome.signals))
        self.assertTrue(all(s.classification in (ACTIONABLE, BLOCKED, INFORMATIONAL)
                            for s in outcome.signals))

    def test_insufficient_history_blocks_once_without_running_strategy(self):
        source = SyntheticSource(seed=5)
        four = source.fetch(SYMBOL, "4h", end_ms=T0, bars_needed=140)
        daily = source.fetch(SYMBOL, "1d", end_ms=T0, bars_needed=50)  # < MIN_BARS
        closes = sorted(bar.time_ms + STEP for bar in four)
        dataset = ReplayDataset("synthetic", {SYMBOL: {"1d": daily, "4h": four}},
                                closes[-6:], closes[-6], closes[-1])
        provider = MarketDataProvider()
        provider.set_bars(SYMBOL, "1d", daily)
        provider.set_bars(SYMBOL, "4h", four)
        harness = PaperHarness(provider)
        config = dataclasses.replace(PaperConfig(source="synthetic", days=3),
                                     output_dir=Path(tempfile.mkdtemp()))
        simulator = make_sim()
        outcome = run_replay(config, MANIFEST, dataset, simulator, harness)
        blocked = [s for s in outcome.signals if s.classification == BLOCKED
                   and "insufficient_history" in s.reason_codes]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(outcome.coverage[SYMBOL]["steps_with_data"], 0)
        kline_calls = [call for call in provider.calls if call["kind"] == "kline"
                       and call["symbol"] == SYMBOL]
        self.assertEqual(kline_calls, [])
        self.assertEqual(simulator.fills, [])

    def test_coverage_gate_boundaries(self):
        ok, _why = coverage_ok({"1d": [0] * MIN_BARS["1d"], "4h": [0] * MIN_BARS["4h"]})
        self.assertTrue(ok)
        ok, why = coverage_ok({"1d": [0] * (MIN_BARS["1d"] - 1), "4h": [0] * MIN_BARS["4h"]})
        self.assertFalse(ok)
        self.assertIn("daily", why)
        ok, why = coverage_ok({"1d": [0] * MIN_BARS["1d"], "4h": []})
        self.assertFalse(ok)
        self.assertIn("4h", why)


# ---------------------------------------------------------------------------
# Market data guards (no fabrication, no private API)
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestMarketDataGuards(unittest.TestCase):
    def test_parse_candle_row_accepts_valid_row(self):
        bar = parse_candle_row([T0, "100.5", "101.5", "99.5", "101.0", "12.5", "1256.25"])
        self.assertIsInstance(bar, KlineBar)
        self.assertEqual(bar.time_ms, T0)
        self.assertEqual(bar.close, 101.0)

    def test_parse_candle_row_drops_malformed_rows(self):
        bad_rows = [
            None,
            "not-a-row",
            [T0, "1", "1", "1"],                       # too short
            [T0, "1", "x", "1", "1", "1"],             # non-numeric
            [0, "1", "1", "1", "1", "1"],              # non-positive ts
            [T0, "1", "nan", "1", "1", "1"],           # NaN
            [T0, "100", "99", "101", "100", "1"],      # high < low
            [T0, "100", "100.5", "99.5", "101", "1"],  # close above high
            [T0, "100", "100.5", "99.5", "99", "1"],   # close below low
        ]
        for row in bad_rows:
            self.assertIsNone(parse_candle_row(row), f"row should be dropped: {row!r}")

    def test_private_hosts_are_blocked_before_any_request(self):
        original = md.urllib.request.urlopen

        def never(*args, **kwargs):
            raise AssertionError("network must never be touched")

        md.urllib.request.urlopen = never
        try:
            with self.assertRaises(MarketDataError):
                md._get_json("https://evil.example.com/api/v2/mix/market/candles")
            with self.assertRaises(MarketDataError):
                md._get_json("https://api.bitget.com/api/v2/mix/order/place-order")
            with self.assertRaises(MarketDataError):
                md._get_json("https://api.bitget.com/api/v2/mix/account/accounts")
        finally:
            md.urllib.request.urlopen = original

    def test_public_request_is_get_without_auth_headers(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _FakeResponse({"code": "00000",
                                  "data": [[str(T0), "1", "1", "1", "1", "1"]]})

        original = md.urllib.request.urlopen
        md.urllib.request.urlopen = fake_urlopen
        try:
            data = md._get_json(
                "https://api.bitget.com/api/v2/mix/market/candles?symbol=BTCUSDT")
        finally:
            md.urllib.request.urlopen = original
        request = captured["request"]
        self.assertEqual(request.get_method(), "GET")
        header_names = {name.lower() for name in request.headers}
        for forbidden in ("access-key", "authorization", "access-sign",
                          "access-passphrase", "access-timestamp"):
            self.assertNotIn(forbidden, header_names)
        self.assertEqual(len(data), 1)

    def test_bad_envelope_raises(self):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse({"code": "40001", "msg": "invalid sign"})

        original = md.urllib.request.urlopen
        md.urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(MarketDataError):
                md._get_json(
                    "https://api.bitget.com/api/v2/mix/market/candles?symbol=BTCUSDT")
        finally:
            md.urllib.request.urlopen = original

    def test_assert_fresh_rejects_stale_and_empty(self):
        with self.assertRaises(InsufficientDataError):
            assert_fresh([], "4h")
        stale = [make_bar(100.0, T0 - 10 * STEP)]
        with self.assertRaises(InsufficientDataError):
            assert_fresh(stale, "4h", now_ms=T0)
        fresh = [make_bar(100.0, T0 - STEP)]
        assert_fresh(fresh, "4h", now_ms=T0)  # must not raise

    def test_synthetic_source_is_deterministic_and_labeled(self):
        first = SyntheticSource(seed=7).fetch(SYMBOL, "4h", end_ms=T0, bars_needed=30)
        second = SyntheticSource(seed=7).fetch(SYMBOL, "4h", end_ms=T0, bars_needed=30)
        self.assertEqual([bar.__dict__ for bar in first], [bar.__dict__ for bar in second])
        other = SyntheticSource(seed=8).fetch(SYMBOL, "4h", end_ms=T0, bars_needed=30)
        self.assertNotEqual(first[-1].close, other[-1].close)
        self.assertEqual(SyntheticSource().name, "synthetic")
        # closed-bars-only guarantee: the last bar must close at or before end
        self.assertLessEqual(first[-1].time_ms + STEP, T0)

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            bars = SyntheticSource(seed=7).fetch(SYMBOL, "4h", end_ms=T0, bars_needed=10)
            save_bars(Path(tmp), "rest", SYMBOL, "4h", bars)
            cached = CacheSource(Path(tmp) / "rest").fetch(SYMBOL, "4h")
            self.assertEqual([bar.__dict__ for bar in cached],
                             [bar.__dict__ for bar in bars])
            self.assertEqual(CacheSource(Path(tmp)).fetch_funding_rate(SYMBOL), [])

    def test_package_has_no_order_or_credential_surface(self):
        forbidden = (
            "place-order", "placeOrder", "place_order", "/api/v2/mix/order",
            "/api/v2/spot/trade", "ACCESS-KEY", "ACCESS-SIGN", "apiKey",
            "passphrase", "os.environ", "getenv", "requests.post",
        )
        package_dir = REPO_ROOT / "paper"
        sources = sorted(package_dir.glob("*.py"))
        self.assertTrue(sources)
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{path.name} must not contain {token!r}")


# ---------------------------------------------------------------------------
# Snapshot mode
# ---------------------------------------------------------------------------
class _BrokenSource:
    name = "broken"

    def fetch(self, symbol, interval, end_ms=None, bars_needed=None):
        raise MarketDataError("boom")

    def fetch_funding_rate(self, symbol):
        return []


class TestSnapshot(unittest.TestCase):
    def test_generate_now_classifies_every_symbol(self):
        config = dataclasses.replace(
            PaperConfig(source="synthetic"), output_dir=Path(tempfile.mkdtemp()))
        two_am = T0 + 2 * 3_600_000
        result = generate_now(config, MANIFEST, now_ms=two_am)
        self.assertEqual(result.data_source, "synthetic")
        self.assertEqual({s.symbol for s in result.signals}, set(trading_symbols(MANIFEST)))
        xau = [s for s in result.signals if s.symbol == "XAUUSDT"][0]
        self.assertEqual(xau.classification, BLOCKED)  # 02:00 UTC is out of session
        self.assertEqual(xau.reason_codes, ("gold_london_or_ny_session",))
        self.assertTrue(all(s.timestamp_ms == two_am for s in result.signals))
        self.assertTrue(all(s.mode == "live_snapshot" for s in result.signals))
        for symbol in trading_symbols(MANIFEST):
            self.assertEqual(result.coverage[symbol]["status"], "ok")

    def test_generate_now_rejects_cache_source(self):
        config = dataclasses.replace(PaperConfig(source="cache"),
                                     output_dir=Path(tempfile.mkdtemp()))
        with self.assertRaises(ValueError):
            generate_now(config, MANIFEST, now_ms=T0)

    def test_generate_now_reports_unavailable_data_honestly(self):
        config = dataclasses.replace(PaperConfig(source="synthetic"),
                                     output_dir=Path(tempfile.mkdtemp()))
        original = signals_module.build_source
        signals_module.build_source = lambda *args, **kwargs: _BrokenSource()
        try:
            result = generate_now(config, MANIFEST, now_ms=T0)
        finally:
            signals_module.build_source = original
        self.assertEqual(len(result.errors), 4)
        self.assertTrue(all(s.classification == BLOCKED for s in result.signals))
        self.assertTrue(all("market_data_unavailable" in s.reason_codes
                            for s in result.signals))


# ---------------------------------------------------------------------------
# Storage, summary and output hygiene
# ---------------------------------------------------------------------------
class TestStorage(unittest.TestCase):
    def _run_artifacts(self, tmp):
        sim = make_sim()
        enter(sim)
        signal = make_signal()
        config = PaperConfig(source="synthetic")
        summary = storage.build_summary(
            simulator=sim, config=config, mode="replay", data_source="synthetic",
            strategy_version="test@0", generated_at_ms=T0, signals=[signal],
            coverage={SYMBOL: {"steps_with_data": 1}}, notes=["test"],
        )
        metadata = {"mode": "replay"}
        paths = storage.write_run(Path(tmp), signals=[signal], metadata=metadata,
                                  summary=summary, simulator=sim)
        return sim, summary, paths

    def test_summary_carries_paper_labels_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            _sim, summary, _paths = self._run_artifacts(tmp)
            self.assertEqual(summary["labels"], list(PAPER_LABELS))
            self.assertIn("PAPER", summary["labels"])
            self.assertIn("SIMULATED", summary["labels"])
            self.assertRegex(summary["last_updated"], ISO_RE)
            self.assertIn("No live orders", summary["disclaimer"])
            self.assertEqual(summary["initial_balance"], 1000.0)

    def test_write_run_creates_reproducible_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            _sim, _summary, paths = self._run_artifacts(tmp)
            for name in ("signals", "fills", "positions", "equity", "events",
                         "metadata", "summary"):
                self.assertIn(name, paths)
                self.assertTrue(Path(paths[name]).exists(), name)
            equity_rows = [json.loads(line) for line in
                           Path(paths["equity"]).read_text(encoding="utf-8").splitlines()]
            self.assertTrue(equity_rows)
            Decimal(equity_rows[-1]["equity"])  # exact decimal strings, not floats
            fills = [json.loads(line) for line in
                     Path(paths["fills"]).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(fills[0]["side"], "entry")

    def test_outputs_contain_no_secret_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            _sim, _summary, paths = self._run_artifacts(tmp)
            for path in paths.values():
                text = Path(path).read_text(encoding="utf-8").lower()
                for token in ("api_key", "apikey", "secret", "passphrase",
                              "access-key", "authorization", "bitget_access"):
                    self.assertNotIn(token, text, f"{path} leaked {token!r}")

    def test_export_to_dashboard_copies_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            dash = Path(tmp) / "dash" / "paper-summary.json"
            _sim, summary, _paths = self._run_artifacts(out)
            path = storage.export_to_dashboard(output_dir=out, path=dash)
            self.assertEqual(path, dash)
            copied = json.loads(dash.read_text(encoding="utf-8"))
            self.assertEqual(copied["labels"], ["PAPER", "SIMULATED"])
            missing = Path(tmp) / "empty"
            with self.assertRaises(FileNotFoundError):
                storage.export_to_dashboard(output_dir=missing, path=dash)

    def test_output_paths_are_gitignored(self):
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("output/", gitignore)
        self.assertIn("dashboard/public/", gitignore)
        self.assertTrue(str(OUTPUT_DIR).replace("\\", "/").endswith("output/paper"))
        self.assertIn("dashboard", str(DASHBOARD_EXPORT_PATH))

    def test_signal_run_summary_is_marked_signals_only(self):
        summary = storage.build_signal_run_summary(
            config=PaperConfig(source="synthetic"), mode="live_snapshot",
            data_source="synthetic", strategy_version="test@0", generated_at_ms=T0,
            signals=[make_signal(classification=BLOCKED)], coverage={})
        self.assertEqual(summary["status"], "signals_only")
        self.assertIsNone(summary["equity"])
        self.assertEqual(summary["labels"], ["PAPER", "SIMULATED"])


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class TestPaperConfig(unittest.TestCase):
    def test_defaults(self):
        config = PaperConfig()
        self.assertEqual(config.initial_balance, Decimal("1000"))
        self.assertEqual(config.source, "rest")
        self.assertEqual(config.replay_funding, "block")

    def test_validation(self):
        with self.assertRaises(ValueError):
            PaperConfig(source="bogus")
        with self.assertRaises(ValueError):
            PaperConfig(replay_funding="whatever")
        with self.assertRaises(ValueError):
            PaperConfig(initial_balance=Decimal("0"))
        with self.assertRaises(ValueError):
            PaperConfig(fee_pct=Decimal("-1"))
        with self.assertRaises(ValueError):
            PaperConfig(days=0)

    def test_manifest_helpers(self):
        self.assertEqual(strategy_version(MANIFEST), "the-morning-sword@1.0.0")
        self.assertEqual(trading_symbols(MANIFEST),
                         ("BTCUSDT", "XAUUSDT", "AXTIUSDT", "SP500USDT"))


if __name__ == "__main__":
    unittest.main()