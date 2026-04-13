[CmdletBinding()]
param(
  [string]$Target = "x86_64-pc-windows-msvc"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.." )).Path
$imageName = "locus-desktop-build-env:latest"
$dockerfile = Join-Path $repoRoot "docker\desktop-build\Dockerfile"

Write-Host "[docker-win-debug] building image $imageName"
& docker build -f $dockerfile -t $imageName $repoRoot

Write-Host "[docker-win-debug] target=$Target"
if ($Target -eq "x86_64-pc-windows-msvc") {
  Write-Host "[docker-win-debug] note: msvc linking is not available on linux containers; this run is for config/prebuild diagnostics."
}

$script = @"
set -euo pipefail
export PATH=\"/usr/local/cargo/bin:\${PATH}\"

npm ci --prefix ui
export RC_x86_64_pc_windows_msvc=llvm-rc

mkdir -p src-tauri/binaries
: > src-tauri/binaries/locus-backend-${Target}.exe
: > src-tauri/binaries/locus-window-probe-${Target}.exe

cd src-tauri

set +e
cargo tauri build --config tauri.conf.json --target ${Target} --no-bundle 2>&1 | tee /tmp/locus-windows-debug.log
status=\${PIPESTATUS[0]}
set -e

echo \"[docker-win-debug] cargo tauri exit code: \$status\"
if [ \$status -ne 0 ]; then
  echo \"[docker-win-debug] tail of debug log:\"
  tail -n 120 /tmp/locus-windows-debug.log || true
fi
exit \$status
"@

& docker run --rm -t `
  -v "${repoRoot}:/workspace" `
  -w /workspace `
  $imageName `
  bash -c $script
