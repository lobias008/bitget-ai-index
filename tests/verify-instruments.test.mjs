/**
 * Tests for scripts/verify-instruments.mjs using MOCKED API responses.
 * No test ever touches the network: every call injects a fake fetch.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
  BITGET_SUCCESS_CODE,
  CONTRACT_PRODUCT_TYPES,
  DEFAULT_BASE_URL,
  DEMO_PRODUCT_TYPES,
  buildMarkdown,
  envelopeRows,
  fetchJson,
  parseArgs,
  parseSymbolRoles,
  verifyInstruments,
  verifySymbol,
  writeReports,
} from '../scripts/verify-instruments.mjs';
import { repoRoot } from '../scripts/_common.mjs';

const FIXED_NOW = new Date('2026-09-22T09:30:00.000Z');
const now = () => FIXED_NOW;

function envelope(data, code = BITGET_SUCCESS_CODE, msg = 'success') {
  return { code, msg, requestTime: 1758530000000, data };
}

/**
 * Build a mock fetch. `routes` maps a URL substring to either:
 *   - a plain object  -> served as JSON with HTTP 200
 *   - { status, body } -> served with that HTTP status
 *   - { throw: Error } -> the fetch promise rejects
 * Anything unmatched returns an empty-data success envelope.
 * Every request is recorded in mock.calls for safety assertions.
 */
function mockFetch(routes = {}) {
  const calls = [];
  const impl = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    for (const [needle, route] of Object.entries(routes)) {
      if (!String(url).includes(needle)) continue;
      if (route && route.throw) throw route.throw;
      const status = route && route.status ? route.status : 200;
      const body = route && Object.hasOwn(route, 'body') ? route.body : route;
      const text = typeof body === 'string' ? body : JSON.stringify(body);
      return { ok: status >= 200 && status < 300, status, text: async () => text };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify(envelope([])) };
  };
  impl.calls = calls;
  return impl;
}

const contractRow = (symbol, overrides = {}) => ({
  symbol,
  symbolStatus: 'normal',
  baseCoin: symbol.replace(/USDT$/, ''),
  quoteCoin: 'USDT',
  pricePlace: '1',
  priceEndStep: '1',
  volumePlace: '3',
  minTradeNum: '0.001',
  minTradeUSDT: '5',
  sizeMultiplier: '0.001',
  feeRate: '0.0006',
  ...overrides,
});

const spotRow = (symbol, overrides = {}) => ({
  symbol,
  baseCoin: symbol.replace(/USDT$/, ''),
  quoteCoin: 'USDT',
  minTradeAmount: '0.0001',
  maxTradeAmount: '5000000',
  priceScale: '2',
  priceEndStep: '1',
  quantityScale: '4',
  status: 'online',
  offTime: '24',
  ...overrides,
});

const fast = { delayMs: 0, timeoutMs: 1000, now };

// ---------------------------------------------------------------------------
// parseSymbolRoles
// ---------------------------------------------------------------------------

test('parseSymbolRoles reads the real manifest symbols and asset classes', () => {
  const text = fs.readFileSync(path.join(repoRoot, 'manifest.yaml'), 'utf8');
  const roles = parseSymbolRoles(text);
  assert.deepEqual([...roles.keys()], ['BTCUSDT', 'XAUUSDT', 'AXTIUSDT', 'SP500USDT']);
  assert.equal(roles.get('BTCUSDT').asset_class, 'crypto');
  assert.equal(roles.get('XAUUSDT').asset_class, 'metal');
  assert.equal(roles.get('AXTIUSDT').asset_class, 'commodity');
  assert.equal(roles.get('SP500USDT').asset_class, 'stock');
  assert.equal(roles.get('BTCUSDT').display_name, 'Bitcoin');
});

test('parseSymbolRoles stops at the end of the block and tolerates comments', () => {
  const text = [
    'strategy_config:',
    '  # a comment',
    '  symbol_roles:',
    '    FOOUSDT:',
    '      asset_class: crypto',
    '      # inline comment',
    '      gates:',
    '        funding: { applies: true }',
    '  margin_budget: "100"',
    '  leverage: 3',
    'user_config_schema:',
    '  something: else',
  ].join('\n');
  const roles = parseSymbolRoles(text);
  assert.deepEqual([...roles.keys()], ['FOOUSDT']);
  assert.equal(roles.get('FOOUSDT').asset_class, 'crypto');
});

// ---------------------------------------------------------------------------
// fetchJson / envelopeRows
// ---------------------------------------------------------------------------

test('fetchJson captures HTTP failures without throwing', async () => {
  const impl = mockFetch({ 'mix/market/contracts': { status: 500, body: 'boom' } });
  const result = await fetchJson('https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=X', { fetchImpl: impl, timeoutMs: 1000 });
  assert.equal(result.http_status, 500);
  assert.equal(result.ok, false);
  const { rows, error } = envelopeRows(result);
  assert.equal(rows, null);
  assert.match(error, /HTTP 500/);
});

