# One-command build. Output: dist\BTSignalMeter\ (then optionally Inno Setup)
$ErrorActionPreference = "Stop"
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --name "BTSignalMeter" `
  --paths app --collect-submodules bleak --collect-submodules winrt `
  --distpath tmp_dist --workpath build app\main.py
Move-Item "tmp_dist\BTSignalMeter" dist
Remove-Item tmp_dist -Recurse -Force
Write-Host "Done: dist\BTSignalMeter.exe"
if (Get-Command iscc -ErrorAction SilentlyContinue) {
  iscc installer.iss
} else {
  Write-Host "Inno Setup not found. Install with: winget install JRSoftware.InnoSetup"
}
