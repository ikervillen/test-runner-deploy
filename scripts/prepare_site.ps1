param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][ValidateSet('main', 'develop')][string]$Branch,
    [string]$SitePath,
    [string]$InputBind,
    [string]$DockerContext
)
$ErrorActionPreference = 'Stop'
if ($SitePath) {
    if (-not (Test-Path -LiteralPath $SitePath -PathType Leaf)) {
        throw 'INGESTA_SITE_CONFIG apunta a un archivo inexistente.'
    }
    Write-Output (Resolve-Path -LiteralPath $SitePath).Path
    return
}
if (-not $InputBind) {
    throw 'Configurar INGESTA_INPUT_BIND con la carpeta Fenix admitida por Docker/WSL2.'
}
if (-not $DockerContext) {
    $DockerContext = & docker context show
    if ($LASTEXITCODE -ne 0) { throw 'No se puede determinar el contexto Docker del runner.' }
    $DockerContext = $DockerContext.Trim()
}
$taskTemplate = Join-Path $PSScriptRoot '../deployment/site.example.json'
$taskSite = Get-Content -LiteralPath $taskTemplate -Raw | ConvertFrom-Json
$taskSite.repository = $Repository
$taskSite.input_bind = $InputBind
$taskSite.docker_context = $DockerContext
$taskDirectory = Join-Path $env:LOCALAPPDATA 'FenixIngesta'
$taskSite.state_root = Join-Path $taskDirectory 'deployments'
# Identidades estables por repositorio/canal; no cambia al actualizar código o reiniciar.
foreach ($taskChannel in @('main', 'develop')) {
    $taskDigest = [Security.Cryptography.SHA256]::Create()
    try {
        $taskBytes = [Text.Encoding]::UTF8.GetBytes($Repository.ToLowerInvariant() + ':' + $taskChannel)
        $taskHex = [BitConverter]::ToString($taskDigest.ComputeHash($taskBytes)).Replace('-', '').ToLowerInvariant()
        $taskSite.environments.$taskChannel.instance = $taskHex.Substring(0, 32)
    }
    finally { $taskDigest.Dispose() }
}
New-Item -ItemType Directory -Path $taskDirectory -Force | Out-Null
$taskTarget = Join-Path $taskDirectory ('site-' + $Branch + '.json')
$taskJson = $taskSite | ConvertTo-Json -Depth 10
$taskTemporary = $taskTarget + '.tmp'
[IO.File]::WriteAllText($taskTemporary, $taskJson, [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $taskTemporary -Destination $taskTarget -Force
Write-Output $taskTarget
