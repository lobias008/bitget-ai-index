"""AI-gated replay: the deterministic paper pipeline with a veto-only reviewer.

This module reuses the Milestone-2 machinery unchanged - same market data,
same strategy harness, same simulator, same risk controls - and inserts the AI
advisor at exactly one point: the list of ``actionable_paper`` signals handed to
``PaperSimulator.process_step`` at each 4h decision time.

Consequences of that single insertion point:

  * The AI can only shrink the list. It cannot add a symbol, change a level,
    resize a position or unlock a gate.
  * ``all_signals`` (and therefore coverage, signal counts and the dashboard's
    data-coverage panel) is recorded BEFORE the filter, so the deterministic
    strategy's own output stays fully auditable and a veto never hides a setup.
  * Signals the strategy blocked or merely watched are never sent to the model
    as trade candidates; a small sample of watch signals is reviewed AFTER the
    replay purely as an audit trail and is never simulated.
  * If no honest setup occurs, the run simply reports zero fills. Performance is
    never manufactured to make the pipeline look busy.
"""
from __future__ import annotations

import json
import os
import time
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from paper.config import (
    PAPER_LABELS,
    PaperConfig,
    strategy_config as manifest_strategy_config,
    strategy_version,
    trading_symbols,
)
from paper.harness import MarketDataProvider, PaperHarness
from paper.market_data import INTERVAL_MS
from paper.signals import (
    INFORMATIONAL,
    REPLAY_MODE,
    ReplayDataset,
    fetch_replay_dataset,
    iso_utc,
    make_psar_fn,
    run_replay,
)
from paper.simulator import PaperSimulator
from paper.storage import FILE_NAMES, write_json, write_jsonl, write_run
from src.instruments import InstrumentRegistry

from .advisor import (
    AiAdvisor,
    OUTCOME_ACCEPTED,
    PURPOSE_ENTRY_GATE,
    PURPOSE_WATCH_SAMPLE,
    REVIEW_INTERVALS,
    make_ai_filter,
)
from .config import (
    AI_DECISIONS_NAME,
    AI_DISCLAIMER,
    AI_DASHBOARD_EXPORT_PATH,
    AI_OUTPUT_DIR,
    AI_ROLE,
    AI_SUMMARY_NAME,
    AiConfig,
)
from .providers import BaseProvider, build_provider
from paper.storage import build_summary

MAX_RECENT_REVIEWS = 12
MAX_RECENT_FILLS = 8
AI_SCHEMA_VERSION = 1

SAFETY_BLOCK = {
    "live_orders_placed": False,
    "private_api_used": False,
    "execution_mode": "signal_only",
    "ai_role": AI_ROLE,
}


@dataclass
class ReplayWorld:
    """The Milestone-2 objects an AI-gated replay needs, built once."""

    provider: MarketDataProvider
    harness: PaperHarness
    registry: InstrumentRegistry
    simulator: PaperSimulator
    strategy_params: Dict[str, Any]


def build_world(paper_config: PaperConfig, manifest: dict, dataset: ReplayDataset) -> ReplayWorld:
    """Mirror paper.cli.cmd_simulate wiring so both paths stay identical."""
    provider = MarketDataProvider(replay_funding=paper_config.replay_funding)
    for symbol, by_interval in dataset.bars.items():
        for interval, bars in by_interval.items():
            provider.set_bars(symbol, interval, bars)
    harness = PaperHarness(provider)
    base_config = manifest_strategy_config(manifest)
    strategy_params = {**base_config, "margin_budget": str(paper_config.initial_balance)}
    registry = InstrumentRegistry(strategy_params)
    simulator = PaperSimulator(
        paper_config,
        strategy_params,
        psar_fn=make_psar_fn(harness, dataset),
        effective_params_fn=lambda symbol: registry.effective_config(symbol, strategy_params),
    )
    return ReplayWorld(provider, harness, registry, simulator, strategy_params)


