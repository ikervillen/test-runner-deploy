param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][string]$Image,
    [Parameter(Mandatory = $true)][string]$Revision,
    [Parameter(Mandatory = $true)][string]$GoldRevision,
    [switch]$LiveFlowOnly,
    [switch]$FreshLive,
    [switch]$ApplicationLive,
    [switch]$FrontendOnly,
    [switch]$GoldOnly,
    [switch]$AuditArCatalog,
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
if ($FreshLive -and -not $LiveFlowOnly) { throw 'FreshLive requiere LiveFlowOnly.' }
if ($FrontendOnly) {
    if ($ApplicationLive -or $LiveFlowOnly -or $GoldOnly -or $IncludeGoldHistory -or $IncludeBackend -or $Immediate) {
        throw 'FrontendOnly actualiza exclusivamente el frontend.'
    }
    $services = @('frontend')
} elseif ($ApplicationLive) {
    if ($LiveFlowOnly -or $GoldOnly -or $IncludeGoldHistory -or $IncludeBackend -or $Immediate) {
        throw 'ApplicationLive conserva la generación Live existente.'
    }
    $services = @('gold-live', 'backend')
} elseif ($LiveFlowOnly) {
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
if ($AuditArCatalog -and (-not $GoldOnly -or $IncludeGoldHistory -or $Immediate)) {
    throw 'AuditArCatalog requiere actualizar exclusivamente Gold Live.'
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
if ($FreshLive -and @($services | Where-Object { -not $workers[$_] }).Count) {
    throw 'La generación limpia requiere un Compose anterior verificable para rollback.'
}
$initializeGold = $false
$initializer = $null
if ($LiveFlowOnly) {
    $present = @(Invoke-Docker volume ls --format '{{.Name}}')
    $goldName = $compose.volumes.'live-gold'.name
    if (-not $goldName) { throw 'Falta definir el volumen Gold Live.' }
    if ($goldName -cnotin $present) {
        if ($goldName -cne $profile.volumes.live.gold) {
            throw "No se puede inicializar un volumen Gold Live distinto del perfil: $goldName."
        }
        $initializer = $compose.services.initialize
        $goldMount = @($initializer.volumes | Where-Object {
            $_.type -eq 'volume' -and $_.source -ceq 'live-gold' -and -not $_.read_only
        })
        if ($goldMount.Count -ne 1 -or -not $goldMount[0].target) {
            throw 'El Compose no define la inicializacion de Gold Live.'
        }
        $initializeGold = $true
    }
    $historyGold = $compose.volumes.'history-gold'.name
    if ($historyGold -and $historyGold -cnotin $present) {
        # Live supports starting without a published historical reference.
        foreach ($service in $services) {
            $compose.services.$service.volumes = @($compose.services.$service.volumes |
                Where-Object { $_.type -ne 'volume' -or $_.source -cne 'history-gold' })
        }
    }
}
foreach ($service in $services) {
    if ($FrontendOnly) { break }
    $definition = $compose.services.$service
    if (-not $definition) { throw "El Compose activo no contiene $service." }
    $namedMounts = @($definition.volumes | Where-Object { $_.type -eq 'volume' })
    $expected = @($namedMounts | ForEach-Object {
        "$($compose.volumes.($_.source).name):$($_.target)"
    } | Sort-Object)
    if (-not $workers[$service]) {
        if (-not $expected.Count) { throw "El Compose no acredita los volúmenes de $service." }
        foreach ($mount in $namedMounts) {
            if ($initializeGold -and $mount.source -ceq 'live-gold') { continue }
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

if ($FrontendOnly) {
    $frontend = $compose.services.frontend
    if (-not $frontend.image -or -not $compose.services.backend) {
        throw 'Falta el frontend o el backend en el Compose activo.'
    }
    $oldId = (Invoke-Docker image inspect $frontend.image --format '{{.Id}}') -join ''
    if ($workers.frontend.Image -cne $oldId) {
        throw 'La imagen del frontend activo difiere de su Compose.'
    }
    $frontendTag = "fenix-quality:sha-$Revision"
    $frontendId = (Invoke-Docker image inspect $frontendTag --format '{{.Id}}') -join ''
    if ($frontendId -cne $proof.frontend_id -or $frontendId -cnotmatch '^sha256:[a-f0-9]{64}$') {
        throw 'La imagen del frontend no coincide con el artefacto aprobado.'
    }
    $frontend.image = $frontendTag
    $candidate = Join-Path (Split-Path -Parent $activeFile) "compose.$($Revision.Substring(0, 12)).json"
    [IO.File]::WriteAllText($candidate, ($compose | ConvertTo-Json -Depth 40), [Text.UTF8Encoding]::new($false))
    Invoke-Docker compose -p $project -f $candidate config --quiet
    try {
        Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
            --wait --wait-timeout 90 frontend
        if ((Get-Worker 'frontend').Image -cne $frontendId) {
            throw 'La imagen frontend activa difiere.'
        }
    } catch {
        Invoke-Docker compose -p $project -f $activeFile up -d --no-deps --no-build --pull never frontend
        throw
    }
    Write-Output "Frontend activo: $frontendTag | rollback: $activeFile"
    return
}

if ($LiveFlowOnly) {
    $hostInfo = ((Invoke-Docker info --format '{{json .}}') -join '') | ConvertFrom-Json
    $t0 = $null
    if ($FreshLive) {
        $zone = [TimeZoneInfo]::FindSystemTimeZoneById('Romance Standard Time')
        $clock = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $zone).DateTime
        $t0 = $clock.Date.AddMinutes(([math]::Floor($clock.TimeOfDay.TotalMinutes / 10) + 1) * 10).ToString('yyyy-MM-ddTHH:mm:ss')
    }
    $payload = @{ compose = $compose; host = $hostInfo; start = $t0; revision = $Revision } | ConvertTo-Json -Depth 40 -Compress
    $transform = @'
import json, sys
from pathlib import Path
from deployment.independent import configure_fresh_live, configure_live_only, read, validate_host

# Windows PowerShell can send a UTF-8 BOM to a native process.
data = json.load(sys.stdin.buffer)
profile = read(Path('/opt/app/deployment/independent.example.json'))
validate_host(profile, data['host'])
compose = configure_live_only(data['compose'], profile)
if data['start']:
    compose = configure_fresh_live(compose, data['revision'], data['start'])
print(json.dumps(compose))
'@
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    $configured = $payload | docker --context $context run --rm -i --pull never --network none --read-only `
        --entrypoint python $tag -B -c $transform
    if ($LASTEXITCODE -ne 0) { throw 'El host no puede aplicar el perfil Live.' }
    $compose = ($configured -join '') | ConvertFrom-Json
}

if ($ApplicationLive) {
    foreach ($service in @('ingesta-history', 'gold-history')) {
        $running = @(Invoke-Docker ps --filter "label=com.docker.compose.project=$project" `
            --filter "label=com.docker.compose.service=$service" --format '{{.ID}}')
        if ($running.Count) { throw "History debe estar detenido: $service." }
    }
    $t0 = @($workers['gold-live'].Config.Env | Where-Object { $_ -clike 'FENIX_LIVE_T0=*' })
    if ($t0.Count -ne 1 -or $workers['backend'].Config.Env -cnotcontains $t0[0]) {
        throw 'Gold y el backend deben pertenecer a la misma generación Live.'
    }
    $frontendTag = "fenix-quality:sha-$Revision"
    $frontendId = (Invoke-Docker image inspect $frontendTag --format '{{.Id}}') -join ''
    if ($frontendId -cne $proof.frontend_id -or $frontendId -cnotmatch '^sha256:[a-f0-9]{64}$') {
        throw 'La imagen del frontend no coincide con el artefacto aprobado.'
    }
    $backend = $compose.services.backend
    if ($backend.environment.APP_GOLD_HISTORY_ENABLED -cne 'false') {
        throw 'El backend debe tener History desactivado.'
    }
    if (@($backend.volumes | Where-Object {
        -not $_.read_only -or ($_.type -eq 'volume' -and $_.source -cne 'live-gold') -or
        ($_.type -eq 'bind' -and $_.target -cne $profile.configuration_target)
    }).Count) {
        throw 'El backend debe montar exclusivamente Gold Live en lectura.'
    }
    $hadFrontend = $null -ne $compose.services.PSObject.Properties['frontend']
    if ($hadFrontend) {
        $ports = @($compose.services.frontend.ports)
    } else {
        $ports = @($backend.ports | ForEach-Object {
            if ($_ -isnot [string] -or $_ -notmatch '^.+:\d+:\d+$') {
                throw 'El puerto del backend debe tener una dirección de enlace explícita.'
            }
            $_ -replace ':\d+$', ':8080'
        })
    }
    if ($ports.Count -ne 1 -or $ports[0] -notmatch '^127\.0\.0\.1:\d+:8080$') {
        throw 'El frontend requiere el enlace local que utiliza el proxy de confianza.'
    }
    $backend.PSObject.Properties.Remove('ports')
    $portIndex = [Array]::IndexOf(@($backend.command), '--port')
    if ($portIndex -lt 0 -or $backend.command[$portIndex+1] -notmatch '^\d{1,5}$') {
        throw 'No se pudo acreditar el puerto interno del backend.'
    }
    $backendPort = [int]$backend.command[$portIndex+1]
    if ($backendPort -lt 1 -or $backendPort -gt 65535) { throw 'Puerto interno inválido.' }
    $health = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$backendPort/api/health', timeout=2)"
    $backend.healthcheck = @{
        test=@('CMD','python','-c',$health)
        interval='15s'; timeout='5s'; retries=3
    }
    $frontend = @{
        image=$frontendTag; ports=$ports; restart='unless-stopped'; read_only=$true
        cpus=0.5; mem_limit='256m'; pids_limit=64
        environment=@{BACKEND_PORT=[string]$backendPort; NGINX_ENVSUBST_TEMPLATE_DIR='/opt/fenix/templates'}
        tmpfs=@('/var/cache/nginx:size=32m,nosuid,nodev', '/var/run:size=8m,nosuid,nodev', '/etc/nginx/conf.d:size=1m,nosuid,nodev')
        healthcheck=@{test=@('CMD-SHELL','wget -q -O /dev/null http://127.0.0.1:8080/'); interval='15s'; timeout='3s'; retries=3}
        logging=@{driver='local'; options=@{'max-size'='10m'; 'max-file'='3'}}
    }
    $compose.services | Add-Member -NotePropertyName 'frontend' -NotePropertyValue $frontend -Force
}

$activeDirectory = Split-Path -Parent $activeFile
$candidate = Join-Path $activeDirectory "compose.$($Revision.Substring(0, 12)).json"
[IO.File]::WriteAllText($candidate, ($compose | ConvertTo-Json -Depth 40), [Text.UTF8Encoding]::new($false))
Invoke-Docker compose -p $project -f $candidate config --quiet
if ($LiveFlowOnly) {
    if ($FreshLive) {
        $volumes = @{}
        $existingVolumes = @(Invoke-Docker volume ls --format '{{.Name}}')
        foreach ($property in $compose.volumes.PSObject.Properties) {
            if ($property.Name -notlike 'live-*') { continue }
            if ($property.Value.name -cin $existingVolumes) { throw "La generación ya existe: $($property.Value.name)" }
            $volumes[$property.Name] = $property.Value
        }
        if ($volumes.Count -ne 6) { throw 'Se requieren seis volúmenes Live nuevos.' }
        $bootstrap = $compose.services.initialize
        $bootstrap.image = $tag
        $initialization = @{name=$project; services=@{initialize=$bootstrap}; volumes=$volumes}
        $initFile = "$candidate.initialize.json"
        [IO.File]::WriteAllText($initFile, ($initialization | ConvertTo-Json -Depth 40), [Text.UTF8Encoding]::new($false))
        Invoke-Docker compose -p $project -f $initFile run --rm --no-deps --pull never initialize
        Write-Output "T0 industrial: $t0 | rollback: $activeFile"
    } elseif ($initializeGold) {
        $initializer.image = $tag
        $initializer.command = @('--uid', [string]$profile.uid, '--gid', [string]$profile.gid, $goldMount[0].target)
        $initializer.volumes = @($goldMount[0])
        $initialization = @{
            name = $project
            services = @{ initialize = $initializer }
            volumes = @{ 'live-gold' = $compose.volumes.'live-gold' }
        }
        $initFile = Join-Path $activeDirectory 'initialize-live-gold.json'
        [IO.File]::WriteAllText($initFile, ($initialization | ConvertTo-Json -Depth 40), [Text.UTF8Encoding]::new($false))
        try {
            Write-Output "Inicializando Gold Live desde Silver confirmado: $goldName"
            Invoke-Docker compose -p $project -f $initFile run --rm --no-deps --pull never initialize
        } finally {
            Remove-Item -LiteralPath $initFile -ErrorAction SilentlyContinue
        }
    }
    Invoke-Docker compose -p $project -f $candidate --profile history stop `
        --timeout ([int]$site.drain_seconds) ingesta-history gold-history
    foreach ($service in @('ingesta-history', 'gold-history')) {
        $running = @(Invoke-Docker ps --filter "label=com.docker.compose.project=$project" `
            --filter "label=com.docker.compose.service=$service" --format '{{.ID}}')
        if ($running.Count) { throw "No se ha detenido $service." }
    }
    try {
        Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
            --timeout ([int]$site.drain_seconds) @services
        if ($FreshLive) {
            foreach ($service in $services) {
                $newWorker = Get-Worker $service
                if ($newWorker.Image -cne $proof.image_id -or
                    $newWorker.Config.Env -cnotcontains "FENIX_LIVE_T0=$t0") {
                    throw "Imagen o T0 inesperado en $service."
                }
                foreach ($mount in @($compose.services.$service.volumes | Where-Object type -eq 'volume')) {
                    $name = $compose.volumes.($mount.source).name
                    if (-not @($newWorker.Mounts | Where-Object { $_.Name -ceq $name -and $_.Destination -ceq $mount.target }).Count) {
                        throw "Montaje inesperado en $service/$($mount.target)."
                    }
                }
            }
        }
    } catch {
        if ($FreshLive) {
            Invoke-Docker compose -p $project -f $activeFile up -d --no-deps --no-build --pull never `
                --timeout ([int]$site.drain_seconds) @services
        }
        throw
    }
} elseif ($ApplicationLive) {
    $writerCutover = $false
    $writerStopped = $false
    try {
        Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
            --wait --wait-timeout 90 backend frontend
        $writerStopped = $true
        Invoke-Docker compose -p $project -f $activeFile stop --timeout ([int]$site.drain_seconds) gold-live
        $goldMount = @($compose.services.'gold-live'.volumes | Where-Object source -ceq 'live-gold')
        if ($goldMount.Count -ne 1) { throw 'El montaje Gold no está acreditado.' }
        $preflight = 'from pathlib import Path; import json, sys; root = Path(sys.argv[1]) / ''published'' / ''operations''; assert all(json.loads(p.read_text())[''state''] == ''ACKNOWLEDGED'' for p in root.glob(''*.json'')), ''Gold still has an unfinished publication'''
        Invoke-Docker compose -p $project -f $candidate run --rm --no-deps --pull never `
            --entrypoint python gold-live -B -c $preflight $goldMount[0].target
        $writerCutover = $true
        Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
            --timeout ([int]$site.drain_seconds) gold-live
        foreach ($service in $services) {
            $actual = Get-Worker $service
            if ($actual.Image -cne $proof.image_id -or $actual.Config.Env -cnotcontains $t0[0]) {
                throw "La actualización no conserva la imagen aprobada y T0 en $service."
            }
            $oldMounts = @($workers[$service].Mounts | Where-Object Type -eq 'volume' |
                ForEach-Object { "$($_.Name):$($_.Destination)" } | Sort-Object)
            $newMounts = @($actual.Mounts | Where-Object Type -eq 'volume' |
                ForEach-Object { "$($_.Name):$($_.Destination)" } | Sort-Object)
            if (($oldMounts -join ';') -cne ($newMounts -join ';')) {
                throw "Se modificaron los montajes de $service."
            }
        }
        $activeFrontend = Get-Worker 'frontend'
        if ($activeFrontend.Image -cne $frontendId) { throw 'La imagen frontend activa difiere.' }
        Write-Output "Aplicación disponible en el puerto publicado: $($ports[0]) | $($t0[0]) | rollback: $activeFile"
    } catch {
        Invoke-Docker compose -p $project -f $candidate stop frontend
        $restore = @('backend')
        if ($writerCutover) {
            Write-Warning 'Gold conserva una revisión capaz de recuperar sus propias operaciones.'
        } elseif ($writerStopped) {
            $restore += 'gold-live'
        }
        if ($hadFrontend) { $restore += 'frontend' }
        Invoke-Docker compose -p $project -f $activeFile up -d --no-deps --no-build --pull never @restore
        throw
    }
} elseif ($AuditArCatalog) {
    $oldGold = $workers['gold-live']
    $t0 = @($oldGold.Config.Env | Where-Object { $_ -clike 'FENIX_LIVE_T0=*' })
    if ($t0.Count -ne 1) { throw 'No se puede acreditar el T0 de Gold Live.' }
    foreach ($service in @('gold-history', 'ingesta-history')) {
        $running = @(Invoke-Docker ps --filter "label=com.docker.compose.project=$project" `
            --filter "label=com.docker.compose.service=$service" --format '{{.ID}}')
        if ($running.Count) { throw "History debe permanecer detenido: $service." }
    }
    $gold = $compose.services.'gold-live'
    $index = [Array]::IndexOf(@($gold.command), '--catalogs')
    if ($index -lt 0 -or -not $gold.command[$index+1]) {
        throw 'El Compose no declara el catálogo efectivo de Gold.'
    }
    $catalogPath = $gold.command[$index+1]
    $configMount = @($gold.volumes | Where-Object {
        $_.target -ceq $profile.configuration_target -and $_.read_only
    })
    if ($configMount.Count -ne 1 -or -not $catalogPath.StartsWith("$($configMount[0].target)/")) {
        throw 'El catálogo debe estar en el montaje de configuración de solo lectura.'
    }
    $audit = 'from pathlib import Path; import sys; from app.gold.catalogs import load_independent_bundle; load_independent_bundle(Path(sys.argv[1]), audit=True)'
    Invoke-Docker compose -p $project -f $candidate run --rm --no-deps --pull never `
        --entrypoint python gold-live -B -c $audit $catalogPath
    $goldMount = @($gold.volumes | Where-Object source -ceq 'live-gold')
    if ($goldMount.Count -ne 1) { throw 'El montaje Gold no está acreditado.' }
    $check = 'from pathlib import Path; import json, sys; root = Path(sys.argv[1]) / ''published'' / ''operations''; assert all(json.loads(p.read_text())[''state''] == ''ACKNOWLEDGED'' for p in root.glob(''*.json'')), ''Gold still has an unfinished publication'''
    try {
        Invoke-Docker compose -p $project -f $activeFile stop --timeout ([int]$site.drain_seconds) gold-live
        Invoke-Docker compose -p $project -f $candidate run --rm --no-deps --pull never `
            --entrypoint python gold-live -B -c $check $goldMount[0].target
    } catch {
        Invoke-Docker compose -p $project -f $activeFile up -d --no-deps --no-build --pull never gold-live
        throw
    }
    Invoke-Docker compose -p $project -f $candidate up -d --no-deps --no-build --pull never `
        --timeout ([int]$site.drain_seconds) gold-live
    $actual = Get-Worker 'gold-live'
    $oldMounts = @($oldGold.Mounts | Where-Object Type -eq 'volume' | ForEach-Object {
        "$($_.Name):$($_.Destination)"
    } | Sort-Object)
    $newMounts = @($actual.Mounts | Where-Object Type -eq 'volume' | ForEach-Object {
        "$($_.Name):$($_.Destination)"
    } | Sort-Object)
    if ($actual.Config.Env -cnotcontains $t0[0] -or ($oldMounts -join ';') -cne ($newMounts -join ';')) {
        throw 'El nuevo Gold no conserva el T0 o sus volúmenes.'
    }
    Write-Output "Gold Live | $($t0[0]) | Compose anterior: $activeFile"
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
if (-not $Immediate -and -not $LiveFlowOnly -and -not $ApplicationLive -and -not $AuditArCatalog) {
    "FENIX_VERIFY_CONTEXT=$context", "FENIX_VERIFY_PROJECT=$project", "FENIX_VERIFY_SINCE=$started", `
        "FENIX_VERIFY_PENDING=$originalPending" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8 -Append
}
