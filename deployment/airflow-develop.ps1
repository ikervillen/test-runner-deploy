param(
    [Parameter(Mandatory = $true)][string]$Site,
    [Parameter(Mandatory = $true)][string]$GoldContext
)
$ErrorActionPreference = 'Stop'
$siteConfig = Get-Content -LiteralPath $Site -Raw | ConvertFrom-Json
$context = $siteConfig.docker_context
$root = (Resolve-Path -LiteralPath $GoldContext).Path
$expectedRevision = 'aed67ba50aee8fd7251db412610c11a1ce179f73'
if ((Get-Content -LiteralPath (Join-Path $root 'GOLD_REVISION') -Raw).Trim() -ne $expectedRevision) {
    throw 'El contexto no corresponde a la revisión Gold esperada.'
}

# Recheck the volume identity and prepare the Airflow-owned volumes.
python -m deployment.gold_site --site $Site
if ($LASTEXITCODE -ne 0) { throw 'No se ha validado el volumen de develop.' }

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
$buildxExe = Get-PinnedDockerTool 'docker-buildx-v0.38.1.exe' `
    'https://github.com/docker/buildx/releases/download/v0.38.1/buildx-v0.38.1.windows-amd64.exe' `
    'fd2e18501910dd535ea3aa7f69010b3cfdfba0f47aa27674124554386dfa26f3'
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
foreach ($image in $images.Keys) {
    & $buildxExe build --load --platform linux/amd64 --provenance=false `
        --file (Join-Path $root $images[$image]) --tag $image $root
    if ($LASTEXITCODE -ne 0) { throw "No se pudo construir $image." }
}

# The database and UI can be validated before any Gold calculation is enabled.
& $composeExe @stack up -d --no-build --wait --wait-timeout 180 airflow-apiserver airflow-dag-processor
if ($LASTEXITCODE -ne 0) { throw 'Airflow no alcanzó su estado de arranque.' }
& $composeExe @stack ps -a
if ($LASTEXITCODE -ne 0) { throw 'No se pudo inspeccionar la pila.' }
python -m deployment.gold_site --site $Site --configure-from $root
if ($LASTEXITCODE -ne 0) { throw 'Airflow arrancó, pero falta inicializar la configuración Gold.' }
