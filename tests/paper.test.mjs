/**
 * Milestone 2 - paper-trading workflow guards.
 *
 * These are structural/offline tests: they read the shipped sources rather
 * than executing a simulator run, so they pass before `npm install` and prove
 * the invariants that matter most for this milestone:
 *
 *   1. the paper package can only ever issue GET requests to allowlisted
 *      public market-data endpoints - there is no order or account plumbing;
 *   2. generated artifacts stay out of git;
 *   3. the dashboard presents the run as PAPER / SIMULATED, never as live,
 *      and never fabricates prices or polls on a timer.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const repoRoot = path.resolve(import.meta.dirname, '..');
const read = (...parts) => fs.readFileSync(path.join(repoRoot, ...parts), 'utf8');
const exists = (...parts) => fs.existsSync(path.join(repoRoot, ...parts));

const pkg = JSON.parse(read('package.json'));

const PAPER_MODULES = [
  '__init__.py',
  'cli.py',
  'config.py',
  'harness.py',
  'market_data.py',
  'signals.py',
  'simulator.py',
  'storage.py',
];

const PAPER_SCRIPTS = {
  'paper:signals': 'signals',
  'paper:simulate': 'simulate',
  'paper:export': 'export',
  'paper:status': 'status',
};

const PAPER_TRANSLATION_KEYS = [
  'paperTitle', 'paperBadge', 'paperLoading', 'paperMissing', 'paperError',
  'paperReload', 'paperEquity', 'paperRealized', 'paperUnrealized',
  'paperReturn', 'paperClosedTrades', 'paperWinRate', 'paperOpenPositions',
  'paperNoPositions', 'paperRecentSignals', 'paperNoSignals', 'paperCbArmed',
  'paperCbLocked', 'paperQty', 'paperEntry', 'paperLastUpdated',
  'paperDataSource', 'paperSyntheticWarning', 'paperNotLive',
  'paperCoverageTitle', 'paperDataEligibility', 'paperSignalOutcome', 'paperEligBacktest', 'paperEligPending', 'paperOutcomeActionable', 'paperOutcomeNoSetup', 'paperOutcomeGated', 'paperOutcomeNotEvaluated', 'paperCovDailyBars', 'paperCovFourHourBars', 'paperCovSteps', 'paperCovWatches', 'paperCovGatedSteps', 'paperCovMissing', 'paperFundingMode', 'paperFundingBlock', 'paperFundingNeutral',
];

// Anything that would let the paper workflow touch an account or place an
// order. The package must not merely avoid calling these - it must not
// contain the plumbing at all.
const PRIVATE_API_RE = /place-order|\/api\/v2\/mix\/order|\/api\/v2\/mix\/account|ACCESS-KEY|apiKey|api_key|api_secret|API_SECRET|passphrase|BITGET_API|requests\.post/i;

test('the paper package ships every module the CLI needs', () => {
  for (const module of PAPER_MODULES) {
    assert.ok(exists('paper', module), `paper/${module} is missing`);
    assert.ok(read('paper', module).trim().length > 0, `paper/${module} is empty`);
  }
});

test('paper modules contain no private API or order plumbing', () => {
  for (const module of PAPER_MODULES) {
    const text = read('paper', module);
    const match = text.match(PRIVATE_API_RE);
    assert.equal(match, null, `paper/${module} references private API surface: ${match && match[0]}`);
  }
});

test('paper market data is GET-only against allowlisted public endpoints', () => {
  const text = read('paper', 'market_data.py');
  assert.match(
    text,
    /ALLOWED_PATH_PREFIXES = \("\/api\/v2\/mix\/market\/", "\/api\/v2\/spot\/market\/"\)/,
    'the public market-data allowlist must stay narrow',
  );
  assert.match(text, /method="GET"/, 'requests must be explicit GETs');
  assert.ok(!text.includes('method="POST"'), 'no POST requests are permitted');
  assert.ok(!/urlopen\([^)]*\bdata=/.test(text), 'urlopen must never send a body');
  assert.ok(!/Request\([^)]*\bdata=/.test(text), 'Request must never carry a body');
});

test('npm exposes the four documented paper commands', () => {
  for (const [name, subcommand] of Object.entries(PAPER_SCRIPTS)) {
    assert.equal(pkg.scripts[name], `node scripts/run-paper.mjs ${subcommand}`, `${name} is misconfigured`);
  }
});

test('no paper command can publish, deploy or trade', () => {
  for (const name of Object.keys(PAPER_SCRIPTS)) {
    const command = pkg.scripts[name];
    assert.ok(!/publish|deploy|upload|playbook|order/i.test(command), `${name} must stay local: ${command}`);
  }
  // `npm start` must remain a safe local status command, never a publisher.
  assert.equal(pkg.scripts.start, 'node scripts/status.mjs');
});

test('the paper runner wraps the CLI without leaving bytecode behind', () => {
  const text = read('scripts', 'run-paper.mjs');
  assert.match(text, /'-m', 'paper\.cli'/, 'runner must invoke the paper CLI module');
  assert.match(text, /PYTHONDONTWRITEBYTECODE: '1'/, 'runner must suppress __pycache__');
  assert.match(text, /stdio: 'inherit'/, 'runner must surface CLI output and exit codes');
});

test('generated paper artifacts are gitignored', () => {
  const ignore = read('.gitignore');
  for (const pattern of ['output/', 'dashboard/public/']) {
    const line = ignore.split(/\r?\n/).some((entry) => entry.trim() === pattern);
    assert.ok(line, `.gitignore must ignore ${pattern}`);
  }
});

test('the dashboard mounts the paper panel below the market pulse', () => {
  const app = read('dashboard', 'src', 'App.jsx');
  assert.match(app, /import PaperPanel from "\.\/components\/market\/PaperPanel"/);
  assert.ok(app.includes('<PaperPanel'), 'App.jsx must render PaperPanel');
  assert.ok(
    app.indexOf('<PaperPanel') > app.indexOf('<MacroPulse'),
    'PaperPanel must sit after MacroPulse so the existing layout is preserved',
  );
});

test('the paper summary hook never polls and treats absence as missing', () => {
  const text = read('dashboard', 'src', 'hooks', 'usePaperSummary.js');
  assert.match(text, /paper-summary\.json/, 'hook must read the generated summary');
  assert.match(text, /cache: "no-store"/, 'hook must not serve a stale cached run');
  assert.match(text, /response\.status === 404/, 'a missing file must be detected');
  assert.match(text, /isMissing = true/);
  assert.match(text, /status: error && error\.isMissing \? "missing" : "error"/);
  assert.ok(!/setInterval|setTimeout/.test(text), 'the hook must not poll on a timer');
});

test('the paper panel labels everything simulated and never fabricates prices', () => {
  const text = read('dashboard', 'src', 'components', 'market', 'PaperPanel.jsx');
  assert.match(text, /usePaperSummary/, 'panel must read the generated summary');
  assert.match(text, /t\.paperBadge/, 'panel must show the PAPER / SIMULATED badge');
  assert.match(text, /summary\.last_updated/, 'panel must show when the snapshot was generated');
  assert.ok(!/Math\.random|setInterval|setTimeout/.test(text), 'panel must not invent market activity');
  assert.ok(!/fetch\(/.test(text), 'panel must not fetch prices itself; data comes from the hook');
});

test('paper copy exists in both dashboard languages', () => {
  const text = read('dashboard', 'src', 'data', 'translations.js');
  for (const key of PAPER_TRANSLATION_KEYS) {
    const occurrences = (text.match(new RegExp(`^\\s*${key}:`, 'gm')) || []).length;
    assert.equal(occurrences, 2, `${key} must be defined for both en and zh`);
  }
  assert.match(text, /paperBadge: "PAPER \/ SIMULATED"/);
  assert.match(text, /paperNotLive: "All figures are PAPER \/ SIMULATED\. No live orders were placed\."/);
  assert.match(text, /npm run paper:simulate/, 'the empty state must tell the user how to generate a run');
});

test('a generated summary, when present, is honest about being simulated', () => {
  const summaryPath = path.join(repoRoot, 'dashboard', 'public', 'paper-summary.json');
  if (!fs.existsSync(summaryPath)) return; // missing output is a supported state
  const raw = fs.readFileSync(summaryPath, 'utf8');
  const summary = JSON.parse(raw);
  assert.deepEqual(summary.labels, ['PAPER', 'SIMULATED'], 'every export must carry the paper labels');
  assert.match(summary.disclaimer, /No live orders were placed/);
  assert.match(summary.last_updated, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/);
  assert.ok(summary.data_source, 'the data source must be declared');
  if (summary.data_source === 'synthetic') {
    assert.ok(
      summary.notes.some((note) => /SYNTHETIC/i.test(note)),
      'a synthetic run must warn that prices are not real market data',
    );
  }
  assert.ok(!/api[_-]?key|secret|passphrase|authorization/i.test(raw), 'the export must never contain credentials');
});

test('the paper panel renders data coverage from the run summary, not hardcoded counts', () => {
  const text = read('dashboard', 'src', 'components', 'market', 'PaperPanel.jsx');
  assert.match(text, /summary\.data_coverage/, 'panel must read data_coverage from the summary');
  assert.match(text, /data_eligibility/, 'panel must show data eligibility');
  assert.match(text, /signal_outcome/, 'panel must show the signal outcome');
  assert.match(text, /min_daily_bars/, 'the daily-bar threshold must come from the summary');
  assert.ok(!/\b134\b|\b123\b/.test(text), 'instrument bar counts must not be hardcoded');
  assert.ok(!/Math\.random|setInterval|setTimeout|fetch\(/.test(text), 'panel must not fabricate or poll');
});

test('the neutral-funding run is labeled as an assumption-based diagnostic', () => {
  const panel = read('dashboard', 'src', 'components', 'market', 'PaperPanel.jsx');
  assert.match(panel, /summary\.replay_funding === "neutral"/, 'panel must detect neutral funding');
  assert.match(panel, /paperFundingNeutral/, 'panel must render the neutral diagnostic label');
  const tr = read('dashboard', 'src', 'data', 'translations.js');
  assert.match(tr, /Assumption-based diagnostic/, 'neutral run must be labeled assumption-based');
  assert.match(tr, /not a verified historical-funding backtest/, 'neutral run must disclaim verified funding history');
});

test('the paper summary carries the data-coverage contract', () => {
  const text = read('paper', 'storage.py');
  assert.match(text, /def build_data_coverage\(/, 'storage must compute the coverage rollup');
  assert.match(text, /"data_coverage": build_data_coverage\(signals, coverage\)/, 'summaries must include data_coverage');
  assert.match(text, /"replay_funding": config\.replay_funding/, 'summaries must declare the funding mode');
  assert.match(text, /MIN_BARS\[DAILY_INTERVAL\]/, 'thresholds must come from MIN_BARS');
});
