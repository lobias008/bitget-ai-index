/**
 * Build the uploadable Playbook tarball LOCALLY. No network, no upload.
 *
 * Order of operations is deliberate: stage -> validate -> archive. If the
 * official validator fails, no artifact is produced, so a broken package can
 * never be handed to the publish step by accident.
 *
 * Output: output/<name>-<version>.tar.gz  (gitignored)
 * Usage:  node scripts/package-playbook.mjs
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {
  repoRoot,
  run,
  stagePackage,
  ensureOutputDir,
  readManifestScalars,
  isMainModule,
} from './_common.mjs';
import { validatePackage } from './validate-playbook.mjs';

const MAX_PACKAGE_BYTES = 10 * 1024 * 1024; // server rejects >10MB with 413

export function buildPackage() {
  const manifest = readManifestScalars();
  const name = manifest.name || 'playbook';
  const version = manifest.version || '0.0.0';

  // Validate first. This stages into its own temp dir and cleans up.
  const validation = validatePackage({ quiet: true });
  if (!validation.ok) {
    process.stdout.write(validation.stdout);
    process.stderr.write(validation.stderr);
    throw new Error('Package validation failed; refusing to build an artifact.');
  }

  const outputDir = ensureOutputDir();
  const stagedDir = fs.mkdtempSync(path.join(outputDir, 'package-'));
  const archivePath = path.join(outputDir, `${name}-${version}.tar.gz`);

  try {
    const staged = stagePackage(stagedDir);
    fs.rmSync(archivePath, { force: true });

    // bsdtar ships with Windows 10+. -C keeps paths relative inside the archive,
    // which is what the upload endpoint expects.
    const tar = run('tar', ['czf', archivePath, '-C', stagedDir, '.']);
    if (tar.status !== 0) {
      throw new Error(`tar failed (exit ${tar.status}): ${tar.stderr.trim()}`);
    }

    const bytes = fs.readFileSync(archivePath);
    const size = bytes.byteLength;
    if (size > MAX_PACKAGE_BYTES) {
      fs.rmSync(archivePath, { force: true });
      throw new Error(
        `Package is ${(size / 1024 / 1024).toFixed(2)}MB, over the 10MB upload limit.`,
      );
    }

    return {
      archivePath,
      size,
      sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
      staged,
      manifest,
    };
  } finally {
    fs.rmSync(stagedDir, { recursive: true, force: true });
  }
}

if (isMainModule(import.meta.url)) {
  try {
    const result = buildPackage();
    console.log('[package] validation passed; artifact built.');
    console.log(`  file      : ${path.relative(repoRoot, result.archivePath)}`);
    console.log(`  size      : ${(result.size / 1024).toFixed(1)} KB (limit 10 MB)`);
    console.log(`  sha256    : ${result.sha256}`);
    console.log(`  contents  : ${result.staged.join(', ')}`);
    console.log('');
    console.log('NOTHING was uploaded. To publish, use the gated command:');
    console.log('  npm run playbook:publish -- --dry-run');
  } catch (error) {
    console.error(`[package] FAILED: ${error.message}`);
    process.exit(1);
  }
}
