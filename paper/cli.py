"""Command-line entry for the local paper-trading workflow.

Commands (all read-only with respect to the exchange):
  signals   one live snapshot of classified paper signals (public data only)
  simulate  deterministic replay: signals -> simulated fills/positions/PnL
  export    (re)copy the latest paper summary into dashboard/public/
  status    show the latest local paper run

Exit codes: 0 ok, 2 configuration error, 3 market data unavailable.
This CLI can never place an order - the package contains no order code and
only ever issues GET requests to allowlisted public market-data endpoints.
"""
from __future__ import annotations

import argparse
import sys
import time
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from src.instruments import InstrumentError, InstrumentRegistry

from .config import (
    PAPER_LABELS,
    PaperConfig,
    load_manifest,
    strategy_config as manifest_strategy_config,
    strategy_version,
    trading_symbols,
)
from .harness import MarketDataProvider, PaperHarness
from .market_data import MarketDataError
from .signals import (
    ACTIONABLE,
    BLOCKED,
    INFORMATIONAL,
    LIVE_MODE,
    REPLAY_MODE,
    fetch_replay_dataset,
    generate_now,
    iso_utc,
    make_psar_fn,
    run_replay,
)
from .simulator import PaperSimulator
from .storage import (
    build_signal_run_summary,
    build_summary,
    export_to_dashboard,
    load_latest,
    write_run,
)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_DATA = 3


def _decimal_arg(raw, name: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, ArithmeticError):
        raise ValueError(f"--{name} must be a number; got {raw!r}")


def config_from_args(args) -> PaperConfig:
    kwargs = {}
    if getattr(args, "source", None):
        kwargs["source"] = args.source
    if getattr(args, "days", None) is not None:
        kwargs["days"] = int(args.days)
    if getattr(args, "symbols", None):
        kwargs["symbols"] = tuple(
            part.strip().upper() for part in str(args.symbols).split(",") if part.strip()
        )
    for flag, attr in (
        ("initial-balance", "initial_balance"),
        ("fee-pct", "fee_pct"),
        ("slippage-pct", "slippage_pct"),
    ):
        raw = getattr(args, attr, None)
        if raw is not None:
            kwargs[attr] = _decimal_arg(raw, flag)
    if getattr(args, "replay_funding", None):
        kwargs["replay_funding"] = args.replay_funding
    if getattr(args, "seed", None) is not None:
        kwargs["seed"] = int(args.seed)
    return PaperConfig(**kwargs)


def _notes_for(config: PaperConfig, data_source: str, mode: str) -> List[str]:
    notes = [
        "Simulated fills at the decision-bar close +/- configured slippage; fees charged per side.",
        "Within a bar, stops are checked before take-profits (conservative).",
        "Position sizing mirrors the strategy risk plan with margin_budget set to the paper balance.",
        "Lot sizes / minimum order quantities from verified metadata are not simulated.",
    ]
    if data_source == "synthetic":
        notes.insert(0, "SYNTHETIC data source - simulated prices, NOT real market data.")
    if data_source == "cache":
        notes.insert(0, "Replayed from locally cached public bars (originally fetched via rest).")
    if mode == REPLAY_MODE:
        if config.replay_funding == "block":
            notes.append("Replay treats historical funding as unavailable: the BTC funding gate fails closed (conservative).")
        else:
            notes.append("Replay assumes a neutral 0.0 funding rate for gated symbols (documented simulation assumption).")
    return notes


def _metadata(config: PaperConfig, mode: str, manifest: dict, coverage: dict,
              errors: Optional[List[str]] = None, window: Optional[dict] = None) -> dict:
    return {
        "mode": mode,
        "generated_at_ms": int(time.time() * 1000),
        "strategy_version": strategy_version(manifest),
        "data_source": config.source,
        "config": {
            "initial_balance": str(config.initial_balance),
            "fee_pct": str(config.fee_pct),
            "slippage_pct": str(config.slippage_pct),
            "days": config.days,
            "source": config.source,
            "replay_funding": config.replay_funding,
            "seed": config.seed,
            "symbols": list(config.symbols) or list(trading_symbols(manifest)),
        },
        "window": window,
        "coverage": coverage,
        "errors": errors or [],
        "safety": {
            "live_orders_placed": False,
            "private_api_used": False,
            "api_scope": "public_read_only_market_data",
            "execution_mode": "signal_only",
        },
        "labels": list(PAPER_LABELS),
    }


def _print_signals(signals) -> None:
    for signal in signals:
        reasons = ",".join(signal.reason_codes) or "-"
        line = (
            f"[paper] {signal.timestamp} {signal.symbol:<10} "
            f"{signal.classification:<16} {signal.direction:<6} reasons={reasons}"
        )
        if signal.classification == ACTIONABLE and signal.entry_reference:
            line += (
                f" entry={signal.entry_reference} stop={signal.stop_loss}"
                f" 2R={signal.target_2r} 4R={signal.target_4r}"
            )
        print(line)


