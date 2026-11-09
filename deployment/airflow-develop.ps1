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
$env:AIRFLOW_IMAGE_TAG = $expectedRevision.Substring(0, 7)
$env:HOST_CONFIRMED_SILVER_ROOT = '/unused-silver-bind'
$env:HOST_GOLD_ROOT = '/unused-gold-bind'
$env:HOST_GOLD_CONFIG_ROOT = '/unused-gold-config-bind'
$env:HOST_GOLD_BACKUP_ROOT = '/unused-gold-backup-bind'
$env:APP_ENV_FILE = Join-Path $root 'backend.develop.env'
[IO.File]::WriteAllText($env:APP_ENV_FILE, "APP_AUTH_MODE=disabled`n")

$stack = @('--context', $context, 'compose', '-p', 'calidad-serie-develop',
    '-f', (Join-Path $root 'compose.gold.yaml'),
    '-f', (Join-Path $root 'compose.airflow.yaml'),
    '-f', (Join-Path $root 'compose.ingesta-develop.yaml'),
    '-f', (Join-Path $PSScriptRoot 'compose.gold.develop.yaml'))

& docker @stack config --quiet
if ($LASTEXITCODE -ne 0) { throw 'El contrato Compose no es válido.' }
& docker @stack build airflow-apiserver backend frontend
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron construir las imágenes candidatas.' }

# The database and UI can be validated before any Gold calculation is enabled.
& docker @stack up -d --wait --wait-timeout 180 airflow-apiserver airflow-dag-processor
if ($LASTEXITCODE -ne 0) { throw 'Airflow no alcanzó su estado de arranque.' }
& docker @stack ps -a
if ($LASTEXITCODE -ne 0) { throw 'No se pudo inspeccionar la pila.' }
