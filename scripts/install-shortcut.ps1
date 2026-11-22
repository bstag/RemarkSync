# Start RemarkSync from the Start Menu and the desktop, with no console window.
# The shortcuts run this checkout's own environment; they install nothing,
# start nothing at login, and -Remove takes them away again.
param(
    [switch]$NoDesktop,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$checkout = Split-Path -Parent $PSScriptRoot
$windowless = Join-Path $checkout '.venv\Scripts\pythonw.exe'
if (-not $Remove -and -not (Test-Path -LiteralPath $windowless)) {
    throw "No environment at $windowless. Create .venv and install RemarkSync into it first (see README)."
}
$folders = @([Environment]::GetFolderPath('Programs'))
if (-not $NoDesktop) { $folders += [Environment]::GetFolderPath('DesktopDirectory') }
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in $folders) {
    $shortcut = Join-Path $folder 'RemarkSync.lnk'
    if ($Remove) {
        if (Test-Path -LiteralPath $shortcut) {
            Remove-Item -LiteralPath $shortcut
            Write-Output "Removed $shortcut"
        }
        continue
    }
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = $windowless
    $link.Arguments = '-m remarksync'
    $link.WorkingDirectory = $checkout
    $link.Description = 'RemarkSync: view, search, edit and sync reMarkable documents'
    $link.Save()
    Write-Output "Created $shortcut"
}
