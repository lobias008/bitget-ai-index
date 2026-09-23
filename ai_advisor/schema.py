"""Strict validation of AI model output.

The model is an untrusted input. Its reply is parsed against a closed schema
and ANY deviation - prose instead of JSON, an unknown key, an out-of-range
confidence, an empty rationale - raises ``AiSchemaError``. The caller records
that as ``schema_invalid`` and the signal is NOT simulated (fail closed).

Validation is deliberately narrow: this module never repairs, completes or
re-interprets a bad reply, because a guessed decision would be indistinguishable
from a real one downstream.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Tuple

DECISION_CONFIRM = "confirm"
DECISION_REJECT = "reject"
DECISION_WATCH = "watch"
DECISIONS: Tuple[str, ...] = (DECISION_CONFIRM, DECISION_REJECT, DECISION_WATCH)

REQUIRED_KEYS: Tuple[str, ...] = ("decision", "confidence", "reasoning", "reason_code")
OPTIONAL_KEYS: Tuple[str, ...] = ("risk_notes",)
ALLOWED_KEYS: Tuple[str, ...] = REQUIRED_KEYS + OPTIONAL_KEYS

MAX_REASONING_CHARS = 1200
MAX_RISK_NOTES = 8
MAX_NOTE_CHARS = 240
REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,39}$")
_FENCE_PATTERN = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")


class AiSchemaError(ValueError):
    """The model reply did not satisfy the response contract."""


@dataclass(frozen=True)
class AiDecision:
    decision: str
    confidence: float
    reasoning: str
    reason_code: str
    risk_notes: Tuple[str, ...] = ()

    @property
    def is_accept(self) -> bool:
        return self.decision == DECISION_CONFIRM

    def to_json(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "reason_code": self.reason_code,
            "risk_notes": list(self.risk_notes),
        }


def extract_json_object(text: Any) -> Dict[str, Any]:
    """Pull exactly one JSON object out of a model reply.

    Tolerates a markdown code fence and surrounding whitespace, because those
    are formatting artifacts. It does NOT tolerate prose mixed into the object
    or multiple objects: ambiguity is treated as invalid output.
    """
    if not isinstance(text, str):
        raise AiSchemaError(f"model reply must be text; got {type(text).__name__}")
    body = text.strip()
    if not body:
        raise AiSchemaError("model reply is empty")
    body = _FENCE_PATTERN.sub("", body.strip()).strip()
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        raise AiSchemaError("model reply contains no JSON object")
    candidate = body[start:end + 1]
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError) as exc:
        raise AiSchemaError(f"model reply is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AiSchemaError("model reply JSON must be an object")
    return parsed


def _clean_text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise AiSchemaError(f"{label} must be a string; got {type(value).__name__}")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise AiSchemaError(f"{label} must not be empty")
    if len(cleaned) > limit:
        raise AiSchemaError(f"{label} exceeds {limit} characters")
    return cleaned


def _clean_confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AiSchemaError(f"confidence must be a number between 0 and 1; got {value!r}")
    number = float(value)
    if number != number or number < 0.0 or number > 1.0:
        raise AiSchemaError(f"confidence must be between 0 and 1; got {value!r}")
    return round(number, 4)


def _clean_risk_notes(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AiSchemaError("risk_notes must be a list of strings")
    if len(value) > MAX_RISK_NOTES:
        raise AiSchemaError(f"risk_notes must contain at most {MAX_RISK_NOTES} items")
    return tuple(_clean_text(item, "risk_notes item", MAX_NOTE_CHARS) for item in value)


def parse_decision(text: Any) -> AiDecision:
    """Validate a raw model reply into an ``AiDecision`` or raise."""
    payload = extract_json_object(text)
    unknown = sorted(key for key in payload if key not in ALLOWED_KEYS)
    if unknown:
        raise AiSchemaError(f"model reply has unexpected key(s): {', '.join(unknown)}")
    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise AiSchemaError(f"model reply is missing key(s): {', '.join(missing)}")

    decision = payload["decision"]
    if not isinstance(decision, str):
        raise AiSchemaError(f"decision must be a string; got {type(decision).__name__}")
    normalized = decision.strip().lower()
    if normalized not in DECISIONS:
        raise AiSchemaError(
            f"decision must be one of {', '.join(DECISIONS)}; got {decision!r}"
        )
    reason_code = _clean_text(payload["reason_code"], "reason_code", 40)
    if not REASON_CODE_PATTERN.match(reason_code):
        raise AiSchemaError(
            f"reason_code must match {REASON_CODE_PATTERN.pattern}; got {reason_code!r}"
        )
    return AiDecision(
        decision=normalized,
        confidence=_clean_confidence(payload["confidence"]),
        reasoning=_clean_text(payload["reasoning"], "reasoning", MAX_REASONING_CHARS),
        reason_code=reason_code,
        risk_notes=_clean_risk_notes(payload.get("risk_notes")),
    )
