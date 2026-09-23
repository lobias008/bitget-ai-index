"""Tests for the AI provider layer: the only network code in ai_advisor/.

Guarantees covered here:
  * inference requests can only reach the two allowlisted HTTPS hosts, and the
    allowlist is re-checked at request time even for a configured base URL;
  * endpoint URLs are built and quoted correctly per provider;
  * a hosted provider with no credential is a hard error - there is no silent
    fallback to the offline fixture;
  * transport and provider failures become AiProviderError carrying the HTTP
    status only, never a response body that could echo a credential;
  * the offline fixture provider is deterministic, contract-valid and marked
    synthetic;
  * no test in this module performs a real network request: urlopen is patched
    everywhere a request path is exercised.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_advisor.config import DEFAULT_BASE_URLS, DEFAULT_MODELS, AiConfig  # noqa: E402
from ai_advisor.prompt import PURPOSE_ENTRY_GATE, ReviewRequest  # noqa: E402
from ai_advisor.providers import (  # noqa: E402
    ALLOWED_AI_URL_PREFIXES,
    AiProviderError,
    BaseProvider,
    CloudflareProvider,
    FixtureProvider,
    OpenRouterProvider,
    ScriptedProvider,
    _assert_allowed_url,
    _extract_cloudflare_text,
    _extract_openrouter_text,
    _post_json,
    build_provider,
)
from ai_advisor.schema import (  # noqa: E402
    DECISION_CONFIRM,
    DECISION_REJECT,
    DECISION_WATCH,
    parse_decision,
)

PLACEHOLDER = "unit-test-placeholder"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def make_request(classification="actionable_paper", expected="actionable_paper", checks=None):
    """A minimal ReviewRequest carrying the fields the fixture provider reads."""
    return ReviewRequest(
        purpose=PURPOSE_ENTRY_GATE,
        symbol="BTCUSDT",
        model=None,
        temperature=0.0,
        system="system prompt",
        user="user payload",
        context={
            "expected_parent_classification": expected,
            "signal": {"classification": classification, "checks": checks or {}},
        },
    )


def forbid_network():
    """Patch urlopen so any accidental request fails the test loudly."""
    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("a test attempted a real network request")
    return mock.patch.object(urllib.request, "urlopen", side_effect=_boom)


class TestUrlAllowlist(unittest.TestCase):
    def test_allowlist_is_exactly_two_https_hosts(self):
        self.assertEqual(
            ALLOWED_AI_URL_PREFIXES,
            ("https://openrouter.ai/", "https://api.cloudflare.com/"),
        )
        for prefix in ALLOWED_AI_URL_PREFIXES:
            self.assertTrue(prefix.startswith("https://"))
            self.assertNotIn("http://", prefix)

    def test_allowed_urls_pass_through_unchanged(self):
        for url in ("https://openrouter.ai/api/v1/chat/completions",
                    "https://api.cloudflare.com/client/v4/accounts/a/ai/run/@cf/m"):
            with self.subTest(url=url):
                self.assertEqual(_assert_allowed_url(url), url)

    def test_everything_else_is_blocked(self):
        blocked = [
            "http://openrouter.ai/api/v1",
            "https://api.bitget.com/api/v2/mix/market/candles",
            "https://evil.example.com/",
            "https://openrouter.ai.evil.example.com/",
            "https://evil.example.com/#https://openrouter.ai/",
            "ftp://openrouter.ai/x",
            "wss://openrouter.ai/x",
            "https://api.cloudflare.com/client/v4/../../etc/passwd",
            "https://openrouter.ai/a b",
            "",
            "   ",
            None,
            123,
            b"https://openrouter.ai/",
        ]
        for url in blocked:
            with self.subTest(url=repr(url)[:48]):
                with self.assertRaises(AiProviderError):
                    _assert_allowed_url(url)

    def test_blocked_message_does_not_echo_the_url(self):
        with self.assertRaises(AiProviderError) as caught:
            _assert_allowed_url("https://super-secret-host.example.com/")
        self.assertNotIn("super-secret-host", str(caught.exception))


class TestEndpointConstruction(unittest.TestCase):
    def test_openrouter_default_endpoint(self):
        with forbid_network():
            provider = OpenRouterProvider(PLACEHOLDER, DEFAULT_MODELS["openrouter"])
        self.assertEqual(provider.endpoint, DEFAULT_BASE_URLS["openrouter"] + "/chat/completions")
        self.assertEqual(provider.endpoint, OPENROUTER_URL)
        self.assertFalse(provider.synthetic)

    def test_openrouter_strips_trailing_slashes(self):
        with forbid_network():
            provider = OpenRouterProvider(PLACEHOLDER, "m", base_url="https://openrouter.ai/api/")
        self.assertEqual(provider.endpoint, "https://openrouter.ai/api/chat/completions")

    def test_openrouter_rejects_empty_credential_or_model(self):
        with forbid_network():
            with self.assertRaises(AiProviderError):
                OpenRouterProvider("  ", "m")
            with self.assertRaises(AiProviderError):
                OpenRouterProvider(PLACEHOLDER, "")

    def test_cloudflare_endpoint_is_quoted(self):
        with forbid_network():
            provider = CloudflareProvider(
                PLACEHOLDER, "abc123XYZ", "@cf/meta/llama-3.1-8b-instruct"
            )
        self.assertEqual(
            provider.endpoint,
            "https://api.cloudflare.com/client/v4/accounts/abc123XYZ/ai/run/"
            "@cf/meta/llama-3.1-8b-instruct",
        )
        self.assertTrue(_assert_allowed_url(provider.endpoint).startswith("https://api.cloudflare.com/"))

    def test_cloudflare_rejects_invalid_account_or_model(self):
        bad_accounts = ["", "  ", "a b", "acct/../x", "x" * 65, "acct!", "acct/id"]
        for account in bad_accounts:
            with self.subTest(account=account[:16]), self.assertRaises(AiProviderError):
                CloudflareProvider(PLACEHOLDER, account, DEFAULT_MODELS["cloudflare"])
        bad_models = ["", " ", "m odel", "x" * 129, "mo del", "m*odel"]
        for model in bad_models:
            with self.subTest(model=model[:16]), self.assertRaises(AiProviderError):
                CloudflareProvider(PLACEHOLDER, "abc123", model)
        with self.assertRaises(AiProviderError):
            CloudflareProvider("", "abc123", DEFAULT_MODELS["cloudflare"])

    def test_cloudflare_custom_base_url_stays_allowlisted_at_request_time(self):
        with forbid_network():
            provider = CloudflareProvider(PLACEHOLDER, "abc123", "@cf/m",
                                          base_url="https://api.cloudflare.com/client/v4/")
        self.assertEqual(
            provider.endpoint, "https://api.cloudflare.com/client/v4/accounts/abc123/ai/run/@cf/m"
        )


class TestBuildProvider(unittest.TestCase):
    def test_default_is_the_offline_fixture(self):
        with forbid_network():
            provider = build_provider(AiConfig(), {})
        self.assertIsInstance(provider, FixtureProvider)
        self.assertTrue(provider.synthetic)
        self.assertEqual(provider.name, "fixture")

    def test_openrouter_requires_a_credential(self):
        with forbid_network():
            config = AiConfig(provider="openrouter")
            with self.assertRaises(AiProviderError) as caught:
                build_provider(config, {})
            message = str(caught.exception)
            self.assertIn("OPENROUTER_API_KEY", message)
            self.assertIn("refusing to fall back", message)
            with self.assertRaises(AiProviderError):
                build_provider(config, {"OPENROUTER_API_KEY": "   "})
            provider = build_provider(config, {"OPENROUTER_API_KEY": PLACEHOLDER})
        self.assertIsInstance(provider, OpenRouterProvider)
        self.assertFalse(provider.synthetic)

    def test_cloudflare_requires_both_values(self):
        with forbid_network():
            config = AiConfig(provider="cloudflare")
            for env in ({}, {"CLOUDFLARE_ACCOUNT_ID": "abc123"},
                        {"CLOUDFLARE_AI_TOKEN": PLACEHOLDER},
                        {"CLOUDFLARE_ACCOUNT_ID": " ", "CLOUDFLARE_AI_TOKEN": " "}):
                with self.subTest(env=sorted(env)), self.assertRaises(AiProviderError):
                    build_provider(config, env)
            provider = build_provider(
                config, {"CLOUDFLARE_ACCOUNT_ID": "abc123", "CLOUDFLARE_AI_TOKEN": PLACEHOLDER}
            )
        self.assertIsInstance(provider, CloudflareProvider)

    def test_missing_credential_never_degrades_to_the_fixture(self):
        with forbid_network():
            for provider_name in ("openrouter", "cloudflare"):
                with self.subTest(provider=provider_name):
                    with self.assertRaises(AiProviderError):
                        build_provider(AiConfig(provider=provider_name), {})

    def test_building_a_provider_makes_no_request(self):
        with forbid_network() as patched:
            build_provider(AiConfig(), {})
            build_provider(AiConfig(provider="openrouter"), {"OPENROUTER_API_KEY": PLACEHOLDER})
            build_provider(AiConfig(provider="cloudflare"),
                           {"CLOUDFLARE_ACCOUNT_ID": "abc123", "CLOUDFLARE_AI_TOKEN": PLACEHOLDER})
        patched.assert_not_called()

    def test_base_provider_is_abstract(self):
        with self.assertRaises(NotImplementedError):
            BaseProvider().complete(make_request())
        self.assertFalse(BaseProvider.synthetic)


class TestFixtureProvider(unittest.TestCase):
    def setUp(self):
        self._guard = forbid_network()
        self._guard.start()
        self.addCleanup(self._guard.stop)
        self.provider = FixtureProvider()

    def test_confirm_for_a_matching_actionable_signal(self):
        decision = parse_decision(self.provider.complete(make_request()))
        self.assertEqual(decision.decision, DECISION_CONFIRM)
        self.assertEqual(decision.reason_code, "FIXTURE_CONFIRM")
        self.assertTrue(decision.risk_notes)
        self.assertIn("synthetic", decision.risk_notes[0])

    def test_watch_for_a_matching_informational_signal(self):
        request = make_request(classification="informational", expected="informational")
        decision = parse_decision(self.provider.complete(request))
        self.assertEqual(decision.decision, DECISION_WATCH)
        self.assertEqual(decision.reason_code, "FIXTURE_WATCH")

    def test_reject_when_the_classification_does_not_match(self):
        request = make_request(classification="blocked", expected="actionable_paper")
        decision = parse_decision(self.provider.complete(request))
        self.assertEqual(decision.decision, DECISION_REJECT)
        self.assertEqual(decision.reason_code, "FIXTURE_REJECT")

    def test_reject_when_the_signal_has_no_classification(self):
        request = make_request(classification="", expected="actionable_paper")
        self.assertEqual(parse_decision(self.provider.complete(request)).decision, DECISION_REJECT)

    def test_reply_always_satisfies_the_response_contract(self):
        for classification, expected in (("actionable_paper", "actionable_paper"),
                                         ("informational", "informational"),
                                         ("blocked", "actionable_paper"),
                                         ("", "actionable_paper")):
            with self.subTest(classification=classification):
                raw = self.provider.complete(make_request(classification, expected))
                decision = parse_decision(raw)
                self.assertIn(decision.decision, (DECISION_CONFIRM, DECISION_REJECT, DECISION_WATCH))
                self.assertGreaterEqual(decision.confidence, 0.05)
                self.assertLessEqual(decision.confidence, 0.95)
                self.assertLessEqual(len(decision.reasoning), 1200)

    def test_confidence_tracks_the_number_of_passed_checks(self):
        seen = []
        for passed in (0, 1, 3, 9, 20):
            checks = {f"check_{index}": index < passed for index in range(max(passed, 1))}
            decision = parse_decision(self.provider.complete(make_request(checks=checks)))
            seen.append(decision.confidence)
        self.assertEqual(seen[0], 0.05)
        self.assertEqual(seen[-1], 0.95)
        self.assertEqual(seen, sorted(seen))
        self.assertLessEqual(seen[3], 0.95)

    def test_output_is_deterministic(self):
        first = self.provider.complete(make_request())
        second = FixtureProvider().complete(make_request())
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), json.loads(second))

    def test_calls_are_recorded(self):
        self.assertEqual(self.provider.calls, [])
        request = make_request()
        self.provider.complete(request)
        self.provider.complete(request)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertIs(self.provider.calls[0], request)

    def test_synthetic_flag_is_set(self):
        self.assertTrue(self.provider.synthetic)
        self.assertEqual(self.provider.name, "fixture")


class TestScriptedProvider(unittest.TestCase):
    def test_replays_strings_and_objects_in_order(self):
        provider = ScriptedProvider(["first", {"decision": "watch"}])
        self.assertEqual(provider.complete(make_request()), "first")
        self.assertEqual(json.loads(provider.complete(make_request())), {"decision": "watch"})
        self.assertEqual(len(provider.calls), 2)
        self.assertTrue(provider.synthetic)

    def test_exception_items_are_raised(self):
        error = AiProviderError("scripted failure")
        provider = ScriptedProvider([error])
        with self.assertRaises(AiProviderError):
            provider.complete(make_request())

    def test_exhaustion_fails_closed(self):
        provider = ScriptedProvider([])
        with self.assertRaises(AiProviderError) as caught:
            provider.complete(make_request())
        self.assertIn("exhausted", str(caught.exception))


class TestPostJsonFailures(unittest.TestCase):
    def test_http_error_reports_the_status_and_never_the_body(self):
        leaky = io.BytesIO(b"echoed-credential-material")
        error = urllib.error.HTTPError(OPENROUTER_URL, 401, "Unauthorized-Reason", {}, leaky)
        with mock.patch.object(urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(AiProviderError) as caught:
                _post_json(OPENROUTER_URL, {}, {}, 5)
        message = str(caught.exception)
        self.assertIn("401", message)
        self.assertNotIn("echoed-credential-material", message)
        self.assertNotIn("Unauthorized-Reason", message)

    def test_url_error_is_wrapped_without_the_reason(self):
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("name resolution leaked")):
            with self.assertRaises(AiProviderError) as caught:
                _post_json(OPENROUTER_URL, {}, {}, 5)
        self.assertIn("URLError", str(caught.exception))
        self.assertNotIn("name resolution leaked", str(caught.exception))

    def test_os_error_is_wrapped(self):
        with mock.patch.object(urllib.request, "urlopen", side_effect=TimeoutError("slow")):
            with self.assertRaises(AiProviderError) as caught:
                _post_json(OPENROUTER_URL, {}, {}, 5)
        self.assertIn("TimeoutError", str(caught.exception))

    def test_non_json_reply_is_rejected(self):
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=io.BytesIO(b"<html>not json</html>")):
            with self.assertRaises(AiProviderError) as caught:
                _post_json(OPENROUTER_URL, {}, {}, 5)
        self.assertIn("non-JSON", str(caught.exception))

    def test_success_returns_the_parsed_payload(self):
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=io.BytesIO(b'{"ok": true}')):
            self.assertEqual(_post_json(OPENROUTER_URL, {"a": 1}, {}, 5), {"ok": True})

    def test_a_blocked_url_never_reaches_urlopen(self):
        with mock.patch.object(urllib.request, "urlopen") as patched:
            with self.assertRaises(AiProviderError):
                _post_json("https://api.bitget.com/api/v2/mix/order", {}, {}, 5)
        patched.assert_not_called()

    def test_provider_request_is_post_with_the_credential_only_in_a_header(self):
        provider = OpenRouterProvider(PLACEHOLDER, "openai/gpt-4o-mini")
        captured = {}

        def _capture(request, timeout=None):
            captured["request"] = request
            captured["timeout"] = timeout
            return io.BytesIO(json.dumps(
                {"choices": [{"message": {"content": '{"ok": 1}'}}]}
            ).encode("utf-8"))

        with mock.patch.object(urllib.request, "urlopen", side_effect=_capture):
            text = provider.complete(make_request())
        request = captured["request"]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, OPENROUTER_URL)
        self.assertEqual(captured["timeout"], provider.timeout_s)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + PLACEHOLDER)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sorted(body), ["messages", "model", "temperature"])
        self.assertNotIn(PLACEHOLDER, json.dumps(body))
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(text, '{"ok": 1}')

    def test_cloudflare_rejects_an_off_allowlist_base_url_at_request_time(self):
        provider = CloudflareProvider(PLACEHOLDER, "abc123", "@cf/m",
                                      base_url="https://api.bitget.com/client/v4")
        with mock.patch.object(urllib.request, "urlopen") as patched:
            with self.assertRaises(AiProviderError):
                provider.complete(make_request())
        patched.assert_not_called()


class TestReplyExtractors(unittest.TestCase):
    def test_openrouter_shapes(self):
        good = {"choices": [{"message": {"content": "hello"}}]}
        self.assertEqual(_extract_openrouter_text(good), "hello")
        for bad in ([], {}, {"choices": []}, {"choices": [{}]},
                    {"choices": [{"message": {}}]},
                    {"choices": [{"message": {"content": "   "}}]},
                    {"choices": [{"message": {"content": 5}}]},
                    {"choices": "nope"}, "string", 5):
            with self.subTest(bad=repr(bad)[:32]), self.assertRaises(AiProviderError):
                _extract_openrouter_text(bad)

    def test_cloudflare_shapes(self):
        self.assertEqual(_extract_cloudflare_text({"result": {"response": "hi"}}), "hi")
        self.assertEqual(
            _extract_cloudflare_text({"success": True, "result": {"response": "hi"}}), "hi"
        )
        with self.assertRaises(AiProviderError) as caught:
            _extract_cloudflare_text({"success": False, "errors": [{"code": 7003}]})
        self.assertIn("7003", str(caught.exception))
        for bad in ([], {"result": {}}, {"result": {"response": ""}},
                    {"result": {"response": 4}}, {"result": "x"}, {"success": False}, "s"):
            with self.subTest(bad=repr(bad)[:32]), self.assertRaises(AiProviderError):
                _extract_cloudflare_text(bad)


if __name__ == "__main__":
    unittest.main()
