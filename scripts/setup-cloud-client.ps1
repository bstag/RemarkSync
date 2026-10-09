# Pin the separately distributed AGPL client; never install it globally.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$clientDir = Join-Path $taskRoot '.tools\rmapi'
$archivePath = Join-Path $clientDir 'rmapi-win64.zip'
$expectedDigest = 'f8132c68044f054b2cca14c30d4525ea0fd78b2af175998d2aad5b3c8afcb05b'
New-Item -ItemType Directory -Force -Path $clientDir | Out-Null
Invoke-WebRequest -Uri 'https://github.com/ddvk/rmapi/releases/download/v0.0.35/rmapi-win64.zip' -OutFile $archivePath
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedDigest) {
    throw 'Cloud client checksum mismatch; refusing to install.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskZip = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $taskExecutables = @($taskZip.Entries | Where-Object { $_.Name -eq 'rmapi.exe' })
    if ($taskExecutables.Count -ne 1) { throw 'Expected one rmapi.exe in the release.' }
    [IO.Compression.ZipFileExtensions]::ExtractToFile($taskExecutables[0], (Join-Path $clientDir 'rmapi.exe'), $true)
} finally { $taskZip.Dispose() }
Write-Output "Installed rmapi v0.0.35 in $clientDir (archive SHA256 verified)."
