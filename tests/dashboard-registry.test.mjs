/**
 * Milestone 1 tests: the dashboard instrument registry export.
 *
 * Guarantees:
 *   - dashboard/src/data/instruments.json is generated from manifest.yaml
 *     through the validated Python registry and matches it exactly;
 *   - all seven platform sections exist, with empty sections marked
 *     "pending verification" instead of fabricated instruments;
 *   - only symbols with verified provenance can ever appear as verified,
 *     and verified_metadata is never emitted for unverified symbols;
 *   - no prices or market activity are fabricated anywhere in the export
 *     or the market components (the mock stream machinery is gone);
 *   - the export is deterministic and signal-only posture is preserved.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { ensureOutputDir, repoRoot, resolvePython, run } from '../scripts/_common.mjs';
import { checkRegistryJson, generateRegistryJson, OUTPUT_PATH } from '../scripts/generate-dashboard-registry.mjs';

const registryExport = JSON.parse(fs.readFileSync(OUTPUT_PATH, 'utf8'));
const manifestText = fs.readFileSync(path.join(repoRoot, 'manifest.yaml'), 'utf8');
const dashboardSrc = path.join(repoRoot, 'dashboard', 'src');

const SECTION_ORDER = ['crypto', 'rtoken', 'stock', 'cfd', 'commodity', 'metal', 'ai_index'];
const VERIFIED_PROVENANCE = new Set(['native_bitget_contract', 'documented_in_sdk_docs', 'verified_public_api']);
const ORIGINALS = ['BTCUSDT', 'XAUUSDT', 'AXTIUSDT', 'SP500USDT'];
const PRECISIONS = {
  BTCUSDT: [1, 4, 0.0001],
  XAUUSDT: [2, 2, 0.01],
  AXTIUSDT: [2, 2, 0.01],
  SP500USDT: [1, 4, 0.0001],
};
const FORBIDDEN_NUMERIC_KEYS = new Set(['price', 'change', 'volume', 'liquidity', 'volatility', 'sentiment', 'history']);

function pythonReady() {
  try {
    const candidate = resolvePython();
    return run(candidate, ['-c', 'import yaml']).status === 0 ? candidate : null;
  } catch {
    return null;
  }
}
const python = pythonReady();
const skipPython = python ? false : 'python + PyYAML unavailable';

function* walkFiles(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walkFiles(full);
    else if (/\.(jsx?|mjs)$/.test(entry.name)) yield full;
  }
}

/** Minimal tolerant parser for the manifest symbol_roles block (test-local). */
function parseManifestRoles(text) {
  const roles = new Map();
  const lines = String(text).split(/\r?\n/);
  const fields = ['asset_class', 'display_name', 'symbol_verification', 'strategy_eligibility', 'execution_availability', 'risk_profile', 'market_type', 'trading_session'];
  let blockIndent = null;
  let current = null;
  for (const raw of lines) {
    const line = raw.replace(/\t/g, '  ');
    const trimmed = line.trim();
    if (blockIndent === null) {
      const m = /^(\s*)symbol_roles:\s*(#.*)?$/.exec(line);
      if (m) blockIndent = m[1].length;
      continue;
    }
    if (trimmed === '' || trimmed.startsWith('#')) continue;
    const indent = line.length - line.trimStart().length;
    if (indent <= blockIndent) {
      blockIndent = null;
      current = null;
      continue;
    }
    if (indent === blockIndent + 2) {
      const m = /^([A-Za-z0-9_]+):\s*(#.*)?$/.exec(trimmed);
      current = m ? m[1] : null;
      if (current) roles.set(current, {});
      continue;
    }
    if (current) {
      const f = new RegExp(`^(${fields.join('|')}):\\s*"?([^"#]*)"?\\s*(#.*)?$`).exec(trimmed);
      if (f) roles.get(current)[f[1]] = f[2].trim();
    }
  }
  return roles;
}

test('export is valid, signal-only, and honest about live data', () => {
  assert.equal(registryExport.schema_version, 1);
  assert.equal(registryExport.execution_mode, 'signal_only');
  assert.equal(registryExport.live_prices, false);
  assert.equal(registryExport.policy.live_trading, 'disabled');
  assert.match(registryExport.source.sha256_prefix16, /^[0-9a-f]{16}$/, 'manifest fingerprint must be recorded');
});

test('all seven platform sections exist in registry order', () => {
  assert.deepEqual(registryExport.asset_classes.map((section) => section.name), SECTION_ORDER);
});

test('the four verified instruments carry the confirmed public-API metadata', () => {
  assert.deepEqual(registryExport.instruments.map((i) => i.symbol).sort(), [...ORIGINALS].sort());
  for (const instrument of registryExport.instruments) {
    assert.equal(instrument.verified, true, instrument.symbol);
    assert.equal(instrument.symbol_verification, 'verified_public_api', instrument.symbol);
    assert.equal(instrument.execution_availability, 'signal_only', instrument.symbol);
    assert.equal(instrument.strategy_eligibility, 'enabled', instrument.symbol);
    assert.equal(instrument.price, null, instrument.symbol);
    const meta = instrument.verified_metadata;
    assert.ok(meta, `${instrument.symbol} must carry verified_metadata`);
    assert.equal(meta.product_type, 'USDT-FUTURES', instrument.symbol);
    assert.equal(meta.trading_status, 'normal', instrument.symbol);
    assert.equal(meta.market_data_available, true, instrument.symbol);
    const [pricePrec, qtyPrec, minOrder] = PRECISIONS[instrument.symbol];
    assert.equal(meta.price_precision, pricePrec, instrument.symbol);
    assert.equal(meta.quantity_precision, qtyPrec, instrument.symbol);
    assert.equal(Number(meta.min_order_size), minOrder, instrument.symbol);
    assert.ok(meta.verified_at, `${instrument.symbol} must record verified_at`);
  }
});

test('unverified instruments can never appear as verified', () => {
  for (const instrument of registryExport.instruments) {
    assert.equal(instrument.verified, VERIFIED_PROVENANCE.has(instrument.symbol_verification), instrument.symbol);
    if (!instrument.verified) {
      assert.equal(instrument.verified_metadata, null, `${instrument.symbol} must not carry verified_metadata`);
    }
  }
});

test('sections without instruments are pending verification, never fabricated', () => {
  for (const section of registryExport.asset_classes) {
    if (section.symbol_count === 0) {
      assert.deepEqual(section.symbols, [], section.name);
      assert.equal(section.pending_verification, true, section.name);
      assert.equal(section.live_trading_supported, false, section.name);
      assert.equal(section.default_execution_availability, 'unavailable', section.name);
    } else {
      assert.equal(section.pending_verification, false, section.name);
    }
  }
  for (const name of ['rtoken', 'cfd', 'ai_index']) {
    const section = registryExport.asset_classes.find((candidate) => candidate.name === name);
    assert.equal(section.symbol_count, 0, `${name} must have zero instruments`);
  }
});

test('no fabricated market data anywhere in the export', () => {
  const offenders = [];
  const walk = (node, keyPath) => {
    if (Array.isArray(node)) {
      node.forEach((item) => walk(item, keyPath));
      return;
    }
    if (node && typeof node === 'object') {
      for (const [key, value] of Object.entries(node)) {
        if (FORBIDDEN_NUMERIC_KEYS.has(key) && value !== null) offenders.push(`${keyPath}.${key}`);
        walk(value, `${keyPath}.${key}`);
      }
    }
  };
  walk(registryExport, 'export');
  assert.deepEqual(offenders, [], 'export must not contain fabricated market numbers');
});

test('generated registry matches manifest.yaml symbol_roles', () => {
  const roles = parseManifestRoles(manifestText);
  assert.deepEqual([...roles.keys()].sort(), registryExport.instruments.map((i) => i.symbol).sort());
  for (const instrument of registryExport.instruments) {
    const role = roles.get(instrument.symbol);
    for (const field of ['asset_class', 'display_name', 'symbol_verification', 'strategy_eligibility', 'execution_availability', 'risk_profile', 'market_type', 'trading_session']) {
      assert.equal(instrument[field], role[field], `${instrument.symbol}.${field}`);
    }
  }
});

test('mock market machinery is gone from the dashboard', () => {
  for (const dead of ['data/mockAssets.js', 'lib/mockStream.js', 'hooks/useMockMarketStream.js', 'components/market/Sparkline.jsx', 'components/market/SentimentMeter.jsx']) {
    assert.ok(!fs.existsSync(path.join(dashboardSrc, dead)), `${dead} must be deleted`);
  }
  const marketDirs = ['components/market', 'data', 'hooks'];
  for (const dir of marketDirs) {
    for (const file of walkFiles(path.join(dashboardSrc, dir))) {
      const text = fs.readFileSync(file, 'utf8');
      assert.ok(!/Math\.random|setInterval/.test(text), `${path.relative(dashboardSrc, file)} must not fabricate market activity`);
      assert.ok(!/mockAssets|mockStream|useMockMarketStream|Sparkline|SentimentMeter/.test(text), `${path.relative(dashboardSrc, file)} references removed mock modules`);
    }
  }
});

test('dashboard renders the registry export, not mocks', () => {
  const app = fs.readFileSync(path.join(dashboardSrc, 'App.jsx'), 'utf8');
  assert.match(app, /from "\.\/data\/instruments\.json"/);
  const matrix = fs.readFileSync(path.join(dashboardSrc, 'components', 'market', 'AssetMatrix.jsx'), 'utf8');
  assert.match(matrix, /registry\.asset_classes/);
  assert.match(matrix, /pendingVerification/);
  const row = fs.readFileSync(path.join(dashboardSrc, 'components', 'market', 'AssetRow.jsx'), 'utf8');
  assert.match(row, /verified_metadata/);
  assert.match(row, /pricesPending/);
});

test('translations explain pending sections and missing prices in both languages', () => {
  const translations = fs.readFileSync(path.join(dashboardSrc, 'data', 'translations.js'), 'utf8');
  for (const key of ['pendingVerification', 'pendingVerificationBody', 'pricesPending', 'verifiedBadge', 'unverifiedBadge', 'productType', 'tradingStatus', 'executionMode']) {
    const occurrences = translations.split(`${key}:`).length - 1;
    assert.ok(occurrences >= 2, `${key} must exist in en and zh`);
  }
  assert.match(translations, /Live data coming next/);
});

test('export is deterministic and the committed file has not drifted', { skip: skipPython }, () => {
  const first = generateRegistryJson({ python });
  const second = generateRegistryJson({ python });
  assert.equal(first, second, 'generator must be deterministic');
  const verdict = checkRegistryJson({ python });
  assert.ok(verdict.ok, `instruments.json ${verdict.reason}: run 'npm run dashboard:registry'`);
});

test('a tampered manifest cannot smuggle an unverified symbol in as verified', { skip: skipPython }, () => {
  const tampered = manifestText.replace(
    'symbol_verification: verified_public_api',
    'symbol_verification: inherited_unverified',
  );
  assert.notEqual(tampered, manifestText, 'manifest must contain verified_public_api to tamper with');
  const dir = ensureOutputDir();
  const tamperPath = path.join(dir, 'tamper-manifest.yaml');
  fs.writeFileSync(tamperPath, tampered, 'utf8');
  try {
    const payload = JSON.parse(generateRegistryJson({ manifestPath: tamperPath, python }));
    const btc = payload.instruments.find((instrument) => instrument.symbol === 'BTCUSDT');
    assert.equal(btc.verified, false, 'downgraded provenance must export as unverified');
    assert.equal(btc.verified_metadata, null, 'unverified symbols must not carry verified_metadata');
  } finally {
    fs.rmSync(tamperPath, { force: true });
  }
});
