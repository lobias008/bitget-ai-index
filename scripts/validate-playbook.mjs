/**
 * Validate the Playbook package using the OFFICIAL validator shipped with
 * @bitget-ai/getagent-skill.
 *
 * Why staging is required: the repo root deliberately contains local-only paths
 * (`tests/`, `scripts/`, `dashboard/`, `output/`). Both the local validator and
 * server-side upload validation reject local-only top-level paths, so we copy
 * ONLY the upload-legal subset into a temp directory and validate that. This
 * proves the artifact we would actually upload is valid.
 *
 * Performs no network I/O. Usage: node scripts/validate-playbook.mjs [--keep]
 */
import fs from 'node:fs';
import path from 'node:path';
import {
  repoRoot,
  resolvePython,
  run,
  stagePackage,
  ensureOutputDir,
  isMainModule,
} from './_common.mjs';

const VALIDATOR = path.join(
  repoRoot,
  'node_modules',
  '@bitget-ai',
  'getagent-skill',
  'skills',
  'getagent',
  'scripts',
  'validate.py',
);

export function validatorPath() {
  return VALIDATOR;
}

export function validateStagedDir(stagedDir) {
  if (!fs.existsSync(VALIDATOR)) {
    throw new Error(
      `Official validator not found at ${VALIDATOR}. Run \`npm install\` so that ` +
        '@bitget-ai/getagent-skill is present.',
    );
  }
  const python = resolvePython();
  return run(python, [VALIDATOR, stagedDir], { cwd: repoRoot });
}

/** Stage the upload-legal subset, validate it, and clean up. */
export function validatePackage({ keep = false, quiet = false } = {}) {
  ensureOutputDir();
  const stagedDir = fs.mkdtempSync(path.join(repoRoot, 'output', 'validate-'));
  try {
    const staged = stagePackage(stagedDir);
    if (!quiet) {
      console.log('[validate] staged upload-legal paths:');
      for (const rel of staged) console.log(`  ${rel}`);
      console.log('');
    }
    const result = validateStagedDir(stagedDir);
    if (!quiet) {
      process.stdout.write(result.stdout);
      if (result.stderr) process.stderr.write(result.stderr);
    }
    return {
      ok: result.status === 0,
      status: result.status,
      stdout: result.stdout,
      stderr: result.stderr,
      stagedDir,
      staged,
    };
  } finally {
    if (!keep) fs.rmSync(stagedDir, { recursive: true, force: true });
  }
}

if (isMainModule(import.meta.url)) {
  try {
    const { ok, stdout } = validatePackage({ keep: process.argv.includes('--keep') });
    if (!ok) {
      console.error('[validate] FAILED - fix the errors above before packaging or publishing.');
      process.exit(1);
    }
    if (/^WARNING/m.test(stdout)) {
      console.log('[validate] passed with warnings (see WARNING lines above).');
    }
    console.log('[validate] package is upload-legal and passes the official validator.');
  } catch (error) {
    console.error(`[validate] error: ${error.message}`);
    process.exit(1);
  }
}
