param([switch]$Shortcut)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
$icon = Join-Path $project 'logo\logo.ico'
$output = Join-Path $project 'Dashboard Bot.exe'

& $compiler /nologo /target:winexe /optimize+ /reference:System.Windows.Forms.dll "/win32icon:$icon" "/out:$output" (Join-Path $PSScriptRoot 'DashboardLauncher.cs')
if ($LASTEXITCODE -ne 0) {
    throw "Build Dashboard gagal dengan kode $LASTEXITCODE."
}
if ($Shortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $project 'Dashboard Bot.lnk'))
    $link.TargetPath = $output
    $link.WorkingDirectory = $project
    $link.IconLocation = "$output,0"
    $link.Save()
}
Write-Output $output
