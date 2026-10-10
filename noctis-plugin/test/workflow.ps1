param(
    [string]$Noctis = "$env:LOCALAPPDATA\Programs\Noctis",
    [string]$Compiler = "$env:USERPROFILE\noctis-lyric-motion\tools\roslyn\tasks\net472\csc.exe",
    [string]$Dotnet = "$env:USERPROFILE\dotnet-sdk\dotnet.exe",
    [string]$Python = "$env:USERPROFILE\lyrics-align\venv-dml\Scripts\python.exe"
)
$ErrorActionPreference = 'Stop'
$test = Join-Path (Split-Path $PSScriptRoot) 'build\workflow'
New-Item -ItemType Directory -Force -Path $test | Out-Null
$refs = Get-ChildItem -LiteralPath $Noctis -Filter '*.dll' | Where-Object {
    ($_.Name -like 'System.*' -and $_.Name -notlike '*Native*') -or $_.Name -in @('netstandard.dll','mscorlib.dll','Avalonia.dll','Avalonia.Base.dll','Avalonia.Controls.dll','Avalonia.Themes.Fluent.dll','Avalonia.Skia.dll','Avalonia.HarfBuzz.dll','Noctis.Plugins.Abstractions.dll')
} | ForEach-Object { '-r:"' + $_.FullName + '"' }
$headless = Join-Path $env:USERPROFILE '.nuget\packages\avalonia.headless\12.1.3\lib\net10.0\Avalonia.Headless.dll'
if (-not (Test-Path -LiteralPath $headless)) { throw 'Cached Avalonia.Headless required. No downloads performed.' }
$sources = Get-ChildItem -LiteralPath (Join-Path (Split-Path $PSScriptRoot) 'src') -Filter '*.cs' | ForEach-Object { '"' + $_.FullName + '"' }
$rsp = Join-Path $test 'test.rsp'
@('-nologo','-nostdlib+','-target:exe','-langversion:latest','-nullable:enable','-warnaserror+',('-out:"' + (Join-Path $test 'WordLyricsTests.dll') + '"'),('-r:"' + $headless + '"')) + $refs + $sources + ('"' + (Join-Path $PSScriptRoot 'WorkflowTests.cs') + '"') | Set-Content -LiteralPath $rsp -Encoding utf8
& $Compiler -noconfig "@$rsp"
if ($LASTEXITCODE -ne 0) { throw 'Workflow test compilation failed.' }
Copy-Item -LiteralPath $headless -Destination $test -Force
Get-ChildItem -LiteralPath $Noctis -Filter 'Avalonia*.dll' | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $test -Force }
foreach ($name in @('Noctis.Plugins.Abstractions.dll','Avalonia.dll','Avalonia.Base.dll','Avalonia.Controls.dll','Avalonia.Dialogs.dll','Avalonia.Markup.dll','Avalonia.Markup.Xaml.dll','Avalonia.Themes.Fluent.dll','Avalonia.Skia.dll','Avalonia.HarfBuzz.dll','SkiaSharp.dll','SkiaSharp.Resources.dll','SkiaSharp.SceneGraph.dll','SkiaSharp.Skottie.dll','libSkiaSharp.dll','HarfBuzzSharp.dll','libHarfBuzzSharp.dll','MicroCom.Runtime.dll')) {
    $path = Join-Path $Noctis $name
    if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination $test -Force }
}
'{"runtimeOptions":{"tfm":"net10.0","framework":{"name":"Microsoft.NETCore.App","version":"10.0.0"},"rollForward":"LatestMinor"}}' | Set-Content -LiteralPath (Join-Path $test 'WordLyricsTests.runtimeconfig.json') -Encoding utf8
& $Dotnet (Join-Path $test 'WordLyricsTests.dll') $Python $test
if ($LASTEXITCODE -ne 0) { throw 'Workflow tests failed.' }
