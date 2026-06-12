# Installs the Liero GIMP plug-ins for the current user (GIMP 3.x, Windows).
#
# GIMP 3 on Windows looks for plug-ins in %APPDATA%\GIMP\<MAJOR.MINOR>\plug-ins,
# one folder per plug-in. The plug-ins are pure Python and run on GIMP's
# bundled Python - nothing to compile.
#
# Usage:  powershell -ExecutionPolicy Bypass -File install-windows-user.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$gimpBase = Join-Path $env:APPDATA 'GIMP'

# pick the newest GIMP 3.x config dir; default to 3.2 if GIMP never ran yet
$ver = $null
if (Test-Path $gimpBase) {
    $dirs = Get-ChildItem $gimpBase -Directory |
        Where-Object { $_.Name -match '^3\.[0-9]+$' } |
        Sort-Object { [version]$_.Name } -Descending
    if ($dirs) { $ver = $dirs[0].Name }
}
if (-not $ver) { $ver = '3.2' }

$pluginBase = Join-Path (Join-Path $gimpBase $ver) 'plug-ins'
New-Item -ItemType Directory -Force -Path $pluginBase | Out-Null

Get-ChildItem (Join-Path $root 'plugins') -Filter *.py | ForEach-Object {
    $name = $_.BaseName
    $dest = Join-Path $pluginBase $name
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item $_.FullName (Join-Path $dest "$name.py") -Force
    $core = Join-Path $dest 'liero_core'
    if (Test-Path $core) { Remove-Item $core -Recurse -Force }
    Copy-Item (Join-Path $root 'liero_core') $core -Recurse
    Remove-Item (Join-Path $core '__pycache__') -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Installed Liero plug-ins for GIMP $ver to $pluginBase"
Write-Host "Restart GIMP and check the Liero menu."
