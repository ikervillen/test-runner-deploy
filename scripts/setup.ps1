param(
    [string]$Python = '3.12',
    [string]$EnvironmentPath = (Join-Path $env:LOCALAPPDATA 'Ingesta_Delta_Lake/envs/dev-0.1')
)
$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$environmentFullPath = [IO.Path]::GetFullPath($EnvironmentPath)
if ($environmentFullPath.StartsWith($projectPath + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase) -or $environmentFullPath -eq $projectPath) {
    throw 'El entorno virtual debe estar fuera del directorio de codigo.'
}
$uvCommand = Get-Command uv -ErrorAction Stop
$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
Push-Location -LiteralPath $projectPath
try {
    $env:UV_PROJECT_ENVIRONMENT = $environmentFullPath
    & $uvCommand.Source sync --locked --python $Python
    if ($LASTEXITCODE -ne 0) { throw 'No se ha podido preparar el entorno bloqueado.' }
    Write-Output "Entorno listo: $environmentFullPath"
    Write-Output 'No se ha ejecutado ingesta ni inicializado datos Delta.'
}
finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
    Pop-Location
}
