[CmdletBinding()]
param(
  [string]$ServiceName = "LocusBackend"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $existing) {
  Write-Host "Service $ServiceName not found."
  exit 0
}

sc.exe stop $ServiceName | Out-Null
Start-Sleep -Seconds 1
sc.exe delete $ServiceName | Out-Null

Write-Host "Removed service $ServiceName"
