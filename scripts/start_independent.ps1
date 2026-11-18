param(
    [Parameter(Mandatory = $true)][string]$Site,
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$Revision,
    [Parameter(Mandatory = $true)][string]$GoldRevision
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$encoding = [Text.UTF8Encoding]::new($false)
if ($Revision -notmatch '^[0-9a-f]{40}$' -or $GoldRevision -notmatch '^[0-9a-f]{40}$' -or
    $Image -cnotmatch '^ghcr\.io/ikervillen/test-runner-deploy@sha256:[0-9a-f]{64}$') {
    throw 'La imagen y ambas revisiones deben estar fijadas.'
}
if (-not $env:INGESTA_GITHUB_TOKEN -or $env:INGESTA_REGISTRY_USER -notmatch '^[A-Za-z0-9_-]+(?:\[bot\])?$') {
    throw 'Faltan las credenciales efímeras de GHCR.'
}
$existingSite = Test-Path -LiteralPath $Site -PathType Leaf
$siteFile = if ($existingSite) { $Site } else { Join-Path $root 'deployment/site.example.json' }
$oldSite = Get-Content -LiteralPath $siteFile -Raw | ConvertFrom-Json
if (-not $existingSite) {
    if (-not $env:FENIX_INPUT_BIND -or -not $env:FENIX_DOCKER_CONTEXT) {
        throw 'Falta la ubicación de Fenix o del contexto Docker.'
    }
    $oldSite.input_bind = $env:FENIX_INPUT_BIND
    $oldSite.docker_context = $env:FENIX_DOCKER_CONTEXT
}
$context = $oldSite.docker_context

function Invoke-Docker {
    & docker --context $context @args
    if ($LASTEXITCODE -ne 0) { throw "Docker falló al ejecutar $($args[0])." }
}

function Write-Config([string]$name, [string]$value) {
    $path = Join-Path $configDirectory $name
    if ((Test-Path -LiteralPath $path) -and [IO.File]::ReadAllText($path) -ceq $value) { return }
    $tmp = "$path.tmp"
    [IO.File]::WriteAllText($tmp, $value, $encoding)
    Move-Item -LiteralPath $tmp -Destination $path -Force
}

function Login-Registry {
    $process = [Diagnostics.ProcessStartInfo]::new()
    $process.FileName = (Get-Command docker -ErrorAction Stop).Source
    $process.Arguments = "login ghcr.io --username $env:INGESTA_REGISTRY_USER --password-stdin"
    $process.UseShellExecute = $false
    $process.RedirectStandardInput = $true
    $process.CreateNoWindow = $true
    $login = [Diagnostics.Process]::Start($process)
    try {
        # Write the token as bytes to avoid Windows PowerShell's pipeline BOM.
        $token = $encoding.GetBytes($env:INGESTA_GITHUB_TOKEN + "`n")
        $login.StandardInput.BaseStream.Write($token, 0, $token.Length)
        $login.StandardInput.BaseStream.Flush()
        $login.StandardInput.BaseStream.Close()
        $login.WaitForExit()
        if ($login.ExitCode -ne 0) { throw 'No se pudo autenticar GHCR.' }
    } finally {
        $login.Dispose()
    }
}

$infoJson = (Invoke-Docker info --format '{{json .}}') -join "`n"
$info = $infoJson | ConvertFrom-Json
Write-Output "Docker: $($info.NCPU) CPU y $([Math]::Round($info.MemTotal / 1GB, 2)) GiB"
Invoke-Docker compose version
$oldWorkers = @(Invoke-Docker ps --filter 'label=es.fenix.ingesta.channel=develop' --format '{{.Names}}')
$oldGold = @(Invoke-Docker ps --filter 'label=com.docker.compose.project=calidad-serie-develop' --format '{{.Names}}')
if ($oldWorkers.Count -or $oldGold.Count) {
    throw "El stack anterior sigue activo: $(@($oldWorkers + $oldGold) -join ', ')."
}

$profile = Get-Content -LiteralPath (Join-Path $root 'deployment/independent.example.json') -Raw | ConvertFrom-Json
$configDirectory = Join-Path $env:LOCALAPPDATA "FenixIngesta/independent/$($Revision.Substring(0, 7))"
New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
$configBind = ([IO.Path]::GetFullPath($configDirectory)).Replace('\', '/')
if ($configBind.Contains(',') -or $oldSite.input_bind.Contains(',')) { throw 'Ruta de montaje inválida.' }
$profile.image = $Image
$profile.configuration_bind = $configBind
$profile.input_bind = $oldSite.input_bind
$profile.forbidden_volumes = @($profile.forbidden_volumes) + @(
    $oldSite.environments.main.volume, $oldSite.environments.develop.volume
)
$present = @(Invoke-Docker volume ls --format '{{.Name}}')
foreach ($lane in $profile.volumes.PSObject.Properties) {
    foreach ($store in $lane.Value.PSObject.Properties) {
        if ($store.Value -notin $present) { continue }
        $labels = ((Invoke-Docker volume inspect $store.Value --format '{{json .Labels}}') -join '') | ConvertFrom-Json
        if ($labels.'com.docker.compose.project' -ne $profile.project -or
            $labels.'com.docker.compose.volume' -ne "$($lane.Name)-$($store.Name)") {
            throw "El volumen $($store.Value) pertenece a otro proyecto."
        }
    }
}

$catalogBytes = [Convert]::FromBase64String($env:GOLD_CATALOGS_GZIP_B64)
$catalogStream = [IO.MemoryStream]::new($catalogBytes)
$decompress = [IO.Compression.GzipStream]::new($catalogStream, [IO.Compression.CompressionMode]::Decompress)
$reader = [IO.StreamReader]::new($decompress, $encoding)
try { $catalogText = $reader.ReadToEnd() }
finally { $reader.Dispose() }
$catalog = $catalogText | ConvertFrom-Json
if ($catalog.schema_id -ne 'quality-catalog-bundle-v1' -or -not $catalog.model_segments) {
    throw 'Falta el catálogo Gold de calidad de serie.'
}

$settings = $oldSite.settings
$sourceToml = @(
    'environment = "development"'
    ('plant = ' + (ConvertTo-Json -InputObject $settings.plant -Compress))
    ('encoding = ' + (ConvertTo-Json -InputObject $settings.encoding -Compress))
    ('input_root = ' + (ConvertTo-Json -InputObject $profile.input_target -Compress))
    'bronze_root = "/unused/bronze"'
    'silver_root = "/unused/silver"'
    'control_root = "/unused/control"'
    'control_format = 3'
    'timestamp_policy = "local_naive_test_v1"'
    'topology_enabled = true'
)
foreach ($section in @('sources', 'line_by_origin', 'line_code_by_origin')) {
    $values = @($settings.$section.PSObject.Properties)
    if (-not $values.Count) { throw "Falta la configuración $section." }
    $sourceToml += "[$section]"
    foreach ($item in $values) {
        $key = ConvertTo-Json -InputObject $item.Name -Compress
        $value = ConvertTo-Json -InputObject $item.Value -Compress
        $sourceToml += "$key = $value"
    }
}
Write-Config 'site.json' ($profile | ConvertTo-Json -Depth 10)
Write-Config 'host.json' $infoJson
Write-Config $profile.pipelines_file ([IO.File]::ReadAllText((Join-Path $root 'deployment/pipelines.example.json')))
Write-Config $profile.source_file (($sourceToml -join "`n") + "`n")
Write-Config $profile.catalogs_file $catalogText
# The backend is bound to loopback, as in the independent site template.
Write-Config $profile.api_env_file "APP_AUTH_MODE=disabled`n"

$originalConfig = $env:DOCKER_CONFIG
$dockerConfig = if ($originalConfig) { $originalConfig } else { Join-Path $env:USERPROFILE '.docker' }
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$tempConfig = Join-Path $tempRoot ('fenix-independent-registry-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempConfig | Out-Null
try {
    $contexts = Join-Path $dockerConfig 'contexts'
    if (Test-Path -LiteralPath $contexts) {
        Copy-Item -LiteralPath $contexts -Destination $tempConfig -Recurse
    }
    $env:DOCKER_CONFIG = $tempConfig
    Login-Registry
    Invoke-Docker pull $Image
    $labels = ((Invoke-Docker image inspect $Image --format '{{json .Config.Labels}}') -join '') | ConvertFrom-Json
    if ($labels.'org.opencontainers.image.revision' -cne $Revision -or
        $labels.'es.fenix.ingesta.gold.revision' -cne $GoldRevision) {
        throw 'La imagen no corresponde a los commits aprobados.'
    }
    $temporary = @('run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m')
    $scoreText = (Invoke-Docker @temporary --entrypoint cat $Image '/opt/quality/config/customer_score.json') -join "`n"
    Write-Config $profile.score_file $scoreText
    $configMount = "type=bind,src=$configBind,dst=/staging"
    Invoke-Docker @temporary --mount $configMount --entrypoint python $Image `
        -m deployment.independent --site /staging/site.json --pipelines "/staging/$($profile.pipelines_file)" `
        --host-info /staging/host.json --output /staging/compose.json
    $inputMount = "type=bind,src=$($profile.input_bind),dst=$($profile.input_target),readonly"
    $probe = "import sys; from pathlib import Path; from ingesta_delta_lake.config import load_config; load_config(Path(sys.argv[1]))"
    Invoke-Docker @temporary --mount "$configMount,readonly" --mount $inputMount `
        --entrypoint python $Image -c $probe "/staging/$($profile.source_file)"
    $composeFile = Join-Path $configDirectory 'compose.json'
    Invoke-Docker compose -f $composeFile config --quiet
    Invoke-Docker compose -f $composeFile up -d --pull never --no-build
    Invoke-Docker compose -f $composeFile ps --all
    Write-Output "Compose: $composeFile"
} finally {
    $env:DOCKER_CONFIG = $originalConfig
    $resolved = [IO.Path]::GetFullPath($tempConfig)
    if ([IO.Path]::GetDirectoryName($resolved).TrimEnd('\') -eq $tempRoot.TrimEnd('\') -and
        [IO.Path]::GetFileName($resolved) -match '^fenix-independent-registry-[0-9a-f]{32}$') {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
