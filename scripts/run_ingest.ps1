param(
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [string]$EnvironmentPath = (Join-Path $env:LOCALAPPDATA 'Ingesta_Delta_Lake/envs/dev-0.1')
)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $EnvironmentPath 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'El entorno no existe. Prepararlo previamente con setup.ps1.'
}
$resolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
& $pythonPath -m ingesta_delta_lake ingest --config $resolvedConfig
exit $LASTEXITCODE
