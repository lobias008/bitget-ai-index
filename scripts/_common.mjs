/**
 * Shared helpers for the safe local tooling in `scripts/`.
 *
 * Nothing in this module performs network I/O, publishes, deploys, or trades.
 * See AGENTS.md for the safety rules these helpers exist to enforce.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** Files allowed at the root of an uploadable Playbook package. */
export const PACKAGE_ROOT_FILES = Object.freeze(['manifest.yaml', 'README.md', 'backtest.yaml']);

/** Directories allowed in an uploadable Playbook package. */
export const PACKAGE_DIRS = Object.freeze(['src']);

/** Rejected by both the local validator and server-side upload validation. */
export const LOCAL_ONLY_NAMES = Object.freeze([
  'tests',
  'notebooks',
  'research',
  'data',
  'backtest_results',
  'logs',
  'output',
  '.venv',
  '__pycache__',
  '.pytest_cache',
]);

const BYTECODE_EXTENSIONS = new Set(['.pyc', '.pyo', '.pyd']);

/** Show only enough of a secret to identify it, never enough to use it. */
export function maskSecret(value) {
  const text = String(value ?? '');
  if (!text) return '<unset>';
  return `${text.slice(0, 4)}...[REDACTED len=${text.length}]`;
}

export function isBytecode(filePath) {
  return BYTECODE_EXTENSIONS.has(path.extname(filePath).toLowerCase());
}

export function isLocalOnlyName(name) {
  return LOCAL_ONLY_NAMES.includes(name);
}

/**
 * Resolve a Python interpreter. `python` is not on PATH on this machine, so
 * fall back to the bundled runtime. Override with PYTHON_BIN.
 */
export function resolvePython() {
  if (process.env.PYTHON_BIN) return process.env.PYTHON_BIN;
  for (const candidate of ['python', 'python3', 'py']) {
    const probe = spawnSync(candidate, ['--version'], { encoding: 'utf8' });
    if (probe.status === 0) return candidate;
  }
  const bundled =
    'C:\\Users\\NIAMO\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe';
  if (fs.existsSync(bundled)) return bundled;
  throw new Error('No Python interpreter found. Set PYTHON_BIN to a Python 3.11+ executable.');
}

/** Resolve git. Prefer PATH, then the locally bundled MinGit. */
export function resolveGit() {
  if (process.env.GIT_BIN) return process.env.GIT_BIN;
  const probe = spawnSync('git', ['--version'], { encoding: 'utf8' });
  if (probe.status === 0) return 'git';
  const bundled = path.join(repoRoot, '.tools', 'mingit', 'cmd', 'git.exe');
  if (fs.existsSync(bundled)) return bundled;
  throw new Error('No git executable found. Set GIT_BIN.');
}

/** Run a command and return a normalized result. Throws only on spawn failure. */
export function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    encoding: 'utf8',
    cwd: options.cwd ?? repoRoot,
    // PYTHONDONTWRITEBYTECODE keeps __pycache__ out of the working tree: the
    // secret/bytecode test asserts none is present, so a stray .pyc written by
    // one script would fail a later `npm test`.
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PYTHONDONTWRITEBYTECODE: '1',
      ...(options.env ?? {}),
    },
    input: options.input,
    stdio: options.stdio,
  });
  if (result.error) throw result.error;
  return {
    status: result.status ?? 1,
    stdout: (result.stdout ?? '').toString(),
    stderr: (result.stderr ?? '').toString(),
  };
}

function copyTreeFiltered(source, destination) {
  fs.mkdirSync(destination, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    if (isLocalOnlyName(entry.name)) continue;
    const from = path.join(source, entry.name);
    const to = path.join(destination, entry.name);
    if (entry.isDirectory()) {
      copyTreeFiltered(from, to);
    } else if (entry.isFile()) {
      if (isBytecode(from)) continue;
      fs.copyFileSync(from, to);
    }
  }
}

/**
 * Copy ONLY the paths an upload may contain into `destDir`, stripping bytecode
 * and local-only directories. Returns the list of staged relative paths.
 */
export function stagePackage(destDir) {
  fs.rmSync(destDir, { recursive: true, force: true });
  fs.mkdirSync(destDir, { recursive: true });

  const staged = [];
  for (const file of PACKAGE_ROOT_FILES) {
    const from = path.join(repoRoot, file);
    if (fs.existsSync(from)) {
      fs.copyFileSync(from, path.join(destDir, file));
      staged.push(file);
    }
  }
  for (const dir of PACKAGE_DIRS) {
    const from = path.join(repoRoot, dir);
    if (fs.existsSync(from)) {
      copyTreeFiltered(from, path.join(destDir, dir));
    }
  }

  const walk = (relative) => {
    const absolute = path.join(destDir, relative);
    for (const entry of fs.readdirSync(absolute, { withFileTypes: true })) {
      const rel = relative ? `${relative}/${entry.name}` : entry.name;
      if (entry.isDirectory()) walk(rel);
      else staged.push(rel);
    }
  };
  if (fs.existsSync(path.join(destDir, 'src'))) walk('src');

  return [...new Set(staged)].sort();
}

/** Parse a few scalar keys out of manifest.yaml without a YAML dependency. */
export function readManifestScalars() {
  const manifestPath = path.join(repoRoot, 'manifest.yaml');
  if (!fs.existsSync(manifestPath)) return {};
  const text = fs.readFileSync(manifestPath, 'utf8');
  const wanted = [
    'name',
    'version',
    'market_type',
    'decision_mode',
    'backtest_support',
    'runtime_profile',
    'execution_mode',
    'follow_trade_supported',
    'output_kind',
  ];
  const out = {};
  for (const line of text.split(/\r?\n/)) {
    const match = /^([a-z_]+):\s*(.+?)\s*$/.exec(line);
    if (match && wanted.includes(match[1]) && out[match[1]] === undefined) {
      out[match[1]] = match[2].replace(/^["']|["']$/g, '');
    }
  }
  return out;
}

export function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(repoRoot, 'output', `${prefix}-`));
}

export function ensureOutputDir() {
  const dir = path.join(repoRoot, 'output');
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/**
 * True when this module is the script Node was asked to run.
 * Uses fileURLToPath so paths containing spaces or non-ASCII compare correctly.
 */
export function isMainModule(moduleUrl) {
  if (!process.argv[1]) return false;
  try {
    return path.resolve(fileURLToPath(moduleUrl)) === path.resolve(process.argv[1]);
  } catch {
    return false;
  }
}
