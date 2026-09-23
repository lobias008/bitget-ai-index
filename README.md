# Bitget AI Index

Multi-asset AI Index Trading Platform built on Bitget public market data, with seven independent sections: **Crypto, rToken, Stock Preps, CFD, Commodity, Metal, AI Indexes**.

**This repository is an independent project.** It is fully separate from The Morning Sword hackathon repository (`lobias008/My-agent`) and from `lobias008/the-morning-sword`; nothing is shared, merged, renamed, or pushed between them. The Morning Sword signal-only Playbook strategy is hosted here **unchanged** as the platform's foundation strategy, together with its deterministic risk controls and test suite.

**SDK:** `@bitget-ai/getagent-skill`
**Execution posture:** `signal_only` everywhere. Live trading is disabled. Publishing is gated and never automatic. AI analysis is separated from deterministic risk controls.

## Platform sections

| Section | Instruments | Status |
| --- | --- | --- |
| Crypto | BTCUSDT | Active in strategy; execution `signal_only` |
| Metal | XAUUSDT | Active in strategy; execution `signal_only` |
| Commodity | AXTIUSDT | Active in strategy; execution `signal_only` |
| Stock Preps | SP500USDT | Active in strategy; execution `signal_only` |
| rToken | none | Closed; no Bitget symbol invented; execution `unavailable` |
| CFD | none | Closed; Bitget documents no CFD product type; execution `unavailable` |
| AI Indexes | none | Closed; pending capability verification; execution `unavailable` |

Declaring a section never enables trading in it. New instruments require verified Bitget API capability plus explicit approval (see `AGENTS.md` and `ARCHITECTURE.md`).

## Verified instruments (live public-API run, 2026-09-21)

`npm run verify:instruments` confirmed 4/4 symbols against Bitget public endpoints (read-only, no auth):

| Symbol | Product type | Status | Price prec | Qty prec | Min order | Market data |
| --- | --- | --- | --- | --- | --- | --- |
| BTCUSDT | USDT-FUTURES | normal | 1 | 4 | 0.0001 | live ticker |
| XAUUSDT | USDT-FUTURES | normal | 2 | 2 | 0.01 | live ticker |
| AXTIUSDT | USDT-FUTURES | normal | 2 | 2 | 0.01 | live ticker |
| SP500USDT | USDT-FUTURES | normal | 1 | 4 | 0.0001 | live ticker |

Confirmed metadata is recorded per symbol under `strategy_config.symbol_roles.<SYM>.verified_metadata` in `manifest.yaml`. Execution-API availability is never claimed from public data and stays disabled.

## Foundation strategy: The Morning Sword (signal-only)

The Morning Sword is an offensive, multi-asset asymmetric yield generator. It waits for the 1D trend to align, attacks only 4H pullbacks with participation and RSI confirmation, and then turns winners into protected runners through a 2R partial profit, immediate breakeven stop, and Parabolic SAR trailing logic. Every position is sized from a fixed 1.0% equity risk budget against a 1.5x 4H ATR stop, with a hard -2.0% daily equity circuit breaker.

## 策略 / Strategy

The strategy scans confirmed Bitget contract symbols and emits signal-only Playbook decisions. It uses 1D EMA 20/50/200 alignment for higher-timeframe bias and only considers 4H pullbacks when participation and RSI confirm that the pullback is orderly rather than broken.

## 开仓 / Entry

Long entries require 1D EMA 20 > EMA 50 > EMA 200, a 4H pullback into the EMA 20/50 zone, three consecutive volume-spike candles, and RSI between 40 and 60. BTC additionally requires absolute funding below 0.05%; Gold is gated to London and New York sessions; the oil proxy requires Parabolic SAR confirmation.

## 平仓 / Exit

The first exit harvests 50% of the position at 1:2 R:R. Immediately after that partial fill, the stop loss moves to entry and the remaining position becomes a Free Trade. The runner is trailed with Parabolic SAR toward 1:4+ R:R.

## 风险 / Risk

Each trade is sized from a strict 1.0% equity risk budget using a 1.5x ATR stop. If reported daily equity loss reaches -2.0%, the circuit breaker emits a portfolio hold signal, cancels new entries, and marks execution as locked for 24 hours.

## GetClaw Multi-Asset Rules Table

| Asset | Symbol | Trend Bias | Entry Trigger | Asset Adaptation |
| --- | --- | --- | --- | --- |
| Bitcoin | `BITGET:BTCUSDT` | 1D EMA 20 > EMA 50 > EMA 200 | 4H EMA 20/50 pullback + volume spike + RSI 40-60 | 1.5x 4H ATR stop; funding filter requires `abs(rate) < 0.05%` |
| Gold | `BITGET:XAUUSDT` | 1D EMA 20 > EMA 50 > EMA 200 | 4H EMA 20/50 pullback + volume spike + RSI 40-60 | Trade London and New York opens only; skip Asian session |
| WTI Oil proxy | `BITGET:AXTIUSDT` | 1D EMA 20 > EMA 50 > EMA 200 | 4H EMA 20/50 pullback + volume spike + RSI 40-60 | Require Parabolic SAR trend confirmation; requested `OILUSDT` was not found in Bitget public contracts |
| S&P 500 | `BITGET:SP500USDT` | 1D EMA 20 > EMA 50 > EMA 200 | 4H EMA 20/50 pullback + volume spike + RSI 40-60 | Require 1D EMA 50 bounce; requested `SPX500USDT` was not found in Bitget public contracts |

