$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $workspace
$builderPython = Join-Path $workspace '.connector-build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $builderPython)) {
    python -m venv (Join-Path $workspace '.connector-build-venv')
}
& $builderPython -m pip install -r (Join-Path $workspace 'requirements-connector-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Connector build dependencies failed to install.' }
& $builderPython -m PyInstaller --noconfirm --clean --onefile --windowed --name TallyConnector --distpath (Join-Path $workspace 'dist') --workpath (Join-Path $workspace 'build') --specpath (Join-Path $workspace 'build') --hidden-import pystray._win32 (Join-Path $workspace 'connector\desktop.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
$builtExe = Join-Path $workspace 'dist\TallyConnector.exe'
$downloadDir = Join-Path $workspace 'app\downloads'
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
Copy-Item -LiteralPath $builtExe -Destination (Join-Path $downloadDir 'TallyConnector.exe') -Force
Write-Output "Built Windows connector: $builtExe"
Write-Output "Website download file: $(Join-Path $downloadDir 'TallyConnector.exe')"
