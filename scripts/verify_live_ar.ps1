$ErrorActionPreference = 'Stop'
$context = $env:FENIX_DOCKER_CONTEXT
if ($env:FENIX_SITE -and (Test-Path -LiteralPath $env:FENIX_SITE -PathType Leaf)) {
    $context = (Get-Content -LiteralPath $env:FENIX_SITE -Raw | ConvertFrom-Json).docker_context
}
$profile = Get-Content -LiteralPath (Join-Path $PSScriptRoot '../deployment/independent.example.json') -Raw | ConvertFrom-Json
function Worker($service) {
    $ids = @(docker --context $context ps -q --filter "label=com.docker.compose.project=$($profile.project)" --filter "label=com.docker.compose.service=$service")
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw "Worker Live no identificado: $service" }
    $ids[0]
}

$ingest = Worker 'ingesta-live'
$gold = Worker 'gold-live'
$api = Worker 'backend'
$backend = ((docker --context $context inspect $api) -join [Environment]::NewLine | ConvertFrom-Json)[0]
$cmd = [string[]]$backend.Config.Cmd
$p = [Array]::IndexOf($cmd, '--port')
if ($p -lt 0 -or $cmd[$p + 1] -notmatch '^\d+$') { throw 'Puerto interno del API no identificado.' }

$silverCheck = @'
import json
import sqlite3
from pathlib import Path

root = Path('/pipelines/live')
status = json.loads((root / 'exchange/ar-intake.json').read_text())
silver = root / 'exchange/silver'
pin = json.loads((silver / 'current.json').read_text())['publication_id']
ar = json.loads((silver / 'publications' / f"{pin}.json").read_text())['tables'].get('LISTA_AR', {})
with sqlite3.connect((root / 'checkpoint/intake.sqlite').as_uri() + '?mode=ro', uri=True) as db:
    pending = db.execute("SELECT count(*) FROM inputs WHERE state='LOT_WAIT' AND family='LISTA_AR'").fetchone()[0]
print(json.dumps(dict(delta_ar=ar.get('version'), lot_wait=pending, **status)))
'@
$goldCheck = @'
import json
import sys
from urllib.request import urlopen

with urlopen(f'http://127.0.0.1:{sys.argv[1]}/api/live/scores', timeout=8) as response:
    view = json.load(response)
hours = []
for hour in view['hours']:
    if hour['state'] != 'closed':
        continue
    present = [r for r in hour['results'] if 'DAR' in r['contributions']
               and all(k in r['contributions'] for k in ('Critico', 'Grave', 'Mayor', 'Menor', 'Leve'))]
    hours.append(dict(end=hour['end'], ar=len(present), results=len(hour['results']),
                      reasons=sorted({r['reason'] for r in hour['results'] if r['reason']})))
print(json.dumps(dict(publication=view['publication_id'], stale=view['stale'], hours=hours)))
'@

$limit = [DateTimeOffset]::UtcNow.AddMinutes(8)
$recovered = $false
do {
    $silver = $silverCheck | docker --context $context exec -i $ingest python -B -
    $silverOk = $LASTEXITCODE -eq 0
    $scores = $goldCheck | docker --context $context exec -i $api python -B - $cmd[$p + 1]
    $scoresOk = $LASTEXITCODE -eq 0
    if ($silverOk -and $scoresOk) {
        $s = $silver | ConvertFrom-Json
        $g = $scores | ConvertFrom-Json
        if ($s.delta_ar -gt 230 -and $s.lot_wait -eq 0 -and !$g.stale -and @($g.hours | Where-Object ar -gt 0).Count) {
            $recovered = $true
            break
        }
    }
    if ([DateTimeOffset]::UtcNow -lt $limit) { Start-Sleep -Seconds 15 }
} while ([DateTimeOffset]::UtcNow -lt $limit)
Write-Output "Silver: $silver"
Write-Output "Gold: $scores"
docker --context $context inspect $ingest $gold $api --format '{{.Name}} {{.State.Status}} {{.RestartCount}}'
if (!$silverOk -or !$scoresOk) { throw 'No se pudo completar la verificacion operativa AR.' }
if (!$recovered) { Write-Warning 'Servicios observados. Queda pendiente acreditar la recuperacion completa de AR.' }
