/**
 * Tests for the secret scanner and the repository's secret hygiene.
 * The positive control plants a FAKE credential and asserts the scanner both
 * detects it and refuses to print it.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { repoRoot, run } from '../scripts/_common.mjs';

const SCANNER = path.join(repoRoot, 'scripts', 'check-secrets.mjs');
// Built at runtime so this test file never contains a literal the scanner would flag.
const FAKE_KEY = 'deadbeef'.repeat(4);

function scan() {
  return run(process.execPath, [SCANNER]);
}

test('scanner passes on the current tree', () => {
  const result = scan();
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test('scanner detects a planted credential and exits non-zero', () => {
  const target = path.join(repoRoot, 'tests', '_planted_secret_probe.js');
  fs.writeFileSync(target, `const apiKey = '${FAKE_KEY}';\n`, 'utf8');
  try {
    const result = scan();
    assert.equal(result.status, 1, 'scanner failed to detect a planted secret');
    assert.match(result.stderr, /FAIL/);
    assert.match(result.stderr, /_planted_secret_probe\.js:1/);
  } finally {
    fs.rmSync(target, { force: true });
  }
});

test('scanner never prints the secret value it finds', () => {
  const target = path.join(repoRoot, 'tests', '_planted_secret_probe.js');
  fs.writeFileSync(target, `const apiKey = '${FAKE_KEY}';\n`, 'utf8');
  try {
    const result = scan();
    const combined = result.stdout + result.stderr;
    assert.ok(!combined.includes(FAKE_KEY), 'scanner leaked the raw credential value');
    assert.match(combined, /REDACTED/);
  } finally {
    fs.rmSync(target, { force: true });
  }
});

test('scanner is clean again after the probe is removed', () => {
  assert.equal(scan().status, 0);
});

test('.env.example declares names only, with no values', () => {
  const text = fs.readFileSync(path.join(repoRoot, '.env.example'), 'utf8');
  const assigned = text
    .split(/\r?\n/)
    .filter((line) => /^[A-Z_][A-Z0-9_]*=.+$/.test(line));
  assert.deepEqual(assigned, [], `.env.example contains real values: ${assigned.join(', ')}`);
  assert.match(text, /^BITGET_ACCESS_KEY=$/m);
});

test('no .env file is present to be accidentally committed', () => {
  assert.ok(!fs.existsSync(path.join(repoRoot, '.env')));
});

test('.gitignore covers every required exclusion', () => {
  const ignore = fs.readFileSync(path.join(repoRoot, '.gitignore'), 'utf8');
  const lines = ignore.split(/\r?\n/).map((line) => line.trim());
  for (const required of [
    '.env',
    '.env.*',
    '!.env.example',
    '.tools/',
    '**/__pycache__/',
    '*.pyc',
    'node_modules/',
    'dist/',
    '.backup/',
  ]) {
    assert.ok(lines.includes(required), `.gitignore is missing: ${required}`);
  }
});

test('.gitignore keeps the test suite under version control', () => {
  const lines = fs
    .readFileSync(path.join(repoRoot, '.gitignore'), 'utf8')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));
  assert.ok(!lines.includes('tests/'), 'tests/ must not be gitignored');
});

test('no python bytecode is present in the working tree', () => {
  const found = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (['node_modules', '.git', '.tools', '.backup'].includes(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.py[co]$/.test(entry.name) || entry.name === '__pycache__') found.push(full);
    }
  };
  walk(repoRoot);
  assert.deepEqual(found, []);
});

