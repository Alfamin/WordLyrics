# Builds the Noctis plugin against the Noctis that is installed on this PC and packs the zip that
# Noctis takes in Settings -> Plugins -> "Install from file...".
#
#   powershell -File build.ps1 -Compiler C:\path\to\csc.exe [-Noctis C:\path\to\Noctis]
#
# No .NET SDK is needed: any Roslyn csc.exe will do (for example the one in the NuGet package
# microsoft.net.compilers.toolset), and the reference assemblies are Noctis' own.
# With the .NET SDK and a checkout of the Noctis repo, a normal class-library project that
# references Noctis.Plugins.Abstractions builds the same source files.
param(
    [string]$Compiler = "$env:USERPROFILE\noctis-lyric-motion\tools\roslyn\tasks\net472\csc.exe",
    [string]$Noctis = "$env:LOCALAPPDATA\Programs\Noctis"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$out = Join-Path $root "build"
$zip = Join-Path $root "WordLyrics-for-Noctis.zip"

if (-not (Test-Path $Compiler)) { throw "Compiler not found: $Compiler" }
if (-not (Test-Path (Join-Path $Noctis "Noctis.Plugins.Abstractions.dll"))) { throw "Noctis not found in $Noctis" }

# A plugin built against a newer plugin kit than the one plugin.json names is refused by every
# Noctis that came with the older kit ("Could not load ... Noctis.Plugins.Abstractions,
# Version=1.2.0.0"), so the kit has to be the one that apiVersion names. Noctis updates itself:
# when it has moved on, point -Noctis at a copy of an older release.
$api = (Get-Content (Join-Path $root "src\plugin.json") -Raw | ConvertFrom-Json).apiVersion
$kit = [System.Reflection.AssemblyName]::GetAssemblyName((Join-Path $Noctis "Noctis.Plugins.Abstractions.dll")).Version
if ("$($kit.Major).$($kit.Minor)" -ne $api) {
    throw "The Noctis in $Noctis has plugin kit $($kit.Major).$($kit.Minor), but plugin.json says apiVersion $api. Use -Noctis with a Noctis release that has kit $api."
}

# Everything the app supplies at run time: the .NET libraries, Avalonia and the plugin kit.
$refs = Get-ChildItem $Noctis -Filter *.dll | Where-Object {
    ($_.Name -like "System.*" -and $_.Name -notlike "*Native*") -or
    $_.Name -in "netstandard.dll", "mscorlib.dll", "Microsoft.Win32.Primitives.dll", "Avalonia.Base.dll", "Avalonia.Controls.dll", "Noctis.Plugins.Abstractions.dll"
} | ForEach-Object { "-r:`"$($_.FullName)`"" }

New-Item -ItemType Directory -Force $out | Out-Null
$rsp = Join-Path $out "build.rsp"
@(
    "-nologo", "-nostdlib+", "-noconfig", "-target:library", "-optimize+", "-deterministic+",
    "-langversion:latest", "-nullable:enable", "-debug-", "-warnaserror-",
    "-out:`"$out\WordLyricsForNoctis.dll`""
) + $refs + (Get-ChildItem (Join-Path $root "src") -Filter *.cs | ForEach-Object { "`"$($_.FullName)`"" }) |
    Set-Content -Encoding utf8 $rsp

& $Compiler "@$rsp"
if ($LASTEXITCODE -ne 0) { throw "Compilation failed." }

Copy-Item (Join-Path $root "src\plugin.json") $out -Force
Compress-Archive -Path (Join-Path $out "WordLyricsForNoctis.dll"), (Join-Path $out "plugin.json") -DestinationPath $zip -Force
"Built $zip"
