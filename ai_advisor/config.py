"""Configuration for the AI review layer.

Every setting is an environment variable with a SAFE default. The default
provider is the offline ``fixture`` provider, which makes no network call at
all, so a fresh checkout can never accidentally reach a paid model endpoint.

Nothing here holds a credential. Provider secrets are read from the
environment at call time by ``providers.py`` and are never written to disk,
logged, or included in a prompt or artifact.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

# Generated AI artifacts live under the gitignored output/ tree, next to the
# paper-trading artifacts they describe.
AI_OUTPUT_DIR = REPO_ROOT / "output" / "ai"
AI_SUMMARY_NAME = "ai-summary.json"
AI_DECISIONS_NAME = "decisions.jsonl"
AI_DASHBOARD_EXPORT_PATH = REPO_ROOT / "dashboard" / "public" / "ai-summary.json"

# Providers the platform is allowed to talk to. Anything else is rejected
# before a request is made; see providers.ALLOWED_AI_URL_PREFIXES.
PROVIDERS = ("fixture", "openrouter", "cloudflare")

DEFAULT_MODELS = {
    "openrouter": "openai/gpt-4o-mini",
    "cloudflare": "@cf/meta/llama-3.1-8b-instruct",
}

DEFAULT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "cloudflare": "https://api.cloudflare.com/client/v4",
}

# Environment variables each hosted provider requires. Missing values are a
# configuration error (exit 2), never a silent fallback to another provider.
REQUIRED_ENV = {
    "fixture": (),
    "openrouter": ("OPENROUTER_API_KEY",),
    "cloudflare": ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_AI_TOKEN"),
}

DEFAULT_TIMEOUT_S = 30
DEFAULT_MAX_CALLS = 40
DEFAULT_WATCH_SAMPLE = 1
DEFAULT_TEMPERATURE = 0.0
MAX_CALLS_HARD_CAP = 500

AI_ROLE = "veto_only"
AI_DISCLAIMER = (
    "AI review is veto-only and advisory: it can confirm, reject or watch a signal the "
    "deterministic strategy already produced, and it can never create a signal, resize a "
    "position, widen a risk limit or place an order. Every failure mode fails closed. "
    "All figures on this page are PAPER / SIMULATED. No live orders were placed; no "
    "private Bitget API was used."
)


class AiConfigError(ValueError):
    """Invalid AI configuration. Never silently substituted with a default."""


@dataclass(frozen=True)
class AiConfig:
    provider: str = "fixture"
    model: Optional[str] = None
    base_url: Optional[str] = None
    timeout_s: int = DEFAULT_TIMEOUT_S
    max_calls: int = DEFAULT_MAX_CALLS
    watch_sample: int = DEFAULT_WATCH_SAMPLE
    temperature: float = DEFAULT_TEMPERATURE

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise AiConfigError(
                f"AI_PROVIDER must be one of {', '.join(PROVIDERS)}; got {self.provider!r}"
            )
        if self.timeout_s <= 0:
            raise AiConfigError("AI_TIMEOUT_S must be a positive integer")
        if self.max_calls < 0:
            raise AiConfigError("AI_MAX_CALLS must be zero or greater")
        if self.max_calls > MAX_CALLS_HARD_CAP:
            raise AiConfigError(
                f"AI_MAX_CALLS must be <= {MAX_CALLS_HARD_CAP} (runaway-cost guard); "
                f"got {self.max_calls}"
            )
        if self.watch_sample < 0:
            raise AiConfigError("AI_WATCH_SAMPLE must be zero or greater")
        if not 0.0 <= self.temperature <= 2.0:
            raise AiConfigError("AI_TEMPERATURE must be between 0.0 and 2.0")

    @property
    def resolved_model(self) -> Optional[str]:
        if self.model:
            return self.model
        return DEFAULT_MODELS.get(self.provider)

    @property
    def resolved_base_url(self) -> Optional[str]:
        if self.base_url:
            return self.base_url
        return DEFAULT_BASE_URLS.get(self.provider)

    @property
    def is_synthetic(self) -> bool:
        """True when no hosted model is involved (offline, deterministic)."""
        return self.provider == "fixture"

    @property
    def required_env(self):
        return REQUIRED_ENV[self.provider]

    def missing_env(self, env=None) -> tuple:
        source = os.environ if env is None else env
        return tuple(name for name in self.required_env if not str(source.get(name, "")).strip())

    def to_json(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.resolved_model,
            "base_url": self.resolved_base_url,
            "timeout_s": self.timeout_s,
            "max_calls": self.max_calls,
            "watch_sample": self.watch_sample,
            "temperature": self.temperature,
            "synthetic": self.is_synthetic,
            "role": AI_ROLE,
        }


def _int_env(env, name: str, default: int) -> int:
    raw = str(env.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise AiConfigError(f"{name} must be an integer; got {raw!r}")


def _float_env(env, name: str, default: float) -> float:
    raw = str(env.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise AiConfigError(f"{name} must be a number; got {raw!r}")


def _str_env(env, name: str) -> Optional[str]:
    raw = str(env.get(name, "") or "").strip()
    return raw or None


def ai_config_from_env(env=None) -> AiConfig:
    """Build an AiConfig from the environment. Blank values keep the default."""
    source = os.environ if env is None else env
    provider = (str(source.get("AI_PROVIDER", "") or "").strip().lower() or "fixture")
    return AiConfig(
        provider=provider,
        model=_str_env(source, "AI_MODEL"),
        base_url=_str_env(source, "AI_BASE_URL"),
        timeout_s=_int_env(source, "AI_TIMEOUT_S", DEFAULT_TIMEOUT_S),
        max_calls=_int_env(source, "AI_MAX_CALLS", DEFAULT_MAX_CALLS),
        watch_sample=_int_env(source, "AI_WATCH_SAMPLE", DEFAULT_WATCH_SAMPLE),
        temperature=_float_env(source, "AI_TEMPERATURE", DEFAULT_TEMPERATURE),
    )
