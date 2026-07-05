$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Work = Join-Path $Root "_build"
$Dist = Join-Path $Work "dist"
$OutName = "塔菲键鼠3.1.exe"
New-Item -ItemType Directory -Force -Path $Work | Out-Null
python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "TaffiKeyboardMouse3" `
  --icon (Join-Path $Root "assets\taffi_icon.ico") `
  --add-data "$Root\assets;assets" `
  --distpath $Dist `
  --workpath $Work `
  --specpath $Work `
  (Join-Path $Root "main.py")
Copy-Item -LiteralPath (Join-Path $Dist "TaffiKeyboardMouse3.exe") -Destination (Join-Path $Root $OutName) -Force
