param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$Revision,
    [Parameter(Mandatory = $true)][string]$GoldRevision,
    [switch]$LiveFlowOnly,
    [switch]$GoldOnly,
    [switch]$IncludeGoldHistory,
    [switch]$IncludeBackend,
    [switch]$Immediate
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
if ($LiveFlowOnly) {
    if ($GoldOnly -or $IncludeGoldHistory -or $IncludeBackend -or $Immediate) {
        throw 'LiveFlowOnly configura el flujo completo y detiene History con drenaje.'
    }
    $services = @('gold-live', 'ingesta-live', 'backend')
} elseif ($GoldOnly) {
    $services = @('gold-live')
    if ($IncludeGoldHistory) { $services += 'gold-history' }
    if ($IncludeBackend) { $services += 'backend' }
} else {
    if ($IncludeGoldHistory) { throw 'IncludeGoldHistory requiere GoldOnly.' }
    $services = @('ingesta-live', 'ingesta-history', 'gold-live')
}

function Invoke-Docker {
    & docker --context $context @args
    if ($LASTEXITCODE -ne 0) { throw "Docker falló al ejecutar $($args[0])." }
}

function Get-Worker([string]$Service, [switch]$AllowStopped, [switch]$AllowMissing) {
    $filters = @('--filter', "label=com.docker.compose.project=$project",
        '--filter', "label=com.docker.compose.service=$Service", '--format', '{{.ID}}')
    $ids = @(Invoke-Docker ps @filters)
    if ($ids.Count -gt 1) {
        throw "Hay $($ids.Count) contenedores en ejecucion para ${Service}: $($ids -join ', ')."
    }
    if ($AllowStopped -and $ids.Count -eq 0) {
        $ids = @(Invoke-Docker ps --all @filters)
        if ($ids.Count -gt 1) {
            # A failed Compose recreation may keep an old container under a prefixed name.
            $expectedName = "$project-$Service-1"
            $canonical = @(Invoke-Docker ps --all --filter "name=^/$expectedName$" @filters)
            if ($canonical.Count -eq 1) { $ids = $canonical }
        }
    }
    if ($AllowMissing -and $ids.Count -eq 0) { return $null }
    if ($ids.Count -ne 1) {
        throw "Se esperaba un contenedor identificable para ${Service}. Encontrados: $($ids.Count) [$($ids -join ', ')]."
    }
    $items = ((Invoke-Docker inspect $ids[0]) -join "`n") | ConvertFrom-Json
    return @($items)[0]
}

function Find-SavedCompose {
    $directory = Join-Path $env:LOCALAPPDATA 'FenixIngesta/independent'
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw "No existe el directorio de Compose guardados: $directory"
    }
    $files = @(Get-ChildItem -LiteralPath $directory -Directory |
        Where-Object { $_.Name -cmatch '^[0-9a-f]{7,40}$' } |
        ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Filter 'compose*.json' -File } |
        Where-Object { $_.Name -cmatch '^compose(?:\.[0-9a-f]{7,40})?\.json$' } |
        Sort-Object LastWriteTimeUtc, FullName -Descending)
    foreach ($file in $files) {
        try { $saved = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json }
        catch { continue }
        if ($saved.name -cne $project -or
            @($services | Where-Object { -not $saved.services.$_ }).Count -or
            -not $saved.services.'ingesta-history' -or -not $saved.services.'gold-history') {
            continue
        }
        $bind = @($saved.services.'ingesta-live'.volumes | Where-Object {
            $_.type -eq 'bind' -and $_.target -ceq $profile.input_target -and $_.read_only
        })
        if ($bind.Count -ne 1 -or
            $bind[0].source.Replace('\', '/').TrimEnd('/') -ine $site.input_bind.Replace('\', '/').TrimEnd('/')) {
            continue
        }
        $config = @($saved.services.'ingesta-live'.volumes | Where-Object {
            $_.type -eq 'bind' -and $_.target -ceq $profile.configuration_target -and $_.read_only
        })
        if ($config.Count -ne 1 -or
            -not (Test-Path -LiteralPath (Join-Path $config[0].source $profile.pipelines_file) -PathType Leaf)) {
            continue
        }
        return $file.FullName
    }
    throw "No hay un Compose guardado del proyecto $project con el origen Fenix autorizado."
}

