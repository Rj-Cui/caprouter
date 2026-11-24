param(
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'CapRouter Tray.lnk'

if ($Uninstall) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
    }
    Write-Output "Removed: $shortcutPath"
    exit 0
}

$target = Join-Path $PSScriptRoot 'start-tray.vbs'
if (-not (Test-Path -LiteralPath $target)) {
    throw "Missing launcher: $target"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$shortcut.Arguments = '"' + $target + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'CapRouter tray toggle'
$shortcut.Save()

Write-Output "Installed: $shortcutPath"
