# Builds the distributable EmperorsTouch Bridge zip.
#
# Usage:  .\build.ps1        (from the bridge/ directory)
#
# Output: build-standalone\EmperorsTouchBridge.zip containing
#   EmperorsTouchBridge\           compiled app (run EmperorsTouchBridge.exe)
#   bridge.py                      source, for auditing or running directly
#   README.md                      usage instructions
#
# Notes:
# - Uses --standalone (folder), NOT --onefile: the onefile self-extracting
#   stub trips antivirus heuristics (9 VirusTotal detections incl. a
#   Microsoft PUA flag vs ~none for the folder build).
# - NUITKA_CACHE_DIR must be a short, real path: letting Nuitka default
#   into AppData broke the MinGW extraction once (missing headers).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:NUITKA_CACHE_DIR = "D:\nuitka-cache"
$outDir = "build-standalone"

# Clean previous build output
if (Test-Path $outDir) {
    Remove-Item $outDir -Recurse -Force
}

python -m nuitka `
    --standalone `
    --windows-console-mode=disable `
    --enable-plugin=tk-inter `
    --include-package=buttplug `
    --assume-yes-for-downloads `
    --output-dir=$outDir `
    --output-filename=EmperorsTouchBridge.exe `
    bridge.py

if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed"
}

# Nuitka names the folder after the script; give it the app name
Rename-Item "$outDir\bridge.dist" "EmperorsTouchBridge"

# Smoke test: the exe must start and stay up
$p = Start-Process "$outDir\EmperorsTouchBridge\EmperorsTouchBridge.exe" -PassThru
Start-Sleep -Seconds 5
if ($p.HasExited) {
    throw "Smoke test failed: exe exited immediately with code $($p.ExitCode)"
}
Stop-Process -Id $p.Id -Force
Write-Host "Smoke test passed"

# Stage source + docs next to the app folder, then zip everything
Copy-Item "bridge.py" "$outDir\bridge.py"
Copy-Item "README.md" "$outDir\README.md"

$zip = "$outDir\EmperorsTouchBridge.zip"
Compress-Archive -Path "$outDir\EmperorsTouchBridge", "$outDir\bridge.py", "$outDir\README.md" -DestinationPath $zip -Force

$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Done: $zip ($size MB)"