if ($Revision -cnotmatch '^[a-f0-9]{40}$' -or $GoldRevision -cnotmatch '^[a-f0-9]{40}$' -or
    $Image -cnotmatch '^ghcr\.io/ikervillen/test-runner-deploy@sha256:[a-f0-9]{64}$') {
    throw 'La imagen y ambas revisiones deben estar fijadas.'
}
if ($Immediate -and -not $GoldOnly) { throw 'El reemplazo inmediato queda limitado a los workers Gold.' }
if ($IncludeBackend -and (-not $GoldOnly -or -not $IncludeGoldHistory -or -not $Immediate)) {
    throw 'IncludeBackend requiere actualizar inmediatamente ambos workers Gold.'
}
$workers = @{}
foreach ($service in $services) {
    $workers[$service] = Get-Worker $service -AllowStopped:$LiveFlowOnly `
        -AllowMissing:$LiveFlowOnly
}
$activeFile = $null
foreach ($service in $services) {
    $candidateFile = $workers[$service].Config.Labels.'com.docker.compose.project.config_files'
    if ($candidateFile -and (Test-Path -LiteralPath $candidateFile -PathType Leaf)) {
        $activeFile = $candidateFile
        break
    }
}
if (-not $activeFile -and $LiveFlowOnly) { $activeFile = Find-SavedCompose }
if (-not $activeFile -or -not (Test-Path -LiteralPath $activeFile -PathType Leaf)) {
    throw "No se encuentra el Compose activo de $($services[0])."
}
$compose = Get-Content -LiteralPath $activeFile -Raw | ConvertFrom-Json
if ($compose.name -cne $project) { throw 'El Compose activo pertenece a otro proyecto.' }
Write-Output "Compose base: $activeFile"
$tag = "fenix-independent:sha-$Revision"
foreach ($service in $services) {
    $definition = $compose.services.$service
    if (-not $definition) { throw "El Compose activo no contiene $service." }
    $namedMounts = @($definition.volumes | Where-Object { $_.type -eq 'volume' })
    $expected = @($namedMounts | ForEach-Object {
        "$($compose.volumes.($_.source).name):$($_.target)"
    } | Sort-Object)
    if (-not $workers[$service]) {
        if (-not $expected.Count) { throw "El Compose no acredita los volúmenes de $service." }
        foreach ($mount in $namedMounts) {
            $volume = $compose.volumes.($mount.source).name
            if (-not $volume) { throw "Falta definir el volumen $($mount.source)." }
            $labels = ((Invoke-Docker volume inspect $volume --format '{{json .Labels}}') -join '') | ConvertFrom-Json
            if ($labels.'com.docker.compose.project' -cne $project -or
                $labels.'com.docker.compose.volume' -cne $mount.source) {
                throw "El volumen $volume no pertenece a $project/$($mount.source)."
            }
        }
        $definition.image = $tag
        continue
    }
    $serviceFile = $workers[$service].Config.Labels.'com.docker.compose.project.config_files'
    $ownCompose = Get-Content -LiteralPath $serviceFile -Raw | ConvertFrom-Json
    $ownId = (Invoke-Docker image inspect $ownCompose.services.$service.image --format '{{.Id}}') -join ''
    if ($ownCompose.name -cne $project -or $ownId -cne $workers[$service].Image) {
        throw "La imagen actual de $service difiere de su Compose."
    }
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

if ($LiveFlowOnly) {
    $hostInfo = ((Invoke-Docker info --format '{{json .}}') -join '') | ConvertFrom-Json
    $payload = @{ compose = $compose; host = $hostInfo } | ConvertTo-Json -Depth 40 -Compress
    $transform = @'
import json, sys
from pathlib import Path
from deployment.independent import configure_live_only, read, validate_host

data = json.load(sys.stdin)
profile = read(Path('/opt/app/deployment/independent.example.json'))
validate_host(profile, data['host'])
print(json.dumps(configure_live_only(data['compose'], profile)))
'@
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    $configured = $payload | docker --context $context run --rm -i --pull never --network none --read-only `
        --entrypoint python $tag -B -c $transform
    if ($LASTEXITCODE -ne 0) { throw 'El host no puede aplicar el perfil Live.' }
    $compose = ($configured -join '') | ConvertFrom-Json
}

$activeDirectory = Split-Path -Parent $activeFile
$candidate = Join-Path $activeDirectory "compose.$($Revision.Substring(0, 12)).json"
[IO.File]::WriteAllText($candidate, ($compose | ConvertTo-Json -Depth 40), [Text.UTF8Encoding]::new($false))
Invoke-Docker compose -p $project -f $candidate config --quiet
if ($LiveFlowOnly) {
    Invoke-Docker compose -p $project -f $candidate --profile history stop `
        --timeout ([int]$site.drain_seconds) ingesta-history gold-history
    foreach ($service in @('ingesta-history', 'gold-history')) {
        $running = @(Invoke-Docker ps --filter "label=com.docker.compose.project=$project" `
            --filter "label=com.docker.compose.service=$service" --format '{{.ID}}')
        if ($running.Count) { throw "No se ha detenido $service." }
    }
    Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
        --timeout ([int]$site.drain_seconds) @services
} elseif ($Immediate) {
    $order = if ($IncludeBackend) { @('backend', 'gold-history', 'gold-live') } else { $services }
    foreach ($service in $order) {
        Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
            --force-recreate --timeout 0 $service
    }
} else {
    $originalOperations = 'import json; from pathlib import Path; print(",".join(p.stem for p in Path("/pipelines/live/gold/published/operations").glob("*.json") if json.loads(p.read_text())["state"] != "ACKNOWLEDGED"))'
    $originalPending = ($originalOperations | docker --context $context exec -i ($workers['gold-live'].Id) python -) -join ''
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo leer el registro actual de Gold.' }
    $started = [DateTimeOffset]::UtcNow.ToString('o')
    Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
        --timeout ([int]$site.drain_seconds) @services
}
foreach ($service in $services) {
    $worker = Get-Worker $service
    if ($worker.Image -cne $proof.image_id) {
        throw "No se ha reemplazado $service con la imagen aprobada."
    }
    if ($LiveFlowOnly -and $service -in @('ingesta-live', 'gold-live')) {
        if ($worker.HostConfig.NanoCpus -ne 2000000000 -or $worker.HostConfig.Memory -ne 4GB) {
            throw "El presupuesto efectivo no coincide en $service."
        }
    }
    Write-Output "${service} | $($worker.Id.Substring(0, 12)) | $tag"
}
Invoke-Docker compose -p $project -f $candidate ps --all
if (-not $Immediate -and -not $LiveFlowOnly) {
    "FENIX_VERIFY_CONTEXT=$context", "FENIX_VERIFY_PROJECT=$project", "FENIX_VERIFY_SINCE=$started", `
        "FENIX_VERIFY_PENDING=$originalPending" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
}
