# ARCHITECTURE.md

How this repository is organized, and how the configuration-driven instrument
registry works. Read `AGENTS.md` first for the non-negotiable safety rules;
this file explains the design those rules protect.

---

## 1. System Overview

The project is one repository containing two deliberately separated codebases
plus local-only tooling:

```
+---------------------------------------------------------------+
| manifest.yaml        Playbook package manifest (upload root)  |
| README.md            Strategy doc with required CJK markers   |
| src/main.py          THE STRATEGY - deterministic logic       |
| src/instruments.py   Config-driven instrument registry        |
+---------------------------------------------------------------+
        ^ packaged by scripts/package-playbook.mjs (upload-legal
          paths only), validated by the official validate.py
        |
+---------------------------------------------------------------+
| dashboard/           React + Vite UI, its own package.json    |
|                      Never imports from or is imported by src/ |
+---------------------------------------------------------------+
        |
+---------------------------------------------------------------+
| scripts/  Local Node tooling (check, secrets scan, package,   |
|           gated publish). Never uploaded.                     |
| tests/    JS (node --test) + Python (unittest) suites.        |
|           Local-only; never packaged.                         |
| index.js  Inert, env-driven Playbook definition. No secrets.  |
+---------------------------------------------------------------+
```

Data flow for a strategy run (`src/main.py:run()`):

1. Build an `InstrumentRegistry` from `manifest.yaml -> strategy_config`
   (fail-safe: on `InstrumentError` the whole portfolio returns `hold`
   with zero signals rather than trading on a broken config).
2. For each requested symbol:
   - Unknown symbol -> `watch` signal, reason `unknown_symbol`, no data fetch.
   - Disabled / non-actionable instrument -> `watch`, reason
     `instrument_disabled`, no data fetch.
   - Otherwise fetch market data through the documented
     `data.crypto.futures.kline` path and evaluate the deterministic
     strategy (EMAs, RSI, ATR, volume, gates).
3. Gates (funding, trading session, Parabolic SAR) are read from the
   registry, not hardcoded per symbol.
4. Position planning uses `registry.effective_config(symbol, config)`,
   which overlays the instrument's risk profile onto the global config.
   For the `standard` profile this overlay is a proven no-op.
5. Each emitted signal carries an additive `meta.instrument` block
   (asset class, session, eligibility, verification status).

## 2. Configuration-Driven Instrument Registry

### Why it exists

The platform will grow seven sections (Crypto, rToken, Stock Preps, CFD,
Commodity, Metal, AI Indexes). Adding a market must be a *configuration*
change, never a strategy-code change. All asset-specific knowledge lives in
`manifest.yaml`; `src/instruments.py` parses, validates, and exposes it;
`src/main.py` only asks the registry questions.

### manifest.yaml -> strategy_config.symbol_roles

One structured entry per instrument:

```yaml
symbol_roles:
  BTCUSDT:
    display_name: "Bitcoin"
    asset_class: crypto                 # key into strategy_config.asset_classes
    market_type: contract
    settlement: crypto_perpetual        # or rwa_perpetual
    trading_session: continuous_24x7
    symbol_verification: native_bitget_contract
        # one of: native_bitget_contract | documented_in_sdk_docs
        #         | verified_public_api (set only from a recorded Bitget
        #           public-API response; see scripts/verify-instruments.mjs)
        #         | inherited_unverified | unverified
    strategy_eligibility: enabled       # enabled | disabled
    execution_availability: signal_only # signal_only | unavailable
    supported_indicators: [ema, rsi, atr, volume, parabolic_sar]
    risk_profile: standard              # key into strategy_config.risk_profiles
    gates:
      funding:         { applies: true, abs_limit_pct: 0.05 }
      session:         { applies: false }
      parabolic_sar:   { applies: false }
      daily_ema50_bounce: { applies: false, implemented: false }  # SP500USDT only
```

Gate semantics (identical to the pre-refactor hardcoded behavior):

