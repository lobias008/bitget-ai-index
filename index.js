/**
 * The Morning Sword - Playbook definition (inert module).
 *
 * SECURITY / SAFETY NOTES
 * ----------------------
 * - This file previously contained a hardcoded API key and auto-executed a
 *   compile -> backtest -> publish pipeline on import. Both were removed.
 * - Credentials now come ONLY from the environment (see `.env.example`).
 * - Importing this module has NO side effects. It performs no network calls,
 *   publishes nothing, and places no orders.
 * - The old code imported `@bitget-ai/getagent-skill` as a library and called
 *   `getagent.createPlaybook(...)`. That export DOES NOT EXIST: the installed
 *   package (v0.5.0) is a CommonJS skill-installer CLI with no `main`/`exports`
 *   entry point. The real control plane is the HTTP API documented under
 *   node_modules/@bitget-ai/getagent-skill/skills/getagent/references/api/.
 *   Publishing is handled by `scripts/publish-playbook.mjs`, which is gated.
 * - Symbols below were corrected to the ones that actually exist on Bitget.
 *   `OILUSDT` and `SPX500USDT` are not public Bitget contracts; the confirmed
 *   equivalents are `AXTIUSDT` (oil-linked proxy) and `SP500USDT`.
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** Fixed by the SDK docs. Deliberately NOT read from user-controlled env. */
export const GETAGENT_API_BASE_URL = 'https://api.bitget.com';

export const REQUIRED_ENV_VARS = Object.freeze([
  'BITGET_ACCESS_KEY',
  'ALLOW_PLAYBOOK_PUBLISH',
  'PUBLISH_CONFIRMATION_PHRASE',
]);

export const PUBLISH_CONFIRMATION_PHRASE = 'I_UNDERSTAND_THIS_PUBLISHES_PUBLICLY';

export const MARKETS = Object.freeze([
  {
    id: 'btc',
    symbol: 'BTCUSDT',
    label: 'Bitcoin',
    assetClass: 'crypto',
    adaptation: {
      stopLoss: '1.5x 4H ATR',
      filters: ['funding_rate_abs_pct < 0.05'],
    },
  },
  {
    id: 'xau',
    symbol: 'XAUUSDT',
    label: 'Gold',
    assetClass: 'metals',
    adaptation: {
      stopLoss: '1.5x 4H ATR',
      filters: ['trade_london_open', 'trade_new_york_open', 'skip_asian_session'],
    },
  },
  {
    id: 'oil',
    symbol: 'AXTIUSDT',
    label: 'WTI Oil (RWA proxy)',
    assetClass: 'commodities',
    adaptation: {
      stopLoss: '1.5x 4H ATR',
      filters: ['parabolic_sar_confirms_trend', 'pause_during_eia_inventory_event_window'],
    },
  },
  {
    id: 'spx',
    symbol: 'SP500USDT',
    label: 'S&P 500 Index',
    assetClass: 'stock_index',
    adaptation: {
      stopLoss: '1.5x 4H ATR',
      filters: ['daily_ema_50_bounce', 'earnings_gap_shield', 'macro_gap_shield'],
    },
  },
]);

export const getClawConfluenceRules = Object.freeze({
  doctrine: 'GetClaw multi-timeframe confluence',
  trendAndEntryBias: {
    longBiasTimeframe: '1D',
    longBiasCondition: 'EMA_20 > EMA_50 > EMA_200',
    entryTimeframe: '4H',
    entryTrigger: [
      'price_pulls_back_to_ema_20_or_ema_50',
      'volume_spike_confirms_participation',
      'rsi_between_40_and_60',
    ],
    shortBias: 'disabled_for_hackathon_release',
  },
  riskAndPositionSizing: {
    equityRiskPerTradePct: 1.0,
    stopDistance: '1.5x_4H_ATR',
    positionSizingFormula:
      'position_size = account_equity * 0.01 / abs(entry_price - stop_loss_price)',
    maxConcurrentExposure: 'one_active_trade_per_asset',
    portfolioIntent: 'asymmetric_yield_generation_with_controlled_downside',
  },
  partialProfitAndBreakevenLock: {
    firstTarget: '2R',
    harvestPctAtFirstTarget: 50,
    breakevenAction: 'move_stop_loss_to_entry_immediately_after_2R_fill',
    stateAfterBreakeven: 'FREE_TRADE',
    runnerPct: 50,
    trailingStop: 'Parabolic_SAR',
    runnerTarget: '4R_or_better',
  },
  circuitBreaker: {
    enabled: true,
    dailyEquityLossLimitPct: 2.0,
    triggerCondition: 'daily_realized_and_unrealized_equity_pnl_pct <= -2.0',
    breachActions: [
      'cancel_open_orders',
      'flatten_active_positions',
      'lock_new_execution_for_24_hours',
      'write_circuit_breaker_audit_event',
    ],
    lockoutHours: 24,
  },
});

export const assetSpecificAdaptations = Object.freeze({
  BTC: {
    symbol: 'BTCUSDT',
    rules: [
      'use_1_5x_4H_ATR_stop_loss',
      'allow_entries_only_when_abs_funding_rate_pct_less_than_0_05',
    ],
  },
  XAU: {
    symbol: 'XAUUSDT',
    rules: [
      'allow_entries_only_during_london_open_and_new_york_open',
      'skip_asian_session_entries',
    ],
  },
  OIL: {
    symbol: 'AXTIUSDT',
    rules: [
      'require_parabolic_sar_trend_confirmation',
      'pause_entries_during_eia_inventory_event_window',
    ],
  },
  SPX: {
    symbol: 'SP500USDT',
    rules: [
      'require_1D_EMA_50_bounce_filter',
      'block_entries_when_earnings_or_macro_gap_shield_is_active',
    ],
  },
});

