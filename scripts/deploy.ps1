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
    $env:INGESTA_GITHUB_TOKEN | docker login ghcr.io --username $env:INGESTA_REGISTRY_USER --password-stdin
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
