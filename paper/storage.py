"""Durable outputs for paper runs: JSONL logs, JSON state, dashboard export.

Everything lands under output/paper/ (gitignored). Money values are stored
as exact Decimal strings in the logs; the dashboard summary carries
display-rounded numbers plus explicit PAPER / SIMULATED labels and a
last-updated timestamp. No secrets or personal account data are ever
written - this workflow has no access to any (public data only).
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from .config import DASHBOARD_EXPORT_PATH, OUTPUT_DIR, PAPER_LABELS
from .market_data import MIN_BARS
from .signals import (
    ACTIONABLE,
    BLOCKED,
    DAILY_INTERVAL,
    DECISION_INTERVAL,
    INFORMATIONAL,
    iso_utc,
)

SUMMARY_NAME = "paper-summary.json"
FILE_NAMES = {
    "signals": "signals.jsonl",
    "fills": "fills.jsonl",
    "positions": "positions.json",
    "equity": "equity.jsonl",
    "events": "events.jsonl",
    "metadata": "run-metadata.json",
    "summary": SUMMARY_NAME,
}


def _default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON serializable: {type(obj)!r}")


def ensure_dir(path: Path) -> Path:
    Path(path).mkdir(parents=True, exist_ok=True)
    return Path(path)


def write_jsonl(path: Path, rows: List[dict]) -> Path:
    path = Path(path)
    text = "".join(json.dumps(row, default=_default, sort_keys=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, payload) -> Path:
    path = Path(path)
    path.write_text(json.dumps(payload, indent=2, default=_default) + "\n", encoding="utf-8")
    return path


def build_data_coverage(signals, coverage):
    """Per-instrument data-readiness and signal-outcome rollup.

    Counts come from the actual run (the coverage dict and the emitted
    signals); the bar thresholds come from market_data.MIN_BARS. Nothing is
    hardcoded and no data is fabricated - a symbol that never ran is reported
    as pending history rather than invented. Two independent fields:
      data_eligibility: backtest_eligible | pending_history (data readiness only)
      signal_outcome:   actionable_setup | no_actionable_setup | gated | not_evaluated
    """
    min_daily = MIN_BARS[DAILY_INTERVAL]
    min_four = MIN_BARS[DECISION_INTERVAL]
    stats: Dict[str, dict] = {}
    for signal in signals:
        entry = stats.setdefault(
            signal.symbol,
            {"actionable": 0, "blocked": 0, "informational": 0, "reasons": {}},
        )
        classification = signal.classification
        if classification == ACTIONABLE:
            entry["actionable"] += 1
        elif classification == BLOCKED:
            entry["blocked"] += 1
            for reason in signal.reason_codes or ():
                entry["reasons"][reason] = entry["reasons"].get(reason, 0) + 1
        elif classification == INFORMATIONAL:
            entry["informational"] += 1
    instruments = []
    for symbol in sorted(coverage):
        cov = coverage.get(symbol) or {}
        daily = int(cov.get("daily_bars", 0))
        four = int(cov.get("four_hour_bars", 0))
        steps = int(cov.get("steps_with_data", 0))
        stat = stats.get(symbol, {"actionable": 0, "blocked": 0, "informational": 0, "reasons": {}})
        if daily >= min_daily and four >= min_four:
            data_eligibility = "backtest_eligible"
            if stat["actionable"] > 0:
                signal_outcome = "actionable_setup"
            elif stat["blocked"] > 0 and stat["informational"] == 0:
                signal_outcome = "gated"
            elif stat["informational"] > 0:
                signal_outcome = "no_actionable_setup"
            else:
                signal_outcome = "not_evaluated"
        else:
            data_eligibility = "pending_history"
            signal_outcome = "not_evaluated"
        instruments.append({
            "symbol": symbol,
            "daily_bars": daily,
            "four_hour_bars": four,
            "steps_with_data": steps,
            "actionable": stat["actionable"],
            "blocked": stat["blocked"],
            "informational": stat["informational"],
            "blocked_reason_counts": dict(sorted(stat["reasons"].items())),
            "data_eligibility": data_eligibility,
            "signal_outcome": signal_outcome,
        })
    return {
        "min_daily_bars": min_daily,
        "min_four_hour_bars": min_four,
        "instruments": instruments,
    }


def build_summary(*, simulator, config, mode: str, data_source: str, strategy_version: str,
                  generated_at_ms: int, signals: List, coverage: Dict, window: Optional[Dict] = None,
                  notes=()) -> dict:
    stats = simulator.stats()
    locked_until = stats.get("locked_until_ms")
    win_rate = stats.get("win_rate_pct")
    return {
        "schema_version": 1,
        "status": "ready",
        "labels": list(PAPER_LABELS),
        "disclaimer": "Simulated paper-trading results. No live orders were placed; no private API was used.",
        "last_updated": iso_utc(generated_at_ms),
        "generated_at_ms": generated_at_ms,
        "mode": mode,
        "data_source": data_source,
        "strategy_version": strategy_version,
        "initial_balance": round(float(config.initial_balance), 2),
        "equity": round(float(stats["equity"]), 2),
        "balance": round(float(stats["balance"]), 2),
        "realized_pnl": round(float(stats["realized_pnl"]), 2),
        "unrealized_pnl": round(float(stats["unrealized_pnl"]), 2),
        "return_pct": round(float(stats["return_pct"]), 2),
        "open_positions": simulator.open_positions_json(),
        "closed_trades": stats["closed_trades"],
        "win_rate_pct": round(win_rate, 1) if win_rate is not None else None,
        "circuit_breaker": {
            "threshold_pct": float(simulator.breaker_pct),
            "trips": stats["breaker_trips"],
            "locked": bool(stats["locked"]),
            "locked_until": iso_utc(locked_until) if locked_until else None,
        },
        "recent_signals": [signal.to_json() for signal in signals[-10:]],
        "coverage": coverage,
        "data_coverage": build_data_coverage(signals, coverage),
        "replay_funding": config.replay_funding,
        "window": window,
        "notes": list(notes),
    }


def build_signal_run_summary(*, config, mode: str, data_source: str, strategy_version: str,
                             generated_at_ms: int, signals: List, coverage: Dict,
                             notes=()) -> dict:
    """Summary for snapshot runs (no simulator state exists yet)."""
    return {
        "schema_version": 1,
        "status": "signals_only",
        "labels": list(PAPER_LABELS),
        "disclaimer": "Simulated paper-trading snapshot. No live orders were placed; no private API was used.",
        "last_updated": iso_utc(generated_at_ms),
        "generated_at_ms": generated_at_ms,
        "mode": mode,
        "data_source": data_source,
        "strategy_version": strategy_version,
        "initial_balance": round(float(config.initial_balance), 2),
        "equity": None,
        "balance": None,
        "realized_pnl": None,
        "unrealized_pnl": None,
        "return_pct": None,
        "open_positions": [],
        "closed_trades": 0,
        "win_rate_pct": None,
        "circuit_breaker": {"threshold_pct": -2.0, "trips": 0, "locked": False, "locked_until": None},
        "recent_signals": [signal.to_json() for signal in signals[-10:]],
        "coverage": coverage,
        "data_coverage": build_data_coverage(signals, coverage),
        "replay_funding": config.replay_funding,
        "window": None,
        "notes": list(notes),
    }


def write_run(output_dir: Path, *, signals: List, metadata: dict, summary: dict,
              simulator=None) -> Dict[str, Path]:
    """Write every artifact of a paper run. Each run overwrites the previous."""
    out = ensure_dir(Path(output_dir))
    paths: Dict[str, Path] = {}
    paths["signals"] = write_jsonl(out / FILE_NAMES["signals"], [s.to_json() for s in signals])
    paths["metadata"] = write_json(out / FILE_NAMES["metadata"], metadata)
    if simulator is not None:
        paths["fills"] = write_jsonl(out / FILE_NAMES["fills"], simulator.fills)
        paths["equity"] = write_jsonl(out / FILE_NAMES["equity"], simulator.equity_history)
        paths["events"] = write_jsonl(out / FILE_NAMES["events"], simulator.events)
        paths["positions"] = write_json(out / FILE_NAMES["positions"], {
            "as_of_ms": simulator.last_step_ms,
            "as_of": iso_utc(simulator.last_step_ms) if simulator.last_step_ms else None,
            "open_positions": simulator.open_positions_json(),
            "closed_trades": simulator.closed_trades,
        })
    paths["summary"] = write_json(out / FILE_NAMES["summary"], summary)
    return paths


def export_to_dashboard(summary: Optional[dict] = None, output_dir: Path = OUTPUT_DIR,
                        path: Path = DASHBOARD_EXPORT_PATH) -> Path:
    """Copy the run summary into dashboard/public/ for the dev server/build."""
    if summary is None:
        source = Path(output_dir) / SUMMARY_NAME
        if not source.exists():
            raise FileNotFoundError(
                f"no paper summary at {source}; run 'npm run paper:simulate' first"
            )
        summary = json.loads(source.read_text(encoding="utf-8"))
    path = Path(path)
    ensure_dir(path.parent)
    return write_json(path, summary)


def load_latest(output_dir: Path = OUTPUT_DIR) -> Optional[dict]:
    summary_path = Path(output_dir) / SUMMARY_NAME
    metadata_path = Path(output_dir) / FILE_NAMES["metadata"]
    if not summary_path.exists():
        return None
    payload = {"summary": json.loads(summary_path.read_text(encoding="utf-8"))}
    if metadata_path.exists():
        payload["metadata"] = json.loads(metadata_path.read_text(encoding="utf-8"))
    return payload