/**
 * Metadata only. Contains NO credential. The access key is attached at call
 * time by `scripts/publish-playbook.mjs` and is never stored on this object,
 * never logged, and never serialized.
 */
export const playbookDefinition = Object.freeze({
  name: 'the-morning-sword',
  version: '1.0.0',
  track: 'Track 1 - Trading Agent',
  strategy: {
    codename: 'The Morning Sword',
    type: 'offensive_multi_asset_asymmetric_yield_generator',
    venue: 'Bitget Playbook',
    thesis:
      'Use GetClaw multi-timeframe confluence to attack only aligned, liquid pullbacks across crypto, gold, oil, and stock index exposure.',
    markets: MARKETS,
    rules: getClawConfluenceRules,
    assetSpecificAdaptations,
  },
  executionPlan: {
    scanCadence: '4H_close',
    signalValidation: [
      'confirm_1D_ema_stack',
      'confirm_4H_pullback_to_ema_20_or_ema_50',
      'confirm_volume_spike',
      'confirm_rsi_40_to_60',
      'apply_asset_specific_filters',
      'size_position_from_1pct_equity_risk_and_1_5x_ATR_stop',
      'place_bracket_order_with_2R_partial_and_4R_runner',
    ],
    orderManagement: [
      'take_50pct_profit_at_2R',
      'move_stop_loss_to_breakeven_after_first_target_fill',
      'trail_remaining_50pct_with_parabolic_sar',
      'respect_daily_negative_2pct_equity_circuit_breaker',
    ],
  },
  backtest: {
    enabled: false,
    mode: 'multi_asset_walk_forward',
    symbols: MARKETS.map((market) => market.symbol),
    note: 'Backtest is run server-side via POST /api/v1/playbook/run, not here.',
  },
  publish: {
    enabled: false,
    destination: 'Bitget Playbook ecosystem',
    visibility: 'private_until_explicitly_confirmed',
    requiresExplicitApproval: true,
    gatedBy: 'scripts/publish-playbook.mjs',
  },
});

/**
 * Resolve the credential from the environment. Never returns a literal,
 * never logs the value.
 */
export function resolveAccessKey(env = process.env) {
  const key = env.BITGET_ACCESS_KEY;
  if (!key || !String(key).trim()) {
    throw new Error(
      'BITGET_ACCESS_KEY is not set. Copy .env.example to .env and provide your ' +
        'Bitget OpenAPI ACCESS-KEY. It must never be hardcoded in source.',
    );
  }
  return String(key).trim();
}

/** Redact a key for safe display: shows length and a 4-char prefix only. */
export function maskAccessKey(key) {
  const value = String(key ?? '');
  if (!value) return '<unset>';
  return `${value.slice(0, 4)}...[REDACTED len=${value.length}]`;
}

/**
 * Gate every irreversible action. Returns a list of unmet requirements;
 * an empty list means the action is permitted.
 */
export function checkPublishGates(env = process.env) {
  const blockers = [];
  if (!env.BITGET_ACCESS_KEY) blockers.push('BITGET_ACCESS_KEY is not set');
  if (String(env.ALLOW_PLAYBOOK_PUBLISH).toLowerCase() !== 'true') {
    blockers.push('ALLOW_PLAYBOOK_PUBLISH must be exactly "true"');
  }
  if (env.PUBLISH_CONFIRMATION_PHRASE !== PUBLISH_CONFIRMATION_PHRASE) {
    blockers.push(
      `PUBLISH_CONFIRMATION_PHRASE must be exactly "${PUBLISH_CONFIRMATION_PHRASE}"`,
    );
  }
  return blockers;
}

/**
 * Running this file directly is intentionally inert: it prints a summary and
 * exits. It performs no network calls, publishes nothing, and trades nothing.
 */
function invokedDirectly() {
  if (!process.argv[1]) return false;
  try {
    return path.resolve(fileURLToPath(import.meta.url)) === path.resolve(process.argv[1]);
  } catch {
    return false;
  }
}

function main() {
  if (!invokedDirectly()) return;

  console.log('[the-morning-sword] Inert Playbook definition module.');
  console.log(`  name            : ${playbookDefinition.name}`);
  console.log(`  version         : ${playbookDefinition.version}`);
  console.log(`  markets         : ${MARKETS.map((m) => m.symbol).join(', ')}`);
  console.log(`  api base url    : ${GETAGENT_API_BASE_URL}`);
  console.log(`  access key      : ${maskAccessKey(process.env.BITGET_ACCESS_KEY)}`);
  console.log('  auto-publish    : DISABLED (publish.enabled = false)');
  console.log('');
  console.log('This module does nothing on its own by design.');
  console.log('  Safe local checks : npm run check && npm test && npm run validate');
  console.log('  Package only      : npm run package');
  console.log('  Publish (gated)   : npm run playbook:publish -- --dry-run');
  const blockers = checkPublishGates();
  if (blockers.length) {
    console.log('');
    console.log('Publish currently blocked by:');
    for (const blocker of blockers) console.log(`  - ${blocker}`);
  }
}

main();

