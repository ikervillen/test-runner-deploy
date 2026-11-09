param(
    [Parameter(Mandatory = $true)][string]$Site,
    [Parameter(Mandatory = $true)][ValidateSet('main', 'develop')][string]$Branch,
    [string]$Image,
    [string]$Commit,
    [Parameter(Mandatory = $true)][string]$RunUrl,
    [string]$Python = 'python',
    [ValidateSet('Deploy', 'Rollback')][string]$Mode = 'Deploy'
)
$ErrorActionPreference = 'Stop'
$projectDirectory = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $env:INGESTA_GITHUB_TOKEN -or -not $env:INGESTA_REGISTRY_USER) {
    throw 'Faltan las credenciales efimeras del job de GitHub Actions.'
}
$originalDockerConfig = $env:DOCKER_CONFIG
$sourceDockerConfig = if ($originalDockerConfig) { $originalDockerConfig } else {
    Join-Path $env:USERPROFILE '.docker'
}
$temporaryParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$temporaryDockerConfig = Join-Path $temporaryParent ('fenix-registry-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temporaryDockerConfig | Out-Null
try {
    # Conserva endpoints/TLS del contexto WSL2; no copia credenciales del usuario.
    $contextsDirectory = Join-Path $sourceDockerConfig 'contexts'
    if (Test-Path -LiteralPath $contextsDirectory) {
        Copy-Item -LiteralPath $contextsDirectory -Destination $temporaryDockerConfig -Recurse
    }
    $env:DOCKER_CONFIG = $temporaryDockerConfig
    # Entregar bytes al stdin de Docker desde Python: la tubería de PowerShell 5.1
    # puede añadir un BOM al token según el perfil/configuración del runner.
    & $Python (Join-Path $projectDirectory 'deployment/registry.py')
    if ($LASTEXITCODE -ne 0) { throw 'No se ha podido autenticar el acceso al registro.' }
    Push-Location -LiteralPath $projectDirectory
    try {
        $deploymentArguments = @('-m', 'deployment.manager', '--site', $Site,
            '--branch', $Branch, '--run-url', $RunUrl)
        if ($Mode -eq 'Rollback') { $deploymentArguments += '--rollback' }
        else {
            if (-not $Image -or -not $Commit) { throw 'Falta imagen o commit de despliegue.' }
            $deploymentArguments += @('--image', $Image, '--commit', $Commit)
        }
        & $Python @deploymentArguments
        if ($LASTEXITCODE -ne 0) { throw 'El despliegue no se ha completado. Consultar su recibo/journal.' }
        if ($Branch -eq 'develop') {
            & $Python -m deployment.inventory --site $Site --branch $Branch
            if ($LASTEXITCODE -ne 0) {
                Write-Warning 'Release activada. No se pudo generar el inventario Docker.'
                $global:LASTEXITCODE = 0
            }
        }
    }
    finally { Pop-Location }
}
finally {
    $env:DOCKER_CONFIG = $originalDockerConfig
    $resolvedTemporary = [IO.Path]::GetFullPath($temporaryDockerConfig)
    if (-not $resolvedTemporary.StartsWith($temporaryParent, [StringComparison]::OrdinalIgnoreCase) `
        -or -not ([IO.Path]::GetFileName($resolvedTemporary)).StartsWith('fenix-registry-')) {
        throw 'Ruta temporal fuera del directorio previsto; no se elimina.'
    }
    Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
}
