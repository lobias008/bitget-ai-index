/**
 * Safety invariants for the inert Playbook definition module (index.js).
 *
 * These tests exist to make the original defects unrepeatable:
 *   - a hardcoded API key literal in source
 *   - an `apiKey` field travelling with the definition object
 *   - auto-publish / auto-backtest enabled by default
 *   - a top-level side effect that fires a network call on import
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
  GETAGENT_API_BASE_URL,
  MARKETS,
  PUBLISH_CONFIRMATION_PHRASE,
  checkPublishGates,
  maskAccessKey,
  playbookDefinition,
  resolveAccessKey,
} from '../index.js';

const repoRoot = path.resolve(import.meta.dirname, '..');
const SOURCE = fs.readFileSync(path.join(repoRoot, 'index.js'), 'utf8');

const CREDENTIAL_KEY_NAMES = new Set([
  'apikey',
  'api_key',
  'accesskey',
  'access_key',
  'secret',
  'secretkey',
  'secret_key',
  'clientsecret',
  'client_secret',
  'password',
  'token',
  'authtoken',
]);

function* walkKeys(value, trail = '') {
  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) yield* walkKeys(item, `${trail}[${index}]`);
    return;
  }
  if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      yield { key, trail: trail ? `${trail}.${key}` : key };
      yield* walkKeys(item, trail ? `${trail}.${key}` : key);
    }
  }
}

test('definition carries no credential field of any name', () => {
  const offenders = [...walkKeys(playbookDefinition)]
    .filter((entry) => CREDENTIAL_KEY_NAMES.has(entry.key.toLowerCase()))
    .map((entry) => entry.trail);
  assert.deepEqual(offenders, []);
});

test('source contains no hardcoded credential literal', () => {
  assert.doesNotMatch(SOURCE, /\b[a-fA-F0-9]{32,}\b/, 'found a 32+ hex literal in index.js');
  assert.doesNotMatch(SOURCE, /API_KEY\s*=\s*['"][^'"]+['"]/, 'found an assigned key literal');
  assert.doesNotMatch(SOURCE, /sk-[A-Za-z0-9_-]{16,}/);
});

test('credentials are read from the environment only', () => {
  assert.match(SOURCE, /env\.BITGET_ACCESS_KEY/);
  assert.throws(() => resolveAccessKey({}), /BITGET_ACCESS_KEY is not set/);
  assert.equal(resolveAccessKey({ BITGET_ACCESS_KEY: 'from-env' }), 'from-env');
});

test('publish and backtest are disabled by default', () => {
  assert.equal(playbookDefinition.publish.enabled, false);
  assert.equal(playbookDefinition.backtest.enabled, false);
  assert.equal(playbookDefinition.publish.requiresExplicitApproval, true);
});

test('publish visibility is not public by default', () => {
  assert.notEqual(playbookDefinition.publish.visibility, 'public-hackathon-submission');
  assert.match(playbookDefinition.publish.visibility, /private/);
});

test('importing the module performs no network I/O', () => {
  assert.doesNotMatch(SOURCE, /^\s*(?:await\s+)?fetch\(/m, 'index.js must not call fetch');
  assert.doesNotMatch(SOURCE, /^deploy\(\)/m, 'index.js must not auto-run a deploy pipeline');
  assert.doesNotMatch(SOURCE, /\bhttps\.request\b|\bhttp\.request\b/);
});

test('api base url is the fixed documented endpoint', () => {
  assert.equal(GETAGENT_API_BASE_URL, 'https://api.bitget.com');
});

test('masking never reveals the full credential', () => {
  const key = 'abcdefghijklmnopqrstuvwxyz012345';
  const masked = maskAccessKey(key);
  assert.ok(!masked.includes(key), 'masked output leaked the full value');
  assert.match(masked, /REDACTED/);
  assert.equal(maskAccessKey(undefined), '<unset>');
  assert.equal(maskAccessKey(''), '<unset>');
});

test('publish gates block by default and require all three switches', () => {
  assert.equal(checkPublishGates({}).length, 3);
  assert.equal(
    checkPublishGates({
      BITGET_ACCESS_KEY: 'k',
      ALLOW_PLAYBOOK_PUBLISH: 'true',
      PUBLISH_CONFIRMATION_PHRASE: PUBLISH_CONFIRMATION_PHRASE,
    }).length,
    0,
  );
});

test('publish gates reject near-miss confirmations', () => {
  const almost = {
    BITGET_ACCESS_KEY: 'k',
    ALLOW_PLAYBOOK_PUBLISH: 'TRUE',
    PUBLISH_CONFIRMATION_PHRASE: PUBLISH_CONFIRMATION_PHRASE.toLowerCase(),
  };
  assert.equal(checkPublishGates(almost).length, 1);
  assert.equal(checkPublishGates({ ...almost, ALLOW_PLAYBOOK_PUBLISH: 'yes' }).length, 2);
});

test('every market symbol is a real confirmed Bitget contract', () => {
  const PHANTOM = new Set(['OILUSDT', 'SPX500USDT']);
  const symbols = MARKETS.map((market) => market.symbol);
  assert.equal(symbols.length, 4);
  assert.deepEqual(symbols, ['BTCUSDT', 'XAUUSDT', 'AXTIUSDT', 'SP500USDT']);
  for (const symbol of symbols) {
    assert.match(symbol, /^[A-Z0-9]+USDT$/);
    assert.ok(!PHANTOM.has(symbol), `${symbol} does not exist on Bitget`);
  }
});

test('asset adaptations reference the same symbols as MARKETS', () => {
  const marketSymbols = new Set(MARKETS.map((market) => market.symbol));
  for (const [asset, adaptation] of Object.entries(playbookDefinition.strategy.assetSpecificAdaptations)) {
    assert.ok(marketSymbols.has(adaptation.symbol), `${asset} uses unknown symbol ${adaptation.symbol}`);
  }
});

test('circuit breaker remains enabled and deterministic', () => {
  const breaker = playbookDefinition.strategy.rules.circuitBreaker;
  assert.equal(breaker.enabled, true);
  assert.equal(breaker.dailyEquityLossLimitPct, 2.0);
  assert.equal(breaker.lockoutHours, 24);
  assert.ok(breaker.breachActions.includes('flatten_active_positions'));
});

test('risk budget stays at the documented 1 percent', () => {
  assert.equal(playbookDefinition.strategy.rules.riskAndPositionSizing.equityRiskPerTradePct, 1.0);
});
