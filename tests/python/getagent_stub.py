"""Local stand-in for the `getagent` Playbook sandbox SDK.

`getagent` is injected by the GetAgent sandbox at runtime and is NOT available
on PyPI (see requirements.txt). To unit-test `src/main.py` locally we install a
minimal in-memory stub into `sys.modules` BEFORE importing the strategy module.

The stub records every `emit_signal` call and lets a test preload the bars and
funding rows that `data.crypto.futures.*` should return, so tests are fully
offline and deterministic. No network access is performed.
"""
from __future__ import annotations

import sys
import types

import pandas as pd

SRC_MAIN_NAME = "strategy_main"


class _Futures:
    """Stands in for `getagent.data.crypto.futures`."""

    def __init__(self) -> None:
        self.kline_payload: list[dict] = []
        self.funding_payload: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    def kline(self, **kwargs):
        self.calls.append(("kline", kwargs))
        return list(self.kline_payload)

    def funding_rate(self, **kwargs):
        self.calls.append(("funding_rate", kwargs))
        return list(self.funding_payload)


class _Crypto:
    def __init__(self) -> None:
        self.futures = _Futures()


class _DataModule(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("getagent.data")
        self.crypto = _Crypto()

    def to_dataframe(self, rows):
        return pd.DataFrame(list(rows))


class _RuntimeModule(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("getagent.runtime")
        self.manifest: dict = {}
        self.signals: list[dict] = []

    def emit_signal(self, **kwargs) -> None:
        self.signals.append(kwargs)

    def reset(self) -> None:
        self.signals = []


def install():
    """Install (or return the already installed) getagent stub. Idempotent."""
    existing = sys.modules.get("getagent")
    if existing is not None and getattr(existing, "__is_local_stub__", False):
        return existing, existing.data, existing.runtime

    data = _DataModule()
    runtime = _RuntimeModule()
    package = types.ModuleType("getagent")
    package.data = data
    package.runtime = runtime
    package.__is_local_stub__ = True

    sys.modules["getagent"] = package
    sys.modules["getagent.data"] = data
    sys.modules["getagent.runtime"] = runtime
    return package, data, runtime


def load_strategy():
    """Import src/main.py against the stub. Returns (module, data, runtime)."""
    import importlib.util
    from pathlib import Path

    _package, data, runtime = install()
    runtime.reset()

    src_main = Path(__file__).resolve().parents[2] / "src" / "main.py"
    if not src_main.exists():
        raise FileNotFoundError(f"strategy source not found: {src_main}")

    # main.py falls back to `from instruments import ...` when it is loaded by
    # file path rather than as `python -m src.main`, so src/ must be importable.
    src_dir = str(src_main.parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    spec = importlib.util.spec_from_file_location(SRC_MAIN_NAME, str(src_main))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, data, runtime


def make_bars(closes, volumes=None, spread=0.5):
    """Build OHLCV rows from a close series. High/low are close +/- spread."""
    rows = []
    for index, close in enumerate(closes):
        volume = float(volumes[index]) if volumes is not None else 100.0
        rows.append(
            {
                "open": float(close),
                "high": float(close) + spread,
                "low": float(close) - spread,
                "close": float(close),
                "volume": volume,
            }
        )
    return rows

