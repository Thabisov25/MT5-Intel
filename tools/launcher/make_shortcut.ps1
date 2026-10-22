<#
  Create a double-clickable shortcut to the launcher, carrying the app icon.

    powershell -ExecutionPolicy Bypass -File tools\launcher\make_shortcut.ps1
    powershell -ExecutionPolicy Bypass -File tools\launcher\make_shortcut.ps1 -Desktop
#>

[CmdletBinding()]
param(
  [string]$Name = 'MT5 Intel',
  [switch]$Desktop
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path

$target = Join-Path $Root 'Start MT5 Intel.bat'
$icon = Join-Path $Root 'assets\mt5-intel.ico'

if (-not (Test-Path $target)) { throw "Launcher not found: $target" }
if (-not (Test-Path $icon)) { throw "Icon not found: $icon (run tools\launcher\make_icon.py first)" }

$folder = if ($Desktop) { [Environment]::GetFolderPath('Desktop') } else { $Root }
$link = Join-Path $folder ("{0}.lnk" -f $Name)

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $target
$sc.WorkingDirectory = $Root
$sc.IconLocation = "$icon,0"
$sc.Description = 'Start the MT5 intel read-only research console'
$sc.WindowStyle = 1
$sc.Save()

Write-Host ("Created {0}" -f $link) -ForegroundColor Green
