[CmdletBinding()]
param(
  [string]$Target = "x86_64-pc-windows-msvc",
  [switch]$SkipPythonDeps,
  [switch]$SkipUiInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Command {
  param([Parameter(Mandatory = $true)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command '$Name' was not found in PATH."
  }
}

function Write-Step {
  param([Parameter(Mandatory = $true)][string]$Message)
  Write-Host "[windows-build] $Message"
}

if (-not $IsWindows) {
  throw "This script must be run on Windows."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$backendDistExe = Join-Path $repoRoot "backend\dist\locus-backend.exe"
$backendSidecarOut = Join-Path $repoRoot "src-tauri\binaries\locus-backend-$Target.exe"
$probeExe = Join-Path $repoRoot "tools\window-probe\target\$Target\release\locus-window-probe.exe"
$probeSidecarOut = Join-Path $repoRoot "src-tauri\binaries\locus-window-probe-$Target.exe"

Require-Command -Name "python"
Require-Command -Name "npm"
Require-Command -Name "cargo"

Push-Location $repoRoot
try {
  if (-not (Test-Path $venvPython)) {
    Write-Step "creating virtualenv at .venv"
    & python -m venv "$repoRoot/.venv"
  }

  Write-Step "updating pip in virtualenv"
  & $venvPython -m pip install --upgrade pip

  if (-not $SkipPythonDeps) {
    Write-Step "installing backend requirements + pyinstaller"
    & $venvPython -m pip install -r "$repoRoot/backend/requirements.txt" pyinstaller
  }

  if (-not $SkipUiInstall) {
    Write-Step "installing UI dependencies"
    & npm ci --prefix "$repoRoot/ui"
  }

  Write-Step "building UI"
  & npm run build --prefix "$repoRoot/ui"

  Write-Step "building Python backend sidecar"
  New-Item -ItemType Directory -Path "$repoRoot/src-tauri/binaries" -Force | Out-Null
  & $venvPython -m PyInstaller "$repoRoot/backend/app/main.py" `
    --name locus-backend `
    --onefile `
    --noconsole `
    --paths "$repoRoot/backend" `
    --distpath "$repoRoot/backend/dist" `
    --workpath "$repoRoot/backend/build/pyinstaller" `
    --specpath "$repoRoot/backend/build" `
    --clean

  if (-not (Test-Path $backendDistExe)) {
    throw "Expected backend artifact not found: $backendDistExe"
  }
  Copy-Item -Path $backendDistExe -Destination $backendSidecarOut -Force

  Write-Step "building window-probe sidecar"
  & cargo build --release --manifest-path "$repoRoot/tools/window-probe/Cargo.toml" --target $Target

  if (-not (Test-Path $probeExe)) {
    throw "Expected window-probe artifact not found: $probeExe"
  }
  Copy-Item -Path $probeExe -Destination $probeSidecarOut -Force

  Write-Step "building Tauri bundle"
  Push-Location "$repoRoot/src-tauri"
  try {
    & cargo tauri build --config tauri.conf.json --target $Target
  }
  finally {
    Pop-Location
  }

  Write-Step "done. Bundles are in src-tauri/target/$Target/release/bundle"
}
finally {
  Pop-Location
}
