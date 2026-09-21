/**
 * GATED Playbook publisher. Dry-run by default. Performs no network I/O unless
 * every gate below is satisfied.
 *
 * Gates, in order:
 *   1. `--confirm` must be passed explicitly (otherwise this is a dry run)
 *   2. BITGET_ACCESS_KEY must be set in the environment (never hardcoded)
 *   3. ALLOW_PLAYBOOK_PUBLISH must be exactly "true"
 *   4. PUBLISH_CONFIRMATION_PHRASE must match the expected phrase
 *   5. On a TTY, the operator must type that phrase again interactively
 *   6. The package must pass the official validator and build cleanly
 *
 * Workflow implemented from the installed SDK docs
 * (node_modules/@bitget-ai/getagent-skill/skills/getagent/references/api/):
 *   upload -> [run, only when backtest_support=full] -> confirm -> publish
 *
 * The ACCESS-KEY is only ever placed in a request header. It is never logged,
 * never written to disk, and never included in error output.
 */
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { isMainModule, maskSecret, readManifestScalars } from './_common.mjs';
import { buildPackage } from './package-playbook.mjs';
import {
  GETAGENT_API_BASE_URL,
  PUBLISH_CONFIRMATION_PHRASE,
  checkPublishGates,
  playbookDefinition,
} from '../index.js';

const VALID_BUMPS = new Set(['patch', 'minor', 'major']);

function parseArgs(argv) {
  const options = { confirm: false, dryRun: false, bump: 'patch', skipRun: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--confirm') options.confirm = true;
    else if (arg === '--dry-run') options.dryRun = true;
    else if (arg === '--skip-run') options.skipRun = true;
    else if (arg === '--bump') {
      options.bump = argv[index + 1] ?? '';
      index += 1;
    } else if (arg.startsWith('--bump=')) options.bump = arg.slice('--bump='.length);
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!VALID_BUMPS.has(options.bump)) {
    throw new Error(`--bump must be one of ${[...VALID_BUMPS].join(', ')}`);
  }
  // Dry run wins over confirm: an explicit --dry-run can never publish.
  if (options.dryRun) options.confirm = false;
  return options;
}

