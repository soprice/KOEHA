$ErrorActionPreference = 'Stop'
$appSource = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$programsRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$appTarget = [IO.Path]::GetFullPath((Join-Path $programsRoot 'KOEHA'))
if (-not $appTarget.StartsWith($programsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Invalid installation target'
}
if (-not (Test-Path -LiteralPath (Join-Path $appSource 'KOEHA.exe'))) { throw 'KOEHA.exe is missing' }
if (Get-Process -Name 'KOEHA' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $appTarget 'KOEHA.exe') }) {
    throw 'Close the installed KOEHA app before updating it.'
}
if ($appSource -ne $appTarget) {
    $updating = (Test-Path -LiteralPath (Join-Path $appTarget 'KOEHA.exe')) -and
        (Test-Path -LiteralPath (Join-Path $appTarget '_internal') -PathType Container)
    New-Item -ItemType Directory -Path $appTarget -Force | Out-Null
    Get-ChildItem -LiteralPath $appSource | Copy-Item -Destination $appTarget -Recurse -Force
    if ($updating) {
        foreach ($relative in @('_internal\PySide6\Qt6VirtualKeyboard.dll',
                '_internal\PySide6\plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll')) {
            $retired = Join-Path $appTarget $relative
            $current = Get-Item -LiteralPath $retired -Force -ErrorAction SilentlyContinue
            $linked = $false
            while ($null -ne $current) {
                if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    $linked = $true
                    break
                }
                $current = if ($current -is [IO.FileInfo]) { $current.Directory } else { $current.Parent }
            }
            if (-not $linked -and (Test-Path -LiteralPath $retired -PathType Leaf)) {
                Remove-Item -LiteralPath $retired -Force
            }
        }
    }
}
$shell = New-Object -ComObject WScript.Shell
foreach ($shortcutFolder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $shortcut = $shell.CreateShortcut((Join-Path $shortcutFolder 'KOEHA.lnk'))
    $shortcut.TargetPath = Join-Path $appTarget 'KOEHA.exe'
    $shortcut.WorkingDirectory = $appTarget
    $shortcut.IconLocation = (Join-Path $appTarget 'KOEHA.exe') + ',0'
    $shortcut.Save()
}
$uninstallerExe = Join-Path $appTarget 'Uninstall-KOEHA.exe'
if (Test-Path -LiteralPath $uninstallerExe) {
    $installedBytes = (Get-ChildItem -LiteralPath $appSource -File -Recurse -Force | Measure-Object -Property Length -Sum).Sum
    $estimatedSize = [int][Math]::Min([double][int]::MaxValue, [Math]::Ceiling($installedBytes / 1KB))
    $regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\KOEHA'
    New-Item -Path $regPath -Force | Out-Null
    Set-ItemProperty -Path $regPath -Name 'DisplayName' -Value 'KOEHA'
    Set-ItemProperty -Path $regPath -Name 'DisplayVersion' -Value '0.2.4'
    Set-ItemProperty -Path $regPath -Name 'Publisher' -Value 'KOEHA'
    Set-ItemProperty -Path $regPath -Name 'DisplayIcon' -Value (Join-Path $appTarget 'KOEHA.exe')
    Set-ItemProperty -Path $regPath -Name 'InstallLocation' -Value $appTarget
    Set-ItemProperty -Path $regPath -Name 'EstimatedSize' -Value $estimatedSize -Type DWord
    Set-ItemProperty -Path $regPath -Name 'NoModify' -Value 1 -Type DWord
    Set-ItemProperty -Path $regPath -Name 'NoRepair' -Value 1 -Type DWord
    Set-ItemProperty -Path $regPath -Name 'UninstallString' -Value "`"$uninstallerExe`" --uninstall `"--target=$appTarget`""
    Set-ItemProperty -Path $regPath -Name 'QuietUninstallString' -Value "`"$uninstallerExe`" --uninstall --silent `"--target=$appTarget`""
}
Write-Host "Installed KOEHA to $appTarget"
Write-Host 'Your models, profiles and history remain in LocalAppData\KOEHA.'
