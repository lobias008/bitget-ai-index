"""Unit tests for the Morning Sword Playbook strategy (src/main.py).

These tests are offline and deterministic. They never touch the network, never
emit a real order, and never mutate the strategy source.

The sandbox SDK is stubbed by getagent_stub. Indicator functions are patched
where a test targets decision LOGIC rather than indicator math, and exercised
for real where a test targets indicator math.
"""
from __future__ import annotations

import ast
import unittest
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from getagent_stub import load_strategy, make_bars

strategy, data_stub, runtime_stub = load_strategy()

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_MAIN = REPO_ROOT / "src" / "main.py"

# Mirrors ALLOWED_IMPORTS in the official validator. If the strategy ever adds
# a disallowed import, upload would fail with HTTP 422; this catches it first.
ALLOWED_IMPORTS = {
    "getagent", "getclaw", "nautilus_trader", "pandas", "numpy", "json", "math",
    "datetime", "pathlib", "asyncio", "typing", "dataclasses", "collections",
    "functools", "re", "decimal", "statistics", "itertools", "operator", "copy",
    "enum", "abc", "numbers", "fractions",
}

GATE_KEYS = {
    "daily_ema_stack",
    "four_hour_ema_pullback",
    "three_candle_volume_spike",
    "rsi_40_to_60",
    "btc_funding_filter",
    "gold_london_or_ny_session",
    "oil_parabolic_sar_confirmation",
}


