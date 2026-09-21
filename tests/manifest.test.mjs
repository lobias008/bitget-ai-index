/**
 * Consistency and safety-contract tests for manifest.yaml, README.md and
 * src/main.py.
 *
 * manifest.yaml is parsed with the real YAML parser via the bundled Python
 * interpreter, so these assertions run against the same data the GetAgent
 * server will see - not against a hand-rolled approximation.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { repoRoot, resolvePython, run } from '../scripts/_common.mjs';
import { MARKETS } from '../index.js';

const PHANTOM_SYMBOLS = ['OILUSDT', 'SPX500USDT'];
const SYMBOL_TOKEN = /\b[A-Z0-9]{2,20}USDT\b/g;

function loadManifest() {
  const code = 'import json,sys,yaml\nprint(json.dumps(yaml.safe_load(open(sys.argv[1],encoding="utf-8"))))\n';
  const result = run(resolvePython(), ['-c', code, path.join(repoRoot, 'manifest.yaml')]);
  assert.equal(result.status, 0, `could not parse manifest.yaml: ${result.stderr}`);
  return JSON.parse(result.stdout);
}

const manifest = loadManifest();
const manifestText = fs.readFileSync(path.join(repoRoot, 'manifest.yaml'), 'utf8');
const strategyText = fs.readFileSync(path.join(repoRoot, 'src', 'main.py'), 'utf8');
const readmeText = fs.readFileSync(path.join(repoRoot, 'README.md'), 'utf8');

test('manifest declares every field the upload validator requires', () => {
  for (const field of [
    'name',
    'display_name',
    'version',
    'description',
    'long_description',
    'market_type',
    'trading_symbols',
    'decision_mode',
    'backtest_support',
    'runtime_profile',
    'execution_mode',
    'follow_trade_supported',
  ]) {
    assert.ok(manifest[field] !== undefined, `manifest.yaml is missing '${field}'`);
  }
});

test('manifest name is a valid DNS label', () => {
  assert.match(manifest.name, /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/);
});

test('manifest symbols match the symbols used in code', () => {
  const fromManifest = [...manifest.trading_symbols].sort();
  const fromDefinition = [...MARKETS.map((market) => market.symbol)].sort();
  assert.deepEqual(fromManifest, fromDefinition);
});

test('strategy config symbols match top-level symbols', () => {
  assert.deepEqual(
    [...manifest.strategy_config.trading_symbols].sort(),
    [...manifest.trading_symbols].sort(),
  );
});

test('no symbol appears in code that the manifest does not declare', () => {
  const declared = new Set(manifest.trading_symbols);
  const used = new Set(strategyText.match(SYMBOL_TOKEN) ?? []);
  const undeclared = [...used].filter((symbol) => !declared.has(symbol));
  assert.deepEqual(undeclared, [], `src/main.py references undeclared symbols: ${undeclared}`);
});

test('no non-existent Bitget symbol is configured as tradable', () => {
  const configured = new Set([
    ...manifest.trading_symbols,
    ...manifest.strategy_config.trading_symbols,
    ...Object.keys(manifest.strategy_config.symbol_roles ?? {}),
  ]);
  for (const phantom of PHANTOM_SYMBOLS) {
    assert.ok(!configured.has(phantom), `${phantom} is configured as a tradable symbol`);
  }
  const codeSymbols = new Set(strategyText.match(SYMBOL_TOKEN) ?? []);
  for (const phantom of PHANTOM_SYMBOLS) {
    assert.ok(!codeSymbols.has(phantom), `src/main.py references ${phantom}`);
  }
});

test('phantom symbols survive only as explanatory substitution notes', () => {
  // manifest.yaml legitimately documents WHY OILUSDT / SPX500USDT were replaced.
  // Any mention outside such a note would mean a real misconfiguration.
  const offending = manifestText
    .split(/\r?\n/)
    .filter((line) => PHANTOM_SYMBOLS.some((phantom) => line.includes(phantom)))
    .filter((line) => !/not found|does not exist|unavailable|replaced|substitut/i.test(line));
  assert.deepEqual(offending, [], `unexplained phantom symbol reference: ${offending.join(' | ')}`);
});

test('the Playbook is signal-only and cannot follow trades', () => {
  assert.equal(manifest.execution_mode, 'signal_only');
  assert.equal(manifest.follow_trade_supported, false);
});

test('risk decisions stay deterministic, not LLM-driven', () => {
  assert.equal(manifest.decision_mode, 'deterministic');
  assert.equal(manifest.runtime_profile, 'deterministic');
});

test('subscriber-tunable leverage and risk stay within safe bounds', () => {
  const schema = manifest.user_config_schema;
  assert.ok(schema.leverage.max <= 20, 'leverage cap exceeds 20x');
  assert.ok(schema.leverage.min >= 1);
  assert.ok(schema.risk_per_trade_pct.max <= 1.0, 'risk per trade cap exceeds 1%');
  assert.equal(schema.risk_per_trade_pct.default, 1.0);
});

test('circuit breaker is configured at minus two percent daily', () => {
  assert.equal(manifest.strategy_config.circuit_breaker_daily_loss_pct, -2.0);
  assert.equal(manifest.strategy_config.atr_stop_multiple, 1.5);
  assert.equal(manifest.strategy_config.partial_take_profit_pct, 50.0);
});

test('long_description meets length rules without leaking parameters', () => {
  const text = String(manifest.long_description).trim();
  const words = text.split(/\s+/).length;
  assert.ok(words >= 250 && words <= 500, `long_description is ${words} words; must be 250-500`);
  assert.doesNotMatch(
    text,
    /\b(?:EMA|SMA|WMA|MA|RSI|MACD|ATR|VWAP|ADX|Stoch(?:astic)?|Bollinger|MFI|CCI|OBV|DMI|TRIX|KDJ)[\s_/-]*\d+/i,
    'long_description leaks an indicator period',
  );
});

test('README carries the required plain-language section markers', () => {
  const normalized = readmeText.toLowerCase();
  for (const phrase of ['策略', '开仓', '平仓', '风险']) {
    assert.ok(normalized.includes(phrase.toLowerCase()), `README.md is missing '${phrase}'`);
  }
  assert.ok(readmeText.trim().length >= 200);
});

test('every declared symbol is offered in the user config schema', () => {
  const options = new Set(manifest.user_config_schema.trading_symbols.options);
  for (const symbol of manifest.trading_symbols) {
    assert.ok(options.has(symbol), `${symbol} is not selectable in user_config_schema`);
  }
});

