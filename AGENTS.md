# AGENTS.md - Permanent Project Instructions

Scope: this file governs the entire repository. Any agent or engineer working
here MUST follow these rules. Direct user instructions in a session may add
constraints but never relax the safety rules below.

Project: **The Morning Sword** - a Bitget Playbook trading strategy, being
evolved into a multi-asset AI Index Trading Platform (Crypto, rToken, Stock
Preps, CFD, Commodities, Metals, AI Indexes).

---

## 1. Hard Safety Rules (non-negotiable)

1. **Never expose secrets.** Do not print, echo, log, commit, paste into docs,
   embed in prompts, or copy into backups any credential value. Refer to secrets
   by variable name only (e.g. `BITGET_ACCESS_KEY`). When auditing, redact.
2. **Never execute live trades without explicit approval.** No order placement,
   no position mutation, no `follow_trade` enablement. Approval must be given in
   the current session, by the user, in plain language.
3. **Never publish a Playbook without explicit approval.** `POST
   /api/v1/playbook/publish` is irreversible: it freezes an immutable public
   version. Requires an explicit, in-session user approval AND the
   `ALLOW_PLAYBOOK_PUBLISH=true` + `PUBLISH_CONFIRMATION_PHRASE` gates.
4. **Never deploy automatically.** No auto-deploy, no post-commit deploy hooks,
   no "start" script that publishes. Deployment is always a deliberate,
   separately-named, confirmed action.
5. **Preserve the existing working strategy.** `src/main.py` is the validated,
   working Playbook logic. Do not rewrite, "clean up", or refactor its trading
   math without explicit approval. Additive changes and tests are fine.
6. **Prefer paper trading and dry runs.** Default every new capability to
   dry-run. Make the destructive path opt-in, loud, and explicit.
7. **Separate AI analysis from deterministic risk controls.** LLM/AI output may
   inform *analysis and narrative*, but position sizing, stop placement, circuit
   breakers, and session/funding gates must remain deterministic code. AI must
   never be able to widen risk limits or bypass the circuit breaker.
8. **Verify API capabilities before implementing them.** Read the installed SDK
   docs and confirm an endpoint/export actually exists before writing code
   against it. Do not invent functions, parameters, or response shapes.

---

## 2. Verified Platform Facts (do not re-assume these)

These were verified against `@bitget-ai/getagent-skill@0.5.0` as installed in
`node_modules/`. Re-verify after any SDK upgrade.

- **The npm package is NOT a JavaScript SDK.** It ships only a skill-installer
  CLI (`bin/getagent-skill.js`), docs, examples, and `scripts/validate.py`.
  It declares `"type": "commonjs"` with **no `main`, no `exports`, no `module`**.
  There is **no `createPlaybook` export**. Code that imports it as a library
  will fail. The original `index.js` was broken for this reason.
- **The real control plane is HTTP**, base URL fixed at `https://api.bitget.com`
  (the SDK docs explicitly say not to parameterize it via user env vars).
  Auth is the `ACCESS-KEY` header.
- **Publish workflow order:** `upload` -> `run` -> `confirm` -> `publish`.
  `upload` returns a temporary `draft_id`; `confirm` promotes it to a draft;
  only `publish` assigns the public semver.
- **Upload package contents are restricted** to `manifest.yaml`, `README.md`,
  `src/**`, and optional `backtest.yaml`.
- **Server + local validation reject local-only top-level paths**: `tests/`,
  `notebooks/`, `research/`, `data/`, `backtest_results/`, `logs/`, `output/`,
  `.venv/`, `__pycache__/`, `.pytest_cache/`. These must never enter the tarball.
- **Python import allowlist inside `src/**`**: `getagent`, `getclaw`,
  `nautilus_trader`, `pandas`, `numpy`, `json`, `math`, `datetime`, `pathlib`,
  `asyncio`, `typing`, `dataclasses`, `collections`, `functools`, `re`,
  `decimal`, `statistics`, `itertools`, `operator`, `copy`, `enum`, `abc`,
  `numbers`, `fractions`. **Blocked**: `requests`, `subprocess`, `os`.
  `eval`/`exec`/`compile`/`__import__`/dunder introspection are rejected.
- **`manifest.yaml` `long_description` rules**: 250-500 words (target 300-400),
  must cover thesis/entry/exit/tunables/risks, and must NOT leak indicator
  periods, numeric thresholds, percentages, multipliers, or ratios.
