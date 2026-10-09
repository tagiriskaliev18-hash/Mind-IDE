# Ярлык Mind на рабочем столе Windows: открывает Mind Studio (launch-studio.py) без консоли, со своей иконкой.
#
#   powershell -ExecutionPolicy Bypass -File tools\windows\Install-Mind-Shortcut.ps1 [-Python C:\путь\python.exe]
#
# Ищет Python с PyQt6 (указанный, затем py/python из PATH). Если PyQt6 нет нигде — подсказывает команду установки.
param([string]$Python = "")

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$launcher = Join-Path $repo "launch-studio.py"
$icon = Join-Path $PSScriptRoot "mind.ico"

function Test-PyQt([string]$exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    & $exe -c "import PyQt6.QtWebEngineWidgets" 2>$null
    return $LASTEXITCODE -eq 0
}

$candidates = @($Python)
foreach ($name in "python", "py") {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += & $cmd.Source -c "import sys; print(sys.executable)" 2>$null }
}
$py = $candidates | Where-Object { Test-PyQt $_ } | Select-Object -First 1
if (-not $py) {
    Write-Host "Не найден Python с PyQt6. Установите: python -m pip install PyQt6 — и запустите скрипт снова." -ForegroundColor Yellow
    exit 1
}
# pythonw.exe — тот же Python без чёрного окна консоли
$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }

$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Mind.lnk"
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = "`"$launcher`""
$s.WorkingDirectory = $repo
$s.IconLocation = "$icon,0"
$s.Description = "Mind Studio — ваши модели, Claude и Antigravity в одном разговоре"
$s.Save()
Write-Host "Ярлык обновлён: $lnk"
Write-Host "  Python: $pyw"