test('fetchJson captures network exceptions without throwing', async () => {
  const impl = mockFetch({ '': { throw: new Error('getaddrinfo ENOTFOUND') } });
  const result = await fetchJson('https://api.bitget.com/api/v2/spot/public/symbols?symbol=X', { fetchImpl: impl, timeoutMs: 1000 });
  const { rows, error } = envelopeRows(result);
  assert.equal(rows, null);
  assert.match(error, /request failed.*ENOTFOUND/);
});

test('envelopeRows rejects non-success API codes and non-JSON bodies', () => {
  assert.match(envelopeRows({ ok: true, http_status: 200, body: { code: '40034', msg: 'Parameter error' }, parse_error: false }).error, /API code 40034/);
  assert.match(envelopeRows({ ok: true, http_status: 200, body: null, parse_error: true }).error, /non-JSON/);
  assert.deepEqual(envelopeRows({ ok: true, http_status: 200, body: envelope(null), parse_error: false }).rows, []);
});

// ---------------------------------------------------------------------------
// verifySymbol
// ---------------------------------------------------------------------------

test('a normal USDT-FUTURES contract is verified with full metadata', async () => {
  const impl = mockFetch({
    'market/contracts?productType=USDT-FUTURES': envelope([contractRow('BTCUSDT', { pricePlace: '1', volumePlace: '3' })]),
    'spot/public/symbols': envelope([]),
    'mix/market/ticker': envelope([{ symbol: 'BTCUSDT', lastPr: '120000' }]),
  });
  const rec = await verifySymbol('BTCUSDT', { fetchImpl: impl, declared: { asset_class: 'crypto', display_name: 'Bitcoin' }, ...fast });
  assert.equal(rec.verified, true);
  assert.equal(rec.verified_symbol, 'BTCUSDT');
  assert.equal(rec.market, 'contract');
  assert.equal(rec.product_type, 'USDT-FUTURES');
  assert.equal(rec.asset_class_declared, 'crypto');
  assert.equal(rec.trading_status, 'normal');
  assert.equal(rec.price_precision, 1);
  assert.equal(rec.quantity_precision, 3);
  assert.equal(rec.min_order_size, '0.001');
  assert.equal(rec.market_data_available, true);
  assert.equal(rec.execution_api_available, false);
  assert.equal(rec.verification_timestamp, FIXED_NOW.toISOString());
  assert.ok(rec.source_endpoints.length >= 5); // 3 contract PTs + spot + ticker
});

test('a symbol only listed under COIN-FUTURES is attributed to that product type', async () => {
  const impl = mockFetch({
    'market/contracts?productType=COIN-FUTURES': envelope([contractRow('ETHUSD')]),
    'spot/public/symbols': envelope([]),
    'mix/market/ticker': envelope([{ symbol: 'ETHUSD' }]),
  });
  const rec = await verifySymbol('ETHUSD', { fetchImpl: impl, ...fast });
  assert.equal(rec.verified, true);
  assert.equal(rec.product_type, 'COIN-FUTURES');
});

test('a spot-only symbol is verified through the spot namespace', async () => {
  const impl = mockFetch({
    'spot/public/symbols': envelope([spotRow('RAAPLUSDT')]),
    'spot/market/ticker': envelope([{ symbol: 'RAAPLUSDT' }]),
  });
  const rec = await verifySymbol('RAAPLUSDT', { fetchImpl: impl, ...fast });
  assert.equal(rec.verified, true);
  assert.equal(rec.market, 'spot');
  assert.equal(rec.product_type, 'SPOT');
  assert.equal(rec.trading_status, 'online');
  assert.equal(rec.price_precision, 2);
  assert.equal(rec.quantity_precision, 4);
  assert.equal(rec.min_order_size, '0.0001');
  assert.equal(rec.market_data_available, true);
});

test('a symbol missing from every namespace is UNVERIFIED and stays disabled', async () => {
  const impl = mockFetch({});
  const rec = await verifySymbol('AXTIUSDT', { fetchImpl: impl, declared: { asset_class: 'commodity' }, ...fast });
  assert.equal(rec.verified, false);
  assert.equal(rec.verified_symbol, null);
  assert.equal(rec.market, null);
  assert.equal(rec.trading_status, 'not_listed');
  assert.match(rec.unverified_reason, /not returned by any queried Bitget public config endpoint/);
  assert.match(rec.unverified_reason, /Execution MUST remain disabled/);
  assert.equal(rec.execution_api_available, false);
  assert.equal(rec.market_data_available, false);
});

