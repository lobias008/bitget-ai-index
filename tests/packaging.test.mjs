/**
 * Tests for Playbook packaging. The upload endpoint accepts ONLY
 * manifest.yaml, README.md, src/** and backtest.yaml, and rejects local-only
 * top-level paths. These tests prove the artifact we build is legal.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';

import {
  ensureOutputDir,
  readManifestScalars,
  repoRoot,
  run,
  stagePackage,
} from '../scripts/_common.mjs';
import { validateStagedDir } from '../scripts/validate-playbook.mjs';
import { buildPackage } from '../scripts/package-playbook.mjs';

const ALLOWED = new Set(['README.md', 'manifest.yaml', 'src/main.py', 'src/instruments.py']);

function tempStage() {
  ensureOutputDir();
  return fs.mkdtempSync(path.join(repoRoot, 'output', 'test-stage-'));
}

test('staging produces exactly the upload-legal file set', () => {
  const dir = tempStage();
  try {
    const staged = stagePackage(dir);
    assert.deepEqual(new Set(staged), ALLOWED);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('staging excludes local-only directories and scripts', () => {
  const dir = tempStage();
  try {
    const staged = stagePackage(dir);
    for (const forbidden of ['tests', 'scripts', 'dashboard', 'node_modules', 'output', '.tools', '.backup', 'index.js', 'package.json']) {
      assert.ok(
        !staged.some((rel) => rel === forbidden || rel.startsWith(`${forbidden}/`)),
        `${forbidden} must never be staged`,
      );
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('staging strips python bytecode even when present in src/', () => {
  const cache = path.join(repoRoot, 'src', '__pycache__');
  const pyc = path.join(cache, 'main.cpython-312.pyc');
  fs.mkdirSync(cache, { recursive: true });
  fs.writeFileSync(pyc, 'not-real-bytecode');
  const dir = tempStage();
  try {
    const staged = stagePackage(dir);
    assert.deepEqual(new Set(staged), ALLOWED);
    assert.ok(!staged.some((rel) => rel.includes('__pycache__')));
    assert.ok(!staged.some((rel) => rel.endsWith('.pyc')));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
    fs.rmSync(cache, { recursive: true, force: true });
  }
});

test('the built tarball contains only allowed paths', () => {
  const built = buildPackage();
  try {
    assert.ok(fs.existsSync(built.archivePath));
    assert.ok(built.size > 0);
    assert.ok(built.size < 10 * 1024 * 1024, 'package exceeds the 10MB upload limit');
    assert.match(built.sha256, /^[a-f0-9]{64}$/);

    const listing = run('tar', ['tzf', built.archivePath]);
    assert.equal(listing.status, 0, listing.stderr);
    const entries = listing.stdout
      .split(/\r?\n/)
      .map((line) => line.replace(/^\.\//, '').replace(/\/$/, ''))
      .filter((line) => line && line !== '.');
    assert.deepEqual(new Set(entries), new Set(['manifest.yaml', 'README.md', 'src', 'src/main.py', 'src/instruments.py']));
  } finally {
    fs.rmSync(built.archivePath, { force: true });
  }
});

test('validator rejects a package whose manifest is broken', () => {
  // Deliberately corrupts a STAGED COPY only. The real manifest.yaml is never
  // modified: a crash mid-test must not be able to damage the trading strategy.
  const dir = tempStage();
  try {
    stagePackage(dir);
    fs.writeFileSync(path.join(dir, 'manifest.yaml'), 'name: broken\n', 'utf8');
    const result = validateStagedDir(dir);
    assert.notEqual(result.status, 0, 'validator accepted a broken manifest');
    assert.match(result.stdout, /FAIL/);
    assert.match(result.stdout, /missing required field/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('the real manifest.yaml is untouched by the test suite', () => {
  const manifest = readManifestScalars();
  assert.equal(manifest.name, 'the-morning-sword');
  assert.equal(manifest.execution_mode, 'signal_only');
});

after(() => {
  // Never leave build artifacts behind from the test run.
  const outputDir = path.join(repoRoot, 'output');
  if (fs.existsSync(outputDir)) {
    for (const entry of fs.readdirSync(outputDir)) {
      if (entry.endsWith('.tar.gz')) fs.rmSync(path.join(outputDir, entry), { force: true });
    }
  }
});