- `funding.applies: true` -> a trade is blocked when the absolute funding
  rate exceeds `abs_limit_pct`. Currently BTCUSDT only.
- `session.applies: true` -> trading only inside `windows_utc` hour ranges.
  Currently XAUUSDT only: `[[7,10],[13,16]]` (London / New York).
- `parabolic_sar.applies: true` -> SAR-flip confirmation required.
  Currently AXTIUSDT only.
- `daily_ema50_bounce` is documented in README for SP500USDT but was never
  implemented in strategy code. It is recorded as
  `applies: false, implemented: false` so the truth is in the config.
  Enabling it would be a strategy change requiring explicit approval.

### Current instrument matrix (the four originals, behavior preserved)

| Symbol     | Section   | asset_class | Funding gate | Session gate | SAR gate | Verification           |
|------------|-----------|-------------|--------------|--------------|----------|------------------------|
| BTCUSDT    | Crypto    | crypto      | yes (0.05%)  | no           | no       | verified_public_api (USDT-FUTURES, live 2026-09-21) |
| XAUUSDT    | Metal     | metal       | no           | yes (LON/NY) | no       | verified_public_api (USDT-FUTURES, live 2026-09-21) |
| AXTIUSDT   | Commodity | commodity   | no           | no           | yes      | verified_public_api (USDT-FUTURES, live 2026-09-21) |
| SP500USDT  | Stock     | stock       | no           | no           | no       | verified_public_api (USDT-FUTURES, live 2026-09-21) |

### strategy_config.asset_classes

All seven platform sections are declared, but *declaring a class does not
enable trading in it*. Each class carries:

- `sdk_asset_class`: mapping onto the enum documented by the installed SDK
  (`crypto | stock | rwa | commodity | metal | other`) so interop payloads
  never invent taxonomy values. rtoken -> `rwa`, cfd -> `other`,
  ai_index -> `other`.
- `bitget_data_verified`, `new_instruments_allowed`, `verified_symbols`,
  `symbol_discovery` (the exact API endpoint used to confirm symbols:
  `/api/v2/mix/market/contracts?productType=USDT-FUTURES`),
  `live_trading_supported` (currently `false` everywhere).

