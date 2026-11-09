param(
    [Parameter(Mandatory = $true)][string]$Site,
    [Parameter(Mandatory = $true)][string]$GoldContext,
    [switch]$Activate,
    [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
$siteConfig = Get-Content -LiteralPath $Site -Raw | ConvertFrom-Json
$context = $siteConfig.docker_context
$root = (Resolve-Path -LiteralPath $GoldContext).Path
$expectedRevision = $env:GOLD_SOURCE_COMMIT
if ($expectedRevision -notmatch '^[a-f0-9]{40}$' -or
    (Get-Content -LiteralPath (Join-Path $root 'GOLD_REVISION') -Raw).Trim() -ne $expectedRevision) {
    throw 'El contexto no corresponde a la revisión Gold esperada.'
}

# Recheck the volume identity and prepare the Airflow-owned volumes.
$silverBy = (Get-Date).AddMinutes(15)
do {
    $probe = python -m deployment.gold_site --site $Site | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'No se ha validado el volumen de develop.' }
    if (-not $Activate -or $probe.silver_publication) { break }
    $exportStatus = if ($probe.silver_export.status) { $probe.silver_export.status } else { 'SIN_RESULTADO' }
    $families = @($probe.worker_heartbeat.workers | ForEach-Object { $_.family }) -join ', '
    Write-Output "Esperando Silver confirmada. Exportador=$exportStatus; worker=$($probe.worker_heartbeat.phase); familias=$families"
    Start-Sleep -Seconds 30
} while ((Get-Date) -lt $silverBy)
$probe | ConvertTo-Json -Compress
if ($Activate -and -not $probe.silver_publication) {
    throw 'El worker de develop aún no ha confirmado su primera publicación Silver.'
}
if ($VerifyOnly -and (-not $probe.gold_publication -or -not $env:GOLD_EXISTING_IMAGES)) {
    throw 'La verificación requiere una publicación Gold y las huellas de las imágenes existentes.'
}

$env:INGESTA_DEVELOP_VOLUME = $siteConfig.environments.develop.volume
$env:DOCKER_CONTEXT = $context
$env:AIRFLOW_IMAGE_TAG = $expectedRevision.Substring(0, 7)
$env:HOST_CONFIRMED_SILVER_ROOT = '/unused-silver-bind'
$env:HOST_GOLD_ROOT = '/unused-gold-bind'
$env:HOST_GOLD_CONFIG_ROOT = '/unused-gold-config-bind'
$env:HOST_GOLD_BACKUP_ROOT = '/unused-gold-backup-bind'
$env:APP_ENV_FILE = Join-Path $root 'backend.develop.env'
[IO.File]::WriteAllText($env:APP_ENV_FILE, "APP_AUTH_MODE=disabled`n")

function Get-PinnedDockerTool($name, $uri, $sha256) {
    $toolDirectory = Join-Path $env:RUNNER_TEMP 'fenix-gold-tools'
    New-Item -ItemType Directory -Path $toolDirectory -Force | Out-Null
    $tool = Join-Path $toolDirectory $name
    if (-not (Test-Path -LiteralPath $tool) -or (Get-FileHash -LiteralPath $tool).Hash -ne $sha256) {
        Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $tool
    }
    if ((Get-FileHash -LiteralPath $tool).Hash -ne $sha256) {
        throw "Huella incorrecta de $name."
    }
    return $tool
}
$composeExe = Get-PinnedDockerTool 'docker-compose-v5.6.1.exe' `
    'https://github.com/docker/compose/releases/download/v5.6.1/docker-compose-windows-x86_64.exe' `
    '91b1492f0a923427651e2b955739d732ac36b8191193bf4799e62f3e3525c4cd'
$stack = @('-p', 'calidad-serie-develop',
    '-f', (Join-Path $root 'compose.gold.yaml'),
    '-f', (Join-Path $root 'compose.airflow.yaml'),
    '-f', (Join-Path $root 'compose.ingesta-develop.yaml'),
    '-f', (Join-Path $PSScriptRoot 'compose.gold.develop.yaml'))

& $composeExe @stack config --quiet
if ($LASTEXITCODE -ne 0) { throw 'El contrato Compose no es válido.' }
$tag = $env:AIRFLOW_IMAGE_TAG
$images = [ordered]@{
    "calidad-serie-airflow:${tag}" = 'orchestration/airflow/Dockerfile'
    "calidad-serie-develop-backend:${tag}" = 'backend/Dockerfile'
    "calidad-serie-develop-frontend:${tag}" = 'Dockerfile.frontend'
}
if ($env:GOLD_EXISTING_IMAGES) {
    $expectedImages = $env:GOLD_EXISTING_IMAGES.Split(',')
    $invalidImages = $expectedImages | Where-Object { $_ -cnotmatch '^sha256:[a-f0-9]{64}$' }
    if ($expectedImages.Count -ne $images.Count -or $invalidImages) {
        throw 'Se requieren las tres huellas de imagen del build Gold.'
    }
    $index = 0
    foreach ($image in $images.Keys) {
        $identity = & docker --context $context image inspect --format '{{.Id}}' $image
        if ($LASTEXITCODE -ne 0 -or $identity -ne $expectedImages[$index]) {
            throw "La imagen existente $image no coincide con el build verificado."
        }
        Write-Output "Imagen verificada $image $identity"
        $index++
    }
} else {
    $buildxExe = Get-PinnedDockerTool 'docker-buildx-v0.38.1.exe' `
        'https://github.com/docker/buildx/releases/download/v0.38.1/buildx-v0.38.1.windows-amd64.exe' `
        'fd2e18501910dd535ea3aa7f69010b3cfdfba0f47aa27674124554386dfa26f3'
    foreach ($image in $images.Keys) {
        & $buildxExe build --load --platform linux/amd64 --provenance=false `
            --file (Join-Path $root $images[$image]) --tag $image $root
        if ($LASTEXITCODE -ne 0) { throw "No se pudo construir $image." }
    }
}

function Start-VerifiedGoldApplication {
    # The writer validates every table before promotion. Reopen its manifest and
    # receipts and read counts without reloading the historical facts into Python.
    $verify = @'
import json
from pathlib import Path
from app.gold.publications import Snapshot

gold = Snapshot.open(Path('/gold'), require_gold_contract=True)
print(json.dumps({
    'publication_id': gold.publication_id,
    'input_publication_id': gold.manifest['input_publication_id'],
    'tables': len(gold.manifest['tables']),
    'scores': gold.table('quality_scores').to_pyarrow_dataset().count_rows(),
}))
'@
    & $composeExe @stack run --rm --no-deps --entrypoint /opt/calidad/.venv/bin/python `
        -e PYTHONPATH=/opt/calidad/backend airflow-scheduler -c $verify
    if ($LASTEXITCODE -ne 0) { throw 'La publicación no supera el contrato Gold.' }
    & $composeExe @stack up -d --no-build --wait --wait-timeout 300 backend frontend
    if ($LASTEXITCODE -ne 0) { throw 'La aplicación no alcanzó un estado sano.' }
    $frontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { '5174' }
    $catalog = Invoke-RestMethod -Uri "http://127.0.0.1:$frontendPort/api/catalog" -TimeoutSec 60
    if ($catalog.source -ne 'gold' -or -not $catalog.publication_id) {
        throw 'La aplicación no está leyendo el catálogo Gold.'
    }
    Write-Output "Aplicación Gold verificada. Publicación: $($catalog.publication_id)"
}

if ($VerifyOnly) {
    Start-VerifiedGoldApplication
    return
}

# The database and UI can be validated before any Gold calculation is enabled.
& $composeExe @stack up -d --no-build --wait --wait-timeout 300 airflow-apiserver airflow-dag-processor
if ($LASTEXITCODE -ne 0) { throw 'Airflow no alcanzó su estado de arranque.' }
& $composeExe @stack ps -a
if ($LASTEXITCODE -ne 0) { throw 'No se pudo inspeccionar la pila.' }
if ($Activate) {
    & $composeExe @stack stop --timeout 120 airflow-scheduler
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo detener el scheduler antes de actualizar Gold.' }
}
$configureArgs = @('-m', 'deployment.gold_site', '--site', $Site, '--configure-from', $root)
if ($Activate) { $configureArgs += '--allow-budget-increase' }
python @configureArgs
if ($LASTEXITCODE -ne 0) { throw 'Airflow arrancó, pero falta inicializar la configuración Gold.' }
if (-not $Activate) { return }

# Check Silver through the same mount and engine used by the DAG.
& $composeExe @stack run --rm --no-deps --entrypoint /opt/calidad/.venv/bin/python `
    -e PYTHONPATH=/opt/calidad/backend airflow-scheduler -c `
    "from pathlib import Path; from app.gold.schedule import require_continuous_source; require_continuous_source(Path('/silver'))"
if ($LASTEXITCODE -ne 0) { throw 'Silver aún no cumple el contrato de publicación continua.' }

$running = @(& docker --context $context ps --quiet --filter "volume=$env:INGESTA_DEVELOP_VOLUME")
if ($LASTEXITCODE -ne 0) { throw 'No se pudo comprobar la exclusividad del escritor Gold.' }
$publishers = @()
foreach ($id in $running) {
    $container = (& docker --context $context container inspect $id | ConvertFrom-Json)[0]
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo comprobar un contenedor del volumen.' }
    if (-not ($container.Mounts | Where-Object { $_.Destination -eq '/gold' -and $_.RW })) { continue }
    $project = $container.Config.Labels.'com.docker.compose.project'
    $service = $container.Config.Labels.'com.docker.compose.service'
    if ($project -ne 'calidad-serie-develop' -or $service -notin @('publisher', 'airflow-scheduler')) {
        throw "Otro escritor monta Gold: $id."
    }
    if ($service -eq 'publisher') { $publishers += $id }
}
foreach ($id in $publishers) {
    & docker --context $context update --restart=no $id | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo desactivar el reinicio de watch.' }
    & docker --context $context kill --signal=SIGTERM $id | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo detener watch de forma ordenada.' }
    $stoppedBy = (Get-Date).AddMinutes(5)
    do {
        $state = & docker --context $context container inspect --format '{{.State.Running}}' $id
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo confirmar que watch se haya detenido.' }
        if ($state -eq 'false') { break }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $stoppedBy)
    if ($state -ne 'false') { throw 'Watch sigue activo. Airflow no tomará el volumen.' }
}

& $composeExe @stack up -d --no-build --wait --wait-timeout 300 airflow-scheduler
if ($LASTEXITCODE -ne 0) { throw 'El scheduler no alcanzó un estado sano.' }

$publishBy = (Get-Date).AddMinutes(25)
do {
    $published = python -m deployment.gold_site --site $Site | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo comprobar la publicación Gold.' }
    if ($published.gold_publication_id -and $published.gold_publication_id -ne $probe.gold_publication_id) { break }
    Start-Sleep -Seconds 20
} while ((Get-Date) -lt $publishBy)
if (-not $published.gold_publication_id -or $published.gold_publication_id -eq $probe.gold_publication_id) {
    throw 'Airflow aún no ha promovido una publicación Gold nueva.'
}

Start-VerifiedGoldApplication
