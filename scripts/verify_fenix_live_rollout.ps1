param(
    [string]$Context = 'desktop-linux',
    [string]$BaseUrl = 'http://192.168.10.37:8011',
    [string]$Revision = '',
    [string]$GoldRevision = 'a1b9df74927d99a942a54ba0d1d2ed72aae76bcc'
)
$ErrorActionPreference = 'Stop'
$project = 'fenix-aiala-independent'
function Invoke-Docker {
    & docker --context $Context @args
    if ($LASTEXITCODE -ne 0) { throw "Docker fallo al ejecutar $($args[0])." }
}
$services = @(foreach ($name in 'ingesta-live','gold-live','backend','frontend') {
    $id = @(Invoke-Docker ps -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$name")
    if ($id.Count -ne 1) { throw "$name no tiene exactamente un contenedor activo." }
    $c = @(((Invoke-Docker inspect $id[0]) -join ' ') | ConvertFrom-Json)[0]
    $im = @(((Invoke-Docker image inspect $c.Image) -join ' ') | ConvertFrom-Json)[0]
    $code = $im.Config.Labels.'org.opencontainers.image.revision'
    $gold = $im.Config.Labels.'es.fenix.ingesta.gold.revision'
    if ($name -ne 'frontend' -and ($gold -cne $GoldRevision -or ($Revision -and $code -cne $Revision))) {
        throw "Revision inesperada en $name."
    }
    [pscustomobject]@{
        Service=$name; Id=$id[0]; Image=$c.Image; Revision=$code; Gold=$gold
        T0=@($c.Config.Env | Where-Object { $_ -like 'FENIX_LIVE_T0=*' }) -join ','
        Health=$c.State.Health.Status; RestartCount=$c.RestartCount; OOM=$c.State.OOMKilled
        Volumes=@($c.Mounts | Where-Object Type -eq 'volume' | ForEach-Object { "$($_.Name):$($_.Destination)" }) -join ','
    }
})
$services | Format-List
$t0 = @($services | Where-Object Service -ne 'frontend' | Select-Object -ExpandProperty T0 -Unique)
if ($t0.Count -ne 1 -or -not $t0[0]) { throw 'T0 no coincide entre los servicios Live.' }
foreach ($name in 'gold-history','ingesta-history') {
    if (@(Invoke-Docker ps -q --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$name").Count) {
        throw "$name figura activo."
    }
}
Invoke-Docker stats --no-stream @($services.Id)
Invoke-Docker system df
foreach ($name in 'ingesta-live','gold-live') {
    Write-Host "Eventos recientes $name"
    Invoke-Docker logs --tail 35 ($services | Where-Object Service -eq $name).Id
}
$api = Invoke-RestMethod -Uri "$BaseUrl/api/live/scores" -TimeoutSec 30
[pscustomobject]@{ Publication=$api.publication_id; Published=$api.published_at; Stale=$api.stale } |
    Format-List
$api.active_productions | Format-Table line,machine,state,model,zf_model,production,zf_production -AutoSize
$closed = @(foreach ($hour in $api.hours | Where-Object state -eq 'closed') {
    foreach ($row in $hour.results) {
        [pscustomobject]@{
            Hour=$hour.end; Line=$row.line; Machine=$row.machine; Production=$row.production
            Model=$row.model; Cut=$row.cutoff; State=$row.state; Total=$row.total_penalty
            Partial=$row.partial_subtotal; Class=$row.classification; MIA=$row.contributions.MIA
            DAR=$row.contributions.DAR; ZC=$row.contributions.ZC; Reason=$row.reason
        }
    }
})
if (@($closed | Group-Object Hour,Line,Machine | Where-Object Count -gt 1).Count) {
    throw 'La API muestra cierres duplicados en una posicion.'
}
if (@($closed | Where-Object { $_.State -eq 'partial' -and ($null -ne $_.Total -or $_.Class) }).Count) {
    throw 'La API publica un subtotal como score oficial.'
}
$closed | Sort-Object Hour,Line,Machine | Format-Table -Wrap
