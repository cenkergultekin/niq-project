param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'Programs\NIQ'),
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$niqFiles = @('NIQ-Desktop.exe', 'NIQ-MCP.exe')
foreach ($niqFile in $niqFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "dist\$niqFile") -PathType Leaf)) {
        throw "dist\$niqFile bulunamadi. Once masaustu paketlerini derleyin."
    }
}

$niqInstallPath = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $niqInstallPath -Force | Out-Null
foreach ($niqFile in $niqFiles) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "dist\$niqFile") -Destination (Join-Path $niqInstallPath $niqFile) -Force
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'LICENSE.txt') -Destination $niqInstallPath -Force

$niqExecutable = Join-Path $niqInstallPath 'NIQ-Desktop.exe'
$niqShortcutShell = New-Object -ComObject WScript.Shell
$niqShortcutFolders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($niqShortcutFolder in $niqShortcutFolders) {
    $niqShortcut = $niqShortcutShell.CreateShortcut((Join-Path $niqShortcutFolder 'NIQ.lnk'))
    $niqShortcut.TargetPath = $niqExecutable
    $niqShortcut.WorkingDirectory = $niqInstallPath
    $niqShortcut.IconLocation = "$niqExecutable,0"
    $niqShortcut.Description = 'NIQ'
    $niqShortcut.Save()
}

Write-Output "NIQ kuruldu: $niqExecutable"
if ($Launch) {
    Start-Process -FilePath $niqExecutable -WorkingDirectory $niqInstallPath
}