def make_slicer(dataset: ReplayDataset) -> Callable[[str, str, int], List[Any]]:
    """Point-in-time bar access: only bars CLOSED at or before ``until_ms``.

    This is what keeps the AI honest. A reviewer that could see the bar that
    closes after the decision instant would be grading the strategy with
    information the strategy never had, and any "edge" it found would be
    look-ahead bias rather than skill.
    """
    closes: Dict[tuple, List[int]] = {}
    for symbol, by_interval in dataset.bars.items():
        for interval, bars in by_interval.items():
            closes[(symbol, interval)] = [
                bar.time_ms + INTERVAL_MS[interval] for bar in bars
            ]

    def slicer(symbol: str, interval: str, until_ms: int) -> List[Any]:
        bars = (dataset.bars.get(symbol) or {}).get(interval) or []
        key = (symbol, interval)
        if not bars or key not in closes:
            return []
        end = bisect_right(closes[key], int(until_ms))
        return bars[:end]

    return slicer


def review_watch_sample(advisor: AiAdvisor, signals: Sequence[Any], slicer: Callable, *,
                        watch_sample: int, data_source: str = "", mode: str = REPLAY_MODE) -> List[Any]:
    """Audit a sample of watch signals AFTER the replay. Never simulated.

    ``watch_sample`` is 0 by default in a pure entry-gate run; when it is > 0 the
    last N informational signals per symbol are reviewed so a human can compare
    what the model would have said about setups the strategy declined. Because
    this runs after ``run_replay`` returns, no review produced here can affect a
    fill, and a watch sample can never be promoted (see advisor outcomes).
    """
    if int(watch_sample) <= 0:
        return []
    by_symbol: Dict[str, List[Any]] = {}
    for signal in signals:
        if getattr(signal, "classification", None) != INFORMATIONAL:
            continue
        symbol = str(getattr(signal, "symbol", "") or "")
        if symbol:
            by_symbol.setdefault(symbol, []).append(signal)
    reviews = []
    for symbol in sorted(by_symbol):
        for signal in by_symbol[symbol][-int(watch_sample):]:
            bars = {
                interval: slicer(symbol, interval, signal.timestamp_ms)
                for interval in REVIEW_INTERVALS
            }
            reviews.append(advisor.review(
                purpose=PURPOSE_WATCH_SAMPLE, signal=signal, bars=bars,
                data_source=data_source, mode=mode, time_ms=signal.timestamp_ms,
            ))
    return reviews


def reconcile_accepted_with_fills(advisor: AiAdvisor, simulator: PaperSimulator) -> int:
    """Annotate each accepted entry-gate review with what the simulator did.

    Closes the audit loop the dashboard shows: an accepted review is not a trade
    until the deterministic simulator says so. Matching is exact on symbol and
    decision timestamp, and only entry-side fills count, so a later exit or a
    different step can never be attributed to a review. Fill dicts are COPIED,
    so mutating a review can never mutate the simulator's ledger.
    """
    fills = [dict(fill) for fill in (getattr(simulator, "fills", None) or [])]
    events = [dict(event) for event in (getattr(simulator, "events", None) or [])]
    reconciled = 0
    for review in advisor.reviews:
        if review.purpose != PURPOSE_ENTRY_GATE or review.outcome != OUTCOME_ACCEPTED:
            continue
        matched = [
            dict(fill) for fill in fills
            if fill.get("side") == "entry"
            and str(fill.get("symbol")) == review.symbol
            and int(fill.get("time_ms") or 0) == int(review.time_ms)
        ]
        if matched:
            review.simulator_outcome = "filled"
            review.entry_price = matched[0].get("fill_price")
            review.quantity = matched[0].get("quantity")
            review.fills = matched
        else:
            event = next(
                (item for item in events
                 if str(item.get("symbol")) == review.symbol
                 and int(item.get("time_ms") or 0) == int(review.time_ms)),
                None,
            )
            review.simulator_outcome = str(event.get("type")) if event else "no_simulator_record"
            note = f"simulator: {review.simulator_outcome}"
            review.detail = f"{review.detail} | {note}" if review.detail else note
        reconciled += 1
    return reconciled