## Risk Management Matrix

| Risk Control | Rule | Action |
| --- | --- | --- |
| Position sizing | Fixed 1.0% account equity risk per trade | Size from stop distance: equity x 1.0% / 1.5x ATR risk |
| Initial stop | 1.5x 4H ATR | Place stop at volatility-adjusted invalidation |
| Partial profit | First target at 1:2 R:R | Harvest 50% of the position |
| Breakeven lock | Immediately after 2R partial fill | Move stop loss to entry and mark the trade as a Free Trade |
| Runner management | Remaining 50% | Trail with Parabolic SAR toward 1:4+ R:R |
| Daily circuit breaker | -2.0% daily equity loss | Cancel orders, flatten positions, lock execution for 24 hours |

## Repository Layout

```
manifest.yaml     Playbook manifest (package root - required path)
README.md         This document (package root - required path)
src/main.py       Foundation strategy. Deterministic Playbook logic.
src/instruments.py Configuration-driven instrument registry (7 asset classes)
paper/            Paper-trading simulator - local, simulated, GET-only public data
index.js          Inert, env-driven Playbook definition. No side effects on import.
dashboard/        React + Vite UI - its own package with its own dependencies
scripts/          Safe local tooling (status, check, validate, package, verify)
tests/            Test suite (JS via node:test, Python via unittest)
output/           Generated run artifacts (gitignored, never committed)
requirements.txt  Python deps for local validation of src/main.py
.env.example      Environment variable NAMES only - never real values
AGENTS.md         Permanent project safety rules
ARCHITECTURE.md   Platform + instrument registry architecture
```

The React dashboard and the Python strategy are deliberately separated. `src/`
is the uploadable Python package and must contain only Python. UI code lives
under `dashboard/`. The dashboard renders a generated export of the validated
instrument registry (`dashboard/src/data/instruments.json`, produced by
`npm run dashboard:registry`, drift-checked by `dashboard:registry:check`
and the test suite). It never fabricates prices or market activity: live
read-only market data is a later milestone. Local-only directories
(`node_modules/`, `dist/`,
`output/`, caches) are gitignored and never committed.

## Safe Local Commands

Nothing below touches the network, publishes, deploys, or trades.

```powershell
npm start                  # read-only status summary
npm test                   # JavaScript test suite (node:test)
npm run test:python        # Python strategy + registry tests
npm run check              # secrets + JS syntax + Python syntax + package validation
npm run check:secrets      # scan for hardcoded credentials
npm run verify:instruments # read-only Bitget public-API symbol verification
npm run dashboard:registry # regenerate dashboard/src/data/instruments.json
npm run dashboard:registry:check # fail if the dashboard export drifted
npm run validate           # official GetAgent validator on a staged package
npm run package            # build the upload tarball locally (no upload)
```

Paper trading (simulated only - never places an order):

```powershell
npm run paper:signals      # generate + classify paper signals from the strategy
npm run paper:simulate     # replay them through the simulator and export
npm run paper:export       # re-export the latest run to dashboard/public/
npm run paper:status       # print the latest run summary and artifact paths
```

AI-assisted paper trading (veto-only reviewer - still never places an order):

```powershell
npm run ai:review          # AI-gated replay; the reviewer may VETO signals
npm run ai:status          # print the latest AI run summary and artifact paths
```

Dashboard:

```powershell
npm run dashboard:install  # first time only - installs React/Vite/Tailwind
npm run dashboard:dev      # local dev server
npm run dashboard:build    # production build into dashboard/dist
```

Python is resolved automatically by the tooling; override with `$env:PYTHON_BIN`
if needed. Local validation also needs PyYAML (`pip install -r requirements.txt`).

## Paper trading (Milestone 2)

A local, simulated workflow that feeds the existing deterministic strategy's
signals into a paper simulator and reports hypothetical P&L. **It never trades,
never uses a private API, and never needs credentials.**

- Signals come from the unchanged strategy through an injected data provider
  and clock, and are classified `actionable_paper`, `blocked`, or
  `informational`. A blocked signal records why (funding gate, session gate,
  Parabolic SAR, circuit-breaker lock, unknown or disabled symbol, data
  failure) and is never filled.
