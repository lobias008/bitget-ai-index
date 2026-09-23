"""Safety tests for the AI review layer (Milestone 3).

These are structural guarantees about the shipped source, not about a run: they
prove the AI layer contains no order plumbing, cannot reach a non-allowlisted
host, cannot see a credential, keeps its artifacts gitignored, and refuses to
start unless the manifest still declares execution_mode: signal_only.

Offline and deterministic. No network, no subprocess, no file outside a temp dir.
"""
from __future__ import annotations

import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_advisor import cli as ai_cli  # noqa: E402
from ai_advisor.config import (  # noqa: E402
    AI_DISCLAIMER,
    AI_DASHBOARD_EXPORT_PATH,
    AI_OUTPUT_DIR,
    AI_ROLE,
    PROVIDERS,
)
from ai_advisor.replay import SAFETY_BLOCK  # noqa: E402
from paper.cli import EXIT_CONFIG, EXIT_OK  # noqa: E402
from paper.config import load_manifest  # noqa: E402

AI_DIR = REPO_ROOT / "ai_advisor"
MODULES = [
    "__init__.py",
    "advisor.py",
    "cli.py",
    "config.py",
    "prompt.py",
    "providers.py",
    "replay.py",
    "schema.py",
]
PROVIDERS_FILE = "providers.py"

# Anything that would let the AI layer touch an account or place an order. The
# package must not merely avoid calling these - it must not contain them.
ORDER_TOKENS = [
    "place-order",
    "place_order",
    "placeOrder",
    "/api/v2/mix/order",
    "/api/v2/mix/account",
    "/api/v2/spot/trade",
    "ACCESS-KEY",
    "ACCESS-SIGN",
    "ACCESS-TIMESTAMP",
    "passphrase",
    "api_secret",
    "API_SECRET",
    "BITGET_API",
    "requests.post",
    "api.bitget.com",
    "subprocess",
]

NETWORK_IMPORTS = ["urllib", "socket", "http.client", "requests", "aiohttp", "httpx"]


def sources():
    return {name: (AI_DIR / name).read_text(encoding="utf-8") for name in MODULES}


def non_provider_sources():
    return {name: text for name, text in sources().items() if name != PROVIDERS_FILE}


class TestPackageShape(unittest.TestCase):
    def test_every_module_ships_and_is_not_empty(self):
        self.assertTrue(AI_DIR.is_dir())
        for name in MODULES:
            path = AI_DIR / name
            with self.subTest(module=name):
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 200)
        self.assertEqual(
            sorted(item.name for item in AI_DIR.glob("*.py")), sorted(MODULES)
        )

    def test_no_bytecode_is_shipped(self):
        self.assertEqual(list(AI_DIR.glob("__pycache__")), [])
        self.assertEqual(list(AI_DIR.rglob("*.pyc")), [])


class TestNoOrderPlumbing(unittest.TestCase):
    def test_forbidden_tokens_are_absent_everywhere(self):
        for name, text in sources().items():
            for token in ORDER_TOKENS:
                with self.subTest(module=name, token=token):
                    self.assertNotIn(token, text)

    def test_no_private_api_or_credential_names_outside_providers(self):
        pattern = re.compile(r"\bapi[_-]?key\b|\bsecret\b|\bpassphrase\b", re.IGNORECASE)
        for name, text in non_provider_sources().items():
            if name == "config.py":
                # config.py names the environment variables a provider needs;
                # it never reads or stores their values.
                self.assertIn("OPENROUTER_API_KEY", text)
                self.assertNotIn("os.environ[", text)
                continue
            with self.subTest(module=name):
                self.assertIsNone(pattern.search(text))

    def test_no_module_mutates_the_environment(self):
        pattern = re.compile(r"os\.environ\s*\[[^\]]+\]\s*=|os\.putenv|environ\.setdefault")
        for name, text in sources().items():
            with self.subTest(module=name):
                self.assertIsNone(pattern.search(text))

    def test_no_credential_literal_is_assigned_anywhere(self):
        pattern = re.compile(
            r"\b(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret"
            r"|auth[_-]?token|password|passwd|pwd|token)\b\s*[:=]\s*['\"][^'\"]{12,}['\"]",
            re.IGNORECASE,
        )
        for name, text in sources().items():
            with self.subTest(module=name):
                self.assertIsNone(pattern.search(text))