test('a listed-but-not-normal contract is not verified', async () => {
  const impl = mockFetch({
    'market/contracts?productType=USDT-FUTURES': envelope([contractRow('SP500USDT', { symbolStatus: 'hidden' })]),
  });
  const rec = await verifySymbol('SP500USDT', { fetchImpl: impl, ...fast });
  assert.equal(rec.verified, false);
  assert.equal(rec.trading_status, 'hidden');
  assert.match(rec.unverified_reason, /hidden/);
});

test('rows for OTHER symbols are never treated as a match (no invented mappings)', async () => {
  const impl = mockFetch({
    'symbol=AXTIUSDT': envelope([contractRow('BTCUSDT')]), // API echoes a different symbol
    'spot/public/symbols': envelope([spotRow('BTCUSDT')]),
  });
  const rec = await verifySymbol('AXTIUSDT', { fetchImpl: impl, ...fast });
  assert.equal(rec.verified, false);
  const contractCheck = rec.checks.find((c) => c.market === 'contract' && c.product_type === 'USDT-FUTURES');
  assert.equal(contractCheck.rows_returned, 1);
  assert.equal(contractCheck.exact_matches, 0);
});

test('API-level and transport-level failures degrade to UNVERIFIED, never crash', async () => {
  const impl = mockFetch({
    'market/contracts?productType=USDT-FUTURES': { status: 200, body: JSON.stringify(envelope([], '40034', 'Parameter error')) },
    'market/contracts?productType=COIN-FUTURES': { status: 502, body: 'bad gateway' },
    'market/contracts?productType=USDC-FUTURES': { throw: new Error('socket hang up') },
    'spot/public/symbols': { status: 200, body: '<html>not json</html>' },
  });
  const rec = await verifySymbol('BTCUSDT', { fetchImpl: impl, ...fast });
  assert.equal(rec.verified, false);
  assert.equal(rec.checks.length, 4);
  assert.match(rec.checks[0].error, /API code 40034/);
  assert.match(rec.checks[1].error, /HTTP 502/);
  assert.match(rec.checks[2].error, /socket hang up/);
  assert.match(rec.checks[3].error, /non-JSON/);
});

test('ticker failure keeps config verification but marks market data unavailable', async () => {
  const impl = mockFetch({
    'market/contracts?productType=USDT-FUTURES': envelope([contractRow('BTCUSDT')]),
    'mix/market/ticker': { status: 500, body: 'err' },
  });
  const rec = await verifySymbol('BTCUSDT', { fetchImpl: impl, ...fast });
  assert.equal(rec.verified, true);
  assert.equal(rec.market_data_available, false);
  assert.match(rec.market_data_note, /HTTP 500/);
});

test('--no-ticker skips the market-data probe entirely', async () => {
  const impl = mockFetch({ 'market/contracts?productType=USDT-FUTURES': envelope([contractRow('BTCUSDT')]) });
  const rec = await verifySymbol('BTCUSDT', { fetchImpl: impl, checkTicker: false, ...fast });
  assert.equal(rec.verified, true);
  assert.equal(rec.market_data_available, false);
  assert.match(rec.market_data_note, /--no-ticker/);
  assert.ok(!impl.calls.some((c) => c.url.includes('/ticker')));
});

test('every request is a read-only public GET with no auth headers', async () => {
  const impl = mockFetch({
    'market/contracts?productType=USDT-FUTURES': envelope([contractRow('BTCUSDT')]),
    'mix/market/ticker': envelope([{ symbol: 'BTCUSDT' }]),
  });
  await verifySymbol('BTCUSDT', { fetchImpl: impl, ...fast });
  const allowedPaths = ['/api/v2/mix/market/contracts', '/api/v2/spot/public/symbols', '/api/v2/mix/market/ticker', '/api/v2/spot/market/ticker'];
  assert.ok(impl.calls.length > 0);
  for (const call of impl.calls) {
    assert.ok(call.url.startsWith(`${DEFAULT_BASE_URL}/`), `unexpected host: ${call.url}`);
    assert.ok(allowedPaths.some((p) => call.url.includes(p)), `unexpected path: ${call.url}`);
    assert.equal(call.init.method, 'GET');
    const headerKeys = Object.keys(call.init.headers || {}).map((k) => k.toLowerCase());
    assert.ok(!headerKeys.includes('authorization'), 'auth header sent');
    assert.ok(!headerKeys.includes('access-key'), 'access-key header sent');
    assert.ok(!headerKeys.includes('sign'), 'sign header sent');
    assert.ok(!headerKeys.includes('passphrase'), 'passphrase header sent');
  }
});

// ---------------------------------------------------------------------------
// verifyInstruments / reporting / CLI pieces
// ---------------------------------------------------------------------------

