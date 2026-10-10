# Run the actual installer with fake downloads and isolated destinations. Never launches WordLyrics.
param([string]$Installer = (Join-Path (Split-Path $PSScriptRoot) 'install.ps1'), [string]$TrueShufflePackage = '')
$ErrorActionPreference = 'Stop'
$script:passed = 0
$script:testRoot = Join-Path ([IO.Path]::GetTempPath()) ('wordlyrics-installer-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $script:testRoot | Out-Null

function Assert-True($condition, $name) {
    if (-not $condition) { throw "FAILED: $name" }
    $script:passed++
}
function New-Package($name, $id, $version, [switch]$MissingEntry) {
    $folder = Join-Path $script:testRoot ($name + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $folder | Out-Null
    @{ id = $id; version = $version; type = 'dotnet'; entry = 'Fixture.dll' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $folder 'plugin.json') -Encoding utf8
    if (-not $MissingEntry) { 'fixture plugin ' + $version | Set-Content -LiteralPath (Join-Path $folder 'Fixture.dll') -Encoding utf8 }
    'fixture dependency' | Set-Content -LiteralPath (Join-Path $folder 'FixtureDependency.dll') -Encoding ascii
    $zip = $folder + '.zip'
    Compress-Archive -Path (Join-Path $folder '*') -DestinationPath $zip
    return $zip
}
$script:wordPlugin = New-Package 'word' 'dev.moshi.wordlyrics' '1.0.1'
$script:motion = New-Package 'motion' 'dev.moshi.lyricmotion' '1.4.1'
$script:finder = New-Package 'finder' 'dev.moshi.freemusicfinder' '2.3.0'
$script:shuffle = New-Package 'shuffle' 'dev.moshi.trueshuffle' '1.1.0'
$script:wrong = New-Package 'wrong' 'dev.example.wrong' '1.1.0'
$script:missing = New-Package 'missing' 'dev.moshi.trueshuffle' '1.1.0' -MissingEntry
$source = Join-Path $script:testRoot 'source\WordLyrics-main'
New-Item -ItemType Directory -Path (Join-Path $source 'wordlyrics'),(Join-Path $source 'noctis-plugin') | Out-Null
'@echo off' | Set-Content -LiteralPath (Join-Path $source 'WordLyrics.bat') -Encoding ascii
'fixture app' | Set-Content -LiteralPath (Join-Path $source 'wordlyrics\__main__.py') -Encoding utf8
'__version__ = "1.6.1"' | Set-Content -LiteralPath (Join-Path $source 'wordlyrics\__init__.py') -Encoding ascii
'fixture requirements' | Set-Content -LiteralPath (Join-Path $source 'requirements.txt') -Encoding ascii
foreach ($name in @('runner','menu','repair','drafts','search')) { 'fixture module' | Set-Content -LiteralPath (Join-Path $source ('wordlyrics\'+$name+'.py')) -Encoding ascii }
'{}' | Set-Content -LiteralPath (Join-Path $source 'lyrics-providers.example.json') -Encoding ascii
'fixture links' | Set-Content -LiteralPath (Join-Path $source 'extra-links.txt') -Encoding ascii
Copy-Item -LiteralPath $script:wordPlugin -Destination (Join-Path $source 'noctis-plugin\WordLyrics-for-Noctis.zip')
$script:appPackage = Join-Path $script:testRoot 'app.zip'
Compress-Archive -LiteralPath $source -DestinationPath $script:appPackage
$global:WordLyricsInstallerFixtureState = [pscustomobject]@{
    AppPackage=$script:appPackage; Motion=$script:motion; Finder=$script:finder; Shuffle=$script:shuffle
    Wrong=$script:wrong; Missing=$script:missing; RealPackage=$TrueShufflePackage; Case=''; Urls=$null
}

function Invoke-WebRequest {
    param($Uri, $OutFile, $TimeoutSec, [switch]$UseBasicParsing)
    $fixtureState = $global:WordLyricsInstallerFixtureState
    $fixtureState.Urls.Add([string]$Uri)
    if ([string]$Uri -like '*/plugin.json') {
        if ($fixtureState.Case -in @('download-failure','reuse-offline-check')) { throw [TimeoutException]::new('private proxy details') }
        $id,$version = switch -Regex ([string]$Uri) { '/TrueShuffle/' { 'dev.moshi.trueshuffle';'1.1.0';break } '/LyricMotion/' { 'dev.moshi.lyricmotion';'1.4.1';break } '/noctic-download-plugin/' { 'dev.moshi.freemusicfinder';'2.3.0';break } }
        @{ id=$id;version=$version } | ConvertTo-Json | Set-Content -LiteralPath $OutFile -Encoding utf8
        return
    }
    $package = switch -Regex ([string]$Uri) {
        'fixture.invalid/wordlyrics' { $fixtureState.AppPackage; break }
        'Alfamin/WordLyrics/archive/' { $fixtureState.AppPackage; break }
        'Alfamin/LyricMotion/' { $fixtureState.Motion; break }
        'Alfamin/noctic-download-plugin/' { $fixtureState.Finder; break }
        'Alfamin/TrueShuffle/' {
            if ($fixtureState.Case -eq 'real-package') { $fixtureState.RealPackage; break }
            if ($fixtureState.Case -eq 'download-failure') { throw 'simulated unavailable source' }
            if ($fixtureState.Case -eq 'fallback' -and ([string]$Uri).StartsWith('https://github.com/')) { throw 'simulated primary source failure' }
            if ($fixtureState.Case -eq 'wrong-package') { $fixtureState.Wrong; break }
            if ($fixtureState.Case -eq 'missing-entry') { $fixtureState.Missing; break }
            $fixtureState.Shuffle; break
        }
        default { throw "Unexpected network call: $Uri" }
    }
    Copy-Item -LiteralPath $package -Destination $OutFile
}
function Start-Process { throw 'Unexpected process launch in installer tests; no process was started' }

$names = @('WORDLYRICS_SOURCE','WORDLYRICS_DIR','WORDLYRICS_NO_START','WORDLYRICS_SHORTCUT_DIR','NOCTIS_DATA_DIR','WORDLYRICS_NO_EXTRAS','WORDLYRICS_NO_NOCTIS','WORDLYRICS_PACKAGE','WORDLYRICS_FINDER_PACKAGE','WORDLYRICS_EXTRAS_DIR','WORDLYRICS_TELEGRAM_DEFAULTS_FILE','WORDLYRICS_OFFLINE_INSTALL','WORDLYRICS_REPAIR_INSTALL')
$saved = @{}; foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:WORDLYRICS_PACKAGE=$null; $env:WORDLYRICS_FINDER_PACKAGE=$null; $env:WORDLYRICS_EXTRAS_DIR=$null; $env:WORDLYRICS_TELEGRAM_DEFAULTS_FILE=$null
    $env:WORDLYRICS_OFFLINE_INSTALL=$null; $env:WORDLYRICS_REPAIR_INSTALL=$null
    $scenarios = @('fresh','same','upgrade','newer','download-failure','wrong-package','missing-entry','no-extras','no-noctis','fallback','reuse-online','reuse-offline','reuse-offline-check','repair-missing-core','repair-missing-plugin','repair-missing-dependency','force-repair')
    if ($TrueShufflePackage) { $scenarios += 'real-package' }
    foreach ($scenario in $scenarios) {
        $script:case = $scenario; $script:urls = [Collections.Generic.List[string]]::new()
        $global:WordLyricsInstallerFixtureState.Case = $scenario; $global:WordLyricsInstallerFixtureState.Urls = $script:urls
        $folder = Join-Path $script:testRoot $scenario
        $data = Join-Path $folder 'Noctis'; $dest = Join-Path $folder 'WordLyrics'; $desk = Join-Path $folder 'Desktop'
        New-Item -ItemType Directory -Path $data,$desk,$dest,(Join-Path $dest 'models'),(Join-Path $dest 'runs') | Out-Null
        $shuffleHome = Join-Path $data 'plugins\dev.moshi.trueshuffle'
        $settings = Join-Path $data 'settings.json'; $progress = Join-Path $data 'plugin-data\dev.moshi.trueshuffle\cycle.json'
        New-Item -ItemType Directory -Path (Split-Path $progress) | Out-Null
        '{"communityPluginsEnabled":false,"keep":"original settings"}' | Set-Content -LiteralPath $settings -Encoding utf8
        '{"Version":1,"Seen":[],"Last":null}' | Set-Content -LiteralPath $progress -Encoding utf8
        'keep model' | Set-Content -LiteralPath (Join-Path $dest 'models\model.bin') -Encoding ascii
        'keep report' | Set-Content -LiteralPath (Join-Path $dest 'runs\report.txt') -Encoding ascii
        '{"genius":false}' | Set-Content -LiteralPath (Join-Path $dest 'lyrics-providers.json') -Encoding ascii
        'my own links' | Set-Content -LiteralPath (Join-Path $dest 'extra-links.txt') -Encoding ascii
        $preserve = @($settings,$progress,(Join-Path $dest 'models\model.bin'),(Join-Path $dest 'runs\report.txt'),(Join-Path $dest 'lyrics-providers.json'),(Join-Path $dest 'extra-links.txt'))
        $hashes = @{}; foreach ($p in $preserve) { $hashes[$p] = (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash }
        $oldVersion = switch ($scenario) { 'same' { '1.1.0' } 'newer' { '2.0.0' } { $_ -in @('upgrade','wrong-package','missing-entry','download-failure') } { '1.0.0' } default { $null } }
        if ($oldVersion) {
            New-Item -ItemType Directory -Path $shuffleHome | Out-Null
            @{ id = 'dev.moshi.trueshuffle'; version = $oldVersion; entry = 'Fixture.dll' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $shuffleHome 'plugin.json') -Encoding utf8
            'original plugin' | Set-Content -LiteralPath (Join-Path $shuffleHome 'Fixture.dll') -Encoding ascii
        }
        $env:WORDLYRICS_SOURCE = 'https://fixture.invalid/wordlyrics.zip'; $env:WORDLYRICS_DIR = $dest
        $env:WORDLYRICS_NO_START = '1'; $env:WORDLYRICS_SHORTCUT_DIR = $desk; $env:NOCTIS_DATA_DIR = $data
        $env:WORDLYRICS_NO_EXTRAS = if ($scenario -eq 'no-extras') { '1' } else { $null }
        $env:WORDLYRICS_NO_NOCTIS = if ($scenario -eq 'no-noctis') { '1' } else { $null }
        $output = (& $Installer 6>&1 | Out-String)
        if ($scenario -like 'reuse-*' -or $scenario -like 'repair-missing-*' -or $scenario -eq 'force-repair') {
            Assert-True (Test-Path -LiteralPath (Join-Path $dest 'installation.json')) "$scenario`: successful install saves local integrity record"
            $env:WORDLYRICS_SOURCE=$null
            $script:urls.Clear()
            if ($scenario -eq 'reuse-offline') { $env:WORDLYRICS_OFFLINE_INSTALL='1';$env:WORDLYRICS_NO_START=$null }
            if ($scenario -eq 'force-repair') { $env:WORDLYRICS_REPAIR_INSTALL='1' }
            if ($scenario -eq 'repair-missing-core') { Remove-Item -LiteralPath (Join-Path $dest 'wordlyrics\menu.py') }
            if ($scenario -eq 'repair-missing-plugin') { Remove-Item -LiteralPath (Join-Path $shuffleHome 'Fixture.dll') }
            if ($scenario -eq 'repair-missing-dependency') { Remove-Item -LiteralPath (Join-Path $shuffleHome 'FixtureDependency.dll') }
            $output = (& $Installer 6>&1 | Out-String)
            if ($scenario -eq 'reuse-offline') { Assert-True ($script:urls.Count -eq 0) 'offline reinstall makes no network requests';Assert-True ($output.Contains('OFFLINE_SETUP')) 'offline setup never triggers Python/model startup downloads' }
            if ($scenario -in @('reuse-online','reuse-offline-check')) { Assert-True ($script:urls.Count -eq 3 -and -not ($script:urls | Where-Object {$_ -notlike '*/plugin.json'})) "$scenario`: installed program/all plugins reuse without ZIP downloads" }
            if ($scenario -eq 'reuse-offline-check') { Assert-True ($output.Contains('Update check unavailable') -and -not $output.Contains('private proxy details')) 'unreachable update checks keep working plugins and explain safely' }
            if ($scenario -eq 'repair-missing-core') { Assert-True (Test-Path -LiteralPath (Join-Path $dest 'wordlyrics\menu.py')) 'missing program file is restored'; Assert-True ([bool]($script:urls | Where-Object {$_ -like '*/archive/*'})) 'incomplete core downloads its program again' }
            if ($scenario -eq 'repair-missing-plugin') { Assert-True (Test-Path -LiteralPath (Join-Path $shuffleHome 'Fixture.dll')) 'missing plugin entry repaired at same version'; Assert-True ([bool]($script:urls | Where-Object {$_ -like '*TrueShuffle*.zip'})) 'incomplete plugin downloads its package' }
            if ($scenario -eq 'repair-missing-dependency') { Assert-True (Test-Path -LiteralPath (Join-Path $shuffleHome 'FixtureDependency.dll')) 'missing plugin dependency repaired at same version'; Assert-True ([bool]($script:urls | Where-Object {$_ -like '*TrueShuffle*.zip'})) 'missing dependency downloads its package despite an intact entry DLL' }
            if ($scenario -eq 'force-repair') { Assert-True ($script:urls.Count -eq 4) 'explicit repair refetches core and three plugin packages' }
            $env:WORDLYRICS_OFFLINE_INSTALL=$null; $env:WORDLYRICS_REPAIR_INSTALL=$null
        }
        if (-not (Test-Path -LiteralPath (Join-Path $dest 'wordlyrics\__main__.py'))) { Write-Host $output }
        Assert-True (Test-Path -LiteralPath (Join-Path $dest 'wordlyrics\__main__.py')) "${scenario}: program installed"
        Assert-True (Test-Path -LiteralPath (Join-Path $dest 'lyrics-providers.example.json')) "${scenario}: provider template installed"
        foreach ($p in $preserve) { Assert-True ((Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash -eq $hashes[$p]) "${scenario}: existing state preserved" }
        if ($scenario -eq 'real-package') {
            $manifest = Get-Content -LiteralPath (Join-Path $shuffleHome 'plugin.json') -Raw | ConvertFrom-Json
            Assert-True ($manifest.version -eq '1.1.0' -and $manifest.entry -eq 'TrueShuffle.dll') 'actual package manifest installed'
            $compiled = Join-Path (Split-Path $TrueShufflePackage) 'TrueShuffle.dll'
            Assert-True ((Get-FileHash -LiteralPath (Join-Path $shuffleHome 'TrueShuffle.dll')).Hash -eq (Get-FileHash -LiteralPath $compiled).Hash) 'actual compiled DLL installed byte-identically'
        } elseif ($scenario -in @('fresh','upgrade','fallback','reuse-online','reuse-offline','reuse-offline-check','repair-missing-core','repair-missing-plugin','repair-missing-dependency','force-repair')) {
            $manifest = Get-Content -LiteralPath (Join-Path $shuffleHome 'plugin.json') -Raw | ConvertFrom-Json
            Assert-True ($manifest.version -eq '1.1.0') "${scenario}: correct shuffle version"
            Assert-True ((Get-Content -LiteralPath (Join-Path $shuffleHome 'Fixture.dll') -Raw).Contains('fixture plugin 1.1.0')) "${scenario}: new DLL installed"
            Assert-True (Test-Path -LiteralPath (Join-Path $data 'plugins\dev.moshi.freemusicfinder\plugin.json')) "${scenario}: other extras still installed"
        } elseif ($oldVersion) {
            Assert-True ((Get-Content -LiteralPath (Join-Path $shuffleHome 'Fixture.dll') -Raw).Trim() -eq 'original plugin') "${scenario}: old/newer plugin preserved"
        } else {
            Assert-True (-not (Test-Path -LiteralPath $shuffleHome)) "${scenario}: shuffle skipped"
        }
        if ($scenario -in @('no-extras','no-noctis')) { Assert-True (-not ($script:urls | Where-Object { $_ -match 'TrueShuffle|LyricMotion|noctic-download-plugin' })) "${scenario}: optional sources never contacted" }
        if ($scenario -eq 'fallback') { Assert-True ($script:urls.Contains('https://raw.githubusercontent.com/Alfamin/TrueShuffle/main/dist/TrueShuffle-for-Noctis.zip')) 'raw fallback requested' }
    }
    "PASS: $script:passed installer assertions across $($scenarios.Count) scenarios; no network, real Noctis or model launch."
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process') }
    Remove-Variable -Name WordLyricsInstallerFixtureState -Scope Global
    # Leave the isolated test fixtures available for inspection; never delete user data.
}
