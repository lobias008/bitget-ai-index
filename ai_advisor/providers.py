"""Model providers for the AI review layer.

This is the ONLY module in the repository that performs AI inference network
I/O, and it is deliberately narrow:

  * POST is allowed to an allowlisted inference host and nothing else. The
    allowlist is checked immediately before every request, including when a
    custom base URL is configured.
  * No Bitget endpoint is reachable from here. Market data stays in
    paper/market_data.py (GET-only, public, allowlisted) and this package
    never imports it.
  * Credentials are read from the environment at call time, are never logged,
    never written to an artifact, and never placed in a prompt. Error messages
    carry the HTTP status only - never a response body, which could echo a key.
  * Every transport or provider-side failure becomes ``AiProviderError``, which
    the advisor records as ``provider_error`` and treats as a veto (fail closed).

The default provider is ``fixture``: fully offline, deterministic, and used by
the test-suite and by ``--source synthetic`` demos. It is always labeled
SYNTHETIC and its output is never presented as a real model opinion.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from .config import DEFAULT_BASE_URLS, AiConfig
from .prompt import ReviewRequest
from .schema import DECISION_CONFIRM, DECISION_REJECT, DECISION_WATCH

ALLOWED_AI_URL_PREFIXES = ("https://openrouter.ai/", "https://api.cloudflare.com/")
USER_AGENT = "bitget-ai-index/1.0 (veto-only paper-trading review; no trading)"
OPENROUTER_COMPLETIONS_PATH = "/chat/completions"
CLOUDFLARE_AI_RUN_PATH = "/ai/run/"
_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,64}$")
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9@._/-]{1,128}$")


class AiProviderError(RuntimeError):
    """The provider could not return a usable reply. Always fails closed."""


def _assert_allowed_url(url: Any) -> str:
    if not isinstance(url, str) or not url.startswith(ALLOWED_AI_URL_PREFIXES):
        raise AiProviderError("blocked: AI endpoint is not on the allowlist")
    if ".." in url or " " in url:
        raise AiProviderError("blocked: malformed AI endpoint URL")
    return url


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


class BaseProvider:
    """Common provider surface. ``synthetic`` marks offline/deterministic output."""

    name = "base"
    synthetic = False

    def complete(self, request: ReviewRequest) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


class FixtureProvider(BaseProvider):
    """Offline deterministic reviewer used for tests and labeled demos.

    It applies a fixed, documented rule to the signal it is handed - confirm an
    actionable setup, watch an informational one, reject anything else - and
    derives confidence from how many deterministic checks passed. It makes no
    network call and produces no opinion about the market. Its output is always
    labeled SYNTHETIC so it can never be mistaken for a real model review.
    """

    name = "fixture"
    synthetic = True

    def __init__(self) -> None:
        self.calls: List[ReviewRequest] = []

    def complete(self, request: ReviewRequest) -> str:
        self.calls.append(request)
        context = request.context or {}
        signal = context.get("signal") or {}
        classification = str(signal.get("classification") or "")
        checks = signal.get("checks") or {}
        if classification == context.get("expected_parent_classification") and classification:
            decision = DECISION_CONFIRM if classification == "actionable_paper" else DECISION_WATCH
        else:
            decision = DECISION_REJECT
        passed = sum(1 for value in checks.values() if value is True)
        confidence = round(_clamp(0.05 + 0.1 * min(passed, 9), 0.05, 0.95), 2)
        reason_code = f"FIXTURE_{decision.upper()}"
        reasoning = (
            "Offline fixture provider (SYNTHETIC, not a real model). Deterministic rule: "
            f"{classification or 'unknown'} -> {decision}; {passed} deterministic check(s) passed."
        )
        payload = {
            "decision": decision,
            "confidence": confidence,
            "reasoning": reasoning,
            "reason_code": reason_code,
            "risk_notes": ["fixture provider output is synthetic and must be labeled as such"],
        }
        return json.dumps(payload)


class ScriptedProvider(BaseProvider):
    """Replays a fixed list of replies, then fails. Test-only."""

    name = "scripted"
    synthetic = True

    def __init__(self, items: Sequence[Any] = ()) -> None:
        self._items: List[Any] = list(items)
        self.calls: List[ReviewRequest] = []

    def complete(self, request: ReviewRequest) -> str:
        self.calls.append(request)
        if not self._items:
            raise AiProviderError("scripted provider exhausted")
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return item
        return json.dumps(item)


def _post_json(url: str, body: Dict[str, Any], headers: Dict[str, str], timeout_s: int) -> Any:
    """POST to an allowlisted AI endpoint. Errors never leak a response body."""
    _assert_allowed_url(url)
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # Deliberately no body: an error page could reflect request material.
        raise AiProviderError(f"AI provider returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise AiProviderError(f"AI provider request failed: {type(exc).__name__}") from exc
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise AiProviderError("AI provider returned a non-JSON reply") from exc


class OpenRouterProvider(BaseProvider):
    """OpenRouter chat-completions provider (POST, allowlisted host only)."""

    name = "openrouter"
    synthetic = False

    def __init__(self, credential: str, model: str, base_url: Optional[str] = None,
                 timeout_s: int = 30) -> None:
        if not str(credential or "").strip():
            raise AiProviderError("an OpenRouter credential is required")
        if not str(model or "").strip():
            raise AiProviderError("an OpenRouter model is required")
        self._credential = str(credential)
        self.model = str(model)
        self.base_url = (base_url or DEFAULT_BASE_URLS["openrouter"]).rstrip("/")
        self.timeout_s = int(timeout_s)

    @property
    def endpoint(self) -> str:
        return self.base_url + OPENROUTER_COMPLETIONS_PATH

    def complete(self, request: ReviewRequest) -> str:
        body = {
            "model": self.model,
            "temperature": float(request.temperature),
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": "Bearer " + self._credential,
        }
        payload = _post_json(_assert_allowed_url(self.endpoint), body, headers, self.timeout_s)
        return _extract_openrouter_text(payload)


def _extract_openrouter_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise AiProviderError("AI provider returned an unexpected reply shape")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AiProviderError("AI provider returned no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise AiProviderError("AI provider returned an empty completion")
    return content


class CloudflareProvider(BaseProvider):
    """Cloudflare Workers AI provider (POST, allowlisted host only)."""

    name = "cloudflare"
    synthetic = False

    def __init__(self, credential: str, account_id: str, model: str,
                 base_url: Optional[str] = None, timeout_s: int = 30) -> None:
        if not str(credential or "").strip():
            raise AiProviderError("a Cloudflare AI credential is required")
        if not _ACCOUNT_ID_PATTERN.match(str(account_id or "")):
            raise AiProviderError("CLOUDFLARE_ACCOUNT_ID is not a valid account identifier")
        if not _MODEL_PATTERN.match(str(model or "")):
            raise AiProviderError("the Cloudflare model identifier is not valid")
        self._credential = str(credential)
        self.account_id = str(account_id)
        self.model = str(model)
        self.base_url = (base_url or DEFAULT_BASE_URLS["cloudflare"]).rstrip("/")
        self.timeout_s = int(timeout_s)

    @property
    def endpoint(self) -> str:
        return (
            self.base_url
            + "/accounts/"
            + urllib.parse.quote(self.account_id, safe="")
            + CLOUDFLARE_AI_RUN_PATH
            + urllib.parse.quote(self.model, safe="/@.")
        )

    def complete(self, request: ReviewRequest) -> str:
        body = {
            "temperature": float(request.temperature),
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": "Bearer " + self._credential,
        }
        payload = _post_json(_assert_allowed_url(self.endpoint), body, headers, self.timeout_s)
        return _extract_cloudflare_text(payload)


def _extract_cloudflare_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise AiProviderError("AI provider returned an unexpected reply shape")
    if not payload.get("success", True):
        errors = payload.get("errors") or []
        code = errors[0].get("code") if errors and isinstance(errors[0], dict) else None
        raise AiProviderError(f"AI provider reported an error (code {code})")
    result = payload.get("result")
    text = result.get("response") if isinstance(result, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise AiProviderError("AI provider returned an empty completion")
    return text


def build_provider(config: AiConfig, env=None) -> BaseProvider:
    """Instantiate the configured provider. A missing credential is an error.

    There is deliberately no fallback: if a hosted provider is configured but
    not credentialed, the run fails with a configuration error instead of
    quietly substituting the offline fixture and reporting it as a model review.
    """
    source = os.environ if env is None else env
    provider = config.provider
    if provider == "fixture":
        return FixtureProvider()
    if provider == "openrouter":
        credential = str(source.get("OPENROUTER_API_KEY", "") or "").strip()
        if not credential:
            raise AiProviderError("OPENROUTER_API_KEY is not set; refusing to fall back silently")
        return OpenRouterProvider(
            credential=credential,
            model=config.resolved_model,
            base_url=config.resolved_base_url,
            timeout_s=config.timeout_s,
        )
    if provider == "cloudflare":
        credential = str(source.get("CLOUDFLARE_AI_TOKEN", "") or "").strip()
        account_id = str(source.get("CLOUDFLARE_ACCOUNT_ID", "") or "").strip()
        if not credential or not account_id:
            raise AiProviderError(
                "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_AI_TOKEN are required; "
                "refusing to fall back silently"
            )
        return CloudflareProvider(
            credential=credential,
            account_id=account_id,
            model=config.resolved_model,
            base_url=config.resolved_base_url,
            timeout_s=config.timeout_s,
        )
    raise AiProviderError(f"unsupported AI provider {provider!r}")