def _counts(signals) -> dict:
    return {
        "actionable": sum(1 for item in signals if item.classification == ACTIONABLE),
        "blocked": sum(1 for item in signals if item.classification == BLOCKED),
        "informational": sum(1 for item in signals if item.classification == INFORMATIONAL),
    }


def cmd_signals(args, config: PaperConfig, manifest: dict) -> int:
    try:
        result = generate_now(config, manifest)
    except ValueError as exc:
        print(f"[paper] configuration error: {exc}")
        return EXIT_CONFIG
    except MarketDataError as exc:
        print(f"[paper] ERROR market data unavailable: {exc}")
        return EXIT_DATA
    version = strategy_version(manifest)
    summary = build_signal_run_summary(
        config=config, mode=LIVE_MODE, data_source=result.data_source,
        strategy_version=version, generated_at_ms=result.generated_at_ms,
        signals=result.signals, coverage=result.coverage,
        notes=_notes_for(config, result.data_source, LIVE_MODE),
    )
    metadata = _metadata(config, LIVE_MODE, manifest, result.coverage, errors=result.errors)
    paths = write_run(config.output_dir, signals=result.signals, metadata=metadata, summary=summary)
    counts = _counts(result.signals)
    print(f"[paper] live snapshot @ {summary['last_updated']} source={result.data_source} (PAPER / SIMULATED)")
    _print_signals(result.signals)
    for error in result.errors:
        print(f"[paper] WARNING {error}")
    print(f"[paper] actionable={counts['actionable']} blocked={counts['blocked']} informational={counts['informational']}")
    print(f"[paper] outputs: {', '.join(str(path) for path in paths.values())}")
    runnable = [symbol for symbol, item in result.coverage.items() if item.get("status") == "ok"]
    if result.errors and not runnable:
        print("[paper] no symbol had usable market data - nothing simulated, nothing fabricated")
        return EXIT_DATA
    return EXIT_OK


def cmd_simulate(args, config: PaperConfig, manifest: dict) -> int:
    symbols = list(config.symbols) or list(trading_symbols(manifest))
    try:
        dataset = fetch_replay_dataset(config, symbols)
    except MarketDataError as exc:
        print(f"[paper] ERROR market data unavailable: {exc}")
        return EXIT_DATA

    provider = MarketDataProvider(replay_funding=config.replay_funding)
    for symbol, by_interval in dataset.bars.items():
        for interval, bars in by_interval.items():
            provider.set_bars(symbol, interval, bars)
    harness = PaperHarness(provider)

    base_config = manifest_strategy_config(manifest)
    strategy_params = {**base_config, "margin_budget": str(config.initial_balance)}
    try:
        registry = InstrumentRegistry(strategy_params)
    except InstrumentError as exc:
        print(f"[paper] configuration error: instrument registry rejected manifest: {exc}")
        return EXIT_CONFIG

    simulator = PaperSimulator(
        config,
        strategy_params,
        psar_fn=make_psar_fn(harness, dataset),
        effective_params_fn=lambda symbol: registry.effective_config(symbol, strategy_params),
    )
    outcome = run_replay(config, manifest, dataset, simulator, harness)

    generated_at_ms = dataset.window_end_ms
    version = strategy_version(manifest)
    window = {
        "start_ms": dataset.window_start_ms,
        "end_ms": dataset.window_end_ms,
        "start": iso_utc(dataset.window_start_ms),
        "end": iso_utc(dataset.window_end_ms),
    }
    summary = build_summary(
        simulator=simulator, config=config, mode=REPLAY_MODE,
        data_source=dataset.source_name, strategy_version=version,
        generated_at_ms=generated_at_ms, signals=outcome.signals,
        coverage=outcome.coverage, window=window,
        notes=_notes_for(config, dataset.source_name, REPLAY_MODE),
    )
    metadata = _metadata(config, REPLAY_MODE, manifest, outcome.coverage, window=window)
    paths = write_run(config.output_dir, signals=outcome.signals, metadata=metadata,
                      summary=summary, simulator=simulator)
    exported = None
    if not getattr(args, "no_export", False):
        exported = export_to_dashboard(summary)

    counts = _counts(outcome.signals)
    stats = simulator.stats()
    print(f"[paper] replay complete (PAPER / SIMULATED): steps={outcome.steps} symbols={len(dataset.bars)} "
          f"source={dataset.source_name} window={window['start']}..{window['end']}")
    print(f"[paper] equity={summary['equity']} USDT | realized={summary['realized_pnl']:+} "
          f"| unrealized={summary['unrealized_pnl']:+} | return={summary['return_pct']:+}%")
    win_rate = "n/a" if summary["win_rate_pct"] is None else f"{summary['win_rate_pct']}%"
    print(f"[paper] closed trades={summary['closed_trades']} (win rate {win_rate}) "
          f"| open positions={stats['open_positions']} | circuit breaker trips={stats['breaker_trips']}")
    print(f"[paper] signals: actionable={counts['actionable']} blocked={counts['blocked']} "
          f"informational={counts['informational']}")
    for symbol, item in sorted(outcome.coverage.items()):
        if item.get("steps_with_data", 0) == 0:
            print(f"[paper] WARNING {symbol}: no decision steps had usable data - nothing simulated for it")
    print(f"[paper] outputs: {', '.join(str(path) for path in paths.values())}")
    if exported is not None:
        print(f"[paper] dashboard summary: {exported}")
    dead = all(item.get("steps_with_data", 0) == 0 for item in outcome.coverage.values())
    return EXIT_DATA if dead else EXIT_OK


