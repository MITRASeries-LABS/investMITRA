# investMITRA auto_paper launcher
# Run from project root: powershell -ExecutionPolicy Bypass -File .\start_auto_paper.ps1
param([string]$ProjectRoot = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path

# Look for engine in scripts/ subfolder (standard project layout)
$engine  = Join-Path $ProjectRoot 'scripts\intraday_signals.py'
$manager = Join-Path $ProjectRoot 'scripts\order_manager.py'

# Fallback to root if not in scripts/
if (!(Test-Path -LiteralPath $engine)) {
    $engine = Join-Path $ProjectRoot 'intraday_signals.py'
}
if (!(Test-Path -LiteralPath $manager)) {
    $manager = Join-Path $ProjectRoot 'order_manager.py'
}

if (!(Test-Path -LiteralPath $engine) -or !(Test-Path -LiteralPath $manager)) {
    throw 'Cannot find intraday_signals.py or order_manager.py in scripts/ or project root.'
}

# Check for existing trading processes
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?([0-9.]*)?.exe$' -and
    $_.CommandLine -match '(intraday_signals|order_manager)[^\r\n]*.py'
})
if ($running.Count -gt 0) {
    $ids = ($running | ForEach-Object { $_.ProcessId }) -join ', '
    throw "Existing trading process(es) PID: $ids. Stop them first."
}

$expectedBuild = '2026-09-11-trial-fix1'
if (!(Select-String -LiteralPath $manager -SimpleMatch $expectedBuild -Quiet)) {
    Write-Warning 'order_manager.py build tag not found - continuing anyway'
}

# Set environment
$env:INVESTMITRA_EXECUTION_MODE = 'auto_paper'
$env:INVESTMITRA_LIVE_TRADING   = 'NO'
$env:PYTHONUNBUFFERED           = '1'

if (!$env:INVESTMITRA_EXECUTION_DB) {
    $env:INVESTMITRA_EXECUTION_DB = Join-Path $ProjectRoot 'data\execution_auto_paper.sqlite3'
}

# Create logs directory
$logdir  = Join-Path $ProjectRoot 'logs'
New-Item -ItemType Directory -Path $logdir -Force | Out-Null
$logfile = Join-Path $logdir ('auto_paper_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')

Write-Host "BUILD=$expectedBuild MODE=auto_paper"
Write-Host "Engine: $engine"
Write-Host "Log:    $logfile"

Push-Location $ProjectRoot
try {
    $ErrorActionPreference = 'Continue'
    & python -u $engine 2>&1 | Tee-Object -FilePath $logfile
    $engineExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $engineExit
