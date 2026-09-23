"""Paper-trading configuration. Simulation costs are configurable; strategy
risk rules are NOT - they always come from manifest.yaml via the registry."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Sequence, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "manifest.yaml"
OUTPUT_DIR = REPO_ROOT / "output" / "paper"
DASHBOARD_EXPORT_PATH = REPO_ROOT / "dashboard" / "public" / "paper-summary.json"

DEFAULT_INITIAL_BALANCE = Decimal("1000")
DEFAULT_FEE_PCT = Decimal("0.06")        # simulated taker-style fee, percent per side
DEFAULT_SLIPPAGE_PCT = Decimal("0.02")   # simulated adverse slippage, percent per fill
DEFAULT_DAYS = 90
SOURCES = ("rest", "synthetic", "cache")
PAPER_LABELS = ("PAPER", "SIMULATED")


@dataclass(frozen=True)
class PaperConfig:
    initial_balance: Decimal = DEFAULT_INITIAL_BALANCE
    fee_pct: Decimal = DEFAULT_FEE_PCT
    slippage_pct: Decimal = DEFAULT_SLIPPAGE_PCT
    source: str = "rest"
    days: int = DEFAULT_DAYS
    symbols: Tuple[str, ...] = ()          # empty = every actionable manifest symbol
    output_dir: Path = OUTPUT_DIR
    replay_funding: str = "block"          # block | neutral (documented simulation assumption)
    seed: int = 20260921

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}; got {self.source!r}")
        if self.replay_funding not in ("block", "neutral"):
            raise ValueError("replay_funding must be 'block' or 'neutral'")
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.fee_pct < 0 or self.slippage_pct < 0:
            raise ValueError("fee_pct and slippage_pct must be non-negative")
        if self.days <= 0:
            raise ValueError("days must be positive")


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        manifest = yaml.safe_load(handle)
    if not isinstance(manifest, dict):
        raise ValueError(f"{path} must parse to a mapping")
    return manifest


def strategy_config(manifest: dict) -> dict:
    return dict(manifest.get("strategy_config") or {})


def strategy_version(manifest: dict) -> str:
    return f"{manifest.get('name', 'unknown')}@{manifest.get('version', '0')}"


def trading_symbols(manifest: dict) -> Tuple[str, ...]:
    config = strategy_config(manifest)
    symbols = config.get("trading_symbols") or manifest.get("trading_symbols") or []
    return tuple(str(symbol) for symbol in symbols)


def decimal_or_default(value, default: Decimal) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return default
    return parsed if parsed.is_finite() else default