- **`README.md` must contain** the plain-language section markers
  `策略`, `开仓`, `平仓`, `风险` and be >= 200 characters.
- **Symbol reality check**: `OILUSDT` and `SPX500USDT` do **not** exist in Bitget
  public contracts. The confirmed tradable symbols are `BTCUSDT`, `XAUUSDT`,
  `AXTIUSDT` (oil-linked proxy), `SP500USDT`. Do not reintroduce the non-existent
  symbols into `manifest.yaml` or `src/main.py`.

---

## 3. Repository Layout

```
manifest.yaml        Playbook manifest (package root - required path)
README.md            Strategy doc (package root - required path, needs CJK markers)
src/main.py          THE STRATEGY. Deterministic Playbook logic. Protect it.
src/instruments.py   Config-driven instrument registry (parsed from manifest.yaml)
index.js             Inert, env-driven Playbook definition. Does NOT auto-run.
dashboard/           React + Vite UI (separate package, own dependencies)
scripts/             Safe local tooling (validate, package, gated publish)
tests/               Test suite (local-only; never packaged for upload)
requirements.txt     Python deps for local validation of src/main.py
ARCHITECTURE.md      System + instrument-registry design document
.env.example         Variable NAMES only. Never real values.
.tools/              Local bundled MinGit/GitHub CLI. Gitignored, never committed.
.backup/             Local snapshots. Gitignored - may contain plaintext secrets.
```

Keep the React dashboard and the Python strategy separated. Do not add JS/JSX or
CSS files under `src/`; that directory is the uploadable Python package.

### Instrument Registry Rules

- All asset-specific behavior (funding / session / Parabolic SAR gates, risk
  profiles, eligibility, execution availability) lives in `manifest.yaml`
  under `strategy_config.symbol_roles`, `asset_classes`, and
  `risk_profiles`, and is read through `src/instruments.py`. Do not
  re-hardcode per-symbol logic in `src/main.py`.
- The four original instruments (BTCUSDT, XAUUSDT, AXTIUSDT, SP500USDT) are
  protected parity targets: any change to their gate behavior or position
  math requires explicit user approval plus a regression test.
- Never add an instrument to an asset class whose `new_instruments_allowed`
  is `false`, and never invent Bitget symbols. Verify the symbol through
  the class's `symbol_discovery` endpoint first (rule 1.8).
- Risk profiles may only tighten the global caps (1.0 percent risk per
  trade, 20x leverage). Deterministic risk controls stay in code; config
  selects within fixed bounds.
- Full design detail, schemas, and fail-safe behavior: `ARCHITECTURE.md`.

---

## 4. Safe Commands

```powershell
npm test                 # run the test suite (offline, no network, no trading)
npm run check            # JS syntax + secret scan + Python compile
npm run check:secrets    # scan the tree for hardcoded credentials
npm run validate         # stage allowed paths, then run the official validate.py
npm run package          # build the upload tarball locally (NO upload)
npm start                # safe local status summary (read-only, no network)
npm run verify:instruments  # READ-ONLY Bitget public-API symbol verification
                         # (no auth, no account data, no orders; writes a
                         # report under output/instrument-verification/)
```

Gated, never automatic:

```powershell
npm run playbook:publish -- --dry-run    # preview only; default behaviour
npm run playbook:publish -- --confirm    # requires env gates + typed phrase
```

---

## 5. Working Agreements

- Ask before any irreversible action: deleting files, rewriting history,
  publishing, deploying, placing orders, force-pushing, rotating credentials.
- Never `git add -A` blindly. `.tools/` (~55 MB of binaries), `.backup/`,
  `__pycache__/`, and `.env` must never be staged. Review `git status` first.
- Never commit a build artifact that can be regenerated (`dist/`).
- Python is not on PATH here. Use the bundled interpreter at
  `C:\Users\NIAMO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`,
  and set `PYTHONIOENCODING=utf-8` (the validator prints CJK markers).
- Git is not on PATH here. Use `.tools\mingit\cmd\git.exe`.
- This folder is OneDrive-synced. Assume any plaintext secret written to disk
  has left the machine, and treat it as compromised.

---

## 6. Definition of Done for Changes

A change is not finished until: `npm run check:secrets` is clean,
`npm test` passes, `npm run validate` passes, and `git status` shows no
secrets, binaries, bytecode, or env files.
