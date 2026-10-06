# DownloadTooler portable release assembly
#
# Runs `tauri build` (unless -SkipBuild) and assembles a portable folder:
#   DownloadTooler.exe + the three engine sidecars + PATCH_README.txt
# then zips it.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\build_release.ps1
#   powershell -ExecutionPolicy Bypass -File tools\build_release.ps1 -SkipBuild
param(
  [string]$Version = "0.3.0",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

# rustup installs cargo under %USERPROFILE%\.cargo\bin which may not be on PATH
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if ((Test-Path $cargoBin) -and ($env:PATH -notlike "*$cargoBin*")) {
  $env:PATH = "$cargoBin;$env:PATH"
}

$Root    = Split-Path -Parent $PSScriptRoot          # tauri-app/
$Rel     = Join-Path $Root "src-tauri\target\release"
$OutRoot = Join-Path $Root "build\release"
$AppDir  = Join-Path $OutRoot "DownloadTooler"

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

if (-not $SkipBuild) {
  Step "tauri build (release)"
  Push-Location $Root
  & npm run tauri build
  $code = $LASTEXITCODE
  Pop-Location
  if ($code -ne 0) { throw "tauri build failed (exit $code)" }
}

Step "assemble portable dir"
$mainExe = Join-Path $Rel "DownloadTooler.exe"
if (-not (Test-Path $mainExe)) { throw "release exe not found: $mainExe" }

Remove-Item -Recurse -Force $OutRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
Copy-Item $mainExe $AppDir

# The engine is a multi-entry onedir bundle, shipped as a bundle resource:
#   <bundle>/engines/dt-engine/dt-engine.exe (+ _internal/)
$engineSrc = Join-Path $Rel "engines"
if (-not (Test-Path $engineSrc)) { throw "engine dir not found: $engineSrc" }
Copy-Item -Recurse $engineSrc (Join-Path $AppDir "engines")

$readme = @'
PATCH INTERFACE
===============
Engine hot-patch: replace the whole engine, or drop a standalone exe, under
    %APPDATA%\DownloadTooler\patches\engine\
accepted layout (first hit wins):
    dt-engine-x86_64-pc-windows-msvc.exe
    dt-engine.exe
    dt-engine\dt-engine.exe          <- full engine folder replacement (recommended)
No repack needed - the patch wins from the next engine call.

The engine is a single multi-entry bundle; the task is chosen by the DT_TASK
env var (dy-download / xhs-note / xhs-download).

State / data locations
----------------------
%LOCALAPPDATA%\DownloadTooler\panel_data   panel settings (download dir, shortcuts)
%LOCALAPPDATA%\DownloadTooler\dy_data      Douyin webview profile
%LOCALAPPDATA%\DownloadTooler\xhs_data     Xiaohongshu webview profile
%LOCALAPPDATA%\DyCollector\cookies.json    Douyin cookie snapshot
%LOCALAPPDATA%\XHSCollector\cookies.json   Xiaohongshu cookie snapshot

Uninstall: delete this folder; optionally delete the data folders above.
'@
Set-Content -Path (Join-Path $AppDir "PATCH_README.txt") -Value $readme -Encoding UTF8

Step "zip"
$zip = Join-Path $OutRoot "DownloadTooler-v$Version-portable.zip"
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $zip -CompressionLevel Optimal

Step "done"
$total = (Get-ChildItem $AppDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("portable dir : {0}" -f $AppDir)
Write-Host ("portable size: {0:N1} MB" -f ($total / 1MB))
Write-Host ("zip          : {0}  ({1:N1} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
