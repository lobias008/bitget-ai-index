/**
 * `npm run paper:*` - local paper-trading workflow runner.
 *
 * Wraps `python -m paper.cli` with the resolved interpreter. Read-only with
 * respect to the exchange: the CLI can only issue GET requests to allowlisted
 * public market-data endpoints, and the paper package contains no order code.
 * Bytecode writing is disabled so runs leave no __pycache__ behind.
 *
 * Exit codes from the CLI are passed through: 0 ok, 2 configuration error,
 * 3 market data unavailable (reported, never fabricated).
 */
import { repoRoot, resolvePython, run, isMainModule } from './_common.mjs';

function main(argv) {
  const python = resolvePython();
  const result = run(python, ['-m', 'paper.cli', ...argv], {
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
    console.error(`[paper] error: ${error.message}`);
    process.exitCode = 1;
  }
}