Status: **crypto** and **metal** are verified and open to new instruments.
**stock** and **commodity** had their symbols (SP500USDT, AXTIUSDT)
live-verified on 2026-09-21 through the discovery endpoint
(`verified_public_api`, recorded in each symbol's `verified_metadata`), so
their class-level `bitget_data_verified` is now `true`; both classes remain
closed to NEW instruments (`new_instruments_allowed: false`) pending
explicit approval. **rtoken**, **cfd**, and **ai_index** have zero
instruments, `unavailable` execution, and are closed: no Bitget symbol or
product type was invented for them.

### strategy_config.risk_profiles

Named tiers (`conservative`, `standard`). Hard caps enforced in code
(`src/instruments.py`): risk per trade <= 1.0%, leverage <= 20x. A profile
may tighten the global strategy config, never widen it. `standard` resolves
to exactly the original global values, so the four existing instruments are
bit-for-bit unchanged.

## 3. src/instruments.py

Frozen-dataclass registry, stdlib-only (validator import allowlist safe):

- `InstrumentRegistry.from_manifest(...)` - build + validate everything.
- `Instrument` - one symbol: identity, eligibility (`is_eligible`,
  `is_executable`, `is_actionable`, `symbol_is_verified`),
  `supports_indicator(name)`, `gate(name)`.
- `Gate` - `applies` flag plus typed `param(name, default)` access.
- `AssetClassDefinition` - class-level defaults and readiness
  (`is_ready_for_new_instruments`).
- Query helpers used by the strategy: `funding_gate_applies`,
  `session_gate_applies`, `parabolic_sar_gate_applies`, `gate_applies`,
  `funding_abs_limit_pct`, `session_windows`, `effective_config`,
  `actionable_symbols`, `symbols_by_asset_class`.
- Errors: `InstrumentError` (base), `UnknownSymbolError`,
  `InvalidInstrumentConfigError`, `UnsupportedAssetClassError`.
- `LEGACY_SYMBOL_ROLES` fallback reproduces the original hardcoded gates
  exactly, so `src/main.py` still runs (with identical behavior) even if the
  registry import or manifest section is unavailable.

Validation is strict: unknown asset class, unknown risk profile, malformed
gate, session window out of range, risk above cap, or leverage above cap all
raise `InvalidInstrumentConfigError` at registry construction - the strategy
then fails safe to `hold`, it never trades on a suspect config.

## 4. Fail-Safe Behaviors (intentional)

| Condition                          | Behavior                                        |
|------------------------------------|-------------------------------------------------|
| Registry config invalid            | Portfolio-wide `hold`, zero signals             |
| Symbol not in registry             | `watch` + reason `unknown_symbol`, no data fetch|
| Instrument disabled / unavailable  | `watch` + reason `instrument_disabled`          |
| Missing market data for a symbol   | That symbol skipped/`watch`; others unaffected  |
| `ALLOW_PLAYBOOK_PUBLISH` not set   | Publish command refuses; dry-run only           |

## 5. Adding a New Instrument (procedure)

1. Verify the symbol against Bitget, using the class's `symbol_discovery`
   endpoint (`symbolStatus: normal`). No invented symbols, ever. The
   read-only helper `npm run verify:instruments` automates this check.

### Verification tooling (`scripts/verify-instruments.mjs`)

- READ-ONLY by construction: GET requests to public endpoints only
  (`/api/v2/mix/market/contracts`, `/api/v2/spot/public/symbols`, and ticker
  probes). No auth headers, no account data, no orders. Safe to rerun.
- Queries each manifest symbol across ALL real contract product types
  (USDT-FUTURES, COIN-FUTURES, USDC-FUTURES) plus spot, so no shared
  namespace is assumed. Bitget DEMO namespaces (SUSDT-FUTURES etc.) are
  deliberately excluded from production verification.
- Exact symbol matching only; a row for any other symbol is never a hit.
  Symbol mappings are never invented or auto-corrected.
- Listed = contract `symbolStatus: normal` or spot `status: online`
  (per the installed SDK docs). Anything else is reported UNVERIFIED and
  execution stays disabled.
- Execution-API availability is never claimed from public data: it requires
  `trade.market.check_symbol_support` in the managed Playbook runtime against
  a bound account. Recorded as `false` until that check is run with approval.
- Records per instrument: requested/verified symbol, product type, declared
  asset class, trading status, price/quantity precision, min order size,
  market-data availability, timestamp, and every source endpoint queried.
- Reports (gitignored): `output/instrument-verification/report-<UTC>.json`,
  `report-latest.json`, `report-latest.md`. Network failures degrade to
  UNVERIFIED entries with the error recorded - never to a false positive.
- Latest recorded run (2026-09-21T21:18:47Z, executed in the user's own
  PowerShell): **4/4 verified** - BTCUSDT, XAUUSDT, AXTIUSDT and SP500USDT
  all confirmed as `USDT-FUTURES` contracts with `symbolStatus: normal` and
  live ticker data. The confirmed metadata is recorded in each symbol's
  `verified_metadata` block in `manifest.yaml`.
2. Confirm the class has `new_instruments_allowed: true`; if not, the class
   capability must be verified first (AGENTS.md rule 8).
3. Add a `symbol_roles` entry with all required fields and explicit gates.
4. Choose a risk profile; it may only tighten global risk.
5. Add parity/error tests in `tests/python/test_instruments.py`.
6. Run `npm run check`, `npm test`, `npm run test:python`, `npm run validate`.
7. Regenerate the dashboard export (`npm run dashboard:registry`); the
   drift check in `tests/dashboard-registry.test.mjs` fails otherwise.

### Dashboard registry export (`scripts/generate-dashboard-registry.mjs`)

The React dashboard never talks to the strategy or the network in Milestone 1.
It renders a static, generated export of the validated instrument registry:

- `npm run dashboard:registry` runs `scripts/export_registry.py`, which loads
  `manifest.yaml` through `src/instruments.py` (`InstrumentRegistry`) and
  writes `dashboard/src/data/instruments.json`. Anything the registry
  validator rejects can never reach the dashboard.
- Deterministic: same manifest bytes -> same JSON bytes (no generation
  timestamps; `verified_at` values are copied verbatim from the manifest).
- `npm run dashboard:registry:check` (and the JS test suite) fail on drift
  between the committed export and `manifest.yaml`.
- Honesty rules enforced by tests: `verified` is derived only from registry
  provenance; `verified_metadata` is emitted only for verified symbols;
  empty sections export `pending_verification: true` and render "Pending
  verification" - fabricated instruments are impossible; `price` is always
  `null` and `live_prices` is `false` until the read-only feed milestone.
- The old hackathon mock machinery (mockAssets/mockStream/
  useMockMarketStream, Sparkline, SentimentMeter) is deleted. Market panels
  show registry facts and "Live data coming next" instead of random walks.

### Paper-trading workflow (`paper/`, Milestone 2)

A local simulation layer that turns the unchanged deterministic strategy into
simulated positions, fills, and hypothetical P&L. It never trades and never
touches a private endpoint.

- **Signal generation** (`paper/signals.py`) reuses `src/main.py` through
  `paper/harness.py`, which injects a market-data provider and a clock in place
  of the live SDK. Every signal records timestamp, symbol, timeframe,
  direction, entry reference, stop, stop distance, 2R/4R targets, strategy
  version, reason codes, gate results, and the full risk plan. Signals are
  classified:
  - `actionable_paper` - all strategy gates passed and the instrument is
    registry-eligible with execution available, so the simulator may act;
  - `blocked` - refused by a hard applicability gate (BTC funding, XAU session,
    AXTI Parabolic SAR), a circuit-breaker lock, an unknown/disabled symbol, or
    a data failure. Recorded with its reason and never traded;
  - `informational` - the strategy evaluated the symbol and chose to watch.
- **Market data** (`paper/market_data.py`) is GET-only against an allowlist
  (`/api/v2/mix/market/`, `/api/v2/spot/market/`) with no auth headers and a
  hard cap on pagination. A `synthetic` source supports offline development and
  is labelled loudly in every artifact; `cache` replays previously fetched bars.
  Stale, short, or malformed data raises - it is never coerced into a fake bar.
  Only closed bars are served, so a decision at time T cannot see T+1.
- **Simulator** (`paper/simulator.py`) is deterministic and uses `Decimal` for
  all internal accounting; rounded floats appear only in display exports.
  Sizing mirrors `_position_plan` exactly (1% of the margin budget at risk,
  stop at ATR x 1.5, notional capped by margin x leverage). Exits follow the
  documented lifecycle: stop loss, 50% partial at 2R, stop to breakeven
  ("Free Trade"), runner to 4R or the Parabolic SAR trail. Within a bar, stops
  are checked before take-profits and gap opens fill at the worse price - both
  deliberately conservative. Fees and slippage are configurable per side. The
  daily circuit breaker (-2%, flatten at close, 24h lock) is enforced in the
  simulator and fed back into the strategy's own pre-trade check using only the
  previous step's same-UTC-day P&L, so there is no look-ahead. Duplicate signals
  (same symbol + timestamp) are rejected by key, entries are refused while a
  position is open or the portfolio is locked, and a cross-position margin
  guard rejects sizing that would over-commit.
- **Storage** (`paper/storage.py`) writes `signals.jsonl`, `fills.jsonl`,
  `positions.json`, `equity.jsonl`, `events.jsonl`, `run-metadata.json`, and
  `paper-summary.json` under gitignored `output/paper/`, then exports the
  summary to `dashboard/public/paper-summary.json`. Exact Decimal values are
  preserved as strings in the logs. Run metadata records
  `live_orders_placed: false` and `private_api_used: false`. No credentials or
  account data are ever written.
- **Dashboard** renders the summary in `components/market/PaperPanel.jsx` via
  `hooks/usePaperSummary.js`: a one-shot fetch with manual reload (no polling
  timers), a missing file is a supported "no run yet" state that explains how
  to generate one, and every figure is badged PAPER / SIMULATED next to the
  snapshot's `last_updated` timestamp. It is a static snapshot of a local run,
  not a live feed, and it shows no prices of its own.

Commands: `npm run paper:signals`, `npm run paper:simulate`,
`npm run paper:export`, `npm run paper:status` - all wrap `python -m paper.cli`
(exit 0 ok, 2 configuration error, 3 market data unavailable).

## 6. Testing & Verification Pipeline

- `npm test` - JS suite (`tests/*.test.mjs`, node --test), 126 cases: packaging
  legality, dashboard separation, secret scanner, gitignore coverage, the
  dashboard registry export (`tests/dashboard-registry.test.mjs`), the
  paper-trading guards (`tests/paper.test.mjs`: GET-only market data, no
  private-API or order plumbing in `paper/`, no publishing npm script,
  gitignored artifacts, PAPER / SIMULATED labelling, no polling, no fabricated
  prices), and the AI-review guards (`tests/ai.test.mjs`: no order or
  private-API plumbing in `ai_advisor/`, exactly one network module, the
  two-host HTTPS allowlist, status-only error messages, no hardcoded
  credentials, no environment mutation, the veto hook as the single insertion
  point, a distinct `ai-summary.json`, `signal_only` + `veto_only`, gitignored
  artifacts, names-only `.env.example` AI variables, a non-polling dashboard
  hook, a panel that fabricates nothing, and all 33 AI translation keys present
  in both languages).
- `npm run test:python` (via `node scripts/run-python-tests.mjs`) -
  400 unittest cases: original strategy regression tests, registry tests
  (parity for the four originals, unknown symbols, missing config, unsupported
  classes, invalid risk profiles, disabled execution, missing market data,
  recorded public-API verification metadata), 72 paper tests
  (`tests/python/test_paper.py`: position sizing, entries, exits, circuit
  breaker, signal classification, look-ahead, replay determinism, market-data
  guards, snapshots, storage, configuration), and 186 AI-review tests
  (`test_ai_schema.py`, `test_ai_providers.py`, `test_ai_advisor.py`,
  `test_ai_replay.py`, `test_ai_safety.py`: the response contract, the URL
  allowlist and provider failure modes, the fail-closed outcome ladder and the
  veto-only filter, the AI-gated replay end to end including confirm-to-fill and
  veto-to-no-fill, and the structural safety guarantees).
- `npm run check` - secret scan, JS syntax, Python syntax (now walking
  `ai_advisor/` too), official Playbook validator on the staged package.
- `npm run dashboard:build` - Vite production build of `dashboard/`.
- `npm run ai:review` / `npm run ai:status` - the AI-gated paper workflow
  (section 9).

Known pre-existing failures, unrelated to Milestone 3 and deliberately not
"fixed" here: `tests/dashboard-registry.test.mjs` still asserts that the
Milestone-1 mock-data file `dashboard/src/data/mockAssets.js` was deleted (it is
retained but unused by the registry panels), and the two `tests/packaging.test.mjs`
cases plus `npm run check` stage 4 require the root dependency
`@bitget-ai/getagent-skill` to be installed with `npm install`. The JS totals
above are therefore 123 passing / 3 known-failing; every Python case passes.

## 7. Packaging Constraints

Upload accepts ONLY `manifest.yaml`, `README.md`, `src/**`, `backtest.yaml`.
`scripts/package-playbook.mjs` stages exactly that set (currently
`manifest.yaml`, `README.md`, `src/main.py`, `src/instruments.py`) and strips
`__pycache__`/`*.pyc`. Local-only paths (`tests/`, `scripts/`, `dashboard/`,
`output/`, `.tools/`, `.backup/`, `index.js`) must never enter the tarball.

## 8. Paper Data Coverage & Funding Diagnostic (Milestone 2)

`paper/storage.py` adds `build_data_coverage(signals, coverage)` and includes a
`data_coverage` block plus `replay_funding` in every paper summary. Counts come
from the run's real `coverage` and emitted signals; the bar thresholds come from
`market_data.MIN_BARS` (210 closed daily / 120 closed 4h) and are never
hardcoded in the dashboard. Each instrument carries two independent fields:

- `data_eligibility`: `backtest_eligible` (has >= MIN_BARS closed daily and 4h
  bars) or `pending_history`. Eligibility depends only on data readiness - a
  symbol can be backtest eligible while producing zero actionable signals.
- `signal_outcome`: `actionable_setup`, `no_actionable_setup`, `gated` (refused
  by an applicability/risk gate such as `btc_funding_filter`,
  `gold_london_or_ny_session`, `oil_parabolic_sar_confirmation`, or the circuit
  breaker), or `not_evaluated` (insufficient history).

`dashboard/src/components/market/PaperPanel.jsx` renders both fields, the
`daily_bars / min_daily_bars` ratio, and the blocked-reason counts. The summary
also carries `replay_funding`; the panel labels `neutral` runs prominently as an
"Assumption-based diagnostic - not a verified historical-funding backtest",
while conservative `block` mode remains the default. This work changes no
strategy logic, risk limit, symbol, MIN_BARS value, or `execution_mode: signal_only`.

## 9. AI-Assisted Paper Trading (Milestone 3)

`ai_advisor/` adds a provider-agnostic, **veto-only** AI reviewer in front of the
existing deterministic paper pipeline. It changes no strategy rule, risk limit,
applicability gate, symbol, `MIN_BARS` value or `execution_mode`: the strategy
still produces every signal and the simulator still enforces every control.

### The single insertion point

`paper/signals.run_replay(..., signal_filter=None)` gained one optional
parameter. When supplied, the filter is called as
`filter(decision_ms, actionable_signals, closed_bars)` and must return the subset
that may be simulated. `all_signals` - and therefore coverage, signal counts and
the dashboard's data-coverage panel - is recorded **before** the hook runs, so a
veto can never hide what the deterministic strategy actually produced. With the
default `None` the replay is identical to Milestone 2.

Because the AI can only shrink a list handed to it, there is no code path by
which it can create a signal, change a symbol, move a level, resize a position or
unlock a gate. An `accepted` review is still not a trade: it then has to pass the
duplicate guard, circuit breaker, position-open guard, missing-bar guard and the
sizing/margin guards in `paper/simulator.py`.

### Fail-closed outcome ladder

`ai_advisor/advisor.py` evaluates these in order, and every non-accepting outcome
drops the signal - so a broken, slow or dishonest model degrades the system
toward doing nothing rather than toward trading.

| Order | Outcome | Meaning |
| --- | --- | --- |
| 1 | `not_promotable` | not the classification this purpose may review, or no symbol/timestamp |
| 2 | `skipped_no_market_data` | no closed public bars at the decision instant |
| 3 | `budget_exhausted` | `AI_MAX_CALLS` reached; no provider call is made |
| 4 | `provider_error` | transport failure, non-JSON reply, or unexpected provider exception |
| 5 | `schema_invalid` | reply violated the response contract |
| 6 | `accepted` / `vetoed` / `watched` | the model's decision |

### Strict response contract

`ai_advisor/schema.py` accepts exactly one JSON object carrying `decision`
(`confirm` | `reject` | `watch`), `confidence` (number in 0..1; booleans are
rejected), `reasoning` (<= 1200 chars) and `reason_code`
(`^[A-Z][A-Z0-9_]{2,39}$`), plus optional `risk_notes` (<= 8 items x <= 240
chars). Unknown keys, prose mixed into the object, multiple objects or a missing
key raise `AiSchemaError` and the signal is not simulated. A markdown code fence
is tolerated because it is a formatting artifact, not ambiguity.

### Providers and the network allowlist

`ai_advisor/providers.py` is the only module in the repository that performs AI
inference I/O. `ALLOWED_AI_URL_PREFIXES` is `https://openrouter.ai/` and
`https://api.cloudflare.com/`; the URL is re-checked immediately before every
request - including a configured `AI_BASE_URL` - and URLs containing `..` or a
space are rejected. No Bitget endpoint is reachable from this package: market
data stays in `paper/market_data.py` (GET-only, public, allowlisted) and
`ai_advisor` never imports it.

Credentials are read from the environment at call time, are never logged, never
written to an artifact and never placed in a prompt; error messages carry the
HTTP status only, never a response body that could echo a key. A hosted provider
with a missing credential is a configuration error (exit 2) - there is
deliberately **no silent fallback to the fixture**.

The default `fixture` provider is fully offline and deterministic. It is labelled
SYNTHETIC in the CLI banner, in `ai-summary.json` (`ai.synthetic_model`,
`ai.synthetic`) and on the dashboard, so it can never be mistaken for a real
model opinion.

### Point-in-time honesty

`ai_advisor/replay.make_slicer(dataset)` serves the reviewer only bars whose
close time is `<= decision_ms` (binary search over precomputed close times). A
reviewer that could see the bar closing after the decision instant would be
grading the strategy with information the strategy never had, and any "edge" it
found would be look-ahead bias rather than skill.

Watch samples (`AI_WATCH_SAMPLE`) are reviewed **after** `run_replay` returns, so
no watch review can influence a fill; a `watch_sample` review that answers
`confirm` is recorded as `not_promotable`, never `accepted`, so it cannot promote
a setup the strategy declined.

### Artifacts

Written under gitignored `output/ai/`: `signals.jsonl`, `fills.jsonl`,
`positions.json`, `equity.jsonl`, `events.jsonl`, `run-metadata.json`,
`ai-summary.json` and `decisions.jsonl` (one JSON object per review, keys in
`advisor.REVIEW_JSON_KEYS` order). The headline summary is deliberately named
`ai-summary.json` rather than `paper-summary.json`, so an AI run and a paper run
never overwrite each other's artifact. `run-metadata.json` carries
`ai.safety = {live_orders_placed: false, private_api_used: false,
execution_mode: "signal_only", ai_role: "veto_only"}`, and internal accounting
stays exact - Decimal values are serialized as strings, never as rounded display
numbers. No credential or account data is ever written.

The summary is exported to gitignored `dashboard/public/ai-summary.json` and
rendered by `components/market/AiPanel.jsx` through `hooks/useAiSummary.js`:
one-shot fetch with manual reload (no polling timers), a missing file is a
supported "no run yet" state, and every figure is badged PAPER / SIMULATED next
to the snapshot's `last_updated` timestamp.

`reconcile_accepted_with_fills` closes the audit loop: each accepted entry-gate
review is annotated with what the simulator actually did (`filled`, with copied
fill dicts; the matching event type; or `no_simulator_record`), matching exactly
on symbol and decision timestamp and counting entry-side fills only, so a later
exit can never be attributed to a review.

### Commands

`npm run ai:review` and `npm run ai:status` wrap `python -m ai_advisor.cli`
(exit 0 ok, 2 configuration error, 3 market data unavailable). `main()` refuses
to run at all unless `manifest.yaml` still declares `execution_mode:
signal_only`. Fully offline demo, loudly labelled:

```powershell
npm run ai:review -- --source synthetic
```
