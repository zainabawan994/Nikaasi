# Require passwords for local PostgreSQL connections (trust -> scram-sha-256).
# Works from a normal PowerShell window when your account can edit pg_hba.conf
# (it applies the change with pg_reload_conf(), so no service restart is needed):
#   powershell -ExecutionPolicy Bypass -File .\scripts\secure-postgres.ps1
# Make sure you know the 'postgres' password first: after this, PostgreSQL asks for it.

$ErrorActionPreference = 'Stop'
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
$hba = 'C:\Program Files\PostgreSQL\18\data\pg_hba.conf'

if (-not (Test-Path $hba)) { Write-Host "Not found: $hba" -ForegroundColor Red; exit 1 }

$lines = Get-Content $hba
$targets = @($lines | Where-Object { $_ -match '^\s*host\s' -and $_ -match '\btrust\s*$' })
if ($targets.Count -eq 0) {
    Write-Host 'No "trust" host lines found - already secured.' -ForegroundColor Green
    exit 0
}

$backup = "$hba.bak-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
try {
    Copy-Item $hba $backup
} catch {
    Write-Host "Cannot write next to pg_hba.conf ($($_.Exception.Message)). Run this from an Administrator PowerShell." -ForegroundColor Red
    exit 1
}
Write-Host "Backup saved: $backup"

$out = foreach ($l in $lines) {
    if ($l -match '^\s*host\s' -and $l -match '\btrust\s*$') { $l -replace '\btrust\s*$', 'scram-sha-256' } else { $l }
}
try {
    Set-Content -Path $hba -Value $out -Encoding ascii
} catch {
    Write-Host "Could not save pg_hba.conf: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
Write-Host "Changed $($targets.Count) line(s) from trust to scram-sha-256:" -ForegroundColor Green
Get-Content $hba | Where-Object { $_ -match '^\s*host\s' } | ForEach-Object { Write-Host "  $_" }

# Apply without restarting the Windows service (this connection still uses the old rules)
$ErrorActionPreference = 'Continue'
$reload = & "$pgBin\psql.exe" -U postgres -h localhost -w -tAc "SELECT pg_reload_conf()" 2>&1
if ("$reload".Trim() -eq 't') {
    Write-Host 'PostgreSQL reloaded its configuration. Passwords are now required.' -ForegroundColor Green
} elseif ("$reload" -match 'no password supplied|password authentication failed') {
    # On Windows every new connection re-reads pg_hba.conf, so the rule is already active
    Write-Host 'Passwords are now required (PostgreSQL already asked this script for one).' -ForegroundColor Green
} else {
    Write-Host "File saved, but reload failed: $reload" -ForegroundColor Yellow
    Write-Host 'Restart the "postgresql-x64-18" service (Services app) or reboot to apply it.' -ForegroundColor Yellow
}
Write-Host "To undo: Copy-Item '$backup' '$hba' -Force, then reload or restart PostgreSQL."
