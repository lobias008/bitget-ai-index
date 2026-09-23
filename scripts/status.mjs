/**
 * `npm start` - a SAFE, read-only local status summary.
 *
 * This replaced the previous `start` script, which ran `node index.js` and
 * attempted an automatic compile -> backtest -> publish pipeline. This command
 * performs no network I/O, publishes nothing, deploys nothing, and trades
 * nothing. It only reads files and prints a summary.
 */
import fs from 'node:fs';
import path from 'node:path';
import {
  repoRoot,
  resolveGit,
  run,
  readManifestScalars,
  maskSecret,
  isMainModule,
} from './_common.mjs';
import { checkPublishGates, playbookDefinition, MARKETS } from '../index.js';

const REQUIRED_ENV_VARS = [
  'BITGET_ACCESS_KEY',
  'ALLOW_PLAYBOOK_PUBLISH',
  'PUBLISH_CONFIRMATION_PHRASE',
  'TRADING_MODE',
  'ALLOW_LIVE_TRADING',
];

function exists(relative) {
  return fs.existsSync(path.join(repoRoot, relative));
}

function gitSummary() {
  try {
    const git = resolveGit();
    const branch = run(git, ['rev-parse', '--abbrev-ref', 'HEAD']);
    const status = run(git, ['status', '--porcelain=v1']);
    const backups = run(git, ['branch', '--list', 'backup/*']);
    return {
      branch: branch.stdout.trim() || 'unknown',
      dirty: status.stdout.split(/\r?\n/).filter(Boolean).length,
      backups: backups.stdout.split(/\r?\n/).filter(Boolean).map((line) => line.trim()),
    };
  } catch {
    return { branch: 'unknown', dirty: 0, backups: [] };
  }
}

function loadDotEnvStatus() {
  const envPath = path.join(repoRoot, '.env');
  if (!fs.existsSync(envPath)) return { present: false, keys: [] };
  const keys = fs
    .readFileSync(envPath, 'utf8')
    .split(/\r?\n/)
    .map((line) => /^\s*([A-Z_][A-Z0-9_]*)\s*=/.exec(line))
    .filter(Boolean)
    .map((match) => match[1]);
  return { present: true, keys };
}

/**
 * Read-only summary of the latest generated paper run. A missing or unreadable
 * file is reported as such - this never fabricates a result.
 */
function readPaperRunStatus() {
  const summaryPath = path.join(repoRoot, 'dashboard', 'public', 'paper-summary.json');
  if (!fs.existsSync(summaryPath)) return 'none yet - run: npm run paper:simulate';
  try {
    const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));
    const equity = summary.equity === null || summary.equity === undefined ? 'n/a' : summary.equity;
    const source = summary.data_source === 'synthetic' ? 'SYNTHETIC data' : (summary.data_source ?? 'unknown source');
    return `${summary.last_updated ?? 'unknown time'} | ${source} | equity ${equity} | PAPER / SIMULATED`;
  } catch {
    return 'present but unreadable - regenerate with: npm run paper:simulate';
  }
}