async function postJson(endpoint, body, accessKey) {
  const response = await fetch(`${GETAGENT_API_BASE_URL}${endpoint}`, {
    method: 'POST',
    headers: { 'ACCESS-KEY': accessKey, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { raw: text.slice(0, 500) };
  }
  if (!response.ok) {
    throw new Error(`${endpoint} -> HTTP ${response.status}: ${JSON.stringify(payload)}`);
  }
  return payload;
}

async function uploadPackage(archivePath, accessKey) {
  const bytes = fs.readFileSync(archivePath);
  const form = new FormData();
  form.append(
    'package',
    new Blob([bytes], { type: 'application/gzip' }),
    path.basename(archivePath),
  );
  // Do not set Content-Type manually; FormData supplies the multipart boundary.
  const response = await fetch(`${GETAGENT_API_BASE_URL}/api/v1/playbook/upload`, {
    method: 'POST',
    headers: { 'ACCESS-KEY': accessKey },
    body: form,
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { raw: text.slice(0, 500) };
  }
  if (!response.ok) {
    throw new Error(`upload -> HTTP ${response.status}: ${JSON.stringify(payload)}`);
  }
  return payload;
}

async function pollRun(runId, accessKey, { timeoutMs = 200_000, intervalMs = 5_000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const response = await fetch(
      `${GETAGENT_API_BASE_URL}/api/v1/playbook/run?run_id=${encodeURIComponent(runId)}`,
      { headers: { 'ACCESS-KEY': accessKey } },
    );
    if (!response.ok) throw new Error(`run poll -> HTTP ${response.status}`);
    const payload = await response.json();
    if (payload.status === 'completed') return payload;
    if (payload.status === 'failed') {
      throw new Error(`backtest run failed: ${payload.failure_reason || 'unknown reason'}`);
    }
    console.log(`  run ${runId}: ${payload.status ?? 'pending'} ...`);
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`run ${runId} did not finish within ${timeoutMs / 1000}s`);
}

async function promptForPhrase() {
  if (!process.stdin.isTTY) {
    throw new Error(
      'Refusing to publish from a non-interactive shell. Re-run in a TTY so the ' +
        'confirmation phrase can be typed by a human.',
    );
  }
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    const typed = await rl.question(
      `Type exactly "${PUBLISH_CONFIRMATION_PHRASE}" to publish publicly: `,
    );
    if (typed.trim() !== PUBLISH_CONFIRMATION_PHRASE) {
      throw new Error('Confirmation phrase did not match. Publish aborted.');
    }
  } finally {
    rl.close();
  }
}

function printPlan({ options, manifest, gates }) {
  console.log('');
  console.log('=== Playbook publish plan ===');
  console.log(`  mode             : ${options.confirm ? 'LIVE (will publish)' : 'DRY RUN (no network)'}`);
  console.log(`  api base url     : ${GETAGENT_API_BASE_URL} (fixed; not configurable)`);
  console.log(`  package name     : ${manifest.name ?? playbookDefinition.name}`);
  console.log(`  manifest version : ${manifest.version ?? 'n/a'}`);
  console.log(`  bump type        : ${options.bump}`);
  console.log(`  access key       : ${maskSecret(process.env.BITGET_ACCESS_KEY)}`);
  console.log(`  backtest_support : ${manifest.backtest_support ?? 'unknown'}`);
  console.log(`  execution_mode   : ${manifest.execution_mode ?? 'unknown'}`);
  console.log('');
  console.log('  steps:');
  console.log('    1. POST /api/v1/playbook/upload   (multipart tar.gz)');
  if (manifest.backtest_support === 'full' && !options.skipRun) {
    console.log('    2. POST /api/v1/playbook/run      (+ poll GET /run)');
  } else {
    console.log(
      '    2. SKIPPED run - backtest_support is not "full", and the API rejects ' +
        'historical evaluation for live-only Playbooks (409)',
    );
  }
  console.log('    3. POST /api/v1/playbook/confirm  (temporary -> draft)');
  console.log('    4. POST /api/v1/playbook/publish  (IRREVERSIBLE: assigns public semver)');
  console.log('');
  if (gates.length) {
    console.log('  blocked by:');
    for (const gate of gates) console.log(`    - ${gate}`);
  } else {
    console.log('  gates: all satisfied');
  }
  console.log('');
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const manifest = readManifestScalars();
  const gates = checkPublishGates();

  if (!options.confirm) {
    printPlan({ options, manifest, gates });
    console.log('Dry run complete. No network calls were made.');
    console.log('To actually publish, pass --confirm AND satisfy every gate above.');
    return;
  }

  if (gates.length) {
    console.error('[publish] Refusing to proceed. Unmet gates:');
    for (const gate of gates) console.error(`  - ${gate}`);
    process.exitCode = 1;
    return;
  }

  printPlan({ options, manifest, gates });
  await promptForPhrase();

  const accessKey = process.env.BITGET_ACCESS_KEY.trim();
  const built = buildPackage();
  console.log(`[publish] package built: ${path.basename(built.archivePath)} (${built.size} bytes)`);

  const uploaded = await uploadPackage(built.archivePath, accessKey);
  const temporaryId = uploaded.draft_id ?? uploaded.temporary_id;
  console.log(`[publish] upload ok: strategy_id=${uploaded.strategy_id} temporary_id=${temporaryId}`);

  if (manifest.backtest_support === 'full' && !options.skipRun) {
    const dispatched = await postJson('/api/v1/playbook/run', { version_id: temporaryId }, accessKey);
    console.log(`[publish] run dispatched: run_id=${dispatched.run_id}`);
    const finished = await pollRun(dispatched.run_id, accessKey);
    console.log(`[publish] run completed: ${JSON.stringify(finished.metrics_output ?? {})}`);
  }

  const confirmed = await postJson(
    '/api/v1/playbook/confirm',
    { temporary_id: temporaryId },
    accessKey,
  );
  console.log(`[publish] confirmed draft: draft_id=${confirmed.draft_id} status=${confirmed.status}`);

  const published = await postJson(
    '/api/v1/playbook/publish',
    { draft_id: confirmed.draft_id ?? temporaryId, bump_type: options.bump },
    accessKey,
  );
  console.log(`[publish] PUBLISHED version=${published.version} status=${published.status}`);
}

if (isMainModule(import.meta.url)) {
  main().catch((error) => {
    console.error(`[publish] aborted: ${error.message}`);
    process.exitCode = 1;
  });
}
