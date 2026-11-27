param(
    [Parameter(Mandatory)] [string] $Informe,
    [string] $Contexto = 'desktop-linux',
    [string] $Contenedor = 'fenix-aiala-independent-gold-live-1'
)

$ErrorActionPreference = 'Stop'
$hostInfo = docker --context $Contexto inspect $Contenedor
if ($LASTEXITCODE -ne 0) { throw 'No se pudo inspeccionar Gold Live.' }
$live = @($hostInfo -join "`n" | ConvertFrom-Json)[0]
$gold = @($live.Mounts | Where-Object Destination -eq '/pipelines/live/gold')
if ($gold.Count -ne 1 -or $gold[0].Type -ne 'volume' -or -not $live.Image) {
    throw 'No se pudo identificar una sola imagen y volumen Gold Live.'
}
$code = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$report = (Resolve-Path -LiteralPath $Informe).Path
$t0 = @($live.Config.Env | Where-Object { $_ -like 'FENIX_LIVE_T0=*' })
if ($t0.Count -ne 1) { throw 'Gold Live no declara un T0 inequívoco.' }
$argsRun = @(
    'run', '--rm', '--pull', 'never', '--read-only', '--network', 'none',
    '--cpus', '0.75', '--memory', '2g', '--pids-limit', '128',
    '--tmpfs', '/tmp:rw,nosuid,noexec,size=512m',
    '-e', $t0[0],
    '-e', 'RAYON_NUM_THREADS=1', '-e', 'TOKIO_WORKER_THREADS=1',
    '-e', 'GOLD_SCORE_WORKERS=1', '-e', 'MALLOC_CONF=background_thread:false',
    '-e', 'PYTHONPATH=/candidate/backend', '-e', 'PYTHONDONTWRITEBYTECODE=1',
    '--mount', "type=volume,source=$($gold[0].Name),target=/pipelines/live/gold,readonly,volume-nocopy",
    '--mount', "type=bind,source=$code,target=/candidate,readonly",
    '--mount', "type=bind,source=$report,target=/incident.json,readonly",
    '--entrypoint', '/opt/venv/bin/python', $live.Image,
    '-B', '/candidate/scripts/dry_run_ar_recovery.py',
    '--gold', '/pipelines/live/gold/published', '--report', '/incident.json'
)
docker --context $Contexto @argsRun
if ($LASTEXITCODE -ne 0) { throw 'Falló la simulación de solo lectura.' }
