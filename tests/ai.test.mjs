/**
 * Milestone 3 - AI-assisted paper-trading guards.
 *
 * Structural/offline tests in the same spirit as tests/paper.test.mjs: they
 * read the shipped sources rather than executing a run, so they pass before
 * `npm install` and prove the invariants that matter for this milestone:
 *
 *   1. the AI layer is veto-only - it can remove a deterministic signal but
 *      contains no order, account or private-API plumbing at all;
 *   2. its only network module may POST to two allowlisted HTTPS hosts;
 *   3. credentials stay in the environment and never reach a prompt, an
 *      artifact, or a committed file;
 *   4. generated AI artifacts stay out of git;
 *   5. the dashboard presents the reviewer as PAPER / SIMULATED and veto-only,
 *      never fabricates a price, and never polls on a timer.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const repoRoot = path.resolve(import.meta.dirname, '..');
const read = (...parts) => fs.readFileSync(path.join(repoRoot, ...parts), 'utf8');
const exists = (...parts) => fs.existsSync(path.join(repoRoot, ...parts));

const pkg = JSON.parse(read('package.json'));

const AI_MODULES = [
  '__init__.py',
  'advisor.py',
  'cli.py',
  'config.py',
  'prompt.py',
  'providers.py',
  'replay.py',
  'schema.py',
];

const AI_SCRIPTS = {
  'ai:review': 'node scripts/run-ai.mjs review',
  'ai:status': 'node scripts/run-ai.mjs status',
};

const AI_ENV_NAMES = [
  'AI_PROVIDER',
  'AI_MODEL',
  'AI_BASE_URL',
  'AI_TIMEOUT_S',
  'AI_MAX_CALLS',
  'AI_WATCH_SAMPLE',
  'AI_TEMPERATURE',
  'OPENROUTER_API_KEY',
  'CLOUDFLARE_ACCOUNT_ID',
  'CLOUDFLARE_AI_TOKEN',
];

const AI_TRANSLATION_KEYS = [
  'aiTitle', 'aiBadge', 'aiRoleVetoOnly', 'aiLoading', 'aiMissing', 'aiError',
  'aiReload', 'aiProvider', 'aiModel', 'aiSynthetic', 'aiCallsUsed', 'aiMaxCalls',
  'aiWatchSample', 'aiOutcomes', 'aiRecentReviews', 'aiNoReviews', 'aiRecentFills',
  'aiNoFills', 'aiDecision', 'aiOutcome', 'aiConfidence', 'aiReasoning',
  'aiRiskNotes', 'aiReasonCode', 'aiSimulatorOutcome', 'aiEntryPrice', 'aiQuantity',
  'aiDisclaimer', 'aiNotLive', 'aiLastUpdated', 'aiDataSource', 'aiPurposeEntryGate',
  'aiPurposeWatchSample',
];

const PROVIDERS = 'providers.py';

// Anything that would let the AI layer touch an account or place an order.
// It must not merely avoid calling these - the plumbing must not exist.
const ORDER_RE =
  /place-order|place_order|placeOrder|\/api\/v2\/mix\/order|\/api\/v2\/mix\/account|\/api\/v2\/spot\/trade|ACCESS-KEY|ACCESS-SIGN|ACCESS-TIMESTAMP|passphrase|api_secret|API_SECRET|BITGET_API|requests\.post|api\.bitget\.com|subprocess/i;

const NETWORK_IMPORT_RE =
  /^[ \t]*(?:import|from)[ \t]+(?:urllib|socket|http\.client|requests|aiohttp|httpx|websocket)\b/m;

const DANGEROUS_SCRIPT_RE = /publish|deploy|upload|playbook|order/i;

function aiSources() {
  return Object.fromEntries(
    AI_MODULES.map((name) => [name, read('ai_advisor', name)]),
  );
}

test('the ai_advisor package ships every module the CLI needs', () => {
  for (const name of AI_MODULES) {
    assert.ok(exists('ai_advisor', name), `ai_advisor/${name} is missing`);
    assert.ok(read('ai_advisor', name).trim().length > 200, `ai_advisor/${name} is empty`);
  }
  const shipped = fs
    .readdirSync(path.join(repoRoot, 'ai_advisor'))
    .filter((entry) => entry.endsWith('.py'))
    .sort();
  assert.deepEqual(shipped, [...AI_MODULES].sort());
});

test('no python bytecode is shipped alongside the AI layer', () => {
  assert.ok(!exists('ai_advisor', '__pycache__'), 'ai_advisor/__pycache__ must not exist');
  const stray = fs
    .readdirSync(path.join(repoRoot, 'ai_advisor'))
    .filter((entry) => entry.endsWith('.pyc'));
  assert.deepEqual(stray, []);
});

test('npm exposes exactly two AI scripts and both wrap the python CLI', () => {
  for (const [name, command] of Object.entries(AI_SCRIPTS)) {
    assert.equal(pkg.scripts[name], command, `${name} must be "${command}"`);
  }
});

test('no AI script can publish, deploy, upload or order', () => {
  for (const [name, command] of Object.entries(AI_SCRIPTS)) {
    assert.ok(!DANGEROUS_SCRIPT_RE.test(name), `${name} is a dangerous script name`);
    assert.ok(!DANGEROUS_SCRIPT_RE.test(command), `${name} runs a dangerous command`);
  }
  // The publish surface must stay exactly as gated as before this milestone.
  const publishing = Object.keys(pkg.scripts).filter((name) => /publish/i.test(name));
  assert.deepEqual(publishing, ['playbook:publish']);
});

test('the AI runner only invokes the AI CLI', () => {
  const source = read('scripts', 'run-ai.mjs');
  assert.match(source, /'-m', 'ai_advisor\.cli'/);
  assert.match(source, /PYTHONDONTWRITEBYTECODE: '1'/);
  for (const token of ['publish-playbook', 'playbook:publish', 'package-playbook', 'urlopen', 'fetch(']) {
    assert.ok(!source.includes(token), `run-ai.mjs must not reference ${token}`);
  }
});

test('the python syntax stage of npm run check covers ai_advisor', () => {
  const source = read('scripts', 'check.mjs');
  assert.match(source, /'src', 'paper', 'ai_advisor', path\.join\('tests', 'python'\)/);
});

test('the AI layer contains no order or private-API plumbing', () => {
  for (const [name, source] of Object.entries(aiSources())) {
    const match = ORDER_RE.exec(source);
    assert.equal(match, null, `ai_advisor/${name} contains ${match && match[0]}`);
  }
});

test('only providers.py may import a network stack', () => {
  for (const [name, source] of Object.entries(aiSources())) {
    if (name === PROVIDERS) continue;
    assert.ok(
      !NETWORK_IMPORT_RE.test(source),
      `ai_advisor/${name} imports a network stack`,
    );
  }
  const providers = aiSources()[PROVIDERS];
  assert.match(providers, /^import urllib\.request$/m);
  assert.match(providers, /^import urllib\.error$/m);
  assert.match(providers, /method="POST"/);
  assert.ok(!/^import socket$/m.test(providers), 'providers.py must not open raw sockets');
  assert.ok(!providers.includes('http.client'), 'providers.py must not use http.client');
});

test('inference is limited to two allowlisted HTTPS hosts', () => {
  const providers = aiSources()[PROVIDERS];
  assert.match(
    providers,
    /ALLOWED_AI_URL_PREFIXES = \("https:\/\/openrouter\.ai\/", "https:\/\/api\.cloudflare\.com\/"\)/,
  );
  assert.match(providers, /def _assert_allowed_url\(/);
  assert.match(providers, /_assert_allowed_url\(url\)/);
  assert.equal(
    providers.split('_assert_allowed_url(self.endpoint)').length - 1,
    2,
    'both hosted providers must re-check the allowlist at request time',
  );
  assert.ok(!providers.includes('http://'), 'no plaintext HTTP endpoint may appear');
});

test('provider errors report the HTTP status and never a response body', () => {
  const providers = aiSources()[PROVIDERS];
  assert.match(providers, /AI provider returned HTTP \{exc\.code\}/);
  assert.ok(!providers.includes('exc.read()'), 'an error body must never be read');
  assert.match(providers, /Deliberately no body/);
});

test('no credential is ever assigned a literal value in the AI layer', () => {
  const literal =
    /\b(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret|auth[_-]?token|password|passwd|pwd|token)\b\s*[:=]\s*['"][^'"]{12,}['"]/gi;
  for (const [name, source] of Object.entries(aiSources())) {
    assert.equal(literal.exec(source), null, `ai_advisor/${name} hardcodes a credential`);
    literal.lastIndex = 0;
  }
});

test('the AI layer never mutates the process environment', () => {
  for (const [name, source] of Object.entries(aiSources())) {
    assert.ok(
      !/os\.environ\s*\[[^\]]+\]\s*=/.test(source),
      `ai_advisor/${name} writes to os.environ`,
    );
    assert.ok(!source.includes('os.putenv'), `ai_advisor/${name} calls os.putenv`);
  }
});

test('the veto hook is the single insertion point and defaults to off', () => {
  const signals = read('paper', 'signals.py');
  assert.match(signals, /signal_filter=None/);
  assert.equal(
    signals.split('if signal_filter is not None and actionable:').length - 1,
    1,
    'the filter must be applied at exactly one place',
  );
  assert.match(signals, /actionable = list\(signal_filter\(decision_ms, actionable, closed_bars\)\)/);
  // all_signals is recorded before the hook runs, so a veto never hides a setup.
  assert.ok(
    signals.indexOf('all_signals.append(signal)') <
      signals.indexOf('if signal_filter is not None and actionable:'),
    'signals must be recorded before the AI filter runs',
  );
  const replay = read('ai_advisor', 'replay.py');
  assert.match(replay, /signal_filter=ai_filter/);
  assert.match(replay, /make_ai_filter\(/);
});

test('the AI run owns its own summary file and never clobbers the paper one', () => {
  const replay = read('ai_advisor', 'replay.py');
  assert.match(replay, /os\.replace\(summary_path, ai_summary_path\)/);
  const config = read('ai_advisor', 'config.py');
  assert.match(config, /AI_SUMMARY_NAME = "ai-summary\.json"/);
  assert.match(config, /AI_DECISIONS_NAME = "decisions\.jsonl"/);
  assert.ok(!config.includes('"paper-summary.json"'), 'the AI layer must not name the paper summary');
});

test('execution stays signal_only and the AI stays veto-only', () => {
  const manifest = read('manifest.yaml');
  assert.match(manifest, /^execution_mode:\s*signal_only\s*$/m);
  const cli = read('ai_advisor', 'cli.py');
  assert.match(cli, /!= "signal_only"/);
  const config = read('ai_advisor', 'config.py');
  assert.match(config, /AI_ROLE = "veto_only"/);
  const replay = read('ai_advisor', 'replay.py');
  assert.match(replay, /"live_orders_placed": False/);
  assert.match(replay, /"private_api_used": False/);
});

test('the offline fixture provider is labelled synthetic', () => {
  const providers = read('ai_advisor', PROVIDERS);
  assert.match(providers, /class FixtureProvider/);
  assert.match(providers, /synthetic = True/);
  const cli = read('ai_advisor', 'cli.py');
  assert.match(cli, /SYNTHETIC MODE/);
  assert.match(cli, /Nothing below is a real model opinion/);
});

test('a missing hosted credential is a hard error, never a silent fallback', () => {
  const providers = read('ai_advisor', PROVIDERS);
  assert.match(providers, /refusing to fall back silently/);
  assert.match(providers, /def build_provider\(config: AiConfig, env=None\)/);
  const cli = read('ai_advisor', 'cli.py');
  assert.match(cli, /Refusing to fall back to the fixture/);
});

test('generated AI artifacts stay out of git', () => {
  const ignore = read('.gitignore');
  const lines = ignore.split(/\r?\n/).map((line) => line.trim());
  for (const entry of ['output/', 'dashboard/public/', '.env', '.env.*', '!.env.example']) {
    assert.ok(lines.includes(entry), `.gitignore is missing ${entry}`);
  }
  const config = read('ai_advisor', 'config.py');
  assert.match(config, /AI_OUTPUT_DIR = REPO_ROOT \/ "output" \/ "ai"/);
  assert.match(
    config,
    /AI_DASHBOARD_EXPORT_PATH = REPO_ROOT \/ "dashboard" \/ "public" \/ "ai-summary\.json"/,
  );
});

test('.env.example documents the AI variables by name only', () => {
  const template = read('.env.example');
  for (const name of AI_ENV_NAMES) {
    const line = template
      .split(/\r?\n/)
      .find((candidate) => candidate.startsWith(`${name}=`));
    assert.ok(line, `.env.example is missing ${name}`);
    assert.equal(line, `${name}=`, `${name} must have a blank value`);
  }
});

test('the dashboard loads the AI summary without polling', () => {
  const hook = read('dashboard', 'src', 'hooks', 'useAiSummary.js');
  assert.match(hook, /"ai-summary\.json"/);
  assert.match(hook, /cache: "no-store"/);
  assert.match(hook, /isMissing = true/);
  assert.ok(!hook.includes('setInterval'), 'the AI hook must not poll');
  assert.ok(!hook.includes('setTimeout'), 'the AI hook must not poll');
  assert.match(hook, /export function useAiSummary\(\)/);
});

test('the AI panel fabricates nothing and labels every figure', () => {
  const panel = read('dashboard', 'src', 'components', 'market', 'AiPanel.jsx');
  assert.ok(!panel.includes('Math.random'), 'AiPanel must not fabricate values');
  assert.ok(!panel.includes('setInterval'), 'AiPanel must not poll');
  assert.ok(!panel.includes('setTimeout'), 'AiPanel must not poll');
  assert.match(panel, /useAiSummary/);
  assert.match(panel, /t\.aiBadge/);
  assert.match(panel, /t\.aiRoleVetoOnly/);
  assert.match(panel, /t\.aiSynthetic/);
  assert.match(panel, /t\.aiNotLive/);
  assert.match(panel, /t\.aiDisclaimer/);
  assert.match(panel, /summary\.last_updated/);
  assert.match(panel, /status === "missing"/);
  assert.ok(!panel.includes('mockAssets'), 'AiPanel must not read mock market data');
});

test('the AI panel is mounted after the paper panel', () => {
  const app = read('dashboard', 'src', 'App.jsx');
  assert.match(app, /import AiPanel from "\.\/components\/market\/AiPanel";/);
  assert.match(app, /<AiPanel t=\{t\} \/>/);
  assert.ok(
    app.indexOf('<PaperPanel t={t} />') < app.indexOf('<AiPanel t={t} />'),
    'AiPanel should render directly after PaperPanel',
  );
});

test('every AI translation key exists in both languages exactly once', () => {
  const source = read('dashboard', 'src', 'data', 'translations.js');
  assert.equal(AI_TRANSLATION_KEYS.length, 33);
  for (const key of AI_TRANSLATION_KEYS) {
    const occurrences = source.split(`${key}:`).length - 1;
    assert.equal(occurrences, 2, `${key} must appear once per language, found ${occurrences}`);
  }
});

test('the AI panel uses every translation key it is given', () => {
  const panel = read('dashboard', 'src', 'components', 'market', 'AiPanel.jsx');
  const unused = AI_TRANSLATION_KEYS.filter((key) => !panel.includes(`t.${key}`));
  assert.deepEqual(unused, [], `unused AI translation keys: ${unused.join(', ')}`);
});

test('the AI layer documents its own safety contract', () => {
  const init = read('ai_advisor', '__init__.py');
  assert.match(init, /VETO-ONLY/);
  assert.match(init, /fails CLOSED/);
  assert.match(init, /PAPER \/ SIMULATED/);
});

test('watch samples are audited after the replay and can never be promoted', () => {
  const replay = read('ai_advisor', 'replay.py');
  assert.ok(
    replay.indexOf('outcome = run_replay(') <
      replay.indexOf('review_watch_sample(\n        advisor'),
    'watch samples must be reviewed only after the replay has finished',
  );
  const advisor = read('ai_advisor', 'advisor.py');
  const start = advisor.indexOf('_WATCH_SAMPLE_OUTCOMES = {');
  assert.ok(start > 0, 'the watch-sample outcome table is missing');
  const block = advisor.slice(start, advisor.indexOf('}', start) + 1);
  assert.match(block, /DECISION_CONFIRM: OUTCOME_NOT_PROMOTABLE/);
  assert.ok(!block.includes('OUTCOME_ACCEPTED'), 'a watch sample can never be accepted');
});

test('the AI reviewer only ever sees closed bars', () => {
  const replay = read('ai_advisor', 'replay.py');
  assert.match(replay, /def make_slicer\(dataset: ReplayDataset\)/);
  assert.match(replay, /bisect_right/);
  assert.match(replay, /look-ahead bias/);
});