class TestNetworkSurface(unittest.TestCase):
    def test_only_providers_may_import_a_network_stack(self):
        # Match real import statements only: the prose in these modules is
        # allowed to *say* "requests" while explaining what it cannot do.
        pattern = re.compile(
            r"^[ \t]*(?:import|from)[ \t]+("
            + "|".join(re.escape(token) for token in NETWORK_IMPORTS)
            + r")\b",
            re.MULTILINE,
        )
        for name, text in non_provider_sources().items():
            match = pattern.search(text)
            with self.subTest(module=name):
                self.assertIsNone(match, f"{name} imports a network stack")

    def test_providers_is_the_single_network_module(self):
        text = sources()[PROVIDERS_FILE]
        self.assertIn("import urllib.request", text)
        self.assertIn("import urllib.error", text)
        self.assertIn("import urllib.parse", text)
        self.assertIn('method="POST"', text)
        self.assertNotIn("socket", text)
        self.assertNotIn("http.client", text)

    def test_the_allowlist_is_checked_before_every_request(self):
        text = sources()[PROVIDERS_FILE]
        self.assertIn("ALLOWED_AI_URL_PREFIXES = ", text)
        self.assertIn("def _assert_allowed_url(", text)
        self.assertIn("_assert_allowed_url(url)", text)
        self.assertEqual(text.count("_assert_allowed_url(self.endpoint)"), 2)
        self.assertIn("https://openrouter.ai/", text)
        self.assertIn("https://api.cloudflare.com/", text)
        self.assertNotIn("http://", text)

    def test_error_messages_carry_the_status_and_never_a_body(self):
        text = sources()[PROVIDERS_FILE]
        self.assertIn("AI provider returned HTTP {exc.code}", text)
        self.assertIn("Deliberately no body", text)
        self.assertNotIn("exc.read()", text)
        self.assertNotIn(".reason", text)

    def test_no_bitget_host_is_reachable_from_the_ai_layer(self):
        for name, text in sources().items():
            with self.subTest(module=name):
                self.assertNotIn("bitget.com", text)
                self.assertNotIn("/api/v2/", text)

    def test_the_ai_layer_never_imports_the_market_data_fetchers(self):
        for name, text in sources().items():
            with self.subTest(module=name):
                self.assertNotIn("fetch_bars", text)
                self.assertNotIn("save_bars", text)
                self.assertNotIn("build_source", text)


class TestArtifactLocations(unittest.TestCase):
    def test_outputs_stay_inside_gitignored_directories(self):
        self.assertEqual(AI_OUTPUT_DIR, REPO_ROOT / "output" / "ai")
        self.assertEqual(AI_DASHBOARD_EXPORT_PATH,
                         REPO_ROOT / "dashboard" / "public" / "ai-summary.json")
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        lines = [line.strip() for line in gitignore.splitlines()]
        self.assertIn("output/", lines)
        self.assertIn("dashboard/public/", lines)
        self.assertIn(".env", lines)
        self.assertIn(".env.*", lines)
        self.assertIn("!.env.example", lines)

    def test_the_ai_summary_name_differs_from_the_paper_one(self):
        from ai_advisor.config import AI_SUMMARY_NAME
        from paper.storage import FILE_NAMES
        self.assertEqual(AI_SUMMARY_NAME, "ai-summary.json")
        self.assertEqual(FILE_NAMES["summary"], "paper-summary.json")

    def test_replay_renames_the_summary_instead_of_overwriting_the_paper_one(self):
        text = (AI_DIR / "replay.py").read_text(encoding="utf-8")
        self.assertIn("os.replace(summary_path, ai_summary_path)", text)
        self.assertIn('AI_DECISIONS_NAME', text)


class TestSafetyContract(unittest.TestCase):
    def test_role_is_veto_only_everywhere(self):
        self.assertEqual(AI_ROLE, "veto_only")
        self.assertEqual(SAFETY_BLOCK["ai_role"], "veto_only")
        self.assertIn("veto-only", AI_DISCLAIMER)
        self.assertIn("PAPER / SIMULATED", AI_DISCLAIMER)
        self.assertIn("No live orders were placed", AI_DISCLAIMER)
        self.assertIn("no private Bitget API", AI_DISCLAIMER)
        self.assertIn("never create a signal", AI_DISCLAIMER)

    def test_safety_block_is_explicitly_false(self):
        self.assertEqual(SAFETY_BLOCK, {
            "live_orders_placed": False,
            "private_api_used": False,
            "execution_mode": "signal_only",
            "ai_role": "veto_only",
        })

    def test_providers_are_a_closed_set_of_two_hosts_plus_offline(self):
        self.assertEqual(PROVIDERS, ("fixture", "openrouter", "cloudflare"))

    def test_manifest_still_declares_signal_only(self):
        manifest = load_manifest()
        self.assertEqual(str(manifest.get("execution_mode")), "signal_only")


