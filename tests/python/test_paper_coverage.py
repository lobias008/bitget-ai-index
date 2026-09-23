"""Unit tests for the paper data-coverage rollup (Milestone 2 dashboard).

build_data_coverage turns a run's real coverage + emitted signals into the
per-instrument fields the dashboard renders: data_eligibility (data readiness
only) and signal_outcome (what the strategy did). Thresholds come from
market_data.MIN_BARS; counts come from the signals/coverage passed in. Nothing
is hardcoded and no data is fabricated. Offline: no network, no orders.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paper.market_data import MIN_BARS  # noqa: E402
from paper.signals import (  # noqa: E402
    ACTIONABLE,
    BLOCKED,
    DAILY_INTERVAL,
    DECISION_INTERVAL,
    INFORMATIONAL,
)
from paper.storage import build_data_coverage  # noqa: E402


def sig(symbol, classification, reasons=()):
    return SimpleNamespace(symbol=symbol, classification=classification, reason_codes=tuple(reasons))


def cov(daily, four, steps):
    return {"daily_bars": daily, "four_hour_bars": four, "steps_with_data": steps}


class TestDataCoverage(unittest.TestCase):
    def test_thresholds_come_from_min_bars(self):
        out = build_data_coverage([], {})
        self.assertEqual(out["min_daily_bars"], MIN_BARS[DAILY_INTERVAL])
        self.assertEqual(out["min_four_hour_bars"], MIN_BARS[DECISION_INTERVAL])
        self.assertEqual(out["min_daily_bars"], 210)
        self.assertEqual(out["min_four_hour_bars"], 120)

    def test_pending_history_when_daily_bars_short(self):
        coverage = {"AXTIUSDT": cov(134, 799, 0), "SP500USDT": cov(123, 738, 0)}
        signals = [sig("AXTIUSDT", BLOCKED, ["insufficient_history"]),
                   sig("SP500USDT", BLOCKED, ["insufficient_history"])]
        by = {i["symbol"]: i for i in build_data_coverage(signals, coverage)["instruments"]}
        for sym, daily in (("AXTIUSDT", 134), ("SP500USDT", 123)):
            self.assertEqual(by[sym]["data_eligibility"], "pending_history")
            self.assertEqual(by[sym]["signal_outcome"], "not_evaluated")
            self.assertEqual(by[sym]["daily_bars"], daily)
            self.assertEqual(by[sym]["blocked_reason_counts"], {"insufficient_history": 1})

    def test_gated_when_all_steps_blocked_by_a_gate(self):
        coverage = {"BTCUSDT": cov(359, 799, 540)}
        signals = [sig("BTCUSDT", BLOCKED, ["btc_funding_filter"]) for _ in range(540)]
        inst = build_data_coverage(signals, coverage)["instruments"][0]
        self.assertEqual(inst["data_eligibility"], "backtest_eligible")
        self.assertEqual(inst["signal_outcome"], "gated")
        self.assertEqual(inst["blocked"], 540)
        self.assertEqual(inst["blocked_reason_counts"], {"btc_funding_filter": 540})

    def test_no_setup_when_watches_present(self):
        coverage = {"XAUUSDT": cov(283, 799, 442)}
        signals = ([sig("XAUUSDT", BLOCKED, ["gold_london_or_ny_session"]) for _ in range(369)]
                   + [sig("XAUUSDT", INFORMATIONAL, ["daily_ema_stack"]) for _ in range(73)])
        inst = build_data_coverage(signals, coverage)["instruments"][0]
        self.assertEqual(inst["data_eligibility"], "backtest_eligible")
        self.assertEqual(inst["signal_outcome"], "no_actionable_setup")
        self.assertEqual(inst["informational"], 73)
        self.assertEqual(inst["blocked_reason_counts"]["gold_london_or_ny_session"], 369)

    def test_actionable_setup_when_signal_present(self):
        coverage = {"BTCUSDT": cov(359, 799, 540)}
        signals = [sig("BTCUSDT", BLOCKED, ["btc_funding_filter"]) for _ in range(10)]
        signals.append(sig("BTCUSDT", ACTIONABLE))
        inst = build_data_coverage(signals, coverage)["instruments"][0]
        self.assertEqual(inst["data_eligibility"], "backtest_eligible")
        self.assertEqual(inst["signal_outcome"], "actionable_setup")
        self.assertEqual(inst["actionable"], 1)

    def test_eligibility_does_not_require_actionable(self):
        coverage = {"XAUUSDT": cov(283, 799, 442)}
        inst = build_data_coverage([sig("XAUUSDT", INFORMATIONAL, ["watch"])], coverage)["instruments"][0]
        self.assertEqual(inst["data_eligibility"], "backtest_eligible")
        self.assertEqual(inst["actionable"], 0)

    def test_four_hour_shortfall_is_pending_history(self):
        inst = build_data_coverage([], {"X": cov(300, 100, 0)})["instruments"][0]
        self.assertEqual(inst["data_eligibility"], "pending_history")


if __name__ == "__main__":
    unittest.main()