def cmd_export(args, config: PaperConfig, manifest: dict) -> int:
    try:
        path = export_to_dashboard(output_dir=config.output_dir)
    except FileNotFoundError as exc:
        print(f"[paper] {exc}")
        return EXIT_CONFIG
    print(f"[paper] exported dashboard summary -> {path}")
    return EXIT_OK


def cmd_status(args, config: PaperConfig, manifest: dict) -> int:
    latest = load_latest(config.output_dir)
    if latest is None:
        print("[paper] no paper run found under output/paper - run: npm run paper:simulate")
        return EXIT_OK
    summary = latest["summary"]
    print(f"[paper] last run: mode={summary.get('mode')} source={summary.get('data_source')} "
          f"updated={summary.get('last_updated')} status={summary.get('status')}")
    print(f"[paper] labels: {' / '.join(summary.get('labels') or [])}")
    if summary.get("equity") is not None:
        print(f"[paper] equity={summary['equity']} | realized={summary['realized_pnl']:+} "
              f"| unrealized={summary['unrealized_pnl']:+} | return={summary['return_pct']:+}% "
              f"| closed trades={summary['closed_trades']} | open={len(summary.get('open_positions') or [])}")
    breaker = summary.get("circuit_breaker") or {}
    print(f"[paper] circuit breaker: trips={breaker.get('trips')} locked={breaker.get('locked')}")
    actionable = [item for item in (summary.get("recent_signals") or [])
                  if item.get("classification") == ACTIONABLE]
    print(f"[paper] recent signals: {len(summary.get('recent_signals') or [])} shown, "
          f"{len(actionable)} actionable")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper",
        description="Local paper-trading workflow (read-only public data; no orders).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", choices=("rest", "synthetic", "cache"), default=None,
                        help="market data source (default: rest)")
    common.add_argument("--symbols", default=None,
                        help="comma-separated subset of manifest trading symbols")
    common.add_argument("--days", type=int, default=None, help="replay window length in days")

    sub.add_parser("signals", parents=[common],
                   help="one live snapshot of classified paper signals")

    sim = sub.add_parser("simulate", parents=[common],
                         help="deterministic replay through the paper simulator")
    sim.add_argument("--initial-balance", dest="initial_balance", default=None)
    sim.add_argument("--fee-pct", dest="fee_pct", default=None)
    sim.add_argument("--slippage-pct", dest="slippage_pct", default=None)
    sim.add_argument("--replay-funding", dest="replay_funding",
                     choices=("block", "neutral"), default=None)
    sim.add_argument("--seed", type=int, default=None, help="synthetic source seed")
    sim.add_argument("--no-export", dest="no_export", action="store_true",
                     help="do not copy the summary into dashboard/public/")

    sub.add_parser("export", help="re-copy the latest paper summary into dashboard/public/")
    sub.add_parser("status", help="show the latest local paper run")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest()
        config = config_from_args(args)
    except (ValueError, OSError) as exc:
        print(f"[paper] configuration error: {exc}")
        return EXIT_CONFIG
    try:
        InstrumentRegistry(manifest_strategy_config(manifest))
    except InstrumentError as exc:
        print(f"[paper] configuration error: instrument registry rejected manifest: {exc}")
        return EXIT_CONFIG
    handlers = {
        "signals": cmd_signals,
        "simulate": cmd_simulate,
        "export": cmd_export,
        "status": cmd_status,
    }
    try:
        return handlers[args.command](args, config, manifest)
    except MarketDataError as exc:
        print(f"[paper] ERROR market data unavailable: {exc}")
        return EXIT_DATA


if __name__ == "__main__":
    sys.exit(main())