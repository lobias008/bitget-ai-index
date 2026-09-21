/**
 * Secret scanner. Enforces AGENTS.md rule 1: never expose secrets.
 *
 * Scans tracked-style text files for hardcoded credentials. Prints only the
 * file, line, rule name, and a masked fingerprint - NEVER the value itself.
 * Exits non-zero when anything is found, so it can gate commits and CI.
 *
 * Usage: node scripts/check-secrets.mjs [--quiet]
 */
import fs from 'node:fs';
import path from 'node:path';
import { repoRoot, maskSecret } from './_common.mjs';

const EXCLUDED_DIRS = new Set([
  'node_modules',
  '.git',
  '.tools',
  'dist',
  'build',
  'coverage',
  '.venv',
  'venv',
  '__pycache__',
  '.pytest_cache',
  '.mypy_cache',
  '.ruff_cache',
  '.vite',
  '.vite-temp',
  'output',
]);

const BINARY_EXTENSIONS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp', '.zip', '.gz', '.tgz',
  '.tar', '.exe', '.dll', '.so', '.dylib', '.pyc', '.pyo', '.pdf', '.woff',
  '.woff2', '.ttf', '.eot', '.mp4', '.mp3', '.bin',
]);

/** Lockfiles contain base64 integrity hashes that are not credentials. */
const INTEGRITY_LINE = /^\s*(integrity|resolved|version|checksum)\s*[:=]/i;

const RULES = [
  {
    name: 'hex-token-32+',
    pattern: /\b[a-fA-F0-9]{32,}\b/g,
    describe: (match) => `hex token (len=${match.length})`,
  },
  {
    name: 'openai-style-key',
    pattern: /\bsk-[A-Za-z0-9_-]{16,}\b/g,
    describe: () => 'OpenAI-style secret key',
  },
  {
    name: 'github-token',
    pattern: /\bgh[pousr]_[A-Za-z0-9]{20,}\b/g,
    describe: () => 'GitHub token',
  },
  {
    name: 'aws-access-key-id',
    pattern: /\bAKIA[0-9A-Z]{16}\b/g,
    describe: () => 'AWS access key id',
  },
  {
    name: 'private-key-block',
    pattern: /-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----/g,
    describe: () => 'PEM private key block',
  },
  {
    name: 'jwt',
    pattern: /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g,
    describe: () => 'JWT',
  },
  {
    name: 'secret-assignment',
    pattern:
      /\b(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|client[_-]?secret|auth[_-]?token|password|passwd|pwd|token)\b\s*[:=]\s*['"`]([^'"`]{12,})['"`]/gi,
    describe: (_match, group) => `literal assigned to a credential name (len=${group.length})`,
    group: 1,
  },
];

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (EXCLUDED_DIRS.has(entry.name)) continue;
      yield* walk(full);
    } else if (entry.isFile()) {
      if (BINARY_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) continue;
      yield full;
    }
  }
}

const quiet = process.argv.includes('--quiet');
const findings = [];
let scanned = 0;

for (const file of walk(repoRoot)) {
  const relative = path.relative(repoRoot, file).replaceAll('\\', '/');
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    continue;
  }
  if (text.includes('\u0000')) continue;
  scanned += 1;

  const lines = text.split(/\r?\n/);
  lines.forEach((line, index) => {
    if (INTEGRITY_LINE.test(line)) return;
    // Redaction markers used by our own tooling are not findings.
    if (line.includes('[REDACTED')) return;
    for (const rule of RULES) {
      rule.pattern.lastIndex = 0;
      let match;
      while ((match = rule.pattern.exec(line)) !== null) {
        const value = rule.group ? match[rule.group] : match[0];
        if (!value || value.length < 12) continue;
        findings.push({
          file: relative,
          line: index + 1,
          rule: rule.name,
          detail: rule.describe(match[0], value),
          fingerprint: maskSecret(value),
        });
        if (match[0].length === 0) rule.pattern.lastIndex += 1;
      }
    }
  });
}

const backupDir = path.join(repoRoot, '.backup');
const backupWarning = fs.existsSync(backupDir);

if (!quiet) {
  console.log(`[check-secrets] scanned ${scanned} text file(s)`);
}

if (backupWarning) {
  console.log(
    '[check-secrets] WARNING: .backup/ exists. It is gitignored, but snapshots taken ' +
      'before remediation may contain plaintext credentials. Rotate any key that was ' +
      'ever stored there, then delete the directory.',
  );
}

if (findings.length === 0) {
  if (!quiet) console.log('[check-secrets] PASS - no hardcoded credentials detected');
  process.exit(0);
}

console.error(`[check-secrets] FAIL - ${findings.length} potential secret(s) detected:`);
for (const finding of findings) {
  console.error(
    `  ${finding.file}:${finding.line}  [${finding.rule}]  ${finding.detail}  ${finding.fingerprint}`,
  );
}
console.error('');
console.error('Remediate by moving the value to an environment variable (see .env.example).');
console.error('If the value was ever committed or synced, treat it as compromised and rotate it.');
process.exit(1);

