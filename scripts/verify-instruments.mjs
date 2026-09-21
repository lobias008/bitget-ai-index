/**
 * Read-only Bitget instrument verification.
 *
 * Safety contract (enforced by tests/verify-instruments.test.mjs):
 *   - ONLY public market-config endpoints are used. No auth headers, no API
 *     keys, no account data, no balances, no orders, no mutations. GET only.
 *   - Safe to rerun at any time; each run writes a fresh timestamped report.
 *   - Symbols are matched EXACTLY against what the API returns. This script
 *     never invents, derives, or "corrects" symbol mappings. A symbol that no
 *     documented endpoint returns is reported UNVERIFIED and stays disabled.
 *
 * Endpoints (verified against the installed @bitget-ai/getagent-skill docs and
 * the Bitget public API v2 documentation):
 *   - Contracts config:
 *       GET /api/v2/mix/market/contracts?productType=<PT>&symbol=<SYM>
 *       listed when the row has symbolStatus === "normal".
 *       Real-market productType enum: USDT-FUTURES, COIN-FUTURES, USDC-FUTURES.
 *       SUSDT-FUTURES / SCOIN-FUTURES / SUSDC-FUTURES are Bitget DEMO-trading
 *       namespaces; they are deliberately NOT treated as production markets.
 *   - Spot config:
 *       GET /api/v2/spot/public/symbols?symbol=<SYM>
 *       listed when the row has status === "online".
 *   - Market-data probes (only for symbols already found in a config API):
 *       GET /api/v2/mix/market/ticker?symbol=<SYM>&productType=<PT>
 *       GET /api/v2/spot/market/ticker?symbol=<SYM>
 *
 * Execution-API availability is NEVER claimed from public data: it requires
 * the managed Playbook runtime (trade.market.check_symbol_support) against a
 * bound account, which this script must not touch. It is recorded as false.
 *
 * Usage:
 *   node scripts/verify-instruments.mjs                 # verify manifest symbols
 *   node scripts/verify-instruments.mjs --symbols BTCUSDT,XAUUSDT
 *   node scripts/verify-instruments.mjs --no-ticker     # skip market-data probe
 *
 * Output (under gitignored output/):
 *   output/instrument-verification/report-<UTC stamp>.json
 *   output/instrument-verification/report-latest.json
 *   output/instrument-verification/report-latest.md
 */
import fs from 'node:fs';
import path from 'node:path';

import { ensureOutputDir, isMainModule, repoRoot } from './_common.mjs';

export const DEFAULT_BASE_URL = 'https://api.bitget.com';
export const CONTRACT_PRODUCT_TYPES = Object.freeze(['USDT-FUTURES', 'COIN-FUTURES', 'USDC-FUTURES']);
export const DEMO_PRODUCT_TYPES = Object.freeze(['SUSDT-FUTURES', 'SCOIN-FUTURES', 'SUSDC-FUTURES']);
export const CONTRACT_LISTED_STATUS = 'normal';
export const SPOT_LISTED_STATUS = 'online';
export const BITGET_SUCCESS_CODE = '00000';
export const DEFAULT_TIMEOUT_MS = 15000;
export const DEFAULT_REQUEST_DELAY_MS = 250;
export const USER_AGENT = 'instrument-verification/1.0 (read-only public market data; no auth)';

/**
 * Parse the symbol_roles keys (plus declared asset_class / display_name) out
 * of manifest.yaml without a YAML dependency. Tolerant line scanner; returns
 * a Map<symbol, { symbol, asset_class, display_name }>.
 */
export function parseSymbolRoles(manifestText) {
  const roles = new Map();
  const lines = String(manifestText).split(/\r?\n/);
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
      if (current && !roles.has(current)) {
        roles.set(current, { symbol: current, asset_class: null, display_name: null });
      }
      continue;
    }
    if (current) {
      const f = /^(asset_class|display_name):\s*"?([^"#]*)"?\s*(#.*)?$/.exec(trimmed);
      if (f) roles.get(current)[f[1]] = f[2].trim();
    }
  }
  return roles;
}

/** GET a URL and return the raw outcome. Never throws; network errors are data. */
export async function fetchJson(url, { fetchImpl, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const impl = fetchImpl ?? globalThis.fetch;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await impl(url, {
      method: 'GET',
      headers: { 'User-Agent': USER_AGENT, Accept: 'application/json' },
      signal: controller.signal,
    });
    const text = await res.text();
    let body = null;
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
    return { url, http_status: res.status, ok: res.ok === true, body, parse_error: body === null };
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    return { url, http_status: null, ok: false, body: null, parse_error: false, error: message };
  } finally {
    clearTimeout(timer);
  }
}

