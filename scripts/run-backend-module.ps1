[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Module,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $repositoryRoot 'backend'
$previousPythonPath = $env:PYTHONPATH

try {
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$backendPath$([System.IO.Path]::PathSeparator)$previousPythonPath"
    }
    else {
        $backendPath
    }
    Push-Location $repositoryRoot
    & uv run python -m $Module @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "El modulo backend finalizo con codigo ${LASTEXITCODE}: $Module"
    }
}
finally {
    if ($null -eq $previousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $previousPythonPath
    }
    Pop-Location
}
