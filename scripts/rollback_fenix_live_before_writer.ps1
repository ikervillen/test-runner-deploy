param([Parameter(Mandatory)][string]$Record)
$ErrorActionPreference = 'Stop'
$old = Get-Content -LiteralPath $Record -Raw | ConvertFrom-Json
if ($old.writer_activation_attempted -ne $false -or -not $old.services.'gold-live') {
    throw 'El escritor nuevo pudo publicar. Mantenga el lector compatible y coordine la reversion.'
}
function Invoke-Docker {
    & docker --context $old.context @args
    if ($LASTEXITCODE -ne 0) { throw "Docker fallo al ejecutar $($args[0])." }
}
foreach ($entry in $old.services.PSObject.Properties) {
    $compose = Get-Content -LiteralPath $entry.Value.compose -Raw | ConvertFrom-Json
    if ($compose.name -cne $old.project) { throw 'Proyecto inesperado en la copia anterior.' }
    $ref = $compose.services.($entry.Name).image
    $image = @(((Invoke-Docker image inspect $ref) -join ' ') | ConvertFrom-Json)[0]
    if ($image.Id -cne $entry.Value.image) { throw "Imagen anterior distinta en $($entry.Name)." }
}
if (-not $old.services.frontend) {
    Invoke-Docker compose -p $old.project -f $old.candidate stop frontend
}
foreach ($name in 'ingesta-live','backend','frontend','gold-live') {
    $entry = $old.services.$name
    if ($entry) {
        Invoke-Docker compose -p $old.project -f $entry.compose up -d --no-deps --no-build --pull never $name
    }
}