class TestCliRefusesToTrade(unittest.TestCase):
    def run_main(self, argv, execution_mode="signal_only"):
        # The real manifest with only execution_mode swapped, so the registry
        # gate still sees a valid strategy_config and the test stays honest
        # about everything except the one field under test.
        manifest = dict(load_manifest())
        manifest["execution_mode"] = execution_mode
        buffer = io.StringIO()
        with mock.patch.object(ai_cli, "load_manifest", return_value=manifest):
            with redirect_stdout(buffer):
                code = ai_cli.main(argv)
        return code, buffer.getvalue()

    def test_refuses_when_execution_mode_is_not_signal_only(self):
        for mode in ("live", "dry_run", "paper", "", "Signal_Only"):
            with self.subTest(mode=mode):
                code, out = self.run_main(["status"], execution_mode=mode)
                self.assertEqual(code, EXIT_CONFIG)
                self.assertIn("signal_only", out)

    def test_status_is_allowed_and_reports_a_missing_run_honestly(self):
        with mock.patch.object(ai_cli, "load_latest_ai", return_value=None):
            code, out = self.run_main(["status"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("no AI run found", out)

    def test_a_hosted_provider_without_a_credential_is_a_configuration_error(self):
        env = {"AI_PROVIDER": "openrouter"}
        with mock.patch.dict("os.environ", env, clear=True):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = ai_cli.main(["review", "--source", "synthetic", "--days", "2"])
        self.assertEqual(code, EXIT_CONFIG)
        out = buffer.getvalue()
        self.assertIn("OPENROUTER_API_KEY", out)
        self.assertIn("Refusing to fall back", out)

    def test_the_offline_fixture_demo_is_labelled_synthetic(self):
        text = (AI_DIR / "cli.py").read_text(encoding="utf-8")
        self.assertIn("SYNTHETIC MODE", text)
        self.assertIn("Nothing below is a real model opinion", text)
        self.assertIn("--no-export", text)


class TestReplayInsertionPoint(unittest.TestCase):
    def test_run_replay_filter_defaults_to_none_and_only_shrinks(self):
        text = (REPO_ROOT / "paper" / "signals.py").read_text(encoding="utf-8")
        self.assertIn("signal_filter=None", text)
        self.assertIn("if signal_filter is not None and actionable:", text)
        self.assertEqual(text.count("if signal_filter is not None and actionable:"), 1)
        self.assertIn("actionable = list(signal_filter(decision_ms, actionable, closed_bars))",
                      text)
        # all_signals is appended before the filter runs, so coverage and signal
        # counts always reflect the deterministic strategy's own output.
        self.assertLess(text.index("all_signals.append(signal)"),
                        text.index("if signal_filter is not None and actionable:"))

    def test_the_advisor_has_no_path_that_creates_a_signal(self):
        text = (AI_DIR / "advisor.py").read_text(encoding="utf-8")
        self.assertNotIn("PaperSignal(", text)
        self.assertNotIn("build_signal", text)
        self.assertIn("if review.outcome == OUTCOME_ACCEPTED:", text)
        self.assertIn("accepted.append(signal)", text)

    def test_watch_samples_are_reviewed_after_the_replay(self):
        text = (AI_DIR / "replay.py").read_text(encoding="utf-8")
        self.assertLess(text.index("outcome = run_replay("),
                        text.index("review_watch_sample(\n        advisor"))

    def test_a_watch_sample_confirm_can_never_be_accepted(self):
        text = (AI_DIR / "advisor.py").read_text(encoding="utf-8")
        block = text[text.index("_WATCH_SAMPLE_OUTCOMES = {"):]
        block = block[:block.index("}") + 1]
        self.assertIn("DECISION_CONFIRM: OUTCOME_NOT_PROMOTABLE", block)
        self.assertNotIn("OUTCOME_ACCEPTED", block)


class TestNpmScripts(unittest.TestCase):
    def setUp(self):
        self.pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.scripts = self.pkg["scripts"]

    def test_ai_scripts_wrap_the_python_cli(self):
        self.assertEqual(self.scripts["ai:review"], "node scripts/run-ai.mjs review")
        self.assertEqual(self.scripts["ai:status"], "node scripts/run-ai.mjs status")

    def test_runner_invokes_the_ai_cli_only(self):
        text = (REPO_ROOT / "scripts" / "run-ai.mjs").read_text(encoding="utf-8")
        self.assertIn("'-m', 'ai_advisor.cli'", text)
        self.assertIn("PYTHONDONTWRITEBYTECODE: '1'", text)
        # The prose may mention what it cannot do; what matters is that it never
        # references the publishing or deployment tooling.
        for token in ("publish-playbook", "playbook:publish", "package-playbook",
                      "paper.cli", "fetch(", "urlopen"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)
        self.assertEqual(text.count("ai_advisor.cli" + chr(39)), 1)

    def test_no_new_script_publishes_deploys_or_trades(self):
        dangerous = re.compile(r"publish|deploy|upload|playbook|order", re.IGNORECASE)
        for name in ("ai:review", "ai:status"):
            with self.subTest(script=name):
                self.assertIsNone(dangerous.search(name))
                self.assertIsNone(dangerous.search(self.scripts[name]))

    def test_the_publishing_surface_is_unchanged(self):
        self.assertEqual(
            sorted(name for name in self.scripts if "publish" in name), ["playbook:publish"]
        )


if __name__ == "__main__":
    unittest.main()
