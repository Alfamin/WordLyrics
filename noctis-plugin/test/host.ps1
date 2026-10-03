# Builds the test host into a copy of Noctis (never the installed one) and runs one scenario.
#   powershell -File host.ps1 -Scenario new-songs -Songs "a.mp3|b.flac|c.mp3|d.mp3"
param(
    [string]$Scenario = "new-songs",
    [string]$Songs = "",
    [string]$App = "$env:USERPROFILE\noctis-lyric-motion\test\app",
    [string]$Compiler = "$env:USERPROFILE\noctis-lyric-motion\tools\roslyn\tasks\net472\csc.exe",
    [string]$Work = "$env:USERPROFILE\lyrics-align\test\noctis",
    [string]$Tool = "",
    [int]$Timeout = 600
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$plugin = Join-Path (Split-Path $here) "build\WordLyricsForNoctis.dll"

if (-not (Test-Path "$App\WlHost.exe") -or (Get-Item "$here\WlHost.cs").LastWriteTime -gt (Get-Item "$App\WlHost.dll" -ErrorAction SilentlyContinue).LastWriteTime) {
    $refs = Get-ChildItem $App -Filter *.dll | Where-Object {
        ($_.Name -like "System.*" -and $_.Name -notlike "*Native*") -or
        $_.Name -in "netstandard.dll", "mscorlib.dll", "Avalonia.Base.dll", "Avalonia.Controls.dll", "Noctis.Plugins.Abstractions.dll"
    } | ForEach-Object { "-r:`"$($_.FullName)`"" }
    $rsp = Join-Path $Work "host.rsp"
    New-Item -ItemType Directory -Force $Work | Out-Null
    @("-nologo", "-nostdlib+", "-target:exe", "-optimize+", "-langversion:latest", "-nullable:enable",
      "-out:`"$App\WlHost.dll`"") + $refs + "`"$here\WlHost.cs`"" | Set-Content -Encoding utf8 $rsp
    & $Compiler -noconfig "@$rsp"
    if ($LASTEXITCODE -ne 0) { throw "host compilation failed" }
    # An app host is the stock launcher with the name of its .dll written into it.
    $bytes = [System.IO.File]::ReadAllBytes("$App\Noctis.exe")
    $old = [System.Text.Encoding]::UTF8.GetBytes("Noctis.dll")
    $new = [System.Text.Encoding]::UTF8.GetBytes("WlHost.dll")
    $hits = 0
    for ($i = 0; $i -le $bytes.Length - $old.Length; $i++) {
        $match = $true
        for ($j = 0; $j -lt $old.Length; $j++) { if ($bytes[$i + $j] -ne $old[$j]) { $match = $false; break } }
        if ($match -and $bytes[$i + $old.Length] -eq 0) {
            for ($j = 0; $j -lt $new.Length; $j++) { $bytes[$i + $j] = $new[$j] }
            $hits++
        }
    }
    if ($hits -lt 1) { throw "app host name not found" }
    [System.IO.File]::WriteAllBytes("$App\WlHost.exe", $bytes)
    Copy-Item "$App\Noctis.runtimeconfig.json" "$App\WlHost.runtimeconfig.json" -Force
    Copy-Item "$App\Noctis.deps.json" "$App\WlHost.deps.json" -Force
    "host built"
}

New-Item -ItemType Directory -Force $Work | Out-Null
$env:WL_OUT = Join-Path $Work "$Scenario.log"
$env:WL_DATA = Join-Path $Work "data"
$env:WL_MUSIC = Join-Path $Work "music"
$env:WL_SCENARIO = $Scenario
$env:WL_SONGS = $Songs
$env:WL_PLUGIN = $plugin
$env:WL_TOOL = $Tool
$env:WL_BACKUP = Join-Path $Work "backups"
if (Test-Path $env:WL_OUT) { Remove-Item $env:WL_OUT -Confirm:$false }
$p = Start-Process -FilePath "$App\WlHost.exe" -WorkingDirectory $App -PassThru
if (-not $p.WaitForExit($Timeout * 1000)) { $p.Kill(); "KILLED after timeout" } else { "exit code $($p.ExitCode)" }
if (Test-Path $env:WL_OUT) { Get-Content $env:WL_OUT -Encoding UTF8 } else { "no log" }
