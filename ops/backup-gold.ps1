param(
    [Parameter(Mandatory=$true)][string]$GoldRoot,
    [Parameter(Mandatory=$true)][string]$BackupRoot
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $BackupRoot -PathType Container)) {
    throw 'El destino independiente debe existir y estar montado antes del backup.'
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$backupName = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N')
$backupTarget = Join-Path $BackupRoot $backupName
Push-Location -LiteralPath $projectRoot
try {
    uv run --locked --extra gold python -m app.gold backup --source $GoldRoot --destination $backupTarget
    if ($LASTEXITCODE -ne 0) { throw 'La copia o su verificacion ha fallado.' }
} finally {
    Pop-Location
}
