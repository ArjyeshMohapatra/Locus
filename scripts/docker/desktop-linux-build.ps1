[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.." )).Path
$imageName = "locus-desktop-build-env:latest"
$dockerfile = Join-Path $repoRoot "docker\desktop-build\Dockerfile"

Write-Host "[docker-linux] building image $imageName"
& docker build -f $dockerfile -t $imageName $repoRoot

$script = @'
set -euo pipefail
export PATH="/usr/local/cargo/bin:${PATH}"

python3 -m venv /tmp/locus-build-venv
source /tmp/locus-build-venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
pip install pyinstaller

npm ci --prefix ui
npm run build --prefix ui

mkdir -p src-tauri/binaries

python3 -m PyInstaller backend/app/main.py \
  --name locus-backend \
  --onefile \
  --paths backend \
  --distpath backend/dist \
  --workpath backend/build/pyinstaller \
  --specpath backend/build \
  --clean

cp -f backend/dist/locus-backend src-tauri/binaries/locus-backend-x86_64-unknown-linux-gnu
chmod +x src-tauri/binaries/locus-backend-x86_64-unknown-linux-gnu

cargo build --release --manifest-path tools/window-probe/Cargo.toml --target x86_64-unknown-linux-gnu
cp -f tools/window-probe/target/x86_64-unknown-linux-gnu/release/locus-window-probe src-tauri/binaries/locus-window-probe-x86_64-unknown-linux-gnu
chmod +x src-tauri/binaries/locus-window-probe-x86_64-unknown-linux-gnu

cd src-tauri
cargo tauri build --config tauri.conf.json --target x86_64-unknown-linux-gnu
'@

Write-Host "[docker-linux] running linux desktop build in container"
& docker run --rm -t `
  -v "${repoRoot}:/workspace" `
  -w /workspace `
  $imageName `
  bash -c $script

Write-Host "[docker-linux] done. Bundles in src-tauri/target/x86_64-unknown-linux-gnu/release/bundle"
