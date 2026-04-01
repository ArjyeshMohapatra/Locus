[CmdletBinding()]
param(
  [string]$ServiceName = "LocusBackend",
  [string]$PythonExe = "",
  [string]$ServiceEntry = "",
  [string]$Host = "127.0.0.1",
  [int]$Port = 8000,
  [string]$DataDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
  throw "Run this script from an elevated PowerShell session (Run as Administrator)."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.." )).Path
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  $PythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
}
if ([string]::IsNullOrWhiteSpace($ServiceEntry)) {
  $ServiceEntry = Join-Path $repoRoot "backend\service_entry.py"
}
if ([string]::IsNullOrWhiteSpace($DataDir)) {
  $DataDir = Join-Path $env:LOCALAPPDATA "locus"
}

$pythonResolved = (Resolve-Path $PythonExe).Path
$entryResolved = (Resolve-Path $ServiceEntry).Path
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null

$binaryPath = "`"$pythonResolved`" `"$entryResolved`" --host $Host --port $Port --data-dir `"$DataDir`""

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
  sc.exe stop $ServiceName | Out-Null
  Start-Sleep -Seconds 1
  sc.exe delete $ServiceName | Out-Null
  Start-Sleep -Seconds 1
}

sc.exe create $ServiceName binPath= $binaryPath start= auto DisplayName= "LOCUS Backend" | Out-Null
sc.exe description $ServiceName "LOCUS backend API service" | Out-Null
sc.exe start $ServiceName | Out-Null

Get-Service -Name $ServiceName | Format-Table -AutoSize
Write-Host "Installed and started $ServiceName"
Write-Host "Check status with: sc.exe query $ServiceName"
Write-Host "Tail logs with: Get-WinEvent -LogName Application -MaxEvents 50"
