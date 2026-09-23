"""Tests for the veto-only advisor: outcomes, budget, and the filter contract.

The advisor is the security boundary between an untrusted model and the
deterministic simulator, so these tests concentrate on the guarantee that
matters: whatever the model says - or fails to say - the advisor can only ever
REMOVE a signal, and every failure mode removes it.

Offline and deterministic: only stub/fixture providers are used here, and no
network is reachable (ai_advisor's single network module is never invoked).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_advisor import advisor as advisor_module  # noqa: E402
from ai_advisor.advisor import (  # noqa: E402
    NON_ACCEPTING_OUTCOMES,
    OUTCOME_ACCEPTED,
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_NO_MARKET_DATA,
    OUTCOME_NOT_PROMOTABLE,
    OUTCOME_PROVIDER_ERROR,
    OUTCOME_SCHEMA_INVALID,
    OUTCOME_VETOED,
    OUTCOME_WATCHED,
    OUTCOMES,
    REVIEW_JSON_KEYS,
    AiAdvisor,
    AiReview,
    make_ai_filter,
)
from ai_advisor.config import AI_ROLE, AiConfig  # noqa: E402
from ai_advisor.prompt import (  # noqa: E402
    PURPOSE_ENTRY_GATE,
    PURPOSE_WATCH_SAMPLE,
    REVIEW_INTERVALS,
    AiPromptError,
)
from ai_advisor.providers import AiProviderError, BaseProvider, ScriptedProvider  # noqa: E402
from ai_advisor.schema import AiSchemaError  # noqa: E402
from paper.signals import ACTIONABLE, BLOCKED, INFORMATIONAL, PaperSignal, iso_utc  # noqa: E402

T0 = 1_758_067_200_000
DAY = 86_400_000
FOUR_H = 14_400_000
SYMBOL = "BTCUSDT"

BARS = {
    "1d": [{"time_ms": T0 - DAY, "close": 100.0}, {"time_ms": T0 - 2 * DAY, "close": 99.0}],
    "4h": [{"time_ms": T0 - FOUR_H, "close": 101.0}],
}


def reply(decision="confirm", confidence=0.8, code="MODEL_CONFIRMED",
          reasoning="Coherent with the supplied closed bars.", notes=None):
    payload = {"decision": decision, "confidence": confidence,
               "reasoning": reasoning, "reason_code": code}
    if notes is not None:
        payload["risk_notes"] = list(notes)
    return json.dumps(payload)


def make_signal(symbol=SYMBOL, ts=T0, classification=ACTIONABLE, checks=None):
    return PaperSignal(
        timestamp_ms=ts,
        symbol=symbol,
        timeframe="4h",
        direction="long",
        classification=classification,
        confidence=0.82 if classification == ACTIONABLE else 0.0,
        entry_reference="100.0",
        stop_loss="97.0",
        stop_distance="3.0",
        target_2r="106.0",
        target_4r="112.0",
        strategy_version="test@0",
        reason_codes=("confluence",),
        checks=checks if checks is not None else {"trend": True, "volume": True},
        data_source="synthetic",
        mode="replay",
        risk_plan={"entry_price": 100.0, "stop_loss": 97.0},
    )


def make_advisor(replies, **config_kwargs):
    provider = ScriptedProvider(list(replies))
    return AiAdvisor(AiConfig(**config_kwargs), provider), provider


def static_slicer(symbol, interval, until_ms):
    return list(BARS.get(interval) or [])


class TestOutcomeVocabulary(unittest.TestCase):
    def test_outcome_set_is_closed_and_explicit(self):
        self.assertEqual(len(OUTCOMES), len(set(OUTCOMES)))
        self.assertEqual(
            set(OUTCOMES),
            {"accepted", "vetoed", "watched", "not_promotable", "schema_invalid",
             "provider_error", "budget_exhausted", "skipped_no_market_data"},
        )
        self.assertEqual(NON_ACCEPTING_OUTCOMES,
                         tuple(name for name in OUTCOMES if name != OUTCOME_ACCEPTED))
        self.assertNotIn(OUTCOME_ACCEPTED, NON_ACCEPTING_OUTCOMES)

    def test_review_json_key_order_is_stable(self):
        self.assertEqual(len(REVIEW_JSON_KEYS), len(set(REVIEW_JSON_KEYS)))
        review = AiReview(time_ms=T0, time=iso_utc(T0), symbol=SYMBOL,
                          purpose=PURPOSE_ENTRY_GATE, parent_classification=ACTIONABLE,
                          outcome=OUTCOME_NOT_PROMOTABLE)
        self.assertEqual(list(review.to_json()), list(REVIEW_JSON_KEYS))

    def test_advisor_requires_config_and_provider(self):
        with self.assertRaises(AiPromptError):
            AiAdvisor("not a config", ScriptedProvider([]))
        with self.assertRaises(AiPromptError):
            AiAdvisor(AiConfig(), None)


class TestDecisionOutcomes(unittest.TestCase):
    def test_confirm_accepts_and_records_the_decision(self):
        advisor, provider = make_advisor([reply(notes=["thin liquidity"])])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS,
                                data_source="synthetic", mode="replay", time_ms=T0)
        self.assertEqual(review.outcome, OUTCOME_ACCEPTED)
        self.assertEqual(review.decision, "confirm")
        self.assertEqual(review.confidence, 0.8)
        self.assertEqual(review.reason_code, "MODEL_CONFIRMED")
        self.assertEqual(review.risk_notes, ["thin liquidity"])
        self.assertEqual(review.detail, "")
        self.assertEqual(review.symbol, SYMBOL)
        self.assertEqual(review.purpose, PURPOSE_ENTRY_GATE)
        self.assertEqual(review.parent_classification, ACTIONABLE)
        self.assertEqual(review.time, iso_utc(T0))
        self.assertEqual(review.time_ms, T0)
        self.assertEqual(advisor.calls_used, 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertIsNone(review.simulator_outcome)
        self.assertEqual(review.fills, [])

    def test_reject_vetoes(self):
        advisor, _ = make_advisor([reply(decision="reject", code="MODEL_REJECTED")])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_VETOED)
        self.assertEqual(review.reason_code, "MODEL_REJECTED")

    def test_watch_does_not_simulate(self):
        advisor, _ = make_advisor([reply(decision="watch", code="MODEL_WATCH")])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_WATCHED)
        self.assertNotEqual(review.outcome, OUTCOME_ACCEPTED)

    def test_prose_reply_is_schema_invalid_and_drops_the_signal(self):
        advisor, provider = make_advisor(
            ["I think this looks bullish, so let us confirm it with high confidence!"]
        )
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_SCHEMA_INVALID)
        self.assertEqual(review.reason_code, "SCHEMA_INVALID")
        self.assertIsNone(review.decision)
        self.assertTrue(review.detail)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(advisor.calls_used, 1)

    def test_reply_with_an_order_field_is_schema_invalid(self):
        advisor, _ = make_advisor(
            [json.dumps({"decision": "confirm", "confidence": 0.9, "reasoning": "r r",
                         "reason_code": "CODE_ONE", "quantity": 5})]
        )
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_SCHEMA_INVALID)
        self.assertIn("quantity", review.detail)

    def test_provider_error_fails_closed(self):
        advisor, _ = make_advisor([AiProviderError("AI provider returned HTTP 503")])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_PROVIDER_ERROR)
        self.assertEqual(review.reason_code, "PROVIDER_ERROR")
        self.assertIn("503", review.detail)

    def test_unexpected_provider_exception_fails_closed_without_leaking(self):
        class Boom(BaseProvider):
            synthetic = True

            def complete(self, request):
                raise RuntimeError("internal detail that must not be recorded")

        advisor = AiAdvisor(AiConfig(), Boom())
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_PROVIDER_ERROR)
        self.assertIn("RuntimeError", review.detail)
        self.assertNotIn("internal detail", review.detail)

    def test_exhausted_scripted_provider_fails_closed(self):
        advisor, _ = make_advisor([])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_PROVIDER_ERROR)
        self.assertEqual(review.reason_code, "PROVIDER_ERROR")
        self.assertIn("exhausted", review.detail)
        self.assertEqual(advisor.calls_used, 1)


class TestGuardsBeforeTheModel(unittest.TestCase):
    def test_budget_zero_never_calls_the_provider(self):
        advisor, provider = make_advisor([reply()], max_calls=0)
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_BUDGET_EXHAUSTED)
        self.assertEqual(review.reason_code, "BUDGET_EXHAUSTED")
        self.assertEqual(advisor.calls_used, 0)
        self.assertEqual(provider.calls, [])

    def test_budget_is_enforced_across_reviews(self):
        advisor, provider = make_advisor([reply(), reply()], max_calls=1)
        first = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        second = advisor.review(purpose=PURPOSE_ENTRY_GATE,
                                signal=make_signal(ts=T0 + FOUR_H), bars=BARS)
        self.assertEqual(first.outcome, OUTCOME_ACCEPTED)
        self.assertEqual(second.outcome, OUTCOME_BUDGET_EXHAUSTED)
        self.assertEqual(advisor.calls_used, 1)
        self.assertEqual(len(provider.calls), 1)

    def test_wrong_classification_is_not_promotable_and_never_calls_the_model(self):
        for classification in (BLOCKED, INFORMATIONAL, "", "unknown"):
            advisor, provider = make_advisor([reply()])
            with self.subTest(classification=classification or "empty"):
                review = advisor.review(
                    purpose=PURPOSE_ENTRY_GATE,
                    signal=make_signal(classification=classification), bars=BARS)
                self.assertEqual(review.outcome, OUTCOME_NOT_PROMOTABLE)
                self.assertEqual(review.reason_code, "NOT_PROMOTABLE")
                self.assertEqual(advisor.calls_used, 0)
                self.assertEqual(provider.calls, [])

    def test_entry_gate_never_reviews_an_informational_signal(self):
        advisor, provider = make_advisor([reply()])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE,
                                signal=make_signal(classification=INFORMATIONAL), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_NOT_PROMOTABLE)
        self.assertEqual(provider.calls, [])

    def test_zero_timestamp_is_invalid(self):
        advisor, provider = make_advisor([reply()])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(ts=0), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_NOT_PROMOTABLE)
        self.assertEqual(review.reason_code, "INVALID_SIGNAL")
        self.assertIsNone(review.time)
        self.assertEqual(provider.calls, [])

    def test_explicit_time_overrides_the_signal_timestamp(self):
        advisor, _ = make_advisor([reply()])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(ts=T0),
                                bars=BARS, time_ms=T0 - FOUR_H)
        self.assertEqual(review.time_ms, T0 - FOUR_H)

    def test_missing_symbol_raises_instead_of_guessing(self):
        advisor, _ = make_advisor([reply()])
        with self.assertRaises(AiPromptError):
            advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(symbol="  "), bars=BARS)

    def test_unknown_purpose_raises(self):
        advisor, _ = make_advisor([reply()])
        with self.assertRaises(AiPromptError):
            advisor.review(purpose="place_trade", signal=make_signal(), bars=BARS)

    def test_no_market_data_is_skipped_without_calling_the_model(self):
        for bars in (None, {}, {"1d": [], "4h": []}, {"1d": [], "4h": None}):
            advisor, provider = make_advisor([reply()])
            with self.subTest(bars=repr(bars)[:24]):
                review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(),
                                        bars=bars)
                self.assertEqual(review.outcome, OUTCOME_NO_MARKET_DATA)
                self.assertEqual(review.reason_code, "SKIPPED_NO_MARKET_DATA")
                self.assertEqual(advisor.calls_used, 0)
                self.assertEqual(provider.calls, [])

    def test_one_non_empty_interval_is_enough_to_review(self):
        advisor, provider = make_advisor([reply()])
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(),
                                bars={"1d": BARS["1d"], "4h": []})
        self.assertEqual(review.outcome, OUTCOME_ACCEPTED)
        self.assertEqual(len(provider.calls), 1)

    def test_signal_dicts_are_accepted(self):
        advisor, _ = make_advisor([reply()])
        payload = make_signal().to_json()
        review = advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=payload, bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_ACCEPTED)
        self.assertEqual(review.symbol, SYMBOL)


class TestWatchSample(unittest.TestCase):
    def test_confirm_on_a_watch_sample_can_never_promote(self):
        advisor, provider = make_advisor([reply()])
        review = advisor.review(purpose=PURPOSE_WATCH_SAMPLE,
                                signal=make_signal(classification=INFORMATIONAL), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_NOT_PROMOTABLE)
        self.assertEqual(review.decision, "confirm")
        self.assertEqual(len(provider.calls), 1)

    def test_reject_and_watch_on_a_watch_sample_are_recorded_as_watched(self):
        for decision in ("reject", "watch"):
            advisor, _ = make_advisor([reply(decision=decision)])
            with self.subTest(decision=decision):
                review = advisor.review(
                    purpose=PURPOSE_WATCH_SAMPLE,
                    signal=make_signal(classification=INFORMATIONAL), bars=BARS)
                self.assertEqual(review.outcome, OUTCOME_WATCHED)

    def test_watch_sample_never_reviews_an_actionable_signal(self):
        advisor, provider = make_advisor([reply()])
        review = advisor.review(purpose=PURPOSE_WATCH_SAMPLE, signal=make_signal(), bars=BARS)
        self.assertEqual(review.outcome, OUTCOME_NOT_PROMOTABLE)
        self.assertEqual(provider.calls, [])
        self.assertEqual(advisor.calls_used, 0)


class TestReporting(unittest.TestCase):
    def test_counts_start_at_zero_for_every_outcome(self):
        advisor, _ = make_advisor([])
        counts = advisor.outcome_counts()
        self.assertEqual(sorted(counts), sorted(OUTCOMES))
        self.assertTrue(all(value == 0 for value in counts.values()))
        self.assertEqual(advisor.stats(),
                         {"calls_used": 0, "max_calls": 40, "reviews": 0, "outcomes": counts})

    def test_counts_and_accepted_filter(self):
        advisor, _ = make_advisor([reply(), reply(decision="reject"), reply(decision="watch")])
        for index in range(3):
            advisor.review(purpose=PURPOSE_ENTRY_GATE,
                           signal=make_signal(ts=T0 + index * FOUR_H), bars=BARS)
        counts = advisor.outcome_counts()
        self.assertEqual(counts[OUTCOME_ACCEPTED], 1)
        self.assertEqual(counts[OUTCOME_VETOED], 1)
        self.assertEqual(counts[OUTCOME_WATCHED], 1)
        self.assertEqual(len(advisor.reviews), 3)
        self.assertEqual([review.outcome for review in advisor.accepted()], [OUTCOME_ACCEPTED])
        self.assertEqual(advisor.stats()["reviews"], 3)
        self.assertEqual(advisor.stats()["calls_used"], 3)

    def test_reviews_are_recorded_in_call_order(self):
        advisor, _ = make_advisor([reply(), reply(decision="reject")])
        advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(ts=T0), bars=BARS)
        advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(ts=T0 + FOUR_H), bars=BARS)
        self.assertEqual([review.time_ms for review in advisor.reviews], [T0, T0 + FOUR_H])


class TestPromptContents(unittest.TestCase):
    def test_request_is_point_in_time_and_veto_only(self):
        advisor, provider = make_advisor([reply()])
        advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS,
                       data_source="rest", mode="replay", time_ms=T0)
        context = provider.calls[0].context
        self.assertEqual(context["execution_mode"], "signal_only")
        self.assertEqual(context["ai_role"], AI_ROLE)
        self.assertEqual(context["purpose"], PURPOSE_ENTRY_GATE)
        self.assertEqual(context["expected_parent_classification"], ACTIONABLE)
        self.assertTrue(context["promotable"])
        self.assertEqual(context["decision_time_ms"], T0)
        self.assertEqual(context["data_source"], "rest")
        self.assertIn("on_violation", context["response_contract"])
        self.assertEqual(sorted(context["market_data"]["intervals"]), sorted(REVIEW_INTERVALS))

    def test_bar_budget_is_bounded(self):
        advisor, provider = make_advisor([reply()])
        many = [{"time_ms": T0 - index * DAY, "close": 100.0} for index in range(500)]
        advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(),
                       bars={"1d": many, "4h": many})
        context = provider.calls[0].context
        self.assertEqual(context["market_data"]["total_closed_bars"], 1000)
        for interval in REVIEW_INTERVALS:
            self.assertLessEqual(len(context["market_data"]["intervals"][interval]), 30)

    def test_no_credential_reaches_the_prompt(self):
        advisor, provider = make_advisor([reply()])
        advisor.review(purpose=PURPOSE_ENTRY_GATE, signal=make_signal(), bars=BARS)
        request = provider.calls[0]
        blob = (request.system + request.user).lower()
        for token in ("authorization", "bearer", "passphrase", "access-key", "access-sign"):
            self.assertNotIn(token, blob)


class TestMakeAiFilter(unittest.TestCase):
    def test_accepted_signals_pass_through_unchanged_and_by_identity(self):
        advisor, _ = make_advisor([reply(), reply(decision="reject"), reply()])
        signals = [make_signal(ts=T0 + index * FOUR_H) for index in range(3)]
        before = [signal.to_json() for signal in signals]
        keep = make_ai_filter(advisor, static_slicer, data_source="synthetic",
                              mode="replay")(T0, signals, {})
        self.assertEqual([id(item) for item in keep], [id(signals[0]), id(signals[2])])
        self.assertEqual([signal.to_json() for signal in signals], before)
        self.assertEqual(len(advisor.reviews), 3)
        self.assertEqual(advisor.outcome_counts()[OUTCOME_ACCEPTED], 2)
        self.assertEqual(advisor.outcome_counts()[OUTCOME_VETOED], 1)

    def test_a_new_list_is_returned_never_the_input(self):
        advisor, _ = make_advisor([reply()])
        signals = [make_signal()]
        keep = make_ai_filter(advisor, static_slicer)(T0, signals, {})
        self.assertIsNot(keep, signals)
        self.assertIsInstance(keep, list)
        self.assertEqual(len(signals), 1)

    def test_all_rejected_yields_nothing(self):
        advisor, _ = make_advisor([reply(decision="reject") for _ in range(3)])
        signals = [make_signal(ts=T0 + index * FOUR_H) for index in range(3)]
        self.assertEqual(make_ai_filter(advisor, static_slicer)(T0, signals, {}), [])

    def test_empty_input_makes_no_call(self):
        advisor, provider = make_advisor([reply()])
        self.assertEqual(make_ai_filter(advisor, static_slicer)(T0, [], {}), [])
        self.assertEqual(make_ai_filter(advisor, static_slicer)(T0, None, {}), [])
        self.assertEqual(provider.calls, [])
        self.assertEqual(advisor.calls_used, 0)

    def test_filter_uses_the_slicer_for_point_in_time_bars(self):
        seen = []

        def recording_slicer(symbol, interval, until_ms):
            seen.append((symbol, interval, until_ms))
            return static_slicer(symbol, interval, until_ms)

        advisor, provider = make_advisor([reply()])
        make_ai_filter(advisor, recording_slicer, data_source="rest")(T0, [make_signal()], {})
        self.assertEqual(sorted(item[:2] for item in seen),
                         sorted((SYMBOL, interval) for interval in REVIEW_INTERVALS))
        self.assertTrue(all(item[2] == T0 for item in seen))
        self.assertEqual(provider.calls[0].context["data_source"], "rest")

    def test_registry_metadata_is_attached_when_available(self):
        class Registry:
            def resolve(self, symbol):
                return {"symbol": symbol, "asset_class": "crypto"}

        advisor, provider = make_advisor([reply()], )
        make_ai_filter(advisor, static_slicer, registry=Registry())(T0, [make_signal()], {})
        self.assertEqual(provider.calls[0].context["instrument"],
                         {"symbol": SYMBOL, "asset_class": "crypto"})

    def test_a_failing_registry_resolver_never_blocks_a_review(self):
        class Broken:
            def resolve(self, symbol):
                raise RuntimeError("registry unavailable")

        advisor, provider = make_advisor([reply()])
        keep = make_ai_filter(advisor, static_slicer, registry=Broken())(T0, [make_signal()], {})
        self.assertEqual(len(keep), 1)
        self.assertEqual(provider.calls[0].context["instrument"], {})

    def test_missing_market_data_at_the_decision_time_drops_the_signal(self):
        advisor, provider = make_advisor([reply()])
        keep = make_ai_filter(advisor, lambda symbol, interval, until_ms: [])(
            T0, [make_signal()], {})
        self.assertEqual(keep, [])
        self.assertEqual(provider.calls, [])
        self.assertEqual(advisor.reviews[0].outcome, OUTCOME_NO_MARKET_DATA)

    def test_budget_exhaustion_drops_the_signal(self):
        advisor, provider = make_advisor([reply()], max_calls=0)
        keep = make_ai_filter(advisor, static_slicer)(T0, [make_signal()], {})
        self.assertEqual(keep, [])
        self.assertEqual(provider.calls, [])

    def test_schema_violation_drops_the_signal(self):
        advisor, _ = make_advisor(["definitely not json"])
        self.assertEqual(make_ai_filter(advisor, static_slicer)(T0, [make_signal()], {}), [])

    def test_filter_only_ever_reduces_the_list(self):
        for replies in ([reply()], [reply(decision="reject")], [reply(decision="watch")],
                        ["garbage"], [AiProviderError("down")]):
            advisor, _ = make_advisor(replies)
            signals = [make_signal()]
            with self.subTest(replies=replies[0].__class__.__name__):
                keep = make_ai_filter(advisor, static_slicer)(T0, signals, {})
                self.assertLessEqual(len(keep), len(signals))
                for item in keep:
                    self.assertIn(item, signals)

    def test_schema_error_type_is_exported_for_reuse(self):
        self.assertTrue(issubclass(AiSchemaError, ValueError))
        self.assertTrue(hasattr(advisor_module, "make_ai_filter"))


if __name__ == "__main__":
    unittest.main()
