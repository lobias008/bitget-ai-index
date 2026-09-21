/**
 * `npm run check` - aggregate offline verification. No network, no publishing.
 *
 *   1. Secret scan            (scripts/check-secrets.mjs)
 *   2. JavaScript syntax      (node --check on every root .js and scripts/*.mjs)
 *   3. Python syntax          (ast.parse - deliberately avoids writing .pyc)
 *   4. Official package validation (scripts/validate-playbook.mjs)
 *
 * Exits non-zero if any stage fails, so it can gate a commit.
 */
import fs from 'node:fs';
import path from 'node:path';
import { repoRoot, resolvePython, run, isMainModule } from './_common.mjs';
import { validatePackage } from './validate-playbook.mjs';

const stages = [];

function record(name, ok, detail = '') {
  stages.push({ name, ok, detail });
  console.log(`${ok ? '  PASS' : '  FAIL'}  ${name}${detail ? ` - ${detail}` : ''}`);
}

function collectJsFiles() {
  const files = [];
  for (const entry of fs.readdirSync(repoRoot, { withFileTypes: true })) {
    if (entry.isFile() && entry.name.endsWith('.js')) files.push(path.join(repoRoot, entry.name));
  }
  const scriptsDir = path.join(repoRoot, 'scripts');
  for (const entry of fs.readdirSync(scriptsDir, { withFileTypes: true })) {
    if (entry.isFile() && entry.name.endsWith('.mjs')) files.push(path.join(scriptsDir, entry.name));
  }
  return files;
}

function collectPyFiles() {
  const out = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === '__pycache__' || entry.name === 'node_modules') continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith('.py')) out.push(full);
    }
  };
  for (const dir of ['src', path.join('tests', 'python')]) {
    const absolute = path.join(repoRoot, dir);
    if (fs.existsSync(absolute)) walk(absolute);
  }
  return out;
}

function main() {
  console.log('');
  console.log('[check] 1/4 secret scan');
  const secrets = run(process.execPath, [path.join(repoRoot, 'scripts', 'check-secrets.mjs'), '--quiet']);
  record('no hardcoded credentials', secrets.status === 0, secrets.stdout.trim() || secrets.stderr.trim().split(/\r?\n/)[0]);

  console.log('[check] 2/4 javascript syntax');
  const jsFiles = collectJsFiles();
  let jsOk = true;
  const jsFailures = [];
  for (const file of jsFiles) {
    const result = run(process.execPath, ['--check', file]);
    if (result.status !== 0) {
      jsOk = false;
      jsFailures.push(`${path.relative(repoRoot, file)}: ${result.stderr.trim().split(/\r?\n/)[0]}`);
    }
  }
  record(`node --check (${jsFiles.length} files)`, jsOk, jsFailures.join(' | '));

  console.log('[check] 3/4 python syntax');
  const pyFiles = collectPyFiles();
  let pyOk = true;
  const pyFailures = [];
  if (pyFiles.length === 0) {
    record('python syntax', true, 'no .py files found');
  } else {
    let python;
    try {
      python = resolvePython();
    } catch (error) {
      python = null;
      pyFailures.push(error.message);
      pyOk = false;
    }
    if (python) {
      for (const file of pyFiles) {
        const code = 'import ast,sys\nast.parse(open(sys.argv[1],encoding="utf-8").read())\n';
        const result = run(python, ['-c', code, file]);
        if (result.status !== 0) {
          pyOk = false;
          pyFailures.push(`${path.relative(repoRoot, file)}: ${result.stderr.trim().split(/\r?\n/).pop()}`);
        }
      }
    }
    record(`ast.parse (${pyFiles.length} files)`, pyOk, pyFailures.join(' | '));
  }

  console.log('[check] 4/4 playbook package validation');
  let validateOk = false;
  let validateDetail = '';
  try {
    const result = validatePackage({ quiet: true });
    validateOk = result.ok;
    if (!result.ok) {
      validateDetail = (result.stdout + result.stderr)
        .split(/\r?\n/)
        .filter((line) => line.includes('FAIL'))
        .slice(0, 4)
        .join(' | ');
    }
  } catch (error) {
    validateDetail = error.message;
  }
  record('official validator on staged package', validateOk, validateDetail);

  const failed = stages.filter((stage) => !stage.ok);
  console.log('');
  if (failed.length === 0) {
    console.log(`[check] ALL ${stages.length} STAGES PASSED`);
    return 0;
  }
  console.error(`[check] ${failed.length}/${stages.length} stage(s) FAILED: ${failed.map((s) => s.name).join(', ')}`);
  return 1;
}

if (isMainModule(import.meta.url)) {
  process.exitCode = main();
}
