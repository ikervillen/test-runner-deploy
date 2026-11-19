param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$Revision,
    [Parameter(Mandatory = $true)][string]$GoldRevision
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$profile = Get-Content -LiteralPath (Join-Path $root 'deployment/independent.example.json') -Raw | ConvertFrom-Json
$sitePath = $env:FENIX_SITE
$hasSite = $sitePath -and (Test-Path -LiteralPath $sitePath -PathType Leaf)
if (-not $hasSite) { $sitePath = Join-Path $root 'deployment/site.example.json' }
$site = Get-Content -LiteralPath $sitePath -Raw | ConvertFrom-Json
$context = if ($hasSite) { $site.docker_context } else { $env:FENIX_DOCKER_CONTEXT }
if (-not $context) { throw 'No se ha configurado el contexto Docker.' }
$project = $profile.project
$services = @('ingesta-live', 'ingesta-history', 'gold-live')

function Invoke-Docker {
    & docker --context $context @args
    if ($LASTEXITCODE -ne 0) { throw "Docker falló al ejecutar $($args[0])." }
}

function Get-Worker([string]$Service) {
    $ids = @(Invoke-Docker ps --filter "label=com.docker.compose.project=$project" `
        --filter "label=com.docker.compose.service=$Service" --format '{{.ID}}')
    if ($ids.Count -ne 1) { throw "Se esperaba un único contenedor activo para $Service." }
    $items = ((Invoke-Docker inspect $ids[0]) -join "`n") | ConvertFrom-Json
    return @($items)[0]
}

if ($Revision -cnotmatch '^[a-f0-9]{40}$' -or $GoldRevision -cnotmatch '^[a-f0-9]{40}$' -or
    $Image -cnotmatch '^ghcr\.io/ikervillen/test-runner-deploy@sha256:[a-f0-9]{64}$') {
    throw 'La imagen y ambas revisiones deben estar fijadas.'
}
$workers = @{}
foreach ($service in $services) { $workers[$service] = Get-Worker $service }
$live = $workers['ingesta-live']
$activeFile = $live.Config.Labels.'com.docker.compose.project.config_files'
if (-not $activeFile -or -not (Test-Path -LiteralPath $activeFile -PathType Leaf)) {
    throw 'No se encuentra el Compose activo de ingesta-live.'
}
$compose = Get-Content -LiteralPath $activeFile -Raw | ConvertFrom-Json
if ($compose.name -cne $project) { throw 'El Compose activo pertenece a otro proyecto.' }
$tag = "fenix-independent:sha-$Revision"
foreach ($service in $services) {
    $definition = $compose.services.$service
    if (-not $definition) { throw "El Compose activo no contiene $service." }
    $previousImageId = (Invoke-Docker image inspect $definition.image --format '{{.Id}}') -join ''
    if ($previousImageId -cne $workers[$service].Image) {
        $serviceFile = $workers[$service].Config.Labels.'com.docker.compose.project.config_files'
        $ownCompose = Get-Content -LiteralPath $serviceFile -Raw | ConvertFrom-Json
        $ownId = (Invoke-Docker image inspect $ownCompose.services.$service.image --format '{{.Id}}') -join ''
        if ($definition.image -cne $tag -or $ownCompose.name -cne $project -or $ownId -cne $workers[$service].Image) {
            throw "La imagen actual de $service difiere del Compose activo."
        }
    }
    $expected = @($definition.volumes | Where-Object { $_.type -eq 'volume' } | ForEach-Object {
        "$($compose.volumes.($_.source).name):$($_.target)"
    } | Sort-Object)
    $mounted = @($workers[$service].Mounts | Where-Object { $_.Type -eq 'volume' } | ForEach-Object {
        "$($_.Name):$($_.Destination)"
    } | Sort-Object)
    if (-not $expected.Count -or ($expected -join "`n") -cne ($mounted -join "`n")) {
        throw "Los volúmenes de $service difieren del Compose activo."
    }
    $definition.image = $tag
}

$archive = Join-Path $Bundle 'image.tar.gz'
$proof = Get-Content -LiteralPath (Join-Path $Bundle 'image.json') -Raw | ConvertFrom-Json
if ($proof.registry_ref -cne $Image -or $proof.local_tag -cne $tag -or
    $proof.image_id -cnotmatch '^sha256:[a-f0-9]{64}$' -or
    $proof.archive_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
    (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -cne $proof.archive_sha256) {
    throw 'El artefacto de imagen no coincide con su comprobante.'
}
Invoke-Docker image load --input $archive
$loaded = @(((Invoke-Docker image inspect $tag) -join "`n") | ConvertFrom-Json)[0]
if ($loaded.Id -cne $proof.image_id -or
    $loaded.Config.Labels.'org.opencontainers.image.revision' -cne $Revision -or
    $loaded.Config.Labels.'es.fenix.ingesta.gold.revision' -cne $GoldRevision) {
    throw 'Docker ha cargado una imagen distinta de la revisión aprobada.'
}

$activeDirectory = Split-Path -Parent $activeFile
$candidate = Join-Path $activeDirectory "compose.$($Revision.Substring(0, 12)).json"
[IO.File]::WriteAllText($candidate, ($compose | ConvertTo-Json -Depth 40), [Text.UTF8Encoding]::new($false))
Invoke-Docker compose -p $project -f $candidate config --quiet
$originalOperations = 'import json; from pathlib import Path; print(",".join(p.stem for p in Path("/pipelines/live/gold/published/operations").glob("*.json") if json.loads(p.read_text())["state"] != "ACKNOWLEDGED"))'
$originalPending = ($originalOperations | docker --context $context exec -i ($workers['gold-live'].Id) python -) -join ''
if ($LASTEXITCODE -ne 0) { throw 'No se pudo leer el registro actual de Gold.' }
$started = [DateTimeOffset]::UtcNow.ToString('o')
Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
    --timeout ([int]$site.drain_seconds) @services
foreach ($service in $services) {
    $worker = Get-Worker $service
    if ($worker.Image -cne $proof.image_id) {
        throw "No se ha reemplazado $service con la imagen aprobada."
    }
    Write-Output "${service} | $($worker.Id.Substring(0, 12)) | $tag"
}
Invoke-Docker compose -p $project -f $candidate ps --all
"FENIX_VERIFY_CONTEXT=$context", "FENIX_VERIFY_PROJECT=$project", "FENIX_VERIFY_SINCE=$started", `
    "FENIX_VERIFY_PENDING=$originalPending" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
