# Run from the existing investMITRA project directory after replacing both files.
param([string]$ProjectRoot = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$engine = Join-Path $ProjectRoot 'intraday_signals.py'
$manager = Join-Path $ProjectRoot 'order_manager.py'
if (!(Test-Path -LiteralPath $engine) -or !(Test-Path -LiteralPath $manager)) {
    throw 'Place this launcher beside the updated intraday_signals.py and order_manager.py.'
}
# Inspect only process identity. Do not print full command lines or credentials,
# and do not kill processes that might own positions or other applications.
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?([0-9.]*)?\.exe$' -and
    $_.CommandLine -match '(intraday_signals|order_manager)[^\r\n]*\.py'
})
if ($running.Count -gt 0) {
    $ids = ($running | ForEach-Object { $_.ProcessId }) -join ', '
    throw "Existing trading Python process(es), PID: $ids. Stop the old trading consoles/tasks and confirm their positions before starting this trial. No process was killed."
}
$expectedBuild = '2026-09-11-trial-fix1'
if (!(Select-String -LiteralPath $manager -SimpleMatch $expectedBuild -Quiet)) {
    throw 'order_manager.py does not match this trial build. Replace both Python files together.'
}
$env:INVESTMITRA_EXECUTION_MODE = 'auto_paper'
$env:INVESTMITRA_LIVE_TRADING = 'NO'
$env:PYTHONUNBUFFERED = '1'
# Use one fixed local journal path so working-directory changes cannot reset it.
if (!$env:INVESTMITRA_EXECUTION_DB) {
    $env:INVESTMITRA_EXECUTION_DB = Join-Path $ProjectRoot 'data\execution_auto_paper.sqlite3'
}
$logdir = Join-Path $ProjectRoot 'logs'
New-Item -ItemType Directory -Path $logdir -Force | Out-Null
$logfile = Join-Path $logdir ('auto_paper_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
Write-Host "BUILD=$expectedBuild MODE=auto_paper"
Write-Host "Engine: $engine"
Write-Host "Log: $logfile"
Push-Location $ProjectRoot
try {
    # Windows PowerShell can classify Python stderr logging as an error record.
    # Capture it in the daily log while preserving the process exit code.
    $ErrorActionPreference = 'Continue'
    & python -u $engine 2>&1 | Tee-Object -FilePath $logfile
    $engineExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $engineExit
