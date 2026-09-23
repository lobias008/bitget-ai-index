"""Tests for the AI review layer's configuration and response contract.

The contract is the security boundary of Milestone 3: an untrusted model may
only ever answer with one JSON object from a closed vocabulary, and anything
else must be discarded rather than interpreted charitably. These tests also
pin the safe defaults - offline `fixture` provider, zero temperature, a hard
cap on model calls, and no silent credential fallback.

Offline and deterministic: no network is touched anywhere in this module.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_advisor.config import (  # noqa: E402
    AI_DECISIONS_NAME,
    AI_DISCLAIMER,
    AI_DASHBOARD_EXPORT_PATH,
    AI_OUTPUT_DIR,
    AI_ROLE,
    AI_SUMMARY_NAME,
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    MAX_CALLS_HARD_CAP,
    PROVIDERS,
    REQUIRED_ENV,
    AiConfig,
    AiConfigError,
    ai_config_from_env,
)
from ai_advisor.schema import (  # noqa: E402
    ALLOWED_KEYS,
    DECISIONS,
    MAX_NOTE_CHARS,
    MAX_REASONING_CHARS,
    MAX_RISK_NOTES,
    AiSchemaError,
    extract_json_object,
    parse_decision,
)

VALID = (
    '{"decision": "confirm", "confidence": 0.72,'
    ' "reasoning": "Close held above the prior daily high on the supplied bars.",'
    ' "reason_code": "TREND_ALIGNED", "risk_notes": ["thin 4h volume"]}'
)


class TestAiConfigDefaults(unittest.TestCase):
    def test_defaults_are_safe_and_offline(self):
        config = AiConfig()
        self.assertEqual(config.provider, "fixture")
        self.assertEqual(config.temperature, 0.0)
        self.assertTrue(config.is_synthetic)
        self.assertEqual(config.required_env, ())
        self.assertEqual(config.missing_env({}), ())
        self.assertLessEqual(config.max_calls, MAX_CALLS_HARD_CAP)
        self.assertGreater(config.timeout_s, 0)

    def test_role_is_veto_only(self):
        self.assertEqual(AI_ROLE, "veto_only")
        self.assertIn("veto-only", AI_DISCLAIMER)
        self.assertIn("PAPER / SIMULATED", AI_DISCLAIMER)
        self.assertIn("No live orders", AI_DISCLAIMER)

    def test_providers_and_required_env_are_closed_sets(self):
        self.assertEqual(PROVIDERS, ("fixture", "openrouter", "cloudflare"))
        self.assertEqual(sorted(REQUIRED_ENV), sorted(PROVIDERS))
        self.assertEqual(REQUIRED_ENV["fixture"], ())
        self.assertEqual(REQUIRED_ENV["openrouter"], ("OPENROUTER_API_KEY",))
        self.assertEqual(
            REQUIRED_ENV["cloudflare"], ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_AI_TOKEN")
        )
        for provider in ("openrouter", "cloudflare"):
            self.assertIn(provider, DEFAULT_MODELS)
            self.assertTrue(DEFAULT_BASE_URLS[provider].startswith("https://"))

    def test_artifact_paths_stay_inside_gitignored_locations(self):
        self.assertEqual(AI_OUTPUT_DIR.name, "ai")
        self.assertEqual(AI_OUTPUT_DIR.parent.name, "output")
        self.assertEqual(AI_SUMMARY_NAME, "ai-summary.json")
        self.assertEqual(AI_DECISIONS_NAME, "decisions.jsonl")
        self.assertEqual(AI_DASHBOARD_EXPORT_PATH.parent.name, "public")
        self.assertEqual(AI_DASHBOARD_EXPORT_PATH.parent.parent.name, "dashboard")
        self.assertEqual(AI_DASHBOARD_EXPORT_PATH.name, "ai-summary.json")

    def test_resolved_model_and_base_url(self):
        config = AiConfig(provider="openrouter")
        self.assertEqual(config.resolved_model, DEFAULT_MODELS["openrouter"])
        self.assertEqual(config.resolved_base_url, DEFAULT_BASE_URLS["openrouter"])
        explicit = AiConfig(provider="openrouter", model="m", base_url="https://openrouter.ai/x")
        self.assertEqual(explicit.resolved_model, "m")
        self.assertEqual(explicit.resolved_base_url, "https://openrouter.ai/x")
        self.assertIsNone(AiConfig(provider="fixture").resolved_model)

    def test_to_json_carries_no_credential(self):
        payload = AiConfig(provider="cloudflare", model="m").to_json()
        self.assertEqual(
            sorted(payload),
            ["base_url", "max_calls", "model", "provider", "role", "synthetic",
             "temperature", "timeout_s", "watch_sample"],
        )
        self.assertEqual(payload["role"], AI_ROLE)
        blob = repr(payload).lower()
        for token in ("token", "secret", "credential", "authorization"):
            self.assertNotIn(token, blob)


class TestAiConfigValidation(unittest.TestCase):
    def test_unknown_provider_is_rejected(self):
        for bad in ("", "openai", "bitget", "FIXTURE ", None):
            with self.subTest(provider=bad), self.assertRaises(AiConfigError):
                AiConfig(provider=bad)

    def test_call_budget_is_capped(self):
        with self.assertRaises(AiConfigError):
            AiConfig(max_calls=MAX_CALLS_HARD_CAP + 1)
        with self.assertRaises(AiConfigError):
            AiConfig(max_calls=-1)
        self.assertEqual(AiConfig(max_calls=0).max_calls, 0)
        self.assertEqual(AiConfig(max_calls=MAX_CALLS_HARD_CAP).max_calls, MAX_CALLS_HARD_CAP)

    def test_other_bounds(self):
        with self.assertRaises(AiConfigError):
            AiConfig(timeout_s=0)
        with self.assertRaises(AiConfigError):
            AiConfig(timeout_s=-5)
        with self.assertRaises(AiConfigError):
            AiConfig(watch_sample=-1)
        with self.assertRaises(AiConfigError):
            AiConfig(temperature=-0.1)
        with self.assertRaises(AiConfigError):
            AiConfig(temperature=2.1)
        self.assertEqual(AiConfig(temperature=2.0).temperature, 2.0)

    def test_missing_env_is_reported_not_defaulted(self):
        config = AiConfig(provider="cloudflare")
        self.assertEqual(config.missing_env({}), config.required_env)
        self.assertEqual(config.missing_env({"CLOUDFLARE_ACCOUNT_ID": "  "}),
                         ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_AI_TOKEN"))
        self.assertEqual(
            config.missing_env({"CLOUDFLARE_ACCOUNT_ID": "acct", "CLOUDFLARE_AI_TOKEN": "t"}), ()
        )


class TestAiConfigFromEnv(unittest.TestCase):
    def test_blank_environment_keeps_safe_defaults(self):
        config = ai_config_from_env({})
        self.assertEqual(config.provider, "fixture")
        self.assertIsNone(config.model)
        self.assertIsNone(config.base_url)
        self.assertEqual(config.temperature, 0.0)

    def test_env_values_are_parsed_and_normalized(self):
        config = ai_config_from_env({
            "AI_PROVIDER": "  OpenRouter ",
            "AI_MODEL": "openai/gpt-4o-mini",
            "AI_BASE_URL": "https://openrouter.ai/api/v1",
            "AI_TIMEOUT_S": "12",
            "AI_MAX_CALLS": "7",
            "AI_WATCH_SAMPLE": "2",
            "AI_TEMPERATURE": "0.25",
        })
        self.assertEqual(config.provider, "openrouter")
        self.assertEqual(config.model, "openai/gpt-4o-mini")
        self.assertEqual(config.timeout_s, 12)
        self.assertEqual(config.max_calls, 7)
        self.assertEqual(config.watch_sample, 2)
        self.assertEqual(config.temperature, 0.25)

    def test_blank_strings_are_ignored_not_adopted(self):
        config = ai_config_from_env({"AI_PROVIDER": "", "AI_MODEL": "   ", "AI_MAX_CALLS": ""})
        self.assertEqual(config.provider, "fixture")
        self.assertIsNone(config.model)

    def test_invalid_numbers_raise(self):
        with self.assertRaises(AiConfigError):
            ai_config_from_env({"AI_MAX_CALLS": "many"})
        with self.assertRaises(AiConfigError):
            ai_config_from_env({"AI_TEMPERATURE": "hot"})
        with self.assertRaises(AiConfigError):
            ai_config_from_env({"AI_PROVIDER": "nope"})
        with self.assertRaises(AiConfigError):
            ai_config_from_env({"AI_MAX_CALLS": str(MAX_CALLS_HARD_CAP + 1)})

    def test_credentials_are_never_read_into_the_config(self):
        env = {"AI_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "unit-test-placeholder"}
        config = ai_config_from_env(env)
        self.assertNotIn("unit-test-placeholder", repr(config.to_json()))


class TestExtractJsonObject(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})

    def test_markdown_fence_is_tolerated(self):
        for wrapped in ('```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '  \n {"a": 1} \n '):
            with self.subTest(wrapped=wrapped):
                self.assertEqual(extract_json_object(wrapped), {"a": 1})

    def test_non_text_and_empty_are_rejected(self):
        for bad in (None, 123, {"a": 1}, [], b'{"a": 1}'):
            with self.subTest(bad=type(bad).__name__), self.assertRaises(AiSchemaError):
                extract_json_object(bad)
        for blank in ("", "   ", "\n\t "):
            with self.subTest(blank=repr(blank)), self.assertRaises(AiSchemaError):
                extract_json_object(blank)

    def test_missing_or_malformed_object_is_rejected(self):
        for bad in ("no json here", "[1, 2, 3]", '{"a": ', '{"a": 1}}{', "null"):
            with self.subTest(bad=bad), self.assertRaises(AiSchemaError):
                extract_json_object(bad)


class TestParseDecision(unittest.TestCase):
    def test_valid_confirm(self):
        decision = parse_decision(VALID)
        self.assertEqual(decision.decision, "confirm")
        self.assertEqual(decision.confidence, 0.72)
        self.assertEqual(decision.reason_code, "TREND_ALIGNED")
        self.assertEqual(decision.risk_notes, ("thin 4h volume",))
        self.assertTrue(decision.is_accept)
        self.assertEqual(decision.to_json()["decision"], "confirm")

    def test_every_allowed_decision_parses(self):
        self.assertEqual(DECISIONS, ("confirm", "reject", "watch"))
        for name in DECISIONS:
            payload = ('{"decision": "%s", "confidence": 0.5, "reasoning": "r r",'
                       ' "reason_code": "CODE_ONE"}' % name)
            with self.subTest(decision=name):
                self.assertEqual(parse_decision(payload).decision, name)

    def test_decision_is_case_insensitive_and_trimmed(self):
        payload = VALID.replace('"confirm"', '"  CONFIRM "')
        self.assertEqual(parse_decision(payload).decision, "confirm")

    def test_unknown_decision_is_rejected(self):
        for bad in ("buy", "sell", "long", "", "confirmed"):
            payload = ('{"decision": "%s", "confidence": 0.5, "reasoning": "r r",'
                       ' "reason_code": "CODE_ONE"}' % bad)
            with self.subTest(decision=bad), self.assertRaises(AiSchemaError):
                parse_decision(payload)

    def test_prose_reply_is_rejected(self):
        prose = ("Sure! I looked at the chart and I think BTC looks bullish, so I would "
                 "confirm this trade with about 80 percent confidence.")
        with self.assertRaises(AiSchemaError):
            parse_decision(prose)

    def test_unknown_key_is_rejected(self):
        payload = ('{"decision": "confirm", "confidence": 0.5, "reasoning": "r r",'
                   ' "reason_code": "CODE_ONE", "order_size": 10}')
        with self.assertRaises(AiSchemaError) as caught:
            parse_decision(payload)
        self.assertIn("order_size", str(caught.exception))

    def test_missing_required_key_is_rejected(self):
        for key in ("decision", "confidence", "reasoning", "reason_code"):
            base = {"decision": "confirm", "confidence": 0.5, "reasoning": "r r",
                    "reason_code": "CODE_ONE"}
            base.pop(key)
            import json as _json
            with self.subTest(missing=key), self.assertRaises(AiSchemaError):
                parse_decision(_json.dumps(base))

    def test_allowed_key_set_is_closed(self):
        self.assertEqual(
            sorted(ALLOWED_KEYS),
            sorted(("decision", "confidence", "reasoning", "reason_code", "risk_notes")),
        )

    def test_confidence_bounds_and_types(self):
        for bad in (1.5, -0.1, "0.5", None, [], {}):
            payload = ('{"decision": "confirm", "confidence": %s, "reasoning": "r r",'
                       ' "reason_code": "CODE_ONE"}' % _json_dumps(bad))
            with self.subTest(confidence=bad), self.assertRaises(AiSchemaError):
                parse_decision(payload)

    def test_boolean_confidence_is_rejected(self):
        for raw in ("true", "false"):
            payload = ('{"decision": "confirm", "confidence": %s, "reasoning": "r r",'
                       ' "reason_code": "CODE_ONE"}' % raw)
            with self.subTest(raw=raw), self.assertRaises(AiSchemaError):
                parse_decision(payload)

    def test_confidence_is_rounded_not_truncated(self):
        payload = VALID.replace("0.72", "0.123456789")
        self.assertEqual(parse_decision(payload).confidence, 0.1235)
        for raw, expected in (("0", 0.0), ("1", 1.0), ("0.0", 0.0), ("1.0", 1.0)):
            with self.subTest(raw=raw):
                payload = VALID.replace("0.72", raw)
                self.assertEqual(parse_decision(payload).confidence, expected)

    def test_nan_confidence_is_rejected(self):
        with self.assertRaises(AiSchemaError):
            parse_decision('{"decision": "confirm", "confidence": NaN, "reasoning": "r r",'
                           ' "reason_code": "CODE_ONE"}')

    def test_reason_code_pattern(self):
        for good in ("ABC", "TREND_ALIGNED", "A1_B2", "X" * 40):
            payload = VALID.replace("TREND_ALIGNED", good)
            with self.subTest(code=good[:12]):
                self.assertEqual(parse_decision(payload).reason_code, good)
        for bad in ("ab", "AB", "lower_case", "1ABC", "A-B", "A B", "X" * 41, "", "A..B"):
            payload = VALID.replace('"TREND_ALIGNED"', _json_dumps(bad))
            with self.subTest(code=bad[:12]), self.assertRaises(AiSchemaError):
                parse_decision(payload)

    def test_reasoning_is_required_and_bounded(self):
        for bad in ("", "   ", "\n\t"):
            payload = VALID.replace('"Close held above the prior daily high on the supplied bars."',
                                    _json_dumps(bad))
            with self.subTest(reasoning=repr(bad)), self.assertRaises(AiSchemaError):
                parse_decision(payload)
        payload = VALID.replace('"Close held above the prior daily high on the supplied bars."',
                                _json_dumps("x" * (MAX_REASONING_CHARS + 1)))
        with self.assertRaises(AiSchemaError):
            parse_decision(payload)
        ok = VALID.replace('"Close held above the prior daily high on the supplied bars."',
                           _json_dumps("x" * MAX_REASONING_CHARS))
        self.assertEqual(len(parse_decision(ok).reasoning), MAX_REASONING_CHARS)

    def test_reasoning_whitespace_is_collapsed(self):
        # The replacement contains JSON escape sequences, so the parsed string
        # really does hold a newline and a tab; _clean_text must collapse them.
        payload = VALID.replace(
            "Close held above the prior daily high on the supplied bars.",
            "Close\\n\\theld   above the  prior daily high on the supplied bars.",
        )
        reasoning = parse_decision(payload).reasoning
        self.assertNotIn("\n", reasoning)
        self.assertNotIn("\t", reasoning)
        self.assertNotIn("  ", reasoning)
        self.assertTrue(reasoning.startswith("Close held above"))

    def test_reasoning_must_be_a_string(self):
        payload = VALID.replace('"Close held above the prior daily high on the supplied bars."',
                                "123")
        with self.assertRaises(AiSchemaError):
            parse_decision(payload)

    def test_risk_notes_optional_and_bounded(self):
        payload = ('{"decision": "watch", "confidence": 0.3, "reasoning": "r r",'
                   ' "reason_code": "CODE_ONE", "risk_notes": null}')
        self.assertEqual(parse_decision(payload).risk_notes, ())
        with self.assertRaises(AiSchemaError):
            parse_decision(payload.replace("null", '"not a list"'))
        with self.assertRaises(AiSchemaError):
            parse_decision(payload.replace("null", '{"a": 1}'))
        many = ", ".join('"note"' for _ in range(MAX_RISK_NOTES + 1))
        with self.assertRaises(AiSchemaError):
            parse_decision(payload.replace("null", "[%s]" % many))
        exact = ", ".join('"note"' for _ in range(MAX_RISK_NOTES))
        self.assertEqual(len(parse_decision(payload.replace("null", "[%s]" % exact)).risk_notes),
                         MAX_RISK_NOTES)
        long_note = _json_dumps("y" * (MAX_NOTE_CHARS + 1))
        with self.assertRaises(AiSchemaError):
            parse_decision(payload.replace("null", "[%s]" % long_note))
        with self.assertRaises(AiSchemaError):
            parse_decision(payload.replace("null", '["  "]'))

    def test_schema_error_is_a_value_error(self):
        self.assertTrue(issubclass(AiSchemaError, ValueError))
        self.assertTrue(issubclass(AiConfigError, ValueError))


def _json_dumps(value):
    import json
    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
