# DownloadTooler engine build script
#
# Builds ONE merged multi-entry engine (onedir) so the three tasks share a single
# Python runtime and the curl_cffi / lxml / DrissionPage deps instead of being
# packaged three times.
#
# Output: tauri-app/src-tauri/engines/dt-engine/dt-engine.exe (+ _internal/)
# The task is selected at runtime via the DT_TASK env var (see tools/engine_launcher.py).
#
# Usage: powershell -ExecutionPolicy Bypass -File tools\build_sidecars.ps1
param(
  [switch]$CleanExport   # drop the previous output dir before building
)

$ErrorActionPreference = "Stop"

# rustup/pip live under the user profile; make sure python is reachable
$Root    = Split-Path -Parent $PSScriptRoot          # tauri-app/
$Engines = Join-Path $Root "src-tauri\engines"
$WorkDir = Join-Path $Root "build\engines"

$DyRoot    = "E:\item\DownloadTooler\Dy_Downloader"
$XhsRoot   = "E:\item\DownloadTooler\XHS_Downloader"
$XhsPython = Join-Path $XhsRoot ".venv\Scripts\python.exe"
# gmssl only exists in the Dy venv; the merged engine is built with the XHS venv,
# so add the Dy site-packages as an extra search path (appended after the venv's own,
# so shared deps still resolve to the XHS venv versions).
$DySite    = Join-Path $DyRoot ".venv\Lib\site-packages"

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }

if (-not (Test-Path $XhsPython)) { throw "python not found: $XhsPython" }
$pyiVer = & $XhsPython -m PyInstaller --version 2>$null
if (-not $pyiVer) { throw "PyInstaller not installed in $XhsPython" }

if ($CleanExport) { Remove-Item -Recurse -Force $Engines -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $Engines, $WorkDir | Out-Null

# Excludes: none of these are used by the engine code paths.
#   numpy  -> only DrissionPage/screencast.py (lazy import, unused here)
#   PIL    -> never imported by dy_app / xhs_app / DrissionPage / curl_cffi
#   others -> GUI / data-science stacks that only bloat the bundle
$Excludes = @(
  "numpy", "PIL", "scipy", "matplotlib", "pandas", "IPython",
  "tkinter", "PySide6", "shiboken6", "PyQt5", "PyQt6", "PySide2"
)
$excludeArgs = @()
foreach ($m in $Excludes) { $excludeArgs += @("--exclude-module", $m) }

Step "PyInstaller onedir: dt-engine (merged multi-entry)"
$pyiArgs = @(
  "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
  "--distpath", $Engines,
  "--workpath", (Join-Path $WorkDir "work"),
  "--specpath", (Join-Path $WorkDir "spec"),
  "--name", "dt-engine",
  "--paths", $DyRoot,
  "--paths", $XhsRoot,
  "--paths", $DySite,
  # entry modules are imported dynamically by the launcher
  "--hidden-import", "dy_app.dyc_download",
  "--hidden-import", "xhs_engine.note_one",
  "--hidden-import", "xhs_engine.download_one",
  "--hidden-import", "gmssl",
  "--hidden-import", "websocket",
  "--hidden-import", "yaml",
  "--collect-all", "DrissionPage",
  "--collect-all", "curl_cffi"
) + $excludeArgs + @((Join-Path $PSScriptRoot "engine_launcher.py"))

& $XhsPython @pyiArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Step "done"
$dir = Join-Path $Engines "dt-engine"
$exe = Join-Path $dir "dt-engine.exe"
if (-not (Test-Path $exe)) { throw "engine exe not found: $exe" }
$size = (Get-ChildItem $dir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("engine exe  : {0} ({1:N1} MB)" -f $exe, ((Get-Item $exe).Length / 1MB))
Write-Host ("engine dir  : {0:N1} MB (onedir, uncompressed)" -f ($size / 1MB))
Write-Host "`nquick check:"
& $exe --help 2>&1 | Select-Object -First 3
Write-Host "(a 'DT_TASK not set' message above is expected - the exe dispatches by task)"
