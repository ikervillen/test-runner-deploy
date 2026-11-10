[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl,
    [Parameter(Mandatory = $true)]
    [string]$ReleaseRef,
    [Parameter(Mandatory = $true)]
    [string]$InstallDirectory,
    [Parameter(Mandatory = $true)]
    [string]$EnvironmentFile,
    [string]$ApplicationUrl
)

$ErrorActionPreference = 'Stop'

function Invoke-CheckedCommand {
    param([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory = $PWD)

    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Wait-Readiness {
    param([string]$Url)

    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 3
        }
    }
    throw "The application did not become ready at $Url within 3 minutes."
}

function Get-EnvironmentValue {
    param([string]$Path, [string]$Name)

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        if ($trimmed -match "^$([regex]::Escape($Name))=(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return $null
}

foreach ($command in @('git', 'docker')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $command"
    }
}

$install = [System.IO.Path]::GetFullPath($InstallDirectory)
$envPath = [System.IO.Path]::GetFullPath($EnvironmentFile)
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "Environment file not found: $envPath"
}

if ([string]::IsNullOrWhiteSpace($ApplicationUrl)) {
    $frontendPort = Get-EnvironmentValue -Path $envPath -Name 'FRONTEND_PORT'
    if ([string]::IsNullOrWhiteSpace($frontendPort)) {
        $frontendPort = '5174'
    }
    $parsedPort = 0
    if (-not [int]::TryParse($frontendPort, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw "FRONTEND_PORT no es un puerto valido: $frontendPort"
    }
    $ApplicationUrl = "http://127.0.0.1:$parsedPort/api/ready"
}

if ($WhatIfPreference -and -not (Test-Path -LiteralPath $install)) {
    Write-Host "WhatIf: repository would be cloned into $install and deployed from $ReleaseRef."
    return
}

if (-not (Test-Path -LiteralPath $install)) {
    if ($PSCmdlet.ShouldProcess($install, "Clone $RepositoryUrl")) {
        Invoke-CheckedCommand git @('clone', $RepositoryUrl, $install)
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $install '.git') -PathType Container)) {
    throw "Install directory is not a Git checkout: $install"
}

$dirty = (& git -C $install status --porcelain)
if ($dirty) {
    throw "Refusing deployment because the checkout has local changes: $install"
}

if ($PSCmdlet.ShouldProcess($install, "Fetch and verify release $ReleaseRef")) {
    Invoke-CheckedCommand git @('-C', $install, 'fetch', '--tags', '--prune', 'origin')
}

$targetCommit = (& git -C $install rev-parse "$ReleaseRef^{commit}").Trim()
if (-not $targetCommit) {
    throw "Release ref cannot be resolved to a commit: $ReleaseRef"
}
$previousCommit = (& git -C $install rev-parse HEAD).Trim()
$stateDirectory = Join-Path $install '.deployment'
$previousPath = Join-Path $stateDirectory 'previous-release.txt'

if ($PSCmdlet.ShouldProcess($install, "Deploy $targetCommit")) {
    New-Item -ItemType Directory -Force -Path $stateDirectory | Out-Null
    Set-Content -LiteralPath $previousPath -Value $previousCommit -NoNewline
    try {
        Invoke-CheckedCommand git @('-C', $install, 'checkout', '--detach', $targetCommit)
        Invoke-CheckedCommand docker @(
            'compose', '--env-file', $envPath, '-f', 'compose.yaml', 'up', '--build', '--detach', '--remove-orphans'
        ) $install
        Wait-Readiness $ApplicationUrl
    }
    catch {
        Write-Error "Deployment failed. Restoring $previousCommit."
        Invoke-CheckedCommand git @('-C', $install, 'checkout', '--detach', $previousCommit)
        Invoke-CheckedCommand docker @(
            'compose', '--env-file', $envPath, '-f', 'compose.yaml', 'up', '--build', '--detach', '--remove-orphans'
        ) $install
        throw
    }
}

Write-Host "Deployment completed: $targetCommit"
