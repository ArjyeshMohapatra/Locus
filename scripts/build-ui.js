const fs = require('fs');
const path = require('path');
const cp = require('child_process');

function resolveUiDir(baseDir) {
  const candidates = ['ui', '../ui', '.'];
  for (const rel of candidates) {
    const candidate = path.resolve(baseDir, rel);
    if (fs.existsSync(path.join(candidate, 'package.json'))) {
      return candidate;
    }
  }
  return null;
}

const cwd = process.cwd();
const uiDir = resolveUiDir(cwd);

if (!uiDir) {
  console.error('Unable to locate ui/package.json');
  process.exit(1);
}

if (process.platform === 'win32') {
  // .cmd shims are expected to be launched via a shell on Windows.
  cp.execSync('npm run build', {
    cwd: uiDir,
    stdio: 'inherit',
    shell: true,
  });
} else {
  cp.execFileSync('npm', ['run', 'build'], {
    cwd: uiDir,
    stdio: 'inherit',
  });
}
