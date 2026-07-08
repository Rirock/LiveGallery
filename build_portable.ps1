param(
    [string]$PythonExe = "python",
    [string]$ReleaseName = "LiveGallery"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "Installing PyInstaller if needed..."
& $PythonExe -m pip install pyinstaller

$distPath = Join-Path $root "release"
$workPath = Join-Path $root "build\\pyinstaller"
$iconPath = Join-Path $root "assets\\LiveGallery.ico"
$logoPath = Join-Path $root "logo.png"
$sourceStage = Join-Path $root "build\\source-package\\LiveGallery"

function Compress-WithRetry {
    param(
        [string]$Path,
        [string]$DestinationPath
    )

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            if (Test-Path $DestinationPath) {
                Remove-Item $DestinationPath -Force
            }
            Compress-Archive -Path $Path -DestinationPath $DestinationPath -CompressionLevel Optimal
            return
        }
        catch {
            if ($attempt -eq 5) {
                throw
            }
            Start-Sleep -Seconds 2
        }
    }
}

Write-Host "Building portable folder: $ReleaseName"
& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name $ReleaseName `
    --icon $iconPath `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $workPath `
    --exclude-module numpy `
    --exclude-module PySide6.QtQml `
    --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtVirtualKeyboard `
    --collect-all pillow_heif `
    --add-data "$logoPath;." `
    src/main.py

$releaseRoot = Join-Path $distPath $ReleaseName
New-Item -ItemType Directory -Force -Path (Join-Path $releaseRoot "cache") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $releaseRoot "logs") | Out-Null
Copy-Item README.md (Join-Path $releaseRoot "README.md") -Force
Copy-Item logo.png (Join-Path $releaseRoot "logo.png") -Force

$duplicateFfmpeg = Join-Path $releaseRoot "_internal\\ffmpeg-win-x86_64-v7.1.exe"
if (Test-Path $duplicateFfmpeg) {
    Remove-Item $duplicateFfmpeg -Force
}

$unusedQtFiles = @(
    "_internal\\PySide6\\Qt6Pdf.dll",
    "_internal\\PySide6\\Qt6Qml.dll",
    "_internal\\PySide6\\Qt6QmlMeta.dll",
    "_internal\\PySide6\\Qt6QmlModels.dll",
    "_internal\\PySide6\\Qt6QmlWorkerScript.dll",
    "_internal\\PySide6\\Qt6Quick.dll",
    "_internal\\PySide6\\Qt6VirtualKeyboard.dll",
    "_internal\\PySide6\\plugins\\platforminputcontexts\\qtvirtualkeyboardplugin.dll"
)
foreach ($relativePath in $unusedQtFiles) {
    $target = Join-Path $releaseRoot $relativePath
    if (Test-Path $target) {
        Remove-Item $target -Force
    }
}

$translationDir = Join-Path $releaseRoot "_internal\\PySide6\\translations"
if (Test-Path $translationDir) {
    Remove-Item $translationDir -Recurse -Force
}

Get-ChildItem -Path (Join-Path $releaseRoot "cache") -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path (Join-Path $releaseRoot "logs") -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$releaseZip = Join-Path $distPath "$ReleaseName-portable-windows.zip"
Compress-WithRetry -Path $releaseRoot -DestinationPath $releaseZip

if (Test-Path $sourceStage) {
    Remove-Item $sourceStage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $sourceStage | Out-Null
Copy-Item src -Destination $sourceStage -Recurse -Force
Copy-Item assets -Destination $sourceStage -Recurse -Force
Copy-Item README.md, requirements.txt, build_portable.ps1, logo.png, .gitignore -Destination $sourceStage -Force
Get-ChildItem -Path $sourceStage -Directory -Recurse -Force | Where-Object {
    $_.Name -in @(".venv", "__pycache__", ".pytest_cache")
} | Sort-Object FullName -Descending | Remove-Item -Recurse -Force
Get-ChildItem -Path $sourceStage -File -Recurse -Force | Where-Object {
    $_.Name -like "*.pyc" -or $_.Name -like "*.pyo"
} | Remove-Item -Force

$sourceZip = Join-Path $distPath "$ReleaseName-source.zip"
Compress-WithRetry -Path $sourceStage -DestinationPath $sourceZip

Write-Host "Portable build ready: $releaseRoot"
Write-Host "Portable zip ready: $releaseZip"
Write-Host "Source zip ready: $sourceZip"
