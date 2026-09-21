/**
 * `npm run test:python` - run the Python unit tests for the strategy.
 *
 * `python` is not on PATH here, so the interpreter is resolved via
 * resolvePython(). Bytecode writing is disabled so the run leaves no
 * __pycache__ behind. No network, no trading.
 */
import path from 'node:path';
import { repoRoot, resolvePython, run, isMainModule } from './_common.mjs';

function main() {
  const testDir = path.join(repoRoot, 'tests', 'python');
  const python = resolvePython();
  console.log(`[test:python] interpreter : ${python}`);
  console.log(`[test:python] tests       : ${path.relative(repoRoot, testDir)}`);
  console.log('');
  const result = run(
    python,
    ['-m', 'unittest', 'discover', '-s', testDir, '-t', testDir, '-v'],
    { env: { PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' } },
  );
  process.stdout.write(result.stdout);
  process.stderr.write(result.stderr);
  return result.status === 0 ? 0 : 1;
}

if (isMainModule(import.meta.url)) {
  try {
    process.exitCode = main();
  } catch (error) {
    console.error(`[test:python] error: ${error.message}`);
    process.exitCode = 1;
  }
}
