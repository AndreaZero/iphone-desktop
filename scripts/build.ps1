param([switch]$SkipDependencyInstall)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv with Python 3.13 first: py -3.13 -m venv .venv'
}
if (-not $SkipDependencyInstall) {
    & $pythonPath -m pip install --disable-pip-version-check -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $pythonPath -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Validation failed.' }
$runtimePath = Join-Path $projectRoot 'dist\iPhone Desktop\.runtime'
$runtimeBackup = Join-Path $projectRoot ('.runtime\bundle-runtime-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $runtimePath) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $runtimeBackup) -Force | Out-Null
    Copy-Item -LiteralPath $runtimePath -Destination $runtimeBackup -Recurse
}
try {
& $pythonPath -m PyInstaller --noconfirm --windowed --onedir --name 'iPhone Desktop' --icon 'iphone_desktop/web/app.ico' --exclude-module IPython --exclude-module jedi --add-data 'iphone_desktop/web;iphone_desktop/web' --collect-data pymobiledevice3 --collect-data developer_disk_image --collect-binaries pytun_pmd3 --collect-data pytun_pmd3 --recursive-copy-metadata pymobiledevice3 --copy-metadata aiohttp run.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
} finally {
    if (Test-Path -LiteralPath $runtimeBackup) {
        New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
        Get-ChildItem -LiteralPath $runtimeBackup -Force | Copy-Item -Destination $runtimePath -Recurse -Force
    }
}
Copy-Item -LiteralPath 'README.md' -Destination 'dist\iPhone Desktop\README.md'
Copy-Item -LiteralPath 'LICENSE','THIRD_PARTY.md','VALIDATION.md','requirements-lock.txt' -Destination 'dist\iPhone Desktop'
& $pythonPath scripts\bundle_notices.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
$shortcutShell = New-Object -ComObject WScript.Shell
$appShortcut = $shortcutShell.CreateShortcut((Join-Path $projectRoot 'iPhone Desktop.lnk'))
$appShortcut.TargetPath = Join-Path $projectRoot 'dist\iPhone Desktop\iPhone Desktop.exe'
$appShortcut.WorkingDirectory = Join-Path $projectRoot 'dist\iPhone Desktop'
$appShortcut.Description = 'Launch iPhone Desktop from the complete package'
$appShortcut.IconLocation = $appShortcut.TargetPath + ',0'
$appShortcut.Save()
Write-Output 'Ready: dist\iPhone Desktop\iPhone Desktop.exe'
