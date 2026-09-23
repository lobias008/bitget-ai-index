"""Integration tests for the AI-gated replay (ai_advisor/replay.py).

These exercise the real Milestone-2 pipeline - synthetic market data, the
unchanged strategy through paper/harness.py, and the real PaperSimulator - with
the veto-only reviewer inserted at its single insertion point. They prove:

  * artifacts land under the requested output directory with the AI-specific
    summary name, so an AI run never overwrites a paper run;
  * every artifact is labelled PAPER / SIMULATED and carries the safety block;
  * a vetoing reviewer produces zero fills while the deterministic strategy's
    own recorded signals and coverage stay byte-identical (recording happens
    before the filter, so a veto can never hide a setup);
  * watch samples are audited but can never be simulated;
  * insufficient history is reported honestly instead of being padded;
  * no credential ever reaches an artifact.

Offline and deterministic: the synthetic data source is used and a dataset is
injected, so no network request is possible.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_advisor.advisor import (  # noqa: E402
    OUTCOME_ACCEPTED,
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_NOT_PROMOTABLE,
    OUTCOME_SCHEMA_INVALID,
    OUTCOME_VETOED,
    OUTCOME_WATCHED,
    REVIEW_JSON_KEYS,
)
from ai_advisor.config import (  # noqa: E402
    AI_DECISIONS_NAME,
    AI_ROLE,
    AI_SUMMARY_NAME,
    AiConfig,
)
from ai_advisor.prompt import PURPOSE_ENTRY_GATE, PURPOSE_WATCH_SAMPLE  # noqa: E402
from ai_advisor.providers import BaseProvider, ScriptedProvider  # noqa: E402
from ai_advisor.replay import (  # noqa: E402
    MAX_RECENT_FILLS,
    MAX_RECENT_REVIEWS,
    SAFETY_BLOCK,
    build_ai_block,
    build_world,
    export_ai_to_dashboard,
    load_latest_ai,
    make_slicer,
    reconcile_accepted_with_fills,
    review_watch_sample,
    run_ai_replay,
)
from paper.config import PAPER_LABELS, PaperConfig, load_manifest  # noqa: E402
from paper.market_data import (  # noqa: E402
    INTERVAL_MS,
    MIN_BARS,
    SyntheticSource,
)
from paper.signals import (  # noqa: E402
    ACTIONABLE,
    BLOCKED,
    INFORMATIONAL,
    ReplayDataset,
    fetch_replay_dataset,
    run_replay,
)
from paper.storage import FILE_NAMES  # noqa: E402

STEP = INTERVAL_MS["4h"]
T0 = 1_758_067_200_000
SYMBOL = "SP500USDT"
MANIFEST = load_manifest()
SENTINELS = {
    "OPENROUTER_API_KEY": "sentinel-openrouter-not-a-real-value",
    "CLOUDFLARE_AI_TOKEN": "sentinel-cloudflare-not-a-real-value",
    "CLOUDFLARE_ACCOUNT_ID": "sentinelaccountid",
    "BITGET_ACCESS_KEY": "sentinel-bitget-not-a-real-value",
}

REJECT_REPLY = json.dumps({
    "decision": "reject",
    "confidence": 0.9,
    "reasoning": "Rejected by the test reviewer to prove the veto path.",
    "reason_code": "TEST_VETO",
})


class AlwaysReject(BaseProvider):
    """A reviewer that vetoes everything. Never runs out of replies."""

    name = "always-reject"
    synthetic = True

    def __init__(self):
        self.calls = []

    def complete(self, request):
        self.calls.append(request)
        return REJECT_REPLY


def temp_dir():
    return Path(tempfile.mkdtemp(prefix="bgi-ai-"))


def make_config(days=2, seed=42, symbols=(SYMBOL,)):
    return dataclasses.replace(
        PaperConfig(source="synthetic", days=days, symbols=symbols, seed=seed),
        output_dir=temp_dir(),
    )


DATASET = None


def dataset_for(config):
    global DATASET
    if DATASET is None:
        DATASET = fetch_replay_dataset(config, [SYMBOL], now_ms=T0)
    return DATASET


def run_once(ai_config=None, provider=None, config=None, do_export=True, dataset=None):
    config = config or make_config()
    out_dir = temp_dir()
    export_path = out_dir / "exported-ai-summary.json"
    ds = dataset if dataset is not None else dataset_for(config)
    result = run_ai_replay(
        config,
        ai_config or AiConfig(),
        MANIFEST,
        provider=provider,
        dataset=ds,
        output_dir=out_dir,
        export_path=export_path,
        do_export=do_export,
    )
    return result, out_dir, export_path


def canonical(result):
    return json.dumps({
        "signals": [signal.to_json() for signal in result.outcome.signals],
        "coverage": result.outcome.coverage,
        "steps": result.steps,
        "fills": result.simulator.fills,
        "equity": result.simulator.equity_history,
        "outcomes": result.advisor.outcome_counts(),
    }, sort_keys=True, default=str)


DEFAULT_RUN = None


def default_run():
    """One shared fixture-provider run, reused by the read-only assertions."""
    global DEFAULT_RUN
    if DEFAULT_RUN is None:
        DEFAULT_RUN = run_once()
    return DEFAULT_RUN


class TestArtifacts(unittest.TestCase):
    def test_every_artifact_is_written_under_the_output_directory(self):
        result, out_dir, export_path = default_run()
        expected = {
            "signals": "signals.jsonl",
            "fills": "fills.jsonl",
            "positions": "positions.json",
            "equity": "equity.jsonl",
            "events": "events.jsonl",
            "metadata": FILE_NAMES["metadata"],
            "summary": AI_SUMMARY_NAME,
            "decisions": AI_DECISIONS_NAME,
        }
        self.assertEqual(sorted(result.paths), sorted(expected))
        for key, name in expected.items():
            with self.subTest(artifact=key):
                self.assertEqual(Path(result.paths[key]).name, name)
                self.assertTrue(Path(result.paths[key]).exists())
                self.assertEqual(Path(result.paths[key]).parent, out_dir)

    def test_the_ai_run_does_not_write_the_paper_summary_name(self):
        _result, out_dir, _export = default_run()
        self.assertFalse((out_dir / FILE_NAMES["summary"]).exists())
        self.assertTrue((out_dir / AI_SUMMARY_NAME).exists())
        self.assertNotEqual(AI_SUMMARY_NAME, FILE_NAMES["summary"])

    def test_export_is_written_and_matches_the_summary(self):
        result, _out_dir, export_path = default_run()
        self.assertEqual(result.exported, export_path)
        self.assertTrue(export_path.exists())
        self.assertEqual(json.loads(export_path.read_text(encoding="utf-8")), result.summary)

    def test_export_can_be_disabled(self):
        result, _out_dir, export_path = run_once(do_export=False)
        self.assertIsNone(result.exported)
        self.assertFalse(export_path.exists())
        self.assertTrue(Path(result.paths["summary"]).exists())

    def test_export_from_disk_and_missing_summary(self):
        _result, out_dir, _export = default_run()
        missing_dir = temp_dir()
        with self.assertRaises(FileNotFoundError):
            export_ai_to_dashboard(output_dir=missing_dir,
                                   path=missing_dir / "ai-summary.json")
        target = temp_dir() / "nested" / "ai-summary.json"
        written = export_ai_to_dashboard(output_dir=out_dir, path=target)
        self.assertEqual(written, target)
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["labels"],
                         list(PAPER_LABELS))


class TestSummaryAndMetadata(unittest.TestCase):
    def test_summary_is_labelled_paper_and_simulated(self):
        result, _out_dir, _export = default_run()
        summary = result.summary
        self.assertEqual(summary["labels"], list(PAPER_LABELS))
        self.assertEqual(summary["labels"], ["PAPER", "SIMULATED"])
        self.assertEqual(summary["mode"], "replay")
        self.assertEqual(summary["data_source"], "synthetic")
        self.assertTrue(summary["last_updated"])
        self.assertIn("disclaimer", summary)

    def test_ai_block_is_veto_only_and_labelled_synthetic(self):
        result, _out_dir, _export = default_run()
        block = result.summary["ai"]
        self.assertEqual(block["role"], AI_ROLE)
        self.assertEqual(block["role"], "veto_only")
        self.assertEqual(block["provider"], "fixture")
        self.assertTrue(block["synthetic_model"])
        self.assertTrue(block["synthetic_market_data"])
        self.assertTrue(block["synthetic"])
        self.assertTrue(result.summary["synthetic"])
        self.assertEqual(block["schema_version"], 1)
        self.assertEqual(block["max_calls"], AiConfig().max_calls)
        self.assertLessEqual(block["calls_used"], block["max_calls"])
        self.assertEqual(sorted(block["outcomes"]), sorted(result.advisor.outcome_counts()))
        self.assertLessEqual(len(block["recent_reviews"]), MAX_RECENT_REVIEWS)
        self.assertLessEqual(len(block["recent_fills"]), MAX_RECENT_FILLS)
        self.assertIn("veto-only", block["disclaimer"])

    def test_metadata_safety_block(self):
        result, _out_dir, _export = default_run()
        metadata = result.metadata
        self.assertEqual(metadata["ai"]["safety"], SAFETY_BLOCK)
        self.assertEqual(SAFETY_BLOCK["live_orders_placed"], False)
        self.assertEqual(SAFETY_BLOCK["private_api_used"], False)
        self.assertEqual(SAFETY_BLOCK["execution_mode"], "signal_only")
        self.assertEqual(metadata["safety"]["live_orders_placed"], False)
        self.assertEqual(metadata["safety"]["private_api_used"], False)
        self.assertEqual(metadata["safety"]["api_scope"], "public_read_only_market_data")
        self.assertEqual(metadata["safety"]["execution_mode"], "signal_only")
        self.assertEqual(metadata["labels"], list(PAPER_LABELS))
        self.assertEqual(metadata["errors"], [])
        self.assertEqual(metadata["ai"]["role"], AI_ROLE)
        self.assertEqual(metadata["ai"]["config"], AiConfig().to_json())
        self.assertEqual(metadata["config"]["ai"], AiConfig().to_json())
        self.assertEqual(metadata["config"]["source"], "synthetic")
        self.assertEqual(metadata["ai"]["stats"]["calls_used"],
                         result.summary["ai"]["calls_used"])

    def test_notes_disclose_the_synthetic_reviewer_and_the_veto(self):
        result, _out_dir, _export = default_run()
        notes = " ".join(result.summary["notes"])
        self.assertIn("SYNTHETIC AI provider", notes)
        self.assertIn("SYNTHETIC data source", notes)
        self.assertIn("veto-only", notes)
        self.assertIn("can never create or resize", notes)

    def test_window_matches_the_dataset(self):
        result, _out_dir, _export = default_run()
        ds = dataset_for(make_config())
        self.assertEqual(result.window["start_ms"], ds.window_start_ms)
        self.assertEqual(result.window["end_ms"], ds.window_end_ms)
        self.assertEqual(result.summary["window"], result.window)

    def test_build_ai_block_without_a_simulator(self):
        result, _out_dir, _export = default_run()
        block = build_ai_block(result.advisor, AiConfig(), result.provider, None, "rest")
        self.assertEqual(block["recent_fills"], [])
        self.assertFalse(block["synthetic_market_data"])
        self.assertTrue(block["synthetic_model"])


class TestDecisionLog(unittest.TestCase):
    def test_every_row_uses_the_documented_key_order(self):
        result, out_dir, _export = default_run()
        rows = [json.loads(line) for line in
                (out_dir / AI_DECISIONS_NAME).read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), len(result.advisor.reviews))
        self.assertTrue(rows, "a small replay still reviews the watch sample")
        for row in rows:
            with self.subTest(symbol=row["symbol"]):
                self.assertEqual(list(row), list(REVIEW_JSON_KEYS))
                self.assertIn(row["purpose"], (PURPOSE_ENTRY_GATE, PURPOSE_WATCH_SAMPLE))
                if row["purpose"] == PURPOSE_WATCH_SAMPLE:
                    self.assertNotEqual(row["outcome"], OUTCOME_ACCEPTED)

    def test_reviews_match_the_advisor_exactly(self):
        result, _out_dir, _export = default_run()
        expected = [review.to_json() for review in result.advisor.reviews][-MAX_RECENT_REVIEWS:]
        self.assertEqual(result.summary["ai"]["recent_reviews"], expected)


class TestLoadLatest(unittest.TestCase):
    def test_roundtrip(self):
        result, out_dir, _export = default_run()
        loaded = load_latest_ai(out_dir)
        self.assertEqual(loaded["summary"], result.summary)
        self.assertEqual(loaded["metadata"], result.metadata)

    def test_missing_run_returns_none(self):
        self.assertIsNone(load_latest_ai(temp_dir()))


class TestVetoSemantics(unittest.TestCase):
    def test_a_vetoing_reviewer_produces_no_fills(self):
        provider = AlwaysReject()
        result, _out_dir, _export = run_once(provider=provider)
        counts = result.advisor.outcome_counts()
        self.assertEqual(counts[OUTCOME_ACCEPTED], 0)
        self.assertEqual(result.simulator.fills, [])
        self.assertEqual(result.simulator.closed_trades, [])
        self.assertEqual(result.summary["closed_trades"], 0)
        self.assertEqual(result.summary["realized_pnl"], 0.0)
        self.assertEqual(result.summary["equity"], result.summary["initial_balance"])
        self.assertTrue(result.advisor.reviews)
        allowed = {
            (PURPOSE_ENTRY_GATE, OUTCOME_VETOED),
            (PURPOSE_WATCH_SAMPLE, OUTCOME_WATCHED),
            (PURPOSE_WATCH_SAMPLE, OUTCOME_NOT_PROMOTABLE),
        }
        for review in result.advisor.reviews:
            with self.subTest(purpose=review.purpose):
                self.assertIn((review.purpose, review.outcome), allowed)
        # A 2-day window emits no actionable setup at all, so the only calls
        # are the post-replay watch audit. Neither path may produce a fill.
        entry_vetoes = [r for r in result.advisor.reviews
                        if r.purpose == PURPOSE_ENTRY_GATE]
        self.assertEqual(len(entry_vetoes), counts[OUTCOME_VETOED])

    def test_recording_happens_before_the_filter(self):
        baseline, _out, _exp = default_run()
        vetoed, _out2, _exp2 = run_once(provider=AlwaysReject())
        self.assertEqual(
            json.dumps([s.to_json() for s in baseline.outcome.signals], sort_keys=True, default=str),
            json.dumps([s.to_json() for s in vetoed.outcome.signals], sort_keys=True, default=str),
        )
        self.assertEqual(baseline.outcome.coverage, vetoed.outcome.coverage)
        self.assertEqual(baseline.steps, vetoed.steps)
        self.assertEqual(baseline.summary["data_coverage"], vetoed.summary["data_coverage"])
        self.assertEqual(baseline.summary["coverage"], vetoed.summary["coverage"])

    def test_accepted_reviews_are_reconciled_with_the_simulator(self):
        result, _out_dir, _export = default_run()
        accepted = [r for r in result.advisor.reviews
                    if r.purpose == PURPOSE_ENTRY_GATE and r.outcome == OUTCOME_ACCEPTED]
        for review in accepted:
            with self.subTest(symbol=review.symbol):
                self.assertIsNotNone(review.simulator_outcome)
                if review.simulator_outcome == "filled":
                    self.assertTrue(review.fills)
                    self.assertIsNotNone(review.entry_price)
                    self.assertIsNotNone(review.quantity)
                else:
                    self.assertEqual(review.fills, [])

    def test_reconcile_only_counts_entry_side_fills_at_the_same_instant(self):
        class FakeSim:
            fills = [
                {"symbol": SYMBOL, "time_ms": T0, "side": "entry", "fill_price": "1", "quantity": "2"},
                {"symbol": SYMBOL, "time_ms": T0, "side": "exit", "fill_price": "3", "quantity": "2"},
                {"symbol": SYMBOL, "time_ms": T0 + STEP, "side": "entry", "fill_price": "4", "quantity": "5"},
            ]
            events = []

        result, _out_dir, _export = default_run()
        from ai_advisor.advisor import AiReview
        result.advisor.reviews.append(AiReview(
            time_ms=T0, time=None, symbol=SYMBOL, purpose=PURPOSE_ENTRY_GATE,
            parent_classification=ACTIONABLE, outcome=OUTCOME_ACCEPTED))
        result.advisor.reviews.append(AiReview(
            time_ms=T0 + 5 * STEP, time=None, symbol=SYMBOL, purpose=PURPOSE_ENTRY_GATE,
            parent_classification=ACTIONABLE, outcome=OUTCOME_ACCEPTED))
        reconcile_accepted_with_fills(result.advisor, FakeSim())
        matched = [r for r in result.advisor.reviews if r.time_ms == T0 and r.purpose == PURPOSE_ENTRY_GATE]
        self.assertEqual(matched[-1].simulator_outcome, "filled")
        self.assertEqual(len(matched[-1].fills), 1)
        self.assertEqual(matched[-1].fills[0]["side"], "entry")
        unmatched = [r for r in result.advisor.reviews if r.time_ms == T0 + 5 * STEP]
        self.assertEqual(unmatched[-1].simulator_outcome, "no_simulator_record")

    def test_reconcile_copies_fills_so_a_review_cannot_mutate_the_ledger(self):
        class FakeSim:
            fills = [{"symbol": SYMBOL, "time_ms": T0, "side": "entry",
                      "fill_price": "1", "quantity": "2"}]
            events = []

        from ai_advisor.advisor import AiAdvisor, AiReview
        advisor = AiAdvisor(AiConfig(), AlwaysReject())
        advisor.reviews.append(AiReview(
            time_ms=T0, time=None, symbol=SYMBOL, purpose=PURPOSE_ENTRY_GATE,
            parent_classification=ACTIONABLE, outcome=OUTCOME_ACCEPTED))
        sim = FakeSim()
        reconcile_accepted_with_fills(advisor, sim)
        advisor.reviews[0].fills[0]["fill_price"] = "tampered"
        self.assertEqual(sim.fills[0]["fill_price"], "1")


class TestWatchSample(unittest.TestCase):
    def test_watch_reviews_are_never_accepted_and_never_simulated(self):
        result, _out_dir, _export = default_run()
        watches = [r for r in result.advisor.reviews if r.purpose == PURPOSE_WATCH_SAMPLE]
        self.assertTrue(watches, "a 2-day replay emits informational signals to audit")
        for review in watches:
            with self.subTest(symbol=review.symbol):
                self.assertIn(review.outcome, (OUTCOME_WATCHED, OUTCOME_NOT_PROMOTABLE))
                self.assertNotEqual(review.outcome, OUTCOME_ACCEPTED)
        filled_keys = {(str(f["symbol"]), int(f["time_ms"])) for f in result.simulator.fills}
        for review in watches:
            self.assertNotIn((review.symbol, review.time_ms), filled_keys)

    def test_watch_sample_zero_disables_the_audit(self):
        result, _out_dir, _export = run_once(ai_config=AiConfig(watch_sample=0))
        self.assertEqual([r for r in result.advisor.reviews if r.purpose == PURPOSE_WATCH_SAMPLE], [])
        self.assertEqual(result.summary["ai"]["watch_sample"], 0)

    def test_review_watch_sample_helper_filters_by_classification(self):
        result, _out_dir, _export = default_run()
        advisor = result.advisor
        before = len(advisor.reviews)
        actionable_only = [s for s in result.outcome.signals if s.classification == ACTIONABLE]
        added = review_watch_sample(advisor, actionable_only, make_slicer(dataset_for(make_config())),
                                    watch_sample=3, data_source="synthetic", mode="replay")
        self.assertEqual(added, [])
        self.assertEqual(len(advisor.reviews), before)

    def test_review_watch_sample_is_a_no_op_at_zero(self):
        result, _out_dir, _export = default_run()
        advisor = result.advisor
        before = len(advisor.reviews)
        self.assertEqual(review_watch_sample(
            advisor, result.outcome.signals, make_slicer(dataset_for(make_config())),
            watch_sample=0), [])
        self.assertEqual(len(advisor.reviews), before)


class TestSlicer(unittest.TestCase):
    def test_slicer_never_returns_a_bar_that_closes_after_the_instant(self):
        ds = dataset_for(make_config())
        slicer = make_slicer(ds)
        for until in (ds.window_start_ms, ds.decision_times[len(ds.decision_times) // 2],
                      ds.window_end_ms, ds.window_end_ms + 10 * STEP):
            for interval in ("1d", "4h"):
                bars = slicer(SYMBOL, interval, until)
                with self.subTest(until=until, interval=interval):
                    self.assertTrue(all(bar.time_ms + INTERVAL_MS[interval] <= until
                                        for bar in bars))
        self.assertEqual(slicer("UNKNOWNUSDT", "1d", ds.window_end_ms), [])
        self.assertEqual(slicer(SYMBOL, "1m", ds.window_end_ms), [])
        full = slicer(SYMBOL, "4h", ds.window_end_ms + 10 * STEP)
        self.assertEqual(len(full), len(ds.bars[SYMBOL]["4h"]))


class TestDeterminism(unittest.TestCase):
    def test_identical_inputs_produce_identical_runs(self):
        first, _o1, _e1 = run_once(provider=AlwaysReject(), config=make_config(seed=7))
        second, _o2, _e2 = run_once(provider=AlwaysReject(), config=make_config(seed=7))
        self.assertEqual(canonical(first), canonical(second))

    def test_a_different_seed_changes_the_data_not_the_labels(self):
        other, _out_dir, _export = run_once(config=make_config(seed=99))
        baseline, _o, _e = default_run()
        self.assertEqual(other.summary["labels"], baseline.summary["labels"])
        self.assertEqual(other.summary["ai"]["role"], AI_ROLE)


class TestInsufficientHistory(unittest.TestCase):
    def test_short_history_is_reported_and_nothing_is_simulated(self):
        source = SyntheticSource(seed=5)
        four = source.fetch(SYMBOL, "4h", end_ms=T0, bars_needed=140)
        daily = source.fetch(SYMBOL, "1d", end_ms=T0, bars_needed=MIN_BARS["1d"] - 40)
        closes = sorted(bar.time_ms + STEP for bar in four)
        dataset = ReplayDataset("synthetic", {SYMBOL: {"1d": daily, "4h": four}},
                                closes[-6:], closes[-6], closes[-1])
        provider = AlwaysReject()
        result, out_dir, _export = run_once(provider=provider, dataset=dataset)
        self.assertEqual(result.outcome.coverage[SYMBOL]["steps_with_data"], 0)
        blocked = [s for s in result.outcome.signals
                   if s.classification == BLOCKED and "insufficient_history" in s.reason_codes]
        self.assertEqual(len(blocked), 1)
        self.assertEqual([s for s in result.outcome.signals if s.classification == ACTIONABLE], [])
        self.assertEqual(result.simulator.fills, [])
        self.assertEqual(provider.calls, [])
        self.assertEqual(result.advisor.outcome_counts()[OUTCOME_ACCEPTED], 0)
        self.assertTrue((out_dir / AI_SUMMARY_NAME).exists())
        self.assertEqual(result.summary["closed_trades"], 0)
        self.assertLess(result.outcome.coverage[SYMBOL]["daily_bars"], MIN_BARS["1d"])


class TestNoSecretsInArtifacts(unittest.TestCase):
    def test_credentials_in_the_environment_never_reach_an_artifact(self):
        with mock.patch.dict(os.environ, SENTINELS, clear=False):
            config = AiConfig(provider="fixture")
            result, out_dir, export_path = run_once(ai_config=config)
        files = [export_path, *(Path(p) for p in result.paths.values())]
        self.assertTrue(files)
        for path in files:
            text = Path(path).read_text(encoding="utf-8")
            for name, value in SENTINELS.items():
                with self.subTest(artifact=path.name, variable=name):
                    self.assertNotIn(value, text)
                    self.assertNotIn(value.lower(), text.lower())

    def test_logs_stay_exact_while_the_summary_is_display_rounded(self):
        result, out_dir, _export = default_run()
        # The summary is a display artifact and is rounded on purpose; the
        # internal accounting and the JSONL logs are not.
        self.assertEqual(result.summary["initial_balance"], 1000.0)
        stats = result.simulator.stats()
        self.assertIsInstance(stats["equity"], Decimal)
        self.assertIsInstance(stats["realized_pnl"], Decimal)
        rows = [json.loads(line)
                for line in (out_dir / FILE_NAMES["equity"]).read_text(
                    encoding="utf-8").splitlines()
                if line.strip()]
        self.assertEqual(len(rows), result.steps)
        for row in rows:
            for field in ("equity", "balance", "unrealized_pnl", "daily_pnl_pct"):
                self.assertIsInstance(row[field], str)
                Decimal(row[field])
        self.assertEqual(Decimal(rows[0]["balance"]), result.simulator.initial_balance)
        self.assertEqual(Decimal(rows[-1]["equity"]), stats["equity"])



class TestFilterWiring(unittest.TestCase):
    """Proves the single insertion point really is the only one.

    ``run_replay`` must hand ``PaperSimulator.process_step`` exactly the list the
    filter returned, and must record its own signals before the filter runs.
    """

    def _record(self, config, dataset, signal_filter):
        world = build_world(config, MANIFEST, dataset)
        seen = []
        original = world.simulator.process_step

        def spy(decision_ms, closed_bars, actionable):
            seen.append((decision_ms, [signal.key for signal in actionable]))
            return original(decision_ms, closed_bars, actionable)

        world.simulator.process_step = spy
        outcome = run_replay(config, MANIFEST, dataset, world.simulator, world.harness,
                             signal_filter=signal_filter)
        return seen, outcome, world

    def test_the_simulator_receives_exactly_what_the_filter_returns(self):
        config = make_config()
        dataset = dataset_for(config)
        baseline, base_outcome, _w1 = self._record(config, dataset, None)
        emptied, emptied_outcome, _w2 = self._record(config, dataset, lambda ms, a, b: [])
        identity, identity_outcome, _w3 = self._record(
            config, dataset, lambda ms, a, b: list(a))

        def canon(outcome):
            return json.dumps([s.to_json() for s in outcome.signals],
                              sort_keys=True, default=str)

        self.assertEqual(len(baseline), config.days * 6 + 1)
        self.assertEqual(emptied, [(ms, []) for ms, _keys in baseline])
        self.assertEqual(identity, baseline)
        self.assertEqual(canon(emptied_outcome), canon(base_outcome))
        self.assertEqual(canon(identity_outcome), canon(base_outcome))
        self.assertEqual(base_outcome.coverage, emptied_outcome.coverage)
        self.assertEqual(base_outcome.steps, emptied_outcome.steps)

    def test_the_filter_is_only_given_actionable_signals(self):
        config = make_config(days=80)
        dataset = fetch_replay_dataset(config, [SYMBOL], now_ms=T0)
        observed = []

        def spy_filter(decision_ms, actionable, closed_bars):
            observed.append((decision_ms, [s.classification for s in actionable],
                             sorted(closed_bars or {})))
            return list(actionable)

        self._record(config, dataset, spy_filter)
        self.assertTrue(observed, "an 80-day window emits at least one actionable signal")
        for _ms, classifications, symbols in observed:
            self.assertTrue(classifications)
            self.assertTrue(all(item == ACTIONABLE for item in classifications))
            self.assertTrue(set(symbols) <= {SYMBOL})

    def test_run_ai_replay_wires_the_ai_filter(self):
        from ai_advisor import replay as replay_module
        source = Path(replay_module.__file__).read_text(encoding="utf-8")
        self.assertIn("make_ai_filter(", source)
        self.assertIn("signal_filter=ai_filter", source)
        self.assertEqual(source.count("signal_filter=ai_filter"), 1)


class TestAcceptToFillIntegration(unittest.TestCase):
    """End-to-end: a confirmed deterministic signal becomes a simulated fill.

    Uses an 80-day synthetic window for SP500USDT with seed 42 because that is
    the shortest window in which the unchanged strategy emits an actionable
    setup for this symbol. The counts are asserted exactly, so a change to the
    strategy or the generator fails loudly instead of silently degrading into a
    vacuous pass. Still fully offline: the reviewer is the fixture provider and
    the market data is the seeded synthetic source.
    """

    @classmethod
    def setUpClass(cls):
        cls.config = make_config(days=80)
        cls.dataset = fetch_replay_dataset(cls.config, [SYMBOL], now_ms=T0)

    def _run(self, provider):
        return run_once(ai_config=AiConfig(watch_sample=0), provider=provider,
                        config=self.config, dataset=self.dataset)

    def test_a_confirmed_setup_is_simulated_and_reconciled(self):
        result, out_dir, _export = self._run(None)
        actionable = [s for s in result.outcome.signals if s.classification == ACTIONABLE]
        self.assertEqual(len(actionable), 1)
        counts = result.advisor.outcome_counts()
        self.assertEqual(counts[OUTCOME_ACCEPTED], 1)
        self.assertEqual(counts[OUTCOME_VETOED], 0)
        self.assertEqual(result.advisor.calls_used, 1)
        self.assertTrue(result.simulator.fills)
        self.assertGreaterEqual(result.summary["closed_trades"], 1)

        accepted = [r for r in result.advisor.reviews if r.outcome == OUTCOME_ACCEPTED]
        self.assertEqual(len(accepted), 1)
        review = accepted[0]
        self.assertEqual(review.symbol, SYMBOL)
        self.assertEqual(review.purpose, PURPOSE_ENTRY_GATE)
        self.assertEqual(review.decision, "confirm")
        self.assertEqual(review.simulator_outcome, "filled")
        self.assertEqual(len(review.fills), 1)
        self.assertEqual(review.fills[0]["side"], "entry")
        self.assertEqual(Decimal(review.entry_price), Decimal(review.fills[0]["fill_price"]))
        self.assertEqual(Decimal(review.quantity), Decimal(review.fills[0]["quantity"]))
        self.assertEqual(review.time_ms, actionable[0].timestamp_ms)

        rows = [json.loads(line)
                for line in (out_dir / AI_DECISIONS_NAME).read_text(encoding="utf-8").splitlines()
                if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], OUTCOME_ACCEPTED)
        self.assertEqual(rows[0]["simulator_outcome"], "filled")
        self.assertEqual(rows[0]["reason_code"], "FIXTURE_CONFIRM")
        self.assertEqual(result.summary["ai"]["recent_fills"], result.simulator.fills)

    def test_the_identical_setup_vetoed_produces_no_fill(self):
        accepted_run, _o1, _e1 = self._run(None)
        vetoed_run, out_dir, _e2 = self._run(AlwaysReject())
        self.assertEqual(
            json.dumps([s.to_json() for s in accepted_run.outcome.signals],
                       sort_keys=True, default=str),
            json.dumps([s.to_json() for s in vetoed_run.outcome.signals],
                       sort_keys=True, default=str),
        )
        self.assertEqual(accepted_run.outcome.coverage, vetoed_run.outcome.coverage)
        counts = vetoed_run.advisor.outcome_counts()
        self.assertEqual(counts[OUTCOME_VETOED], 1)
        self.assertEqual(counts[OUTCOME_ACCEPTED], 0)
        self.assertEqual(vetoed_run.simulator.fills, [])
        self.assertEqual(vetoed_run.simulator.positions, {})
        self.assertEqual(vetoed_run.simulator.closed_trades, [])
        self.assertEqual(vetoed_run.summary["closed_trades"], 0)
        self.assertEqual(vetoed_run.summary["realized_pnl"], 0.0)
        self.assertEqual(vetoed_run.summary["equity"], vetoed_run.summary["initial_balance"])
        self.assertEqual(vetoed_run.summary["ai"]["recent_fills"], [])
        rows = [json.loads(line)
                for line in (out_dir / AI_DECISIONS_NAME).read_text(encoding="utf-8").splitlines()
                if line.strip()]
        self.assertEqual([row["outcome"] for row in rows], [OUTCOME_VETOED])
        self.assertEqual([row["reason_code"] for row in rows], ["TEST_VETO"])

    def test_a_broken_reviewer_also_produces_no_fill(self):
        result, _out_dir, _export = self._run(ScriptedProvider(["not json at all"]))
        counts = result.advisor.outcome_counts()
        self.assertEqual(counts[OUTCOME_SCHEMA_INVALID], 1)
        self.assertEqual(counts[OUTCOME_ACCEPTED], 0)
        self.assertEqual(result.simulator.fills, [])
        self.assertEqual(result.summary["closed_trades"], 0)

    def test_an_exhausted_budget_also_produces_no_fill(self):
        result, _out_dir, _export = run_once(
            ai_config=AiConfig(watch_sample=0, max_calls=0), provider=None,
            config=self.config, dataset=self.dataset)
        counts = result.advisor.outcome_counts()
        self.assertEqual(counts[OUTCOME_BUDGET_EXHAUSTED], 1)
        self.assertEqual(counts[OUTCOME_ACCEPTED], 0)
        self.assertEqual(result.advisor.calls_used, 0)
        self.assertEqual(result.simulator.fills, [])

if __name__ == "__main__":
    unittest.main()