function main() {
  const manifest = readManifestScalars();
  const git = gitSummary();
  const dotEnv = loadDotEnvStatus();

  console.log('');
  console.log('  The Morning Sword - AI Index Trading Platform');
  console.log('  =============================================');
  console.log('');
  console.log('  Repository');
  console.log(`    root              : ${repoRoot}`);
  console.log(`    git branch        : ${git.branch}`);
  console.log(`    uncommitted files : ${git.dirty}`);
  console.log(`    backup branches   : ${git.backups.length ? git.backups.join(', ') : 'none'}`);
  console.log('');
  console.log('  Components');
  console.log(`    playbook manifest : ${exists('manifest.yaml') ? 'manifest.yaml' : 'MISSING'}`);
  console.log(`    strategy logic    : ${exists('src/main.py') ? 'src/main.py' : 'MISSING'}`);
  console.log(`    strategy doc      : ${exists('README.md') ? 'README.md' : 'MISSING'}`);
  console.log(`    definition module : ${exists('index.js') ? 'index.js (inert)' : 'MISSING'}`);
  console.log(`    dashboard         : ${exists('dashboard/package.json') ? 'dashboard/ (React + Vite)' : 'MISSING'}`);
  console.log(`    dashboard deps    : ${exists('dashboard/node_modules') ? 'installed' : 'NOT installed - run: npm run dashboard:install'}`);
  console.log(`    safe tooling      : ${exists('scripts') ? 'scripts/' : 'MISSING'}`);
  console.log(`    tests             : ${exists('tests') ? 'tests/' : 'MISSING'}`);
  console.log(`    paper simulator   : ${exists('paper/cli.py') ? 'paper/ (simulated, GET-only public data)' : 'MISSING'}`);
  console.log('');
  console.log('  Playbook contract (from manifest.yaml)');
  console.log(`    name              : ${manifest.name ?? 'unknown'}`);
  console.log(`    version           : ${manifest.version ?? 'unknown'}`);
  console.log(`    market_type       : ${manifest.market_type ?? 'unknown'}`);
  console.log(`    decision_mode     : ${manifest.decision_mode ?? 'unknown'}`);
  console.log(`    runtime_profile   : ${manifest.runtime_profile ?? 'unknown'}`);
  console.log(`    execution_mode    : ${manifest.execution_mode ?? 'unknown'}`);
  console.log(`    backtest_support  : ${manifest.backtest_support ?? 'unknown'}`);
  console.log(`    follow_trade      : ${manifest.follow_trade_supported ?? 'unknown'}`);
  console.log(`    markets in code   : ${MARKETS.map((market) => market.symbol).join(', ')}`);
  console.log('');
  console.log('  Safety posture');
  console.log(`    auto-publish      : DISABLED (publish.enabled=${playbookDefinition.publish.enabled})`);
  console.log(`    auto-backtest     : DISABLED (backtest.enabled=${playbookDefinition.backtest.enabled})`);
  console.log(`    live trading      : ${String(process.env.ALLOW_LIVE_TRADING).toLowerCase() === 'true' ? 'ENABLED - review immediately' : 'disabled'}`);
  console.log(`    trading mode      : ${process.env.TRADING_MODE || 'unset (defaults to non-executing)'}`);
  console.log(`    .env file         : ${dotEnv.present ? `present (${dotEnv.keys.length} keys)` : 'absent - copy .env.example to .env'}`);
  console.log(`    access key        : ${maskSecret(process.env.BITGET_ACCESS_KEY)}`);
  for (const name of REQUIRED_ENV_VARS) {
    console.log(`      ${name.padEnd(28)}: ${process.env[name] ? 'set' : 'unset'}`);
  }
  const gates = checkPublishGates();
  console.log(`    publish gates     : ${gates.length ? `${gates.length} unmet (publish blocked)` : 'ALL SATISFIED - publish is possible'}`);
  console.log('    paper trading     : SIMULATED ONLY - places no orders, uses no private API');
  console.log(`    latest paper run  : ${readPaperRunStatus()}`);
  console.log('');
  console.log('  Safe commands');
  console.log('    npm test                 run the test suite (offline)');
  console.log('    npm run check            secrets + JS syntax + Python compile + validate');
  console.log('    npm run check:secrets    scan for hardcoded credentials');
  console.log('    npm run validate         run the official Playbook validator');
  console.log('    npm run package          build the upload tarball locally (no upload)');
  console.log('    npm run dashboard:dev    start the React dashboard dev server');
  console.log('    npm run paper:signals    generate + classify paper signals (simulated)');
  console.log('    npm run paper:simulate   replay signals through the paper simulator');
  console.log('    npm run paper:export     re-export the latest run to dashboard/public/');
  console.log('    npm run paper:status     print the latest paper run summary');
  console.log('');
  console.log('  Gated (never automatic, always requires explicit confirmation)');
  console.log('    npm run playbook:publish -- --dry-run');
  console.log('');
  console.log('  See AGENTS.md for the permanent safety rules.');
  console.log('');
}

if (isMainModule(import.meta.url)) {
  try {
    main();
  } catch (error) {
    console.error(`[status] error: ${error.message}`);
    process.exitCode = 1;
  }
}
