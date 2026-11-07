$ErrorActionPreference = 'Stop'
$Root = Join-Path $env:LOCALAPPDATA 'ytgrab'
$Scripts = Join-Path $Root 'venv\Scripts'
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$Parts = @($UserPath -split ';' | Where-Object { $_ -and $_ -ne $Scripts })
[Environment]::SetEnvironmentVariable('Path', ($Parts -join ';'), 'User')
if (Test-Path $Root) {
    $answer = Read-Host "Remove installed program files at $Root? Settings under %APPDATA%\ytgrab will be kept. [y/N]"
    if ($answer -match '^(y|yes)$') { Remove-Item -LiteralPath $Root -Recurse -Force }
}
Write-Host 'Removed ytgrab from the user PATH. Open a new terminal for the change to take effect.'
