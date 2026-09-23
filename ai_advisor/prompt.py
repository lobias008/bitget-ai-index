"""Prompt construction for the AI review layer.

The prompt carries ONLY information that was already available at the
simulated decision instant:

  * the deterministic signal the strategy emitted (its classification, plan
    levels, confidence and per-check results);
  * read-only instrument metadata from manifest.yaml;
  * closed public bars up to that instant, truncated to a fixed budget.

No future bar, no account data and no credential is ever included. The request
is deterministic for a given input so a replay can be reproduced exactly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from paper.signals import ACTIONABLE, INFORMATIONAL, iso_utc

from .config import AI_ROLE, AiConfig

PURPOSE_ENTRY_GATE = "entry_gate"
PURPOSE_WATCH_SAMPLE = "watch_sample"
PURPOSES = (PURPOSE_ENTRY_GATE, PURPOSE_WATCH_SAMPLE)

# Purpose -> the parent classification the AI is allowed to review. Anything
# else is "not_promotable" and never reaches a provider.
PURPOSE_CLASSIFICATION = {
    PURPOSE_ENTRY_GATE: ACTIONABLE,
    PURPOSE_WATCH_SAMPLE: INFORMATIONAL,
}

MAX_BARS_PER_INTERVAL = 30
REVIEW_INTERVALS = ("1d", "4h")
PROMPT_SCHEMA_VERSION = 1

SYSTEM_PROMPT = (
    "You are a read-only second-opinion reviewer inside a signal-only paper-trading "
    "research platform. A deterministic, already-audited strategy produced the signal you "
    "are shown. Your only job is to sanity-check it against the closed price history "
    "provided. You cannot create signals, change position size, move a stop, widen a risk "
    "limit, or place an order; the platform can only act on a signal the deterministic "
    "strategy already emitted, and every deterministic risk control still applies after "
    "you. Reply with exactly one JSON object and no other text."
)

RESPONSE_CONTRACT = {
    "format": "a single JSON object, no prose, no markdown fences",
    "required_keys": ["decision", "confidence", "reasoning", "reason_code"],
    "optional_keys": ["risk_notes"],
    "decision": {
        "type": "string",
        "allowed": ["confirm", "reject", "watch"],
        "confirm": "the setup is coherent with the supplied closed bars; it may proceed to the deterministic simulator",
        "reject": "the setup contradicts the supplied closed bars; it must not be simulated",
        "watch": "insufficient evidence either way; it must not be simulated",
    },
    "confidence": {"type": "number", "range": [0.0, 1.0]},
    "reasoning": {"type": "string", "max_chars": 1200, "must": "cite the supplied data, not outside knowledge"},
    "reason_code": {"type": "string", "pattern": "UPPER_SNAKE_CASE, 3-40 chars"},
    "risk_notes": {"type": "array of string", "max_items": 8, "max_chars_each": 240},
    "on_violation": "the reply is discarded as schema_invalid and the signal is NOT simulated",
}


class AiPromptError(ValueError):
    """A review request could not be built from the supplied inputs."""


@dataclass(frozen=True)
class ReviewRequest:
    purpose: str
    symbol: str
    model: Optional[str]
    temperature: float
    system: str
    user: str
    context: Dict[str, Any] = field(default_factory=dict)


def signal_json(signal: Any) -> Dict[str, Any]:
    """Accept a PaperSignal or an already-serialized signal dict."""
    serializer = getattr(signal, "to_json", None)
    if callable(serializer):
        payload = serializer()
    elif isinstance(signal, dict):
        payload = dict(signal)
    else:
        raise AiPromptError(f"unsupported signal type {type(signal).__name__}")
    if not str(payload.get("symbol", "")).strip():
        raise AiPromptError("signal is missing a symbol")
    return payload


def bars_payload(bars: Optional[Dict[str, Sequence[Any]]], limit: int = MAX_BARS_PER_INTERVAL) -> Dict[str, Any]:
    """Truncate each interval to the most recent `limit` CLOSED bars."""
    intervals: Dict[str, List[dict]] = {}
    total = 0
    for interval in sorted((bars or {})):
        series = list(bars.get(interval) or ())
        total += len(series)
        rows = []
        for bar in series[-limit:]:
            row = getattr(bar, "to_row", None)
            rows.append(dict(row()) if callable(row) else dict(bar))
        intervals[interval] = rows
    return {"bar_limit": int(limit), "total_closed_bars": int(total), "intervals": intervals}


def instrument_context(instrument: Any) -> Dict[str, Any]:
    """Read-only registry metadata; empty when the caller has none."""
    if instrument is None:
        return {}
    describe = getattr(instrument, "describe", None)
    if callable(describe):
        try:
            payload = describe()
        except Exception:
            return {}
        return dict(payload) if isinstance(payload, dict) else {}
    return dict(instrument) if isinstance(instrument, dict) else {}


def build_review_context(*, purpose: str, signal: Any, bars=None, instrument: Any = None,
                         data_source: str, mode: str, time_ms: Optional[int] = None,
                         bar_limit: int = MAX_BARS_PER_INTERVAL) -> Dict[str, Any]:
    if purpose not in PURPOSES:
        raise AiPromptError(f"unknown review purpose {purpose!r}")
    payload = signal_json(signal)
    if time_ms is None:
        time_ms = int(payload.get("timestamp_ms") or 0)
    if time_ms <= 0:
        raise AiPromptError("a review requires a positive decision timestamp")
    classification = str(payload.get("classification") or "")
    expected = PURPOSE_CLASSIFICATION[purpose]
    return {
        "task": "paper_signal_review",
        "schema_version": PROMPT_SCHEMA_VERSION,
        "purpose": purpose,
        "expected_parent_classification": expected,
        "parent_classification": classification,
        "promotable": classification == expected,
        "requested_symbol": str(payload.get("symbol")),
        "decision_time_ms": int(time_ms),
        "decision_time": iso_utc(int(time_ms)),
        "mode": mode,
        "data_source": data_source,
        "execution_mode": "signal_only",
        "ai_role": AI_ROLE,
        "instrument": instrument_context(instrument),
        "signal": payload,
        "market_data": bars_payload(bars, bar_limit),
        "response_contract": RESPONSE_CONTRACT,
    }


def build_review_request(*, purpose: str, signal: Any, ai_config: AiConfig, bars=None,
                         instrument: Any = None, data_source: str, mode: str,
                         time_ms: Optional[int] = None,
                         bar_limit: int = MAX_BARS_PER_INTERVAL) -> ReviewRequest:
    context = build_review_context(
        purpose=purpose, signal=signal, bars=bars, instrument=instrument,
        data_source=data_source, mode=mode, time_ms=time_ms, bar_limit=bar_limit,
    )
    user = json.dumps(context, sort_keys=True, default=str, separators=(",", ":"))
    return ReviewRequest(
        purpose=purpose,
        symbol=context["requested_symbol"],
        model=ai_config.resolved_model,
        temperature=float(ai_config.temperature),
        system=SYSTEM_PROMPT,
        user=user,
        context=context,
    )
