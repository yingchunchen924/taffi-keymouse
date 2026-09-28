$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Work = Join-Path $Root "_build"
$Dist = Join-Path $Work "dist"
$DisplayName = -join ([char[]](0x5854, 0x83F2, 0x952E, 0x9F20))
$ToolsDirName = -join ([char[]](0x5DE5, 0x5177))
$OutName = "${DisplayName}3.2.2.exe"
$OutPath = Join-Path $Root $OutName
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
Copy-Item -LiteralPath (Join-Path $Dist "TaffiKeyboardMouse3.exe") -Destination $OutPath -Force

$ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) (Join-Path $ToolsDirName "${DisplayName}.lnk")
if (Test-Path -LiteralPath $ShortcutPath) {
  $Shell = New-Object -ComObject WScript.Shell
  $Shortcut = $Shell.CreateShortcut($ShortcutPath)
  $Shortcut.TargetPath = $OutPath
  $Shortcut.Arguments = ""
  $Shortcut.WorkingDirectory = $Root
  $Shortcut.IconLocation = "$(Join-Path $Root "assets\taffi_icon.ico"),0"
  $Shortcut.Save()
}
