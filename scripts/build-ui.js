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

const npmCmd = process.platform === 'win32' ? 'npm.cmd' : 'npm';
cp.execFileSync(npmCmd, ['run', 'build'], {
  cwd: uiDir,
  stdio: 'inherit',
});
