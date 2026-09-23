"""Command-line entry for the AI-assisted paper-trading workflow.

Commands (all read-only with respect to the exchange):
  review    AI-gated deterministic replay -> simulated fills/positions/PnL
  status    show the latest local AI run

Exit codes: 0 ok, 2 configuration error, 3 market data unavailable.

This CLI can never place an order. It refuses to run at all unless
manifest.yaml still declares ``execution_mode: signal_only``, the ai_advisor
package contains no order code, and the only network it can perform is a POST
to an allowlisted AI inference endpoint plus the GET-only public market-data
requests made by paper/market_data.py.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from paper.cli import EXIT_CONFIG, EXIT_DATA, EXIT_OK, config_from_args
from paper.config import (
    PAPER_LABELS,
    PaperConfig,
    load_manifest,
    strategy_config as manifest_strategy_config,
    strategy_version,
    trading_symbols,
)
from paper.market_data import MarketDataError
from paper.signals import ACTIONABLE, BLOCKED, INFORMATIONAL
from src.instruments import InstrumentError, InstrumentRegistry

from .advisor import OUTCOME_ACCEPTED, OUTCOME_VETOED
from .config import (
    AI_OUTPUT_DIR,
    PROVIDERS,
    AiConfig,
    AiConfigError,
    ai_config_from_env,
)
from .providers import AiProviderError, build_provider
from .replay import load_latest_ai, run_ai_replay

SYNTHETIC_BANNER = (
    "[ai] SYNTHETIC MODE - the reviewer is the offline deterministic fixture and/or the "
    "prices are the seeded synthetic source. Nothing below is a real model opinion or "
    "real market data."
)


def _decimal_arg(raw, name: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, ArithmeticError):
        raise ValueError(f"--{name} must be a number; got {raw!r}")


def ai_config_from_args(args, env=None) -> AiConfig:
    """Environment first, explicit flags second. Blank flags change nothing."""
    base = ai_config_from_env(env)
    overrides = {}
    for flag, field_name, cast in (
        ("ai-provider", "provider", lambda value: str(value).strip().lower()),
        ("ai-model", "model", str),
        ("ai-base-url", "base_url", str),
        ("ai-timeout-s", "timeout_s", int),
        ("ai-max-calls", "max_calls", int),
        ("ai-watch-sample", "watch_sample", int),
        ("ai-temperature", "temperature", float),
    ):
        raw = getattr(args, field_name, None)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        try:
            overrides[field_name] = cast(raw)
        except (TypeError, ValueError):
            raise ValueError(f"--{flag} has an invalid value: {raw!r}")
    return replace(base, **overrides) if overrides else base


def _counts(signals) -> dict:
    return {
        "actionable": sum(1 for item in signals if item.classification == ACTIONABLE),
        "blocked": sum(1 for item in signals if item.classification == BLOCKED),
        "informational": sum(1 for item in signals if item.classification == INFORMATIONAL),
    }


def cmd_review(args, config: PaperConfig, manifest: dict) -> int:
    try:
        ai_config = ai_config_from_args(args)
    except (AiConfigError, ValueError) as exc:
        print(f"[ai] configuration error: {exc}")
        return EXIT_CONFIG
    missing = ai_config.missing_env()
    if missing:
        print(f"[ai] configuration error: provider '{ai_config.provider}' requires "
              f"{', '.join(missing)} to be set. Refusing to fall back to the fixture "
              f"silently - re-run with --ai-provider fixture if you want an offline demo.")
        return EXIT_CONFIG
    try:
        provider = build_provider(ai_config)
    except AiProviderError as exc:
        print(f"[ai] configuration error: {exc}")
        return EXIT_CONFIG
    try:
        InstrumentRegistry(manifest_strategy_config(manifest))
    except InstrumentError as exc:
        print(f"[ai] configuration error: instrument registry rejected manifest: {exc}")
        return EXIT_CONFIG

    if provider.synthetic or config.source == "synthetic":
        print(SYNTHETIC_BANNER)

    try:
        result = run_ai_replay(config, ai_config, manifest, provider=provider,
                               do_export=not getattr(args, "no_export", False))
    except MarketDataError as exc:
        print(f"[ai] ERROR market data unavailable: {exc}")
        return EXIT_DATA

    summary = result.summary
    stats = result.simulator.stats()
    outcomes = result.advisor.outcome_counts()
    counts = _counts(result.outcome.signals)
    window = result.window

    print(f"[ai] AI-gated replay complete (PAPER / SIMULATED): steps={result.steps} "
          f"symbols={len(result.outcome.coverage)} source={result.source_name} "
          f"window={window['start']}..{window['end']}")
    print(f"[ai] reviewer: provider={ai_config.provider} model={ai_config.resolved_model or '-'} "
          f"role=veto_only synthetic={str(summary['ai']['synthetic']).lower()}")
    print(f"[ai] equity={summary['equity']} USDT | realized={summary['realized_pnl']:+} "
          f"| unrealized={summary['unrealized_pnl']:+} | return={summary['return_pct']:+}% "
          f"| closed trades={summary['closed_trades']} | open positions={stats['open_positions']} "
          f"| circuit breaker trips={stats['breaker_trips']}")
    print(f"[ai] deterministic signals: actionable={counts['actionable']} blocked={counts['blocked']} "
          f"informational={counts['informational']}")
    print(f"[ai] ai reviews: accepted={outcomes['accepted']} vetoed={outcomes['vetoed']} "
          f"watched={outcomes['watched']} not_promotable={outcomes['not_promotable']} "
          f"schema_invalid={outcomes['schema_invalid']} provider_error={outcomes['provider_error']} "
          f"budget_exhausted={outcomes['budget_exhausted']} "
          f"skipped_no_market_data={outcomes['skipped_no_market_data']}")
    print(f"[ai] ai calls used={result.advisor.calls_used} / {ai_config.max_calls}")
    for symbol, item in sorted(result.outcome.coverage.items()):
        if item.get("steps_with_data", 0) == 0:
            print(f"[ai] WARNING {symbol}: no decision steps had usable data - nothing simulated for it")
    print(f"[ai] outputs: {', '.join(str(path) for path in result.paths.values())}")
    if result.exported is not None:
        print(f"[ai] dashboard summary: {result.exported}")
    if counts["actionable"] == 0:
        print("[ai] the deterministic strategy produced no actionable setup in this window; "
              "nothing was simulated and nothing was fabricated")
    elif outcomes["accepted"] == 0:
        print("[ai] the reviewer accepted no actionable setup (vetoed, watched, or failed closed); "
              "nothing was simulated")
    dead = all(item.get("steps_with_data", 0) == 0 for item in result.outcome.coverage.values())
    return EXIT_DATA if dead else EXIT_OK


def cmd_status(args, config: PaperConfig, manifest: dict) -> int:
    latest = load_latest_ai(AI_OUTPUT_DIR)
    if latest is None:
        print("[ai] no AI run found under output/ai - run: npm run ai:review")
        return EXIT_OK
    summary = latest["summary"]
    block = summary.get("ai") or {}
    print(f"[ai] last run: mode={summary.get('mode')} source={summary.get('data_source')} "
          f"updated={summary.get('last_updated')} status={summary.get('status')}")
    print(f"[ai] labels: {' / '.join(summary.get('labels') or list(PAPER_LABELS))}")
    print(f"[ai] reviewer: provider={block.get('provider')} model={block.get('model') or '-'} "
          f"role={block.get('role')} synthetic={str(block.get('synthetic')).lower()}")
    print(f"[ai] calls used={block.get('calls_used')} / {block.get('max_calls')} "
          f"watch_sample={block.get('watch_sample')}")
    print(f"[ai] outcomes: {', '.join(f'{k}={v}' for k, v in sorted((block.get('outcomes') or {}).items()))}")
    if summary.get("equity") is not None:
        print(f"[ai] equity={summary['equity']} | realized={summary['realized_pnl']:+} "
              f"| unrealized={summary['unrealized_pnl']:+} | return={summary['return_pct']:+}% "
              f"| closed trades={summary['closed_trades']} "
              f"| open={len(summary.get('open_positions') or [])}")
    reviews = block.get("recent_reviews") or []
    accepted = [item for item in reviews if item.get("outcome") == OUTCOME_ACCEPTED]
    vetoed = [item for item in reviews if item.get("outcome") == OUTCOME_VETOED]
    print(f"[ai] recent reviews: {len(reviews)} shown, {len(accepted)} accepted, {len(vetoed)} vetoed")
    print(f"[ai] {block.get('disclaimer', '')}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai",
        description="AI-assisted paper trading (veto-only reviewer; read-only public data; no orders).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", choices=("rest", "synthetic", "cache"), default=None,
                        help="market data source (default: rest)")
    common.add_argument("--symbols", default=None,
                        help="comma-separated subset of manifest trading symbols")
    common.add_argument("--days", type=int, default=None, help="replay window length in days")

    ai = argparse.ArgumentParser(add_help=False)
    ai.add_argument("--ai-provider", dest="provider", choices=PROVIDERS, default=None,
                    help="model provider (default: fixture, which is fully offline)")
    ai.add_argument("--ai-model", dest="model", default=None)
    ai.add_argument("--ai-base-url", dest="base_url", default=None,
                    help="must stay on the allowlisted AI host")
    ai.add_argument("--ai-timeout-s", dest="timeout_s", type=int, default=None)
    ai.add_argument("--ai-max-calls", dest="max_calls", type=int, default=None,
                    help="hard cap on model calls for this run")
    ai.add_argument("--ai-watch-sample", dest="watch_sample", type=int, default=None,
                    help="audit the last N watch signals per symbol after the replay")
    ai.add_argument("--ai-temperature", dest="temperature", default=None)

    review = sub.add_parser("review", parents=[common, ai],
                            help="AI-gated deterministic replay through the paper simulator")
    review.add_argument("--initial-balance", dest="initial_balance", default=None)
    review.add_argument("--fee-pct", dest="fee_pct", default=None)
    review.add_argument("--slippage-pct", dest="slippage_pct", default=None)
    review.add_argument("--replay-funding", dest="replay_funding",
                        choices=("block", "neutral"), default=None)
    review.add_argument("--seed", type=int, default=None, help="synthetic source seed")
    review.add_argument("--no-export", dest="no_export", action="store_true",
                        help="do not copy the summary into dashboard/public/")

    sub.add_parser("status", help="show the latest local AI run")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest()
        config = config_from_args(args)
    except (ValueError, OSError) as exc:
        print(f"[ai] configuration error: {exc}")
        return EXIT_CONFIG
    if str(manifest.get("execution_mode")) != "signal_only":
        print("[ai] configuration error: manifest execution_mode is not signal_only; "
              "the AI-assisted paper workflow refuses to run")
        return EXIT_CONFIG
    try:
        InstrumentRegistry(manifest_strategy_config(manifest))
    except InstrumentError as exc:
        print(f"[ai] configuration error: instrument registry rejected manifest: {exc}")
        return EXIT_CONFIG
    handlers = {"review": cmd_review, "status": cmd_status}
    try:
        return handlers[args.command](args, config, manifest)
    except MarketDataError as exc:
        print(f"[ai] ERROR market data unavailable: {exc}")
        return EXIT_DATA


if __name__ == "__main__":
    sys.exit(main())