@contextmanager
def patched(**overrides):
    originals = {}
    for name, value in overrides.items():
        originals[name] = getattr(strategy, name)
        setattr(strategy, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            setattr(strategy, name, value)


def constant_series(value, length=30):
    return pd.Series([float(value)] * length)


def ema_lookup(mapping):
    def _ema(frame, period):
        return pd.Series([float(mapping[period])] * len(frame), index=frame.index)

    return _ema


def frame_with_volumes(volumes, close=80.0):
    return pd.DataFrame(make_bars([close] * len(volumes), volumes=volumes))


# 27 flat bars then 3 loud bars: satisfies the three-candle volume spike rule
# against a 20-bar rolling mean baseline.
SPIKE_VOLUMES = [100.0] * 27 + [500.0, 500.0, 500.0]
FLAT_VOLUMES = [100.0] * 30

BASE_PATCHES = dict(
    _fetch_frame=lambda symbol, interval, limit: frame_with_volumes(SPIKE_VOLUMES),
    _ema=ema_lookup({20: 110.0, 50: 100.0, 200: 90.0}),
    _rsi=lambda frame, period=14: constant_series(50.0, len(frame)),
    _atr=lambda frame, period=14: constant_series(2.0, len(frame)),
    _parabolic_sar=lambda frame, acceleration=0.02, maximum=0.2: constant_series(70.0, len(frame)),
    _funding_ok=lambda symbol, config, registry=None: (True, 0.01),
    _session_ok=lambda symbol, config, registry=None: True,
)


class TestNumericCoercion(unittest.TestCase):
    def test_valid_numeric_string(self):
        self.assertEqual(strategy._to_float("1.5"), 1.5)

    def test_none_falls_back_to_default(self):
        self.assertEqual(strategy._to_float(None, 7.0), 7.0)

    def test_garbage_falls_back_to_default(self):
        self.assertEqual(strategy._to_float("not-a-number", 3.0), 3.0)

    def test_nan_falls_back_to_default(self):
        self.assertEqual(strategy._to_float(float("nan"), 2.0), 2.0)

    def test_infinity_falls_back_to_default(self):
        self.assertEqual(strategy._to_float(float("inf"), 2.0), 2.0)

    def test_latest_of_empty_series(self):
        self.assertEqual(strategy._latest(pd.Series(dtype=float), 1.25), 1.25)

    def test_latest_takes_final_value(self):
        self.assertEqual(strategy._latest(pd.Series([1.0, 2.0, 3.0])), 3.0)


class TestIndicators(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame(make_bars([float(value) for value in range(1, 61)]))

    def test_ema_length_matches_frame(self):
        self.assertEqual(len(strategy._ema(self.frame, 20)), len(self.frame))

    def test_ema_tracks_uptrend_above_midpoint(self):
        ema = strategy._ema(self.frame, 20)
        self.assertGreater(ema.iloc[-1], 30.0)

    def test_rsi_is_bounded_0_to_100(self):
        rsi = strategy._rsi(self.frame).dropna()
        self.assertTrue(((rsi >= 0) & (rsi <= 100)).all())

    def test_rsi_is_high_in_a_mostly_rising_series(self):
        closes = []
        price = 100.0
        for index in range(60):
            price += -0.5 if index % 5 == 4 else 1.0
            closes.append(price)
        mixed = pd.DataFrame(make_bars(closes))
        self.assertGreater(strategy._rsi(mixed).iloc[-1], 60.0)

    def test_rsi_is_na_when_the_window_contains_no_losses(self):
        # Real edge case: a pure uptrend has zero down-closes, so
        # loss.replace(0, pd.NA) makes relative strength - and RSI - NA.
        # _decision_for_symbol then coerces it to 0.0 via _to_float, which
        # fails the 40-60 gate. Net effect: parabolic runs are never entered.
        self.assertTrue(pd.isna(strategy._rsi(self.frame).iloc[-1]))

    def test_atr_is_positive_and_finite(self):
        atr = strategy._atr(self.frame).dropna()
        self.assertTrue((atr > 0).all())

    def test_parabolic_sar_length_matches_frame(self):
        self.assertEqual(len(strategy._parabolic_sar(self.frame)), len(self.frame))

    def test_parabolic_sar_below_price_in_uptrend(self):
        sar = strategy._parabolic_sar(self.frame).dropna()
        self.assertLess(sar.iloc[-1], self.frame["close"].iloc[-1])

    def test_parabolic_sar_short_frame_is_all_na(self):
        short = pd.DataFrame(make_bars([1.0, 2.0]))
        self.assertTrue(strategy._parabolic_sar(short).isna().all())


class TestPositionPlan(unittest.TestCase):
    CONFIG = {
        "margin_budget": "100",
        "risk_per_trade_pct": 1.0,
        "atr_stop_multiple": 1.5,
        "leverage": 3,
    }

    def test_stop_distance_is_atr_times_multiple(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, self.CONFIG)
        self.assertAlmostEqual(plan["stop_distance"], 3.0)

    def test_risk_budget_is_margin_times_risk_pct(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, self.CONFIG)
        self.assertAlmostEqual(plan["risk_usdt"], 1.0)

    def test_quantity_is_risk_divided_by_stop_distance(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, self.CONFIG)
        self.assertAlmostEqual(plan["quantity"], 1.0 / 3.0)

    def test_stop_loss_sits_one_stop_distance_below_entry(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, self.CONFIG)
        self.assertAlmostEqual(plan["stop_loss"], 97.0)

    def test_first_target_is_two_r(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, self.CONFIG)
        self.assertAlmostEqual(plan["take_profit_50pct_at_2r"], 106.0)

    def test_runner_target_is_four_r(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, self.CONFIG)
        self.assertAlmostEqual(plan["runner_target_4r_plus"], 112.0)

    def test_breakeven_lock_equals_entry(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, self.CONFIG)
        self.assertAlmostEqual(plan["move_stop_to_breakeven_after_2r"], 100.0)

    def test_runner_trails_with_parabolic_sar(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, self.CONFIG)
        self.assertEqual(plan["runner_trailing_stop"], "parabolic_sar")
        self.assertEqual(plan["state_after_partial"], "Free Trade")

    def test_notional_is_capped_by_margin_times_leverage(self):
        # Tiny stop distance would imply an enormous position; the cap must bind.
        plan = strategy._position_plan("BTCUSDT", 100000.0, 0.0001, self.CONFIG)
        self.assertAlmostEqual(plan["notional_usdt"], 300.0)

    def test_zero_atr_does_not_divide_by_zero(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 0.0, self.CONFIG)
        self.assertEqual(plan["quantity"], 0.0)
        self.assertEqual(plan["stop_distance"], 0.0)

    def test_missing_config_uses_documented_defaults(self):
        plan = strategy._position_plan("BTCUSDT", 100.0, 2.0, {})
        self.assertAlmostEqual(plan["stop_distance"], 3.0)
        self.assertAlmostEqual(plan["risk_usdt"], 1.0)


class TestSessionGate(unittest.TestCase):
    CONFIG = {"london_session_utc": [7, 10], "new_york_session_utc": [13, 16]}

    def _freeze(self, hour):
        class _FixedDatetime:
            @staticmethod
            def now(tz=None):
                return type("T", (), {"hour": hour})()

        return _FixedDatetime

    def test_non_gold_symbols_are_never_session_gated(self):
        with patched(datetime=self._freeze(3)):
            self.assertTrue(strategy._session_ok("BTCUSDT", self.CONFIG))

    def test_gold_allowed_during_london_open(self):
        with patched(datetime=self._freeze(8)):
            self.assertTrue(strategy._session_ok("XAUUSDT", self.CONFIG))

    def test_gold_allowed_during_new_york_open(self):
        with patched(datetime=self._freeze(14)):
            self.assertTrue(strategy._session_ok("XAUUSDT", self.CONFIG))

    def test_gold_blocked_during_asian_session(self):
        with patched(datetime=self._freeze(2)):
            self.assertFalse(strategy._session_ok("XAUUSDT", self.CONFIG))

    def test_gold_session_upper_bound_is_exclusive(self):
        with patched(datetime=self._freeze(10)):
            self.assertFalse(strategy._session_ok("XAUUSDT", self.CONFIG))


class TestFundingGate(unittest.TestCase):
    def test_non_btc_symbols_skip_the_funding_check(self):
        ok, rate = strategy._funding_ok("XAUUSDT", {})
        self.assertTrue(ok)
        self.assertIsNone(rate)

    def test_btc_passes_when_abs_funding_below_limit(self):
        data_stub.crypto.futures.funding_payload = [{"funding_rate": 0.0001}]
        ok, rate = strategy._funding_ok("BTCUSDT", {"funding_rate_abs_limit_pct": 0.05})
        self.assertTrue(ok)
        self.assertAlmostEqual(rate, 0.01)

    def test_btc_blocked_when_funding_exceeds_limit(self):
        data_stub.crypto.futures.funding_payload = [{"funding_rate": 0.004}]
        ok, _rate = strategy._funding_ok("BTCUSDT", {"funding_rate_abs_limit_pct": 0.05})
        self.assertFalse(ok)

    def test_btc_blocked_when_funding_data_missing(self):
        data_stub.crypto.futures.funding_payload = []
        ok, _rate = strategy._funding_ok("BTCUSDT", {"funding_rate_abs_limit_pct": 0.05})
        self.assertFalse(ok)


class TestConfluenceDecision(unittest.TestCase):
    def decision(self, **overrides):
        settings = {**BASE_PATCHES, **overrides}
        with patched(**settings):
            return strategy._decision_for_symbol("SP500USDT", {})

    def test_all_gates_passing_produces_long(self):
        result = self.decision()
        self.assertEqual(result["action"], "long")
        self.assertGreater(result["confidence"], 0.5)

    def test_every_documented_gate_is_reported(self):
        result = self.decision()
        self.assertEqual(set(result["meta"]["getclaw_checks"]), GATE_KEYS)

    def test_broken_daily_trend_stack_blocks_entry(self):
        result = self.decision(_ema=ema_lookup({20: 90.0, 50: 100.0, 200: 110.0}))
        self.assertEqual(result["action"], "watch")
        self.assertFalse(result["meta"]["getclaw_checks"]["daily_ema_stack"])

    def test_price_above_ema_zone_blocks_entry(self):
        result = self.decision(
            _fetch_frame=lambda symbol, interval, limit: frame_with_volumes(SPIKE_VOLUMES, close=200.0)
        )
        self.assertEqual(result["action"], "watch")
        self.assertFalse(result["meta"]["getclaw_checks"]["four_hour_ema_pullback"])

    def test_missing_volume_spike_blocks_entry(self):
        result = self.decision(
            _fetch_frame=lambda symbol, interval, limit: frame_with_volumes(FLAT_VOLUMES)
        )
        self.assertEqual(result["action"], "watch")
        self.assertFalse(result["meta"]["getclaw_checks"]["three_candle_volume_spike"])

    def test_rsi_above_60_blocks_entry(self):
        result = self.decision(_rsi=lambda frame, period=14: constant_series(72.0, len(frame)))
        self.assertEqual(result["action"], "watch")
        self.assertFalse(result["meta"]["getclaw_checks"]["rsi_40_to_60"])

    def test_rsi_below_40_blocks_entry(self):
        result = self.decision(_rsi=lambda frame, period=14: constant_series(25.0, len(frame)))
        self.assertEqual(result["action"], "watch")
        self.assertFalse(result["meta"]["getclaw_checks"]["rsi_40_to_60"])

    def test_funding_rejection_blocks_entry(self):
        result = self.decision(_funding_ok=lambda symbol, config, registry=None: (False, 0.42))
        self.assertEqual(result["action"], "watch")
        self.assertFalse(result["meta"]["getclaw_checks"]["btc_funding_filter"])

    def test_session_rejection_blocks_entry(self):
        result = self.decision(_session_ok=lambda symbol, config, registry=None: False)
        self.assertEqual(result["action"], "watch")
        self.assertFalse(result["meta"]["getclaw_checks"]["gold_london_or_ny_session"])

    def test_oil_proxy_requires_price_above_parabolic_sar(self):
        settings = {
            **BASE_PATCHES,
            "_parabolic_sar": lambda frame, acceleration=0.02, maximum=0.2: constant_series(95.0, len(frame)),
        }
        with patched(**settings):
            result = strategy._decision_for_symbol("AXTIUSDT", {})
        self.assertEqual(result["action"], "watch")
        self.assertFalse(result["meta"]["getclaw_checks"]["oil_parabolic_sar_confirmation"])

    def test_non_oil_symbols_ignore_parabolic_sar_gate(self):
        settings = {
            **BASE_PATCHES,
            "_parabolic_sar": lambda frame, acceleration=0.02, maximum=0.2: constant_series(95.0, len(frame)),
        }
        with patched(**settings):
            result = strategy._decision_for_symbol("SP500USDT", {})
        self.assertEqual(result["action"], "long")
        self.assertTrue(result["meta"]["getclaw_checks"]["oil_parabolic_sar_confirmation"])

    def test_na_rsi_degrades_to_watch_without_crashing(self):
        na_rsi = lambda frame, period=14: pd.Series([pd.NA] * len(frame), index=frame.index)
        result = self.decision(_rsi=na_rsi)
        self.assertEqual(result["action"], "watch")
        self.assertEqual(result["metrics"]["rsi_4h"], 0.0)
        self.assertFalse(result["meta"]["getclaw_checks"]["rsi_40_to_60"])

    def test_decision_includes_a_risk_plan(self):
        result = self.decision()
        plan = result["meta"]["risk_plan"]
        self.assertIn("stop_loss", plan)
        self.assertIn("take_profit_50pct_at_2r", plan)
        self.assertIn("runner_target_4r_plus", plan)


class TestCircuitBreaker(unittest.TestCase):
    def setUp(self):
        runtime_stub.reset()

    def test_breached_breaker_locks_portfolio_and_skips_symbols(self):
        runtime_stub.manifest = {
            "trading_symbols": ["BTCUSDT", "XAUUSDT"],
            "strategy_config": {
                "reported_daily_pnl_pct": -5.0,
                "circuit_breaker_daily_loss_pct": -2.0,
            },
        }
        with patched(_decision_for_symbol=lambda symbol, config, registry=None: self.fail(
            "strategy must not evaluate symbols while the circuit breaker is locked"
        )):
            strategy.run()

        self.assertEqual(len(runtime_stub.signals), 1)
        signal = runtime_stub.signals[0]
        self.assertEqual(signal["action"], "hold")
        self.assertEqual(signal["symbol"], "PORTFOLIO")
        self.assertEqual(signal["confidence"], 1.0)
        self.assertEqual(signal["meta"]["circuit_breaker"], "LOCKED_24H")
        self.assertIn("flatten_positions", signal["meta"]["actions"])

    def test_breaker_triggers_exactly_at_the_limit(self):
        runtime_stub.manifest = {
            "strategy_config": {
                "reported_daily_pnl_pct": -2.0,
                "circuit_breaker_daily_loss_pct": -2.0,
            },
        }
        strategy.run()
        self.assertEqual(runtime_stub.signals[0]["meta"]["circuit_breaker"], "LOCKED_24H")

    def test_healthy_pnl_evaluates_every_symbol_then_summarises(self):
        runtime_stub.manifest = {
            "trading_symbols": ["BTCUSDT", "XAUUSDT"],
            "strategy_config": {"reported_daily_pnl_pct": 0.0},
        }
        fake = {
            "action": "watch",
            "confidence": 0.38,
            "metrics": {},
            "meta": {"getclaw_checks": {}, "risk_plan": {}},
        }
        with patched(_decision_for_symbol=lambda symbol, config, registry=None: dict(fake)):
            strategy.run()

        self.assertEqual(len(runtime_stub.signals), 3)
        self.assertEqual([s["symbol"] for s in runtime_stub.signals], ["BTCUSDT", "XAUUSDT", "PORTFOLIO"])
        self.assertEqual(runtime_stub.signals[-1]["meta"]["strategy"], "The Morning Sword")

    def test_symbol_failure_degrades_to_watch_without_crashing(self):
        runtime_stub.manifest = {
            "trading_symbols": ["BTCUSDT"],
            "strategy_config": {"reported_daily_pnl_pct": 0.0},
        }

        def boom(symbol, config, registry=None):
            raise RuntimeError("exchange unreachable")

        with patched(_decision_for_symbol=boom):
            strategy.run()

        degraded = runtime_stub.signals[0]
        self.assertEqual(degraded["action"], "watch")
        self.assertEqual(degraded["confidence"], 0.0)
        self.assertIn("exchange unreachable", degraded["meta"]["error"])


class TestPackageHygiene(unittest.TestCase):
    def test_strategy_only_uses_allowlisted_imports(self):
        # Mirrors the validator: ALLOWED_IMPORTS plus local import roots, where
        # local roots are "src" and the stem of every module directly under it.
        # Relative imports (level > 0) are skipped by the validator entirely.
        local_roots = {"src"}
        src_dir = REPO_ROOT / "src"
        for path in src_dir.rglob("*.py"):
            first = path.relative_to(src_dir).parts[0]
            local_roots.add(first[:-3] if first.endswith(".py") else first)

        allowed = ALLOWED_IMPORTS | local_roots
        for source in (SRC_MAIN, src_dir / "instruments.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])
            disallowed = imported - allowed
            self.assertFalse(
                disallowed,
                f"{source.name} imports {sorted(disallowed)}, which the upload validator rejects",
            )

    def test_strategy_has_no_dynamic_code_execution(self):
        tree = ast.parse(SRC_MAIN.read_text(encoding="utf-8"))
        banned = {"eval", "exec", "compile", "__import__"}
        found = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned
        }
        self.assertFalse(found, f"banned dynamic execution calls present: {sorted(found)}")

    def test_strategy_exposes_required_entry_point(self):
        self.assertTrue(callable(getattr(strategy, "run", None)))


if __name__ == "__main__":
    unittest.main(verbosity=2)



