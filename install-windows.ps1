$ErrorActionPreference = 'Stop'
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path (Join-Path $Project 'pyproject.toml'))) {
    throw 'Run this script from the extracted ytgrab project folder.'
}
$Python = Get-Command py -ErrorAction SilentlyContinue
if ($Python) {
    & py -3 --version
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.9+ is required. Install it from python.org and retry.' }
    $PythonCommand = 'py'
    $PythonArgs = @('-3')
} else {
    $Python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $Python) { throw 'Python 3.9+ is required. Install it from python.org and retry.' }
    $PythonCommand = $Python.Source
    $PythonArgs = @()
}
$Root = Join-Path $env:LOCALAPPDATA 'ytgrab'
$Venv = Join-Path $Root 'venv'
New-Item -ItemType Directory -Force -Path $Root | Out-Null
if (-not (Test-Path $Venv)) { & $PythonCommand @PythonArgs -m venv $Venv }
$Vpy = Join-Path $Venv 'Scripts\python.exe'
& $Vpy -m pip install --upgrade pip
& $Vpy -m pip install "${Project}[tui]"
if ($LASTEXITCODE -ne 0) { throw 'ytgrab installation failed.' }
$Scripts = Join-Path $Venv 'Scripts'
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$PathParts = @($UserPath -split ';' | Where-Object { $_ })
if ($PathParts -notcontains $Scripts) {
    [Environment]::SetEnvironmentVariable('Path', ((@($PathParts) + @($Scripts)) -join ';'), 'User')
}
Write-Host "Installed ytgrab in $Venv"
Write-Host "Open a new PowerShell/Windows Terminal window, then run: ytgrab --version"
Write-Host 'Optional media tools: install FFmpeg and a JavaScript runtime (Deno or Node.js) and ensure they are on PATH.'
Write-Host 'VLC streaming requires desktop VLC; the installer also checks common VLC locations.'
