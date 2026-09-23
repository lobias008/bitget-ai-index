"""Local getagent harness for the paper-trading workflow.

Runs ``src/main.py`` UNMODIFIED against provider-served, closed-bar-only
public data:

  * A provider-backed stand-in for the ``getagent`` SDK is installed into
    ``sys.modules`` only while the strategy module is imported, then the
    previous modules are restored - other locally installed stubs (e.g. the
    unit-test stub in tests/python/getagent_stub.py) are never disturbed.
  * The strategy module's ``datetime`` attribute is replaced with a clock
    shim whose ``now()`` returns the simulated decision time during replays,
    so session gates use only information available at that instant
    (no look-ahead).
  * Every kline / funding_rate call is logged for auditability.

Trading rules and risk controls are never re-implemented or altered here;
this module only feeds the strategy data and records what it emits.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .market_data import INTERVAL_MS, KlineBar

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_MAIN = REPO_ROOT / "src" / "main.py"
STRATEGY_MODULE_NAME = "paper_strategy_main"
STUB_NAMES = ("getagent", "getagent.data", "getagent.runtime")
_DEFAULT_CLOCK = object()  # sentinel: clock follows sim_now_ms


class MarketDataProvider:
    """Serves the getagent data stub from preloaded bars - closed bars only.

    ``sim_now_ms`` is the simulated decision instant. A kline request never
    returns a bar whose close time is in the future relative to it, and never
    returns more than the requested ``limit`` bars. With ``sim_now_ms=None``
    (live snapshot mode) everything loaded is served - the public REST source
    has already filtered to closed bars - and funding is delegated to the
    source. During replays, funding history is not publicly documented, so
    ``replay_funding`` decides between failing the gate closed ("block",
    default) or assuming a neutral 0.0 rate ("neutral", documented
    simulation assumption).
    """

    def __init__(self, source=None, replay_funding: str = "block") -> None:
        if replay_funding not in ("block", "neutral"):
            raise ValueError("replay_funding must be 'block' or 'neutral'")
        self.source = source
        self.replay_funding = replay_funding
        self.sim_now_ms: Optional[int] = None
        self.calls: List[dict] = []
        self._bars: Dict[Tuple[str, str], List[KlineBar]] = {}
        self._closes: Dict[Tuple[str, str], List[int]] = {}

    def set_bars(self, symbol: str, interval: str, bars) -> None:
        ordered = sorted(bars, key=lambda bar: bar.time_ms)
        self._bars[(symbol, interval)] = ordered
        self._closes[(symbol, interval)] = [
            bar.time_ms + INTERVAL_MS[interval] for bar in ordered
        ]

    def bars(self, symbol: str, interval: str) -> List[KlineBar]:
        return list(self._bars.get((symbol, interval), ()))

    def set_sim_time(self, now_ms: Optional[int]) -> None:
        self.sim_now_ms = now_ms

    def closed_slice(self, symbol: str, interval: str, limit: Optional[int] = None) -> List[KlineBar]:
        key = (symbol, interval)
        bars = self._bars.get(key)
        if not bars:
            return []
        if self.sim_now_ms is None:
            end = len(bars)
        else:
            end = bisect_right(self._closes[key], self.sim_now_ms)
        start = max(0, end - int(limit)) if limit else 0
        return bars[start:end]

    def kline(self, **kwargs) -> List[dict]:
        symbol = kwargs.get("symbol")
        interval = kwargs.get("interval")
        limit = kwargs.get("limit")
        sliced = self.closed_slice(symbol, interval, limit)
        newest_close = None
        if sliced and interval in INTERVAL_MS:
            newest_close = sliced[-1].time_ms + INTERVAL_MS[interval]
        self.calls.append({
            "kind": "kline",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "sim_now_ms": self.sim_now_ms,
            "returned": len(sliced),
            "newest_close_ms": newest_close,
        })
        return [bar.to_row() for bar in sliced]

    def funding_rate(self, **kwargs) -> List[dict]:
        symbol = kwargs.get("symbol")
        self.calls.append({
            "kind": "funding_rate",
            "symbol": symbol,
            "sim_now_ms": self.sim_now_ms,
        })
        if self.sim_now_ms is None:
            if self.source is None:
                return []
            return list(self.source.fetch_funding_rate(symbol))
        if self.replay_funding == "neutral":
            return [{"funding_rate": 0.0}]
        return []


class _StubFutures:
    """Stands in for ``getagent.data.crypto.futures``."""

    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    def kline(self, **kwargs):
        return self._provider.kline(**kwargs)

    def funding_rate(self, **kwargs):
        return self._provider.funding_rate(**kwargs)


def _install_stub(provider: MarketDataProvider):
    data_module = types.ModuleType("getagent.data")
    data_module.crypto = types.SimpleNamespace(futures=_StubFutures(provider))
    data_module.to_dataframe = lambda rows: pd.DataFrame(list(rows))

    runtime_module = types.ModuleType("getagent.runtime")
    runtime_module.manifest = {}
    runtime_module.signals = []

    def emit_signal(**kwargs) -> None:
        runtime_module.signals.append(kwargs)

    runtime_module.emit_signal = emit_signal

    package = types.ModuleType("getagent")
    package.data = data_module
    package.runtime = runtime_module
    package.__is_paper_harness_stub__ = True

    saved = {name: sys.modules.get(name) for name in STUB_NAMES}
    sys.modules["getagent"] = package
    sys.modules["getagent.data"] = data_module
    sys.modules["getagent.runtime"] = runtime_module
    return runtime_module, saved


def _restore_stub(saved: Dict[str, object]) -> None:
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _make_clock():
    class _PaperDateTime(datetime):
        """datetime stand-in whose now() follows the simulated clock."""

        _sim_now_ms: Optional[int] = None

        @classmethod
        def set_sim_time(cls, now_ms: Optional[int]) -> None:
            cls._sim_now_ms = now_ms

        @classmethod
        def now(cls, tz=None):
            if cls._sim_now_ms is None:
                return datetime.now(tz)
            value = datetime.fromtimestamp(cls._sim_now_ms // 1000, tz=timezone.utc)
            return value.astimezone(tz) if tz is not None else value

    return _PaperDateTime


def _load_strategy_module():
    if not SRC_MAIN.exists():
        raise FileNotFoundError(f"strategy source not found: {SRC_MAIN}")
    src_dir = str(SRC_MAIN.parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    spec = importlib.util.spec_from_file_location(STRATEGY_MODULE_NAME, str(SRC_MAIN))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PaperHarness:
    """One strategy-module instance bound to one provider-backed stub.

    Each harness owns its strategy copy, so several harnesses (e.g. in unit
    tests) never share mutable state. The strategy file itself is read-only.
    """

    def __init__(self, provider: MarketDataProvider) -> None:
        self.provider = provider
        runtime_module, saved = _install_stub(provider)
        try:
            self.strategy = _load_strategy_module()
        finally:
            _restore_stub(saved)
        self.runtime = runtime_module
        self.clock = _make_clock()
        self.strategy.datetime = self.clock  # session-gate clock injection

    def run_decision(self, strategy_config: dict, symbols, sim_now_ms: Optional[int] = None,
                     clock_ms=_DEFAULT_CLOCK) -> List[dict]:
        """Run the unmodified strategy once; return its emitted signal dicts.

        ``sim_now_ms`` controls data slicing (None = serve everything loaded).
        ``clock_ms`` controls the session-gate clock and defaults to
        ``sim_now_ms``; live snapshots pass an explicit timestamp so the
        simulated clock stays deterministic while funding stays live.
        """
        self.provider.set_sim_time(sim_now_ms)
        self.clock.set_sim_time(sim_now_ms if clock_ms is _DEFAULT_CLOCK else clock_ms)
        self.runtime.signals = []
        self.runtime.manifest = {
            "strategy_config": dict(strategy_config),
            "trading_symbols": list(symbols),
        }
        self.strategy.run()
        return [dict(item) for item in self.runtime.signals]