def build_ai_block(advisor: AiAdvisor, ai_config: AiConfig, provider: Any,
                   simulator: Optional[PaperSimulator] = None,
                   data_source: str = "") -> Dict[str, Any]:
    """The dashboard-facing AI section. Explicitly labeled, never implied live."""
    synthetic_model = bool(getattr(provider, "synthetic", ai_config.is_synthetic))
    synthetic_market_data = str(data_source) == "synthetic"
    fills = list(getattr(simulator, "fills", None) or []) if simulator is not None else []
    return {
        "schema_version": AI_SCHEMA_VERSION,
        "role": AI_ROLE,
        "provider": ai_config.provider,
        "model": ai_config.resolved_model,
        "temperature": ai_config.temperature,
        "synthetic_model": synthetic_model,
        "synthetic_market_data": synthetic_market_data,
        "synthetic": bool(synthetic_model or synthetic_market_data),
        "calls_used": advisor.calls_used,
        "max_calls": ai_config.max_calls,
        "watch_sample": ai_config.watch_sample,
        "outcomes": advisor.outcome_counts(),
        "recent_reviews": [review.to_json() for review in advisor.reviews[-MAX_RECENT_REVIEWS:]],
        "recent_fills": [dict(fill) for fill in fills[-MAX_RECENT_FILLS:]],
        "disclaimer": AI_DISCLAIMER,
    }


def _ai_notes(paper_config: PaperConfig, data_source: str, ai_block: Dict[str, Any]) -> List[str]:
    notes = [
        "Simulated fills at the decision-bar close +/- configured slippage; fees charged per side.",
        "Within a bar, stops are checked before take-profits (conservative).",
        "Position sizing mirrors the strategy risk plan with margin_budget set to the paper balance.",
        "Lot sizes / minimum order quantities from verified metadata are not simulated.",
        "AI review is veto-only: it can drop a deterministic signal but can never create or resize one.",
    ]
    if ai_block.get("synthetic_model"):
        notes.insert(0, "SYNTHETIC AI provider - deterministic offline fixture, NOT a real model opinion.")
    if data_source == "synthetic":
        notes.insert(0, "SYNTHETIC data source - simulated prices, NOT real market data.")
    if data_source == "cache":
        notes.insert(0, "Replayed from locally cached public bars (originally fetched via rest).")
    if paper_config.replay_funding == "block":
        notes.append("Replay treats historical funding as unavailable: the BTC funding gate fails closed (conservative).")
    else:
        notes.append("Replay assumes a neutral 0.0 funding rate for gated symbols (documented simulation assumption).")
    notes.append(AI_DISCLAIMER)
    return notes


def _ai_metadata(paper_config: PaperConfig, manifest: dict, ai_config: AiConfig,
                 advisor: AiAdvisor, coverage: Dict[str, Any], window: Optional[Dict[str, Any]],
                 data_source: str, generated_at_ms: int) -> Dict[str, Any]:
    return {
        "mode": REPLAY_MODE,
        "generated_at_ms": int(generated_at_ms),
        "strategy_version": strategy_version(manifest),
        "data_source": data_source,
        "config": {
            "initial_balance": str(paper_config.initial_balance),
            "fee_pct": str(paper_config.fee_pct),
            "slippage_pct": str(paper_config.slippage_pct),
            "days": paper_config.days,
            "source": paper_config.source,
            "replay_funding": paper_config.replay_funding,
            "seed": paper_config.seed,
            "symbols": list(paper_config.symbols) or list(trading_symbols(manifest)),
            "ai": ai_config.to_json(),
        },
        "window": window,
        "coverage": coverage,
        "errors": [],
        "safety": {
            "live_orders_placed": False,
            "private_api_used": False,
            "api_scope": "public_read_only_market_data",
            "execution_mode": "signal_only",
        },
        "labels": list(PAPER_LABELS),
        "ai": {
            "role": AI_ROLE,
            "config": ai_config.to_json(),
            "stats": advisor.stats(),
            "safety": dict(SAFETY_BLOCK),
        },
    }


@dataclass
class AiReplayResult:
    outcome: Any
    simulator: PaperSimulator
    advisor: AiAdvisor
    provider: BaseProvider
    summary: Dict[str, Any]
    metadata: Dict[str, Any]
    paths: Dict[str, Path]
    exported: Optional[Path]
    window: Dict[str, Any]
    source_name: str
    steps: int


