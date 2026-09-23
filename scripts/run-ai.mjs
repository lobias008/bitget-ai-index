/**
 * `npm run ai:*` - AI-assisted paper-trading workflow runner.
 *
 * Wraps `python -m ai_advisor.cli` with the resolved interpreter. Read-only
 * with respect to the exchange: market data still comes from the GET-only
 * allowlisted public endpoints in paper/market_data.py, and the only network
 * call the AI layer can make is a POST to an allowlisted inference host. No
 * order code exists anywhere in ai_advisor/. Bytecode writing is disabled so
 * runs leave no __pycache__ behind.
 *
 * The reviewer is veto-only and `execution_mode` must stay `signal_only`: the
 * CLI refuses to start otherwise. Nothing here can place an order, publish a
 * Playbook, or deploy.
 *
 * Exit codes from the CLI are passed through: 0 ok, 2 configuration error,
 * 3 market data unavailable (reported, never fabricated).
 */
import { repoRoot, resolvePython, run, isMainModule } from './_common.mjs';

function main(argv) {
  const python = resolvePython();
  const result = run(python, ['-m', 'ai_advisor.cli', ...argv], {
    cwd: repoRoot,
    env: { PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' },
    stdio: 'inherit',
  });
  return result.status ?? 1;
}

if (isMainModule(import.meta.url)) {
  try {
    process.exitCode = main(process.argv.slice(2));
  } catch (error) {
    console.error(`[ai] error: ${error.message}`);
    process.exitCode = 1;
  }
}
