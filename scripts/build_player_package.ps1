[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [string]$Version = "0.1.4"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("trpg-prep-player-" + [Guid]::NewGuid().ToString("N"))
$packageRoot = Join-Path $temporaryDirectory ("TRPG-Prep-" + $Version)

try {
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
    foreach ($directory in @("backend", "frontend")) {
        $source = Join-Path $repo $directory
        $destination = Join-Path $packageRoot $directory
        & robocopy $source $destination /E /XD "__pycache__" (Join-Path $source "domain\examples") /XF "*.pyc"
        if ($LASTEXITCODE -gt 7) {
            throw "Could not copy runtime directory: $directory"
        }
    }

    foreach ($file in @(
        ".python-version", "CHANGELOG.md", "LICENSE", "README.md", "VERSION",
        "pyproject.toml", "start.bat", "start.vbs", "stop.bat", "uv.lock"
    )) {
        Copy-Item -LiteralPath (Join-Path $repo $file) -Destination (Join-Path $packageRoot $file)
    }
    New-Item -ItemType Directory -Path (Join-Path $packageRoot "scripts") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo "scripts\bootstrap_uv.ps1") -Destination (Join-Path $packageRoot "scripts\bootstrap_uv.ps1")

    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    New-Item -ItemType Directory -Path (Split-Path $resolvedOutput) -Force | Out-Null
    Compress-Archive -Path $packageRoot -DestinationPath $resolvedOutput -CompressionLevel Optimal

    $entries = @(tar -tf $resolvedOutput)
    $forbidden = $entries | Select-String -Pattern '(__pycache__|\.pyc$|(^|/)(data|Resource|output|docs|scripts/test|domain/examples|.*\.db$|.*\.pdf$|.*\.log$|.*\.jsonl$|.*\.jpg$))'
    if ($forbidden) {
        throw ("Player package contains forbidden files: " + ($forbidden -join ", "))
    }
    Write-Output "Created player package: $resolvedOutput"
    Write-Output "Files: $($entries.Count)"
}
finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