def run_ai_replay(paper_config: PaperConfig, ai_config: AiConfig, manifest: dict, *,
                  provider: Optional[BaseProvider] = None, dataset: Optional[ReplayDataset] = None,
                  output_dir: Optional[Path] = None, export_path: Optional[Path] = None,
                  do_export: bool = True) -> AiReplayResult:
    """Run the AI-gated replay and write every artifact under output/ai/."""
    symbols = list(paper_config.symbols) or list(trading_symbols(manifest))
    if dataset is None:
        dataset = fetch_replay_dataset(paper_config, symbols)
    world = build_world(paper_config, manifest, dataset)
    if provider is None:
        provider = build_provider(ai_config)
    advisor = AiAdvisor(ai_config, provider)
    slicer = make_slicer(dataset)
    ai_filter = make_ai_filter(
        advisor, slicer, data_source=dataset.source_name, mode=REPLAY_MODE, registry=world.registry,
    )

    outcome = run_replay(paper_config, manifest, dataset, world.simulator, world.harness,
                         signal_filter=ai_filter)
    review_watch_sample(
        advisor, outcome.signals, slicer, watch_sample=ai_config.watch_sample,
        data_source=dataset.source_name, mode=REPLAY_MODE,
    )
    reconcile_accepted_with_fills(advisor, world.simulator)

    window = {
        "start_ms": dataset.window_start_ms,
        "end_ms": dataset.window_end_ms,
        "start": iso_utc(dataset.window_start_ms),
        "end": iso_utc(dataset.window_end_ms),
    }
    ai_block = build_ai_block(advisor, ai_config, provider, world.simulator, dataset.source_name)
    summary = build_summary(
        simulator=world.simulator, config=paper_config, mode=REPLAY_MODE,
        data_source=dataset.source_name, strategy_version=strategy_version(manifest),
        generated_at_ms=dataset.window_end_ms, signals=outcome.signals,
        coverage=outcome.coverage, window=window,
        notes=_ai_notes(paper_config, dataset.source_name, ai_block),
    )
    summary["ai"] = ai_block
    summary["synthetic"] = bool(ai_block["synthetic"])
    metadata = _ai_metadata(
        paper_config, manifest, ai_config, advisor, outcome.coverage, window,
        dataset.source_name, int(time.time() * 1000),
    )

    out_dir = Path(output_dir) if output_dir is not None else AI_OUTPUT_DIR
    paths = write_run(out_dir, signals=outcome.signals, metadata=metadata,
                      summary=summary, simulator=world.simulator)
    # write_run uses the paper summary name; the AI run owns a distinct file so
    # a paper run and an AI run never overwrite each other's headline artifact.
    summary_path = Path(paths.pop("summary"))
    ai_summary_path = summary_path.with_name(AI_SUMMARY_NAME)
    os.replace(summary_path, ai_summary_path)
    paths["summary"] = ai_summary_path
    paths["decisions"] = write_jsonl(
        Path(out_dir) / AI_DECISIONS_NAME, [review.to_json() for review in advisor.reviews]
    )

    exported = None
    if do_export:
        exported = export_ai_to_dashboard(
            summary, path=Path(export_path) if export_path is not None else AI_DASHBOARD_EXPORT_PATH
        )

    return AiReplayResult(
        outcome=outcome, simulator=world.simulator, advisor=advisor, provider=provider,
        summary=summary, metadata=metadata, paths=paths, exported=exported, window=window,
        source_name=dataset.source_name, steps=outcome.steps,
    )


def export_ai_to_dashboard(summary: Optional[dict] = None, output_dir: Path = AI_OUTPUT_DIR,
                           path: Path = AI_DASHBOARD_EXPORT_PATH) -> Path:
    """Copy the AI run summary into dashboard/public/ for the dev server/build."""
    if summary is None:
        source = Path(output_dir) / AI_SUMMARY_NAME
        if not source.exists():
            raise FileNotFoundError(
                f"no AI summary at {source}; run 'npm run ai:review' first"
            )
        summary = json.loads(source.read_text(encoding="utf-8"))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return write_json(path, summary)


def load_latest_ai(output_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    out = Path(output_dir) if output_dir is not None else AI_OUTPUT_DIR
    summary_path = out / AI_SUMMARY_NAME
    if not summary_path.exists():
        return None
    payload: Dict[str, Any] = {"summary": json.loads(summary_path.read_text(encoding="utf-8"))}
    metadata_path = out / FILE_NAMES["metadata"]
    if metadata_path.exists():
        payload["metadata"] = json.loads(metadata_path.read_text(encoding="utf-8"))
    return payload
