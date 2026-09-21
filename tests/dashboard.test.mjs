/**
 * Structural integrity tests for the React dashboard after its separation from
 * the Python strategy. These run fully offline - they resolve imports on disk
 * rather than invoking a bundler, so they work even before `npm install`.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const repoRoot = path.resolve(import.meta.dirname, '..');
const dashboardRoot = path.join(repoRoot, 'dashboard');
const dashboardPkg = JSON.parse(fs.readFileSync(path.join(dashboardRoot, 'package.json'), 'utf8'));
const RESOLVE_EXTENSIONS = ['', '.jsx', '.js', '.mjs', '.css', '.json'];

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (/\.(jsx?|mjs)$/.test(entry.name)) yield full;
  }
}

const sourceFiles = [...walk(path.join(dashboardRoot, 'src'))];

function resolves(specifier, fromFile) {
  const base = path.resolve(path.dirname(fromFile), specifier);
  return RESOLVE_EXTENSIONS.some((ext) => {
    const candidate = base + ext;
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return true;
    return fs.existsSync(path.join(candidate, 'index.jsx')) || fs.existsSync(path.join(candidate, 'index.js'));
  });
}

function packageNameOf(specifier) {
  const parts = specifier.split('/');
  return specifier.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
}

const IMPORT_RE = /(?:^|\n)\s*import\s+(?:[\s\S]*?\s+from\s+)?['"]([^'"]+)['"]/g;

function importsOf(file) {
  const text = fs.readFileSync(file, 'utf8');
  const found = [];
  let match;
  IMPORT_RE.lastIndex = 0;
  while ((match = IMPORT_RE.exec(text)) !== null) found.push(match[1]);
  return found;
}

test('dashboard has its own package manifest', () => {
  assert.equal(dashboardPkg.private, true);
  assert.ok(dashboardPkg.dependencies.react, 'react must be declared');
  assert.ok(dashboardPkg.dependencies['react-dom'], 'react-dom must be declared');
  assert.ok(dashboardPkg.devDependencies.vite, 'vite must be declared');
});

test('tailwind is pinned to v3 to match the existing CSS syntax', () => {
  // tailwind.config.js uses the v3 `content`/`theme.extend` shape and index.css
  // uses @tailwind directives; Tailwind v4 would silently stop generating CSS.
  assert.match(dashboardPkg.devDependencies.tailwindcss, /^\^3\./);
  const css = fs.readFileSync(path.join(dashboardRoot, 'src', 'index.css'), 'utf8');
  assert.match(css, /@tailwind base;/);
});

test('index.html entry point resolves', () => {
  const html = fs.readFileSync(path.join(dashboardRoot, 'index.html'), 'utf8');
  const match = /<script[^>]+src="([^"]+)"/.exec(html);
  assert.ok(match, 'no module script tag in index.html');
  const entry = path.join(dashboardRoot, match[1].replace(/^\//, ''));
  assert.ok(fs.existsSync(entry), `entry point missing: ${match[1]}`);
});

test('dashboard source files were all moved', () => {
  assert.ok(sourceFiles.length >= 25, `expected a full component tree, found ${sourceFiles.length}`);
  for (const expected of ['App.jsx', 'main.jsx']) {
    assert.ok(fs.existsSync(path.join(dashboardRoot, 'src', expected)), `missing src/${expected}`);
  }
});

test('every relative import inside the dashboard resolves', () => {
  const broken = [];
  for (const file of sourceFiles) {
    for (const specifier of importsOf(file)) {
      if (!specifier.startsWith('.')) continue;
      if (!resolves(specifier, file)) {
        broken.push(`${path.relative(dashboardRoot, file)} -> ${specifier}`);
      }
    }
  }
  assert.deepEqual(broken, []);
});

test('every bare import is declared in dashboard/package.json', () => {
  const declared = new Set([
    ...Object.keys(dashboardPkg.dependencies ?? {}),
    ...Object.keys(dashboardPkg.devDependencies ?? {}),
  ]);
  const missing = new Set();
  for (const file of sourceFiles) {
    for (const specifier of importsOf(file)) {
      if (specifier.startsWith('.') || specifier.startsWith('/')) continue;
      const pkg = packageNameOf(specifier);
      if (!declared.has(pkg)) missing.add(`${pkg} (from ${path.relative(dashboardRoot, file)})`);
    }
  }
  assert.deepEqual([...missing], []);
});

test('the dashboard never reaches into the Python strategy package', () => {
  const crossing = [];
  for (const file of sourceFiles) {
    for (const specifier of importsOf(file)) {
      if (!specifier.startsWith('.')) continue;
      const resolved = path.resolve(path.dirname(file), specifier);
      if (!resolved.startsWith(dashboardRoot)) crossing.push(`${path.relative(dashboardRoot, file)} -> ${specifier}`);
    }
  }
  assert.deepEqual(crossing, []);
});

test('root src/ contains only the Python strategy', () => {
  const entries = fs.readdirSync(path.join(repoRoot, 'src')).filter((name) => name !== '__pycache__');
  assert.deepEqual(entries, ['instruments.py', 'main.py']);
});

test('tailwind content globs match real files', () => {
  const config = fs.readFileSync(path.join(dashboardRoot, 'tailwind.config.js'), 'utf8');
  assert.match(config, /\.\/index\.html/);
  assert.match(config, /\.\/src\/\*\*\/\*\.\{js,jsx\}/);
  assert.ok(fs.existsSync(path.join(dashboardRoot, 'index.html')));
  assert.ok(sourceFiles.length > 0);
});
