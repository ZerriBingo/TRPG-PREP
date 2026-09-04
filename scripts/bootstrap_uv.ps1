[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [Parameter(Mandatory = $true)]
    [string]$DestinationRoot
)

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 may otherwise negotiate an obsolete TLS default.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Keep the launcher reproducible. Update the hashes together with the version.
$assets = @{
    "AMD64" = @{
        Name = "uv-x86_64-pc-windows-msvc.zip"
        Sha256 = "ddbfcee1ac615a0499f6aa97b5ec8ebdf3ee4a7714a48055ec2ba0030e3cf810"
    }
    "ARM64" = @{
        Name = "uv-aarch64-pc-windows-msvc.zip"
        Sha256 = "d3360363a3cb671f2c854f4ef48cf4a57fe8664f8ec6a248076d68b797a8acc0"
    }
    "X86" = @{
        Name = "uv-i686-pc-windows-msvc.zip"
        Sha256 = "62396154da2dc04a9fffb027e75ae3d971ca3ac7d3f0ffa7dd2c27c94798ce3f"
    }
}

$architecture = $env:PROCESSOR_ARCHITEW6432
if ([string]::IsNullOrWhiteSpace($architecture)) {
    $architecture = $env:PROCESSOR_ARCHITECTURE
}
$architecture = $architecture.ToUpperInvariant()
if (-not $assets.ContainsKey($architecture)) {
    throw "Unsupported Windows architecture: $architecture"
}

$asset = $assets[$architecture]
$versionDirectory = Join-Path $DestinationRoot $Version
$uvPath = Join-Path $versionDirectory "uv.exe"

function Test-UvBinary {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    try {
        $versionOutput = (& $Path --version 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        return $versionOutput -like "uv $Version*"
    } catch {
        return $false
    }
}

if (Test-UvBinary -Path $uvPath) {
    exit 0
}

New-Item -ItemType Directory -Force -Path $versionDirectory | Out-Null
$temporaryDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("trpg-prep-uv-" + [Guid]::NewGuid().ToString("N"))

try {
    New-Item -ItemType Directory -Force -Path $temporaryDirectory | Out-Null
    $archivePath = Join-Path $temporaryDirectory $asset.Name
    $extractDirectory = Join-Path $temporaryDirectory "extract"
    $downloadUrl = "https://github.com/astral-sh/uv/releases/download/$Version/$($asset.Name)"

    Write-Output "Downloading uv $Version..."
    Invoke-WebRequest -UseBasicParsing -Uri $downloadUrl -OutFile $archivePath

    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $asset.Sha256) {
        throw "The downloaded uv archive failed SHA-256 verification."
    }

    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractDirectory -Force
    $downloadedUvPath = Join-Path $extractDirectory "uv.exe"
    if (-not (Test-Path -LiteralPath $downloadedUvPath -PathType Leaf)) {
        throw "The uv archive did not contain uv.exe."
    }

    $stagedPath = Join-Path $versionDirectory "uv-staged.exe"
    Remove-Item -LiteralPath $stagedPath -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $downloadedUvPath -Destination $stagedPath -Force
    if (-not (Test-UvBinary -Path $stagedPath)) {
        throw "The downloaded uv executable failed its version check."
    }
    Move-Item -LiteralPath $stagedPath -Destination $uvPath -Force
    if (-not (Test-UvBinary -Path $uvPath)) {
        throw "The installed uv executable failed its version check."
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
} finally {
    Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