test('verifyInstruments aggregates verified and unverified symbols', async () => {
  const routed = mockFetch({
    'contracts?productType=USDT-FUTURES&symbol=BTCUSDT': envelope([contractRow('BTCUSDT')]),
    'contracts?productType=USDT-FUTURES&symbol=XAUUSDT': envelope([contractRow('XAUUSDT', { symbolStatus: 'normal' })]),
    'ticker': envelope([{ lastPr: '1' }]),
  });
  const roles = new Map([
    ['BTCUSDT', { symbol: 'BTCUSDT', asset_class: 'crypto', display_name: 'Bitcoin' }],
    ['XAUUSDT', { symbol: 'XAUUSDT', asset_class: 'metal', display_name: 'Gold' }],
    ['AXTIUSDT', { symbol: 'AXTIUSDT', asset_class: 'commodity', display_name: 'WTI Oil (RWA proxy)' }],
  ]);
  const report = await verifyInstruments(['BTCUSDT', 'XAUUSDT', 'AXTIUSDT'], { fetchImpl: routed, manifestRoles: roles, ...fast });
  assert.equal(report.summary.total, 3);
  assert.deepEqual(report.summary.verified, ['BTCUSDT', 'XAUUSDT']);
  assert.deepEqual(report.summary.unverified, ['AXTIUSDT']);
  assert.deepEqual(report.contract_product_types_queried, [...CONTRACT_PRODUCT_TYPES]);
  assert.deepEqual(report.demo_namespaces_excluded, [...DEMO_PRODUCT_TYPES]);
  assert.equal(report.policy.symbol_mapping, 'exact match only; never invented');
  const btc = report.instruments[0];
  assert.equal(btc.asset_class_declared, 'crypto');
  assert.equal(btc.display_name_declared, 'Bitcoin');
  assert.equal(report.instruments[2].asset_class_declared, 'commodity');
});

test('buildMarkdown renders verified rows and bold UNVERIFIED markers', async () => {
  const impl = mockFetch({
    'contracts?productType=USDT-FUTURES&symbol=BTCUSDT': envelope([contractRow('BTCUSDT')]),
    'ticker': envelope([{ lastPr: '1' }]),
  });
  const report = await verifyInstruments(['BTCUSDT', 'AXTIUSDT'], { fetchImpl: impl, ...fast });
  const md = buildMarkdown(report);
  assert.match(md, /# Bitget Instrument Verification Report/);
  assert.match(md, /\| BTCUSDT \| VERIFIED \|/);
  assert.match(md, /\| AXTIUSDT \| \*\*UNVERIFIED\*\* \|/);
  assert.match(md, /USDT-FUTURES/);
  assert.match(md, /never invented/);
  assert.match(md, /Execution MUST remain disabled/);
});

test('writeReports produces timestamped JSON plus latest JSON and markdown', async () => {
  const impl = mockFetch({});
  const report = await verifyInstruments(['BTCUSDT'], { fetchImpl: impl, ...fast });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'verify-report-'));
  try {
    const written = writeReports(report, { outRoot: dir });
    assert.ok(fs.existsSync(written.jsonPath));
    assert.ok(fs.existsSync(written.latestJson));
    assert.ok(fs.existsSync(written.latestMd));
    const parsed = JSON.parse(fs.readFileSync(written.latestJson, 'utf8'));
    assert.equal(parsed.summary.total, 1);
    assert.equal(parsed.instruments[0].verified, false);
    assert.match(fs.readFileSync(written.latestMd, 'utf8'), /UNVERIFIED/);
    assert.match(path.basename(written.jsonPath), /^report-2026-09-22T09-30-00-000Z\.json$/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('parseArgs defaults are read-only-safe and flags are honored', () => {
  const defaults = parseArgs([]);
  assert.equal(defaults.baseUrl, DEFAULT_BASE_URL);
  assert.equal(defaults.checkTicker, true);
  assert.equal(defaults.symbols, null);
  const parsed = parseArgs(['--symbols', 'BTCUSDT, XAUUSDT', '--no-ticker', '--timeout', '5000', '--delay', '0', '--base-url', 'https://example.invalid']);
  assert.deepEqual(parsed.symbols, ['BTCUSDT', 'XAUUSDT']);
  assert.equal(parsed.checkTicker, false);
  assert.equal(parsed.timeoutMs, 5000);
  assert.equal(parsed.delayMs, 0);
  assert.equal(parsed.baseUrl, 'https://example.invalid');
});

test('demo-trading namespaces are never queried as production markets', async () => {
  const impl = mockFetch({});
  await verifySymbol('BTCUSDT', { fetchImpl: impl, ...fast });
  for (const call of impl.calls) {
    for (const demo of DEMO_PRODUCT_TYPES) {
      assert.ok(!call.url.includes(demo), `demo namespace queried: ${call.url}`);
    }
  }
});
