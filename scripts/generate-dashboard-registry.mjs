/**
 * `npm run dashboard:registry` - regenerate dashboard/src/data/instruments.json
 * from manifest.yaml through the validated Python instrument registry.
 *
 * Safety contract:
 *   - READ-ONLY on manifest.yaml. No network, no trading, no publishing.
 *   - Deterministic: same manifest bytes -> same JSON bytes (no timestamps).
 *   - `--check` regenerates in memory and fails on drift, so tests and the
 *     pre-commit routine can prove the dashboard data always matches the
 *     registry. Nothing is ever fabricated here: the Python exporter derives
 *     every field from src/instruments.py validation.
 */
import fs from 'node:fs';
import path from 'node:path';

import { isMainModule, repoRoot, resolvePython, run } from './_common.mjs';

export const EXPORT_SCRIPT = path.join(repoRoot, 'scripts', 'export_registry.py');
export const MANIFEST_PATH = path.join(repoRoot, 'manifest.yaml');
export const OUTPUT_PATH = path.join(repoRoot, 'dashboard', 'src', 'data', 'instruments.json');

/** Run the Python exporter and return the JSON payload as a string. */
export function generateRegistryJson({ manifestPath = MANIFEST_PATH, python = resolvePython() } = {}) {
  const result = run(python, [EXPORT_SCRIPT, manifestPath, '-']);
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || '').trim();
    throw new Error(`export_registry.py failed (exit ${result.status}): ${detail}`);
  }
  // Fail loudly rather than writing anything that is not valid JSON.
  JSON.parse(result.stdout);
  return result.stdout;
}

/** Compare a fresh export against the committed instruments.json. */
export function checkRegistryJson(options = {}) {
  const fresh = generateRegistryJson(options);
  if (!fs.existsSync(OUTPUT_PATH)) return { ok: false, reason: 'missing', fresh };
  const committed = fs.readFileSync(OUTPUT_PATH, 'utf8');
  return { ok: committed === fresh, reason: committed === fresh ? null : 'drift', fresh, committed };
}

function main(argv) {
  const check = argv.includes('--check');
  if (check) {
    const verdict = checkRegistryJson();
    if (!verdict.ok) {
      console.error(`[dashboard:registry] --check FAILED (${verdict.reason}): run 'npm run dashboard:registry' and commit the result`);
      return 1;
    }
    console.log('[dashboard:registry] --check OK: instruments.json matches manifest.yaml');
    return 0;
  }
  const json = generateRegistryJson();
  fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
  fs.writeFileSync(OUTPUT_PATH, json, 'utf8');
  console.log(`[dashboard:registry] wrote ${path.relative(repoRoot, OUTPUT_PATH)} (${Buffer.byteLength(json, 'utf8')} bytes)`);
  return 0;
}

if (isMainModule(import.meta.url)) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (error) {
    console.error(`[dashboard:registry] error: ${error.message}`);
    process.exitCode = 1;
  }
}
