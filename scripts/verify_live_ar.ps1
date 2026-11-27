param([switch]$Once, [string]$Contexto, [string]$Proyecto='fenix-aiala-independent', [string]$Casos)
$ErrorActionPreference = 'Stop'
$context = $env:FENIX_DOCKER_CONTEXT
if ($env:FENIX_SITE -and (Test-Path -LiteralPath $env:FENIX_SITE -PathType Leaf)) {
    $context = (Get-Content -LiteralPath $env:FENIX_SITE -Raw | ConvertFrom-Json).docker_context
}
if ($Contexto) { $context = $Contexto }
if (!$context) { $context = 'desktop-linux' }
function Worker($service) {
    $ids = @(docker --context $context ps -q --filter "label=com.docker.compose.project=$Proyecto" --filter "label=com.docker.compose.service=$service")
    if ($LASTEXITCODE -ne 0 -or $ids.Count -ne 1) { throw "Worker Live no identificado: $service" }
    $ids[0]
}

$ingest = Worker 'ingesta-live'
$gold = Worker 'gold-live'
$api = Worker 'backend'

$silverCheck = @'
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from ingesta_delta_lake.ar_storage import ar_silver_pin
from ingesta_delta_lake.pipeline_publications import SilverSnapshot

root = Path('/pipelines/live')
status = json.loads((root / 'exchange/ar-intake.json').read_text())
ss = SilverSnapshot.open(SimpleNamespace(exchange=root / 'exchange'))
ar = ss.manifest['tables'].get('LISTA_AR', {})
with sqlite3.connect((root / 'checkpoint/intake.sqlite').as_uri() + '?mode=ro', uri=True) as db:
    pending = dict(db.execute("""
        SELECT state, count(*) FROM inputs AS i WHERE family='LISTA_AR'
        AND state IN ('CAPTURED','LOT_WAIT','WAITING_REVIEW','WAITING_RETRY','TRANSFERRED')
        AND NOT EXISTS (SELECT 1 FROM inputs AS n WHERE n.family=i.family
                        AND n.source_path=i.source_path AND n.rowid>i.rowid)
        GROUP BY state
    """))
print(json.dumps(dict(table=ar.get('table_id'), version=ar.get('version'), pending=pending,
                      pending_count=sum(pending.values()), blocked=status['blocked_origins'],
                      pin_ok=status['identity']==ss.manifest['identity']
                      and status['pin']==ar_silver_pin(ss.manifest))))
'@
$goldCheck = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'verify_live_ar_events.py') -Raw
$caseArgs = @()
if ($Casos) {
    $bytes = [Text.Encoding]::UTF8.GetBytes((Get-Content -LiteralPath $Casos -Raw))
    $caseArgs = @([Convert]::ToBase64String($bytes))
}

$limit = [DateTimeOffset]::UtcNow.AddMinutes(8)
$stage = 'RECOVERY_PENDING'
do {
    $silver = $silverCheck | docker --context $context exec -i $ingest python -B -
    $silverOk = $LASTEXITCODE -eq 0
    $scores = $goldCheck | docker --context $context exec -i $gold python -B - @caseArgs
    $scoresOk = $LASTEXITCODE -eq 0
    if ($silverOk -and $scoresOk) {
        $s = $silver | ConvertFrom-Json
        $g = $scores | ConvertFrom-Json
        if ($s.pin_ok -and $s.pending_count -eq 0 -and @($s.blocked).Count -eq 0 -and
            $s.table -eq $g.table -and $s.version -eq $g.version -and
            $g.attribution_rule -eq 'ar-reference-scope-v1') {
            if ($g.reference_checks_ok) { $stage = 'REFERENCE_CASES_VERIFIED'; break }
            if (!$Casos) { break }
        }
    }
    if (!$Once -and [DateTimeOffset]::UtcNow -lt $limit) { Start-Sleep -Seconds 20 }
} while (!$Once -and [DateTimeOffset]::UtcNow -lt $limit)
Write-Output "AR verification: $stage"
Write-Output "Silver: $silver"
Write-Output "Gold: $scores"
docker --context $context inspect $ingest $gold $api --format '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} {{.RestartCount}}'
if (!$silverOk -or !$scoresOk) { throw 'No se pudo completar la verificacion operativa AR.' }