/** Unwrap the Bitget { code, msg, data } envelope into rows. Never throws. */
export function envelopeRows(result) {
  if (result.error) return { rows: null, error: `request failed: ${result.error}` };
  if (!result.ok) return { rows: null, error: `HTTP ${result.http_status}` };
  if (result.parse_error || !result.body || typeof result.body !== 'object') {
    return { rows: null, error: 'non-JSON response' };
  }
  const code = result.body.code == null ? null : String(result.body.code);
  if (code !== BITGET_SUCCESS_CODE) {
    return { rows: null, error: `API code ${code}: ${result.body.msg || 'unknown error'}` };
  }
  const data = result.body.data;
  if (Array.isArray(data)) return { rows: data, error: null };
  if (data && typeof data === 'object') return { rows: [data], error: null };
  if (data == null) return { rows: [], error: null };
  return { rows: null, error: 'unexpected data shape' };
}

function toNumberOrNull(value) {
  if (value == null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * Verify ONE requested symbol against every documented namespace.
 * Returns a full record; never throws for network/API problems.
 */
export async function verifySymbol(requested, options = {}) {
  const {
    fetchImpl,
    baseUrl = DEFAULT_BASE_URL,
    productTypes = CONTRACT_PRODUCT_TYPES,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    delayMs = DEFAULT_REQUEST_DELAY_MS,
    checkTicker = true,
    declared = {},
    now = () => new Date(),
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  } = options;

  const checks = [];
  const endpoints = [];
  let contractHit = null;
  let spotHit = null;

  for (const productType of productTypes) {
    const url = `${baseUrl}/api/v2/mix/market/contracts?productType=${encodeURIComponent(productType)}&symbol=${encodeURIComponent(requested)}`;
    const result = await fetchJson(url, { fetchImpl, timeoutMs });
    const { rows, error } = envelopeRows(result);
    // Exact match only. Rows for other symbols are NEVER treated as a hit.
    const exact = (rows || []).filter((row) => row && row.symbol === requested);
    checks.push({
      market: 'contract',
      product_type: productType,
      endpoint: url,
      http_status: result.http_status,
      error,
      rows_returned: rows ? rows.length : null,
      exact_matches: exact.length,
    });
    endpoints.push(url);
    if (exact.length > 0 && !contractHit) {
      contractHit = { row: exact[0], productType, url };
    }
    if (delayMs) await sleep(delayMs);
  }

  const spotUrl = `${baseUrl}/api/v2/spot/public/symbols?symbol=${encodeURIComponent(requested)}`;
  const spotResult = await fetchJson(spotUrl, { fetchImpl, timeoutMs });
  const spotParsed = envelopeRows(spotResult);
  const spotExact = (spotParsed.rows || []).filter((row) => row && row.symbol === requested);
  checks.push({
    market: 'spot',
    product_type: 'SPOT',
    endpoint: spotUrl,
    http_status: spotResult.http_status,
    error: spotParsed.error,
    rows_returned: spotParsed.rows ? spotParsed.rows.length : null,
    exact_matches: spotExact.length,
  });
  endpoints.push(spotUrl);
  if (spotExact.length > 0) spotHit = { row: spotExact[0], url: spotUrl };
  if (delayMs) await sleep(delayMs);

  const contractListed = Boolean(contractHit && contractHit.row.symbolStatus === CONTRACT_LISTED_STATUS);
  const spotListed = Boolean(spotHit && spotHit.row.status === SPOT_LISTED_STATUS);
  const verified = contractListed || spotListed;

  // Market-data availability probe (ticker) only where config says listed.
  let marketData = { available: false, endpoint: null, note: 'not probed' };
  if (!checkTicker) {
    marketData.note = 'not probed (--no-ticker)';
  } else if (!contractListed && !spotListed) {
    marketData.note = 'not probed (symbol not listed in any config API)';
  }
  if (checkTicker && (contractListed || spotListed)) {
    const url = contractListed
      ? `${baseUrl}/api/v2/mix/market/ticker?symbol=${encodeURIComponent(requested)}&productType=${encodeURIComponent(contractHit.productType)}`
      : `${baseUrl}/api/v2/spot/market/ticker?symbol=${encodeURIComponent(requested)}`;
    const result = await fetchJson(url, { fetchImpl, timeoutMs });
    const { rows, error } = envelopeRows(result);
    const available = !error && Array.isArray(rows) && rows.length > 0;
    marketData = {
      available,
      endpoint: url,
      note: error || (available ? 'ticker returned live data' : 'ticker returned empty data'),
    };
    checks.push({
      market: contractListed ? 'contract' : 'spot',
      product_type: contractListed ? contractHit.productType : 'SPOT',
      probe: 'ticker',
      endpoint: url,
      http_status: result.http_status,
      error,
      rows_returned: rows ? rows.length : null,
    });
    endpoints.push(url);
  }

  const primary = contractHit || spotHit || null;
  const row = primary ? primary.row : null;
  const isContract = Boolean(contractHit);

  let tradingStatus = 'not_listed';
  if (row) tradingStatus = isContract ? (row.symbolStatus ?? 'unknown') : (row.status ?? 'unknown');

  const record = {
    requested_symbol: requested,
    verified,
    verified_symbol: verified ? requested : null,
    market: primary ? (isContract ? 'contract' : 'spot') : null,
    product_type: primary ? (isContract ? contractHit.productType : 'SPOT') : null,
    asset_class_declared: declared.asset_class || null,
    display_name_declared: declared.display_name || null,
    trading_status: tradingStatus,
    price_precision: row ? toNumberOrNull(isContract ? row.pricePlace : row.priceScale) : null,
    quantity_precision: row ? toNumberOrNull(isContract ? row.volumePlace : row.quantityScale) : null,
    min_order_size: row ? (isContract ? (row.minTradeNum ?? null) : (row.minTradeAmount ?? null)) : null,
    size_multiplier: isContract && row ? (row.sizeMultiplier ?? null) : null,
    base_coin: row ? (row.baseCoin ?? null) : null,
    quote_coin: row ? (row.quoteCoin ?? null) : null,
    market_data_available: marketData.available,
    market_data_endpoint: marketData.endpoint,
    market_data_note: marketData.note,
    execution_api_available: false,
    execution_api_note:
      'Not verifiable from public endpoints; requires trade.market.check_symbol_support ' +
      'in the managed Playbook runtime against a bound account. Execution stays disabled.',
    verification_timestamp: (typeof now === 'function' ? now() : now).toISOString(),
    source_endpoints: endpoints,
    checks,
  };
  if (!verified) {
    const listedButBlocked = Boolean(primary) && !verified;
    record.unverified_reason = listedButBlocked
      ? `found in ${record.product_type} but trading_status=${tradingStatus} (not ${isContract ? CONTRACT_LISTED_STATUS : SPOT_LISTED_STATUS})`
      : 'symbol not returned by any queried Bitget public config endpoint';
    record.unverified_reason += '. Execution MUST remain disabled.';
  }
  return record;
}

/** Verify a list of symbols sequentially (polite rate) and build the report object. */
export async function verifyInstruments(symbols, options = {}) {
  const { manifestRoles = new Map(), now = () => new Date(), onProgress = null, ...rest } = options;
  const instruments = [];
  for (const symbol of symbols) {
    if (onProgress) onProgress(`verifying ${symbol} ...`);
    instruments.push(await verifySymbol(symbol, { ...rest, declared: manifestRoles.get(symbol) || {}, now }));
  }
  return {
    generated_at: (typeof now === 'function' ? now() : now).toISOString(),
    base_url: rest.baseUrl ?? DEFAULT_BASE_URL,
    contract_product_types_queried: [...(rest.productTypes ?? CONTRACT_PRODUCT_TYPES)],
    spot_queried: true,
    demo_namespaces_excluded: [...DEMO_PRODUCT_TYPES],
    policy: {
      access: 'public endpoints only; no auth, no account data, no orders',
      symbol_mapping: 'exact match only; never invented',
      execution_api: 'never claimed from public data; stays disabled',
      rerun: 'safe; read-only',
    },
    instruments,
    summary: {
      total: instruments.length,
      verified: instruments.filter((i) => i.verified).map((i) => i.requested_symbol),
      unverified: instruments.filter((i) => !i.verified).map((i) => i.requested_symbol),
    },
  };
}

/** Render the human-readable markdown report. Pure function; fully testable. */
export function buildMarkdown(report) {
  const lines = [];
  lines.push('# Bitget Instrument Verification Report');
  lines.push('');
  lines.push(`Generated: ${report.generated_at}`);
  lines.push(`Base URL: ${report.base_url}`);
  lines.push(`Contract product types queried: ${report.contract_product_types_queried.join(', ')}`);
  lines.push(`Demo namespaces excluded: ${report.demo_namespaces_excluded.join(', ')}`);
  lines.push('Access: read-only public endpoints. No auth, no account data, no orders.');
  lines.push('');
  lines.push('## Summary');
  lines.push('');
  lines.push(`- Verified: ${report.summary.verified.length ? report.summary.verified.join(', ') : '(none)'}`);
  lines.push(`- UNVERIFIED: ${report.summary.unverified.length ? report.summary.unverified.join(', ') : '(none)'}`);
  lines.push('');
  lines.push('## Instruments');
  lines.push('');
  lines.push('| Requested | Result | Verified symbol | Market | Product type | Asset class (declared) | Trading status | Price prec | Qty prec | Min order size | Market data | Execution API | Verified at (UTC) |');
  lines.push('|---|---|---|---|---|---|---|---|---|---|---|---|---|');
  for (const i of report.instruments) {
    lines.push(`| ${[
      i.requested_symbol,
      i.verified ? 'VERIFIED' : '**UNVERIFIED**',
      i.verified_symbol ?? '-',
      i.market ?? '-',
      i.product_type ?? '-',
      i.asset_class_declared ?? '-',
      i.trading_status,
      i.price_precision ?? '-',
      i.quantity_precision ?? '-',
      i.min_order_size ?? '-',
      i.market_data_available ? 'yes' : `no (${i.market_data_note})`,
      i.execution_api_available ? 'yes' : 'disabled (not publicly verifiable)',
      i.verification_timestamp,
    ].join(' | ')} |`);
  }
  lines.push('');
  lines.push('## Source endpoints');
  lines.push('');
  for (const i of report.instruments) {
    lines.push(`### ${i.requested_symbol}`);
    for (const url of i.source_endpoints) lines.push(`- ${url}`);
    if (i.unverified_reason) lines.push(`- Reason: ${i.unverified_reason}`);
    lines.push('');
  }
  lines.push('## Policy notes');
  lines.push('');
  lines.push('- Symbol mappings are never invented: only exact API matches count.');
  lines.push('- UNVERIFIED symbols keep execution disabled until confirmed.');
  lines.push('- Execution-API availability requires the managed Playbook runtime and a bound account; it is never claimed from public data.');
  lines.push('');
  return lines.join('\n');
}

/** Write the JSON + markdown reports under output/instrument-verification/. */
export function writeReports(report, { outRoot = null } = {}) {
  const base = outRoot ?? path.join(ensureOutputDir(), 'instrument-verification');
  fs.mkdirSync(base, { recursive: true });
  const stamp = report.generated_at.replace(/[:.]/g, '-');
  const jsonPath = path.join(base, `report-${stamp}.json`);
  const latestJson = path.join(base, 'report-latest.json');
  const latestMd = path.join(base, 'report-latest.md');
  const markdown = buildMarkdown(report);
  fs.writeFileSync(jsonPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  fs.writeFileSync(latestJson, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  fs.writeFileSync(latestMd, markdown, 'utf8');
  return { jsonPath, latestJson, latestMd, markdown };
}

export function parseArgs(argv) {
  const args = { symbols: null, baseUrl: DEFAULT_BASE_URL, checkTicker: true, timeoutMs: DEFAULT_TIMEOUT_MS, delayMs: DEFAULT_REQUEST_DELAY_MS };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === '--symbols') {
      args.symbols = String(argv[++i] ?? '').split(',').map((s) => s.trim()).filter(Boolean);
    } else if (token === '--base-url') {
      args.baseUrl = String(argv[++i] ?? DEFAULT_BASE_URL);
    } else if (token === '--timeout') {
      args.timeoutMs = Number(argv[++i]) || DEFAULT_TIMEOUT_MS;
    } else if (token === '--delay') {
      args.delayMs = Number(argv[++i] ?? 0);
    } else if (token === '--no-ticker') {
      args.checkTicker = false;
    }
  }
  return args;
}

export async function main(argv = []) {
  const args = parseArgs(argv);
  const manifestPath = path.join(repoRoot, 'manifest.yaml');
  let symbols = args.symbols;
  let manifestRoles = new Map();
  if (fs.existsSync(manifestPath)) {
    manifestRoles = parseSymbolRoles(fs.readFileSync(manifestPath, 'utf8'));
  }
  if (!symbols || symbols.length === 0) {
    symbols = [...manifestRoles.keys()];
  }
  if (symbols.length === 0) {
    console.error('[verify] no symbols to verify (manifest.yaml has no symbol_roles and --symbols was empty)');
    return 1;
  }
  console.log(`[verify] read-only verification of ${symbols.length} symbol(s) against ${args.baseUrl}`);
  const report = await verifyInstruments(symbols, {
    manifestRoles,
    baseUrl: args.baseUrl,
    timeoutMs: args.timeoutMs,
    delayMs: args.delayMs,
    checkTicker: args.checkTicker,
    onProgress: (msg) => console.log(`[verify] ${msg}`),
  });
  const written = writeReports(report);
  console.log('');
  console.log(written.markdown);
  console.log(`[verify] JSON report:  ${written.jsonPath}`);
  console.log(`[verify] latest JSON:  ${written.latestJson}`);
  console.log(`[verify] latest markdown: ${written.latestMd}`);
  console.log(`[verify] verified: ${report.summary.verified.length}/${report.summary.total}` +
    (report.summary.unverified.length ? ` | UNVERIFIED: ${report.summary.unverified.join(', ')}` : ''));
  return 0;
}

if (isMainModule(import.meta.url)) {
  main(process.argv.slice(2))
    .then((code) => { process.exitCode = code; })
    .catch((err) => {
      console.error(`[verify] fatal: ${err && err.message ? err.message : err}`);
      process.exitCode = 1;
    });
}
