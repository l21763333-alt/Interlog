[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ReportArgs
)

$ErrorActionPreference = "Stop"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$runtime = Join-Path $PSScriptRoot "iterlog.py"
$payload = [Console]::In.ReadToEnd()
$env:ITERLOG_HOST = "codex"

$candidates = @()
if ($env:ITERLOG_PYTHON) {
    $candidates += ,@($env:ITERLOG_PYTHON, @())
}
$candidates += ,@("python", @())
$candidates += ,@("py", @("-3"))
$candidates += ,@("python3", @())

foreach ($candidate in $candidates) {
    $command = Get-Command $candidate[0] -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        continue
    }
    $prefix = @($candidate[1])
    $probe = & $command.Source @prefix --version 2>&1
    if ($LASTEXITCODE -ne 0 -or "$probe" -notmatch "Python\s+(\d+)\.(\d+)") {
        continue
    }
    $major = [int] $Matches[1]
    $minor = [int] $Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        continue
    }
    $payload | & $command.Source @prefix $runtime @ReportArgs
    exit $LASTEXITCODE
}

Write-Error "Iterlog requires Python 3.10+. Set ITERLOG_PYTHON to an absolute interpreter path."
exit 1
