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

## 6. Testing & Verification Pipeline

- `npm test` - JS suite (`tests/*.test.mjs`, node --test): packaging
  legality, dashboard separation, secret scanner, gitignore coverage.
- `npm run test:python` (via `node scripts/run-python-tests.mjs`) -
  128 unittest cases: 56 original strategy regression tests + 72 registry
  tests (parity for the four originals, unknown symbols, missing config,
  unsupported classes, invalid risk profiles, disabled execution, missing
  market data).
- `npm run check` - secret scan, JS syntax, Python syntax, official
  Playbook validator on the staged package.
- `npm run dashboard:build` - Vite production build of `dashboard/`.

## 7. Packaging Constraints

Upload accepts ONLY `manifest.yaml`, `README.md`, `src/**`, `backtest.yaml`.
`scripts/package-playbook.mjs` stages exactly that set (currently
`manifest.yaml`, `README.md`, `src/main.py`, `src/instruments.py`) and strips
`__pycache__`/`*.pyc`. Local-only paths (`tests/`, `scripts/`, `dashboard/`,
`output/`, `.tools/`, `.backup/`, `index.js`) must never enter the tarball.
