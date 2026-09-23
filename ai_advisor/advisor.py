"""The veto-only AI advisor.

The advisor wraps an untrusted model behind a closed set of outcomes. It can
only ever REMOVE a deterministic signal from the paper pipeline; it has no code
path that creates, resizes or re-levels one. Every non-accepting outcome -
including its own failures - drops the signal, so a broken model degrades the
system toward doing nothing rather than toward trading.

Outcome ladder, evaluated in this exact order:

  not_promotable          the signal is not the kind this purpose may review
  skipped_no_market_data  no closed bars were available at the decision time
  budget_exhausted        AI_MAX_CALLS was reached (no provider call is made)
  provider_error          the provider failed or returned an unusable reply
  schema_invalid          the reply violated the response contract
  accepted / vetoed / watched        the model's decision

``accepted`` is still not a trade: the signal then has to pass every
deterministic control in paper/simulator.py (duplicate guard, circuit breaker,
position-open guard, missing-bar guard, sizing and margin guards).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from paper.signals import iso_utc

from .config import AiConfig
from .prompt import (
    PURPOSES,
    PURPOSE_CLASSIFICATION,
    PURPOSE_ENTRY_GATE,
    PURPOSE_WATCH_SAMPLE,
    REVIEW_INTERVALS,
    AiPromptError,
    build_review_request,
    signal_json,
)
from .providers import AiProviderError, BaseProvider
from .schema import DECISION_CONFIRM, DECISION_REJECT, DECISION_WATCH, AiSchemaError, parse_decision

OUTCOME_ACCEPTED = "accepted"
OUTCOME_VETOED = "vetoed"
OUTCOME_WATCHED = "watched"
OUTCOME_NOT_PROMOTABLE = "not_promotable"
OUTCOME_SCHEMA_INVALID = "schema_invalid"
OUTCOME_PROVIDER_ERROR = "provider_error"
OUTCOME_BUDGET_EXHAUSTED = "budget_exhausted"
OUTCOME_NO_MARKET_DATA = "skipped_no_market_data"

OUTCOMES = (
    OUTCOME_ACCEPTED,
    OUTCOME_VETOED,
    OUTCOME_WATCHED,
    OUTCOME_NOT_PROMOTABLE,
    OUTCOME_SCHEMA_INVALID,
    OUTCOME_PROVIDER_ERROR,
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_NO_MARKET_DATA,
)

# Outcomes that must never be simulated. Listed explicitly so a new outcome
# cannot accidentally default to "allowed".
NON_ACCEPTING_OUTCOMES = tuple(name for name in OUTCOMES if name != OUTCOME_ACCEPTED)

REVIEW_JSON_KEYS = (
    "time_ms",
    "time",
    "symbol",
    "purpose",
    "parent_classification",
    "outcome",
    "decision",
    "confidence",
    "reasoning",
    "risk_notes",
    "reason_code",
    "detail",
    "simulator_outcome",
    "entry_price",
    "quantity",
    "fills",
)

_ENTRY_GATE_OUTCOMES = {
    DECISION_CONFIRM: OUTCOME_ACCEPTED,
    DECISION_REJECT: OUTCOME_VETOED,
    DECISION_WATCH: OUTCOME_WATCHED,
}
_WATCH_SAMPLE_OUTCOMES = {
    # A watch sample is an audit of a signal the strategy chose NOT to act on.
    # "confirm" can never promote it into a trade, so it is recorded as
    # not_promotable rather than accepted.
    DECISION_CONFIRM: OUTCOME_NOT_PROMOTABLE,
    DECISION_REJECT: OUTCOME_WATCHED,
    DECISION_WATCH: OUTCOME_WATCHED,
}


@dataclass
class AiReview:
    """One recorded AI decision. Mutable only so reconciliation can annotate it."""

    time_ms: int
    time: Optional[str]
    symbol: str
    purpose: str
    parent_classification: str
    outcome: str
    decision: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    risk_notes: List[str] = field(default_factory=list)
    reason_code: Optional[str] = None
    detail: str = ""
    simulator_outcome: Optional[str] = None
    entry_price: Optional[str] = None
    quantity: Optional[str] = None
    fills: List[dict] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "time_ms": self.time_ms,
            "time": self.time,
            "symbol": self.symbol,
            "purpose": self.purpose,
            "parent_classification": self.parent_classification,
            "outcome": self.outcome,
            "decision": self.decision,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "risk_notes": list(self.risk_notes),
            "reason_code": self.reason_code,
            "detail": self.detail,
            "simulator_outcome": self.simulator_outcome,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "fills": [dict(fill) for fill in self.fills],
        }


def _symbol_of(signal: Any) -> str:
    symbol = getattr(signal, "symbol", None)
    if symbol is None and isinstance(signal, dict):
        symbol = signal.get("symbol")
    return str(symbol or "").strip()


class AiAdvisor:
    """Records reviews, enforces the call budget, and never mutates a signal."""

    def __init__(self, config: AiConfig, provider: BaseProvider) -> None:
        if not isinstance(config, AiConfig):
            raise AiPromptError("AiAdvisor requires an AiConfig")
        if provider is None:
            raise AiPromptError("AiAdvisor requires a provider")
        self.config = config
        self.provider = provider
        self.reviews: List[AiReview] = []
        self.calls_used = 0

    # -- recording ----------------------------------------------------------
    def _blank(self, purpose: str, symbol: str, time_ms: int, parent: str) -> AiReview:
        return AiReview(
            time_ms=int(time_ms),
            time=iso_utc(int(time_ms)) if int(time_ms) > 0 else None,
            symbol=symbol,
            purpose=purpose,
            parent_classification=parent,
            outcome=OUTCOME_NOT_PROMOTABLE,
        )

    def _record(self, review: AiReview, *, outcome: str, reason_code: Optional[str] = None,
                detail: str = "") -> AiReview:
        review.outcome = outcome
        review.reason_code = reason_code
        review.detail = detail
        self.reviews.append(review)
        return review

    # -- review -------------------------------------------------------------
    def review(self, *, purpose: str, signal: Any, bars: Optional[Dict[str, Sequence[Any]]] = None,
               instrument: Any = None, data_source: str = "", mode: str = "",
               time_ms: Optional[int] = None) -> AiReview:
        if purpose not in PURPOSES:
            raise AiPromptError(f"unknown review purpose {purpose!r}")
        payload = signal_json(signal)
        symbol = _symbol_of(payload)
        parent = str(payload.get("classification") or "")
        decision_ms = int(time_ms if time_ms is not None else (payload.get("timestamp_ms") or 0))
        review = self._blank(purpose, symbol, decision_ms, parent)

        if not symbol or decision_ms <= 0:
            return self._record(
                review, outcome=OUTCOME_NOT_PROMOTABLE, reason_code="INVALID_SIGNAL",
                detail="a review needs a symbol and a positive decision timestamp",
            )

        expected = PURPOSE_CLASSIFICATION[purpose]
        if parent != expected:
            return self._record(
                review, outcome=OUTCOME_NOT_PROMOTABLE, reason_code="NOT_PROMOTABLE",
                detail=f"purpose {purpose} reviews only {expected}; got {parent or 'unknown'}",
            )

        closed = {interval: list((bars or {}).get(interval) or ()) for interval in REVIEW_INTERVALS}
        if sum(len(series) for series in closed.values()) <= 0:
            return self._record(
                review, outcome=OUTCOME_NO_MARKET_DATA, reason_code="SKIPPED_NO_MARKET_DATA",
                detail="no closed public bars were available at the decision time",
            )

        if self.calls_used >= self.config.max_calls:
            return self._record(
                review, outcome=OUTCOME_BUDGET_EXHAUSTED, reason_code="BUDGET_EXHAUSTED",
                detail=f"AI call budget of {self.config.max_calls} exhausted; failing closed",
            )

        request = build_review_request(
            purpose=purpose, signal=payload, ai_config=self.config, bars=closed,
            instrument=instrument, data_source=data_source, mode=mode, time_ms=decision_ms,
        )
        self.calls_used += 1
        try:
            raw = self.provider.complete(request)
        except AiProviderError as exc:
            return self._record(review, outcome=OUTCOME_PROVIDER_ERROR,
                                reason_code="PROVIDER_ERROR", detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - any failure must fail closed
            return self._record(
                review, outcome=OUTCOME_PROVIDER_ERROR, reason_code="PROVIDER_ERROR",
                detail=f"unexpected provider failure: {type(exc).__name__}",
            )

        try:
            decision = parse_decision(raw)
        except AiSchemaError as exc:
            return self._record(review, outcome=OUTCOME_SCHEMA_INVALID,
                                reason_code="SCHEMA_INVALID", detail=str(exc))

        review.decision = decision.decision
        review.confidence = decision.confidence
        review.reasoning = decision.reasoning
        review.risk_notes = list(decision.risk_notes)
        review.reason_code = decision.reason_code
        review.detail = ""
        table = _WATCH_SAMPLE_OUTCOMES if purpose == PURPOSE_WATCH_SAMPLE else _ENTRY_GATE_OUTCOMES
        review.outcome = table[decision.decision]
        self.reviews.append(review)
        return review

    # -- reporting ----------------------------------------------------------
    def outcome_counts(self) -> Dict[str, int]:
        counts = {name: 0 for name in OUTCOMES}
        for review in self.reviews:
            counts[review.outcome] = counts.get(review.outcome, 0) + 1
        return counts

    def stats(self) -> Dict[str, Any]:
        return {
            "calls_used": self.calls_used,
            "max_calls": self.config.max_calls,
            "reviews": len(self.reviews),
            "outcomes": self.outcome_counts(),
        }

    def accepted(self) -> List[AiReview]:
        return [review for review in self.reviews if review.outcome == OUTCOME_ACCEPTED]


def make_ai_filter(advisor: AiAdvisor, slicer: Callable[[str, str, int], Sequence[Any]], *,
                   data_source: str = "", mode: str = "", registry: Any = None) -> Callable:
    """Build the veto-only filter passed to ``paper.signals.run_replay``.

    Signature matches what run_replay calls: ``filter(decision_ms, actionable,
    closed_bars) -> list``. It returns a NEW list holding the SAME signal
    objects that were accepted - it never edits a signal, and never adds one.
    """

    def _filter(decision_ms: int, actionable: Sequence[Any], closed_bars=None) -> List[Any]:
        accepted: List[Any] = []
        for signal in actionable or ():
            symbol = _symbol_of(signal)
            bars = {interval: slicer(symbol, interval, decision_ms) for interval in REVIEW_INTERVALS}
            instrument = None
            if registry is not None and symbol:
                resolver = getattr(registry, "resolve", None)
                if callable(resolver):
                    try:
                        instrument = resolver(symbol)
                    except Exception:
                        instrument = None
            review = advisor.review(
                purpose=PURPOSE_ENTRY_GATE, signal=signal, bars=bars, instrument=instrument,
                data_source=data_source, mode=mode, time_ms=int(decision_ms),
            )
            if review.outcome == OUTCOME_ACCEPTED:
                accepted.append(signal)
        return accepted

    return _filter
