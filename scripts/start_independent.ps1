param(
    [Parameter(Mandatory = $true)][string]$Site,
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][string]$Revision,
    [Parameter(Mandatory = $true)][string]$GoldRevision,
    [switch]$LiveOnly
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$encoding = [Text.UTF8Encoding]::new($false)
if ($Revision -cnotmatch '^[0-9a-f]{40}$') { throw 'La revisión de ingesta debe ser un SHA completo.' }
if ($GoldRevision -cnotmatch '^[0-9a-f]{40}$') { throw 'La revisión Gold debe ser un SHA completo.' }
if ($Image -cnotmatch '^ghcr\.io/ikervillen/test-runner-deploy@sha256:[0-9a-f]{64}$') {
    throw 'Se requiere el digest de la imagen publicada en GHCR.'
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
$previousCompose = $null
if ($LiveOnly) {
    $running = @(Invoke-Docker ps --filter "label=com.docker.compose.project=$($profile.project)" `
        --filter 'label=com.docker.compose.service=ingesta-live' --format '{{.ID}}')
    if ($running.Count -ne 1) { throw 'Se esperaba un único contenedor live en ejecución.' }
    $location = (Invoke-Docker inspect $running[0] --format '{{json .Config.Labels}}') | ConvertFrom-Json
    $previousFile = $location.'com.docker.compose.project.config_files'
    if (-not (Test-Path -LiteralPath $previousFile -PathType Leaf)) { throw 'No se encuentra el Compose activo.' }
    $previousCompose = [IO.File]::ReadAllText($previousFile)
}
$profile.forbidden_volumes = @($profile.forbidden_volumes) + @(
    $oldSite.environments.main.volume, $oldSite.environments.develop.volume
)
$liveCpus = [int]$profile.resources.'ingesta-live'.cpus
$sharedCpus = [int]$info.NCPU - $liveCpus
if ($sharedCpus -lt 1) { throw 'Docker no dispone de CPU aparte de las reservadas para live.' }
$index = 0
foreach ($service in $profile.resources.PSObject.Properties) {
    if ($service.Name -eq 'ingesta-live') { continue }
    $service.Value.cpuset = [string]($liveCpus + ($index % $sharedCpus))
    $index += 1
}
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

$bundleRoot = (Resolve-Path -LiteralPath $Bundle).Path
$archive = Join-Path $bundleRoot 'image.tar.gz'
$manifest = Get-Content -LiteralPath (Join-Path $bundleRoot 'image.json') -Raw | ConvertFrom-Json
$localImage = 'fenix-independent:sha-' + $Revision
if ($manifest.registry_ref -cne $Image -or $manifest.local_tag -cne $localImage -or
    $manifest.image_id -cnotmatch '^sha256:[a-f0-9]{64}$' -or
    $manifest.archive_sha256 -cnotmatch '^[a-f0-9]{64}$') {
    throw 'El manifiesto del artefacto no corresponde a la imagen aprobada.'
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -cne $manifest.archive_sha256) {
    throw 'El archivo de imagen no coincide con la huella verificada.'
}
Invoke-Docker image load --input $archive
$loadedId = (Invoke-Docker image inspect $localImage --format '{{.Id}}') -join ''
if ($loadedId -cne $manifest.image_id) { throw 'Docker cargó una imagen distinta de la esperada.' }
$labels = ((Invoke-Docker image inspect $localImage --format '{{json .Config.Labels}}') -join '') | ConvertFrom-Json
if ($labels.'org.opencontainers.image.revision' -cne $Revision -or
    $labels.'es.fenix.ingesta.gold.revision' -cne $GoldRevision) {
    throw 'La imagen no corresponde a los commits aprobados.'
}
$temporary = @('run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m')
$scoreText = (Invoke-Docker @temporary --entrypoint cat $localImage '/opt/quality/config/customer_score.json') -join "`n"
Write-Config $profile.score_file $scoreText
$configMount = "type=bind,src=$configBind,dst=/staging"
Invoke-Docker @temporary --mount $configMount --entrypoint python $localImage `
    -m deployment.independent --site /staging/site.json --pipelines "/staging/$($profile.pipelines_file)" `
    --host-info /staging/host.json --output /staging/compose.json
$inputMount = "type=bind,src=$($profile.input_bind),dst=$($profile.input_target),readonly"
$probe = "import sys; from pathlib import Path; from ingesta_delta_lake.config import load_config; load_config(Path(sys.argv[1]))"
Invoke-Docker @temporary --mount "$configMount,readonly" --mount $inputMount `
    --entrypoint python $localImage -c $probe "/staging/$($profile.source_file)"
$composeFile = Join-Path $configDirectory 'compose.json'
$compose = Get-Content -LiteralPath $composeFile -Raw | ConvertFrom-Json
foreach ($service in $compose.services.PSObject.Properties) {
    if ($service.Value.image -cne $Image) { throw 'El Compose refiere otra imagen.' }
    $service.Value.image = $localImage
}
Write-Config 'compose.json' ($compose | ConvertTo-Json -Depth 30)
Invoke-Docker compose -f $composeFile config --quiet
if ($LiveOnly) {
    $activeStack = $previousCompose | ConvertFrom-Json
    $liveVolumes = @($compose.services.'ingesta-live'.volumes | Where-Object { $_.type -eq 'volume' })
    foreach ($mount in $liveVolumes) {
        $currentVolume = $activeStack.volumes.($mount.source)
        if (-not $currentVolume -or $currentVolume.name -cne $compose.volumes.($mount.source).name -or
            $currentVolume.name -notin $present) {
            throw "El volumen activo de live ha cambiado o falta: $($mount.source)."
        }
    }
    $activeStack.services.'ingesta-live' = $compose.services.'ingesta-live'
    Write-Config 'compose.json' ($activeStack | ConvertTo-Json -Depth 30)
    Write-Config 'compose.rollback.json' $previousCompose
    Invoke-Docker compose -f $composeFile config --quiet
    $timeout = [int]$oldSite.drain_seconds
    try {
        Invoke-Docker compose -f $composeFile up -d --no-deps --no-build --pull never `
            --timeout $timeout --wait --wait-timeout 325 ingesta-live
    } catch {
        $rollback = Join-Path $configDirectory 'compose.rollback.json'
        Invoke-Docker compose -f $rollback up -d --no-deps --no-build --pull never `
            --timeout $timeout ingesta-live
        throw
    }
} else {
    Invoke-Docker compose -f $composeFile up -d --pull never --no-build
}
Invoke-Docker compose -f $composeFile ps --all
Write-Output "Compose: $composeFile"