- Market data is GET-only against allowlisted public Bitget endpoints. A
  `synthetic` source exists for offline development and is labelled as such in
  every artifact. Missing, stale, or malformed data is reported - never
  fabricated. Only closed bars are visible at each decision time, so there is
  no look-ahead bias.
- The simulator is deterministic and uses exact `Decimal` accounting. It
  mirrors the strategy's own sizing and exit lifecycle (stop loss, 50% partial
  at 2R, stop to breakeven, runner to 4R or the Parabolic SAR trail), applies
  configurable fees and slippage, rejects duplicate signals, and preserves the
  daily circuit breaker (-2%, flatten, 24h lock).
- Artifacts are written to gitignored `output/paper/` and exported to
  `dashboard/public/paper-summary.json`, which the dashboard renders in the
  Paper Trading panel. Every figure is badged **PAPER / SIMULATED** with the
  snapshot's last-updated timestamp. If no run exists yet, the panel says so
  and shows the command to generate one - it never invents numbers.

Typical loop:

```powershell
npm run paper:simulate     # generate signals, simulate, export to the dashboard
npm run dashboard:dev      # open the dashboard and read the Paper Trading panel
```

Insufficient history is reported rather than padded, so a short data window
yields an honest "insufficient history" result instead of manufactured
performance.

## AI-assisted paper trading (Milestone 3)

A provider-agnostic, **veto-only** AI reviewer sits in front of the paper
simulator. It can confirm, reject or watch a signal the deterministic strategy
already produced - it can never create one, resize it, move a stop, widen a risk
limit, or place an order. `execution_mode` stays `signal_only`, and the CLI
refuses to run otherwise.

- **One insertion point.** `paper/signals.run_replay(..., signal_filter=None)` may
  be given a filter returning the subset of actionable signals allowed to reach
  the simulator. Coverage and signal counts are recorded before the filter runs,
  so a veto never hides what the strategy produced. With no filter, the replay is
  identical to Milestone 2.
- **Fail closed.** Invalid model output, a provider error, an exhausted
  `AI_MAX_CALLS` budget and missing market data all drop the signal. An accepted
  review still has to pass every deterministic control in `paper/simulator.py`
  (duplicate guard, circuit breaker, position-open guard, sizing and margin).
- **Strict contract.** The reply must be a single JSON object with `decision`
  (`confirm` | `reject` | `watch`), `confidence` (0..1), `reasoning` and an
  UPPER_SNAKE_CASE `reason_code`; anything else is discarded as `schema_invalid`.
- **Allowlisted network.** `ai_advisor/providers.py` is the only module that
  performs AI inference I/O and may POST only to `https://openrouter.ai/` or
  `https://api.cloudflare.com/`. Credentials come from the environment, are never
  logged or written to an artifact, and a missing credential is a configuration
  error - never a silent fallback to the fixture.
- **Honest labelling.** The default offline `fixture` provider and the
  `synthetic` market-data source are both labelled SYNTHETIC in the CLI, in
  `output/ai/ai-summary.json` and on the dashboard. No market data, fill or
  performance figure is ever invented: a run with no real setup reports zero
  fills rather than manufacturing activity.
- **Point-in-time.** The reviewer sees only bars closed at or before the simulated
  decision instant, and watch samples are audited after the replay so they can
  never be promoted into a trade.

Artifacts land in gitignored `output/ai/` (`ai-summary.json`, `decisions.jsonl`,
plus the usual signals / fills / equity / events / positions / run-metadata
files) and are exported to `dashboard/public/ai-summary.json`, rendered by
`AiPanel.jsx` with every figure badged PAPER / SIMULATED.

Configuration is environment-only and documented in `.env.example` (names, no
values): `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, `AI_TIMEOUT_S`, `AI_MAX_CALLS`,
`AI_WATCH_SAMPLE`, `AI_TEMPERATURE`, plus `OPENROUTER_API_KEY` or
`CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_AI_TOKEN` for a hosted provider.

## Publishing

Publishing is **gated and never automatic**. `npm start` and `npm run check`
cannot publish. The only path is `npm run playbook:publish -- --dry-run`
(default, no network) or `--confirm`, which still refuses unless
`BITGET_ACCESS_KEY` is set in the environment, `ALLOW_PLAYBOOK_PUBLISH=true`,
the confirmation phrase matches and is retyped in an interactive TTY, and the
package passes the official validator. Per `AGENTS.md`, no Playbook is ever
published without explicit owner approval.

## Security Notes

- No credential is stored in this repository. API access keys are supplied via
  `BITGET_ACCESS_KEY` in the environment (see `.env.example`, names only).
- `scripts/check-secrets.mjs` gates commits and CI. It reports only a masked
  fingerprint, never the value.
- `.env*` files (except `.env.example`), backups, caches, bundled binaries and
  build outputs are gitignored and excluded from every commit and package.

Trading is risky. This platform is a signal-only research implementation and should be reviewed with isolated credentials, exchange permissions, and conservative execution limits before any live use.
