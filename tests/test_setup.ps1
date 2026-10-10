# Tests the real one-line installer with intercepted downloads and process launches.
# Never downloads/executes Noctis, opens a window, or reads real Telegram credentials.
param([string]$Installer = (Join-Path (Split-Path $PSScriptRoot) 'install.ps1'))
$ErrorActionPreference = 'Stop'
$fixtureRoot = Join-Path ([IO.Path]::GetTempPath()) ('wordlyrics-setup-tests-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixtureRoot | Out-Null
$source = Join-Path $fixtureRoot 'source\WordLyrics-main'
New-Item -ItemType Directory -Path (Join-Path $source 'wordlyrics'),(Join-Path $source 'noctis-plugin') | Out-Null
'@echo off' | Set-Content -LiteralPath (Join-Path $source 'WordLyrics.bat') -Encoding ascii
'fixture' | Set-Content -LiteralPath (Join-Path $source 'wordlyrics\__main__.py') -Encoding ascii
'__version__ = "1.5.0"' | Set-Content -LiteralPath (Join-Path $source 'wordlyrics\__init__.py') -Encoding ascii
$packages = @{}
foreach ($id in @('dev.moshi.wordlyrics','dev.moshi.lyricmotion','dev.moshi.freemusicfinder','dev.moshi.trueshuffle')) {
    $folder = Join-Path $fixtureRoot $id
    New-Item -ItemType Directory -Path $folder | Out-Null
    @{ id=$id; version='9.0.0'; entry='Fixture.dll' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $folder 'plugin.json') -Encoding ascii
    'fixture' | Set-Content -LiteralPath (Join-Path $folder 'Fixture.dll') -Encoding ascii
    $zip = $folder + '.zip'; Compress-Archive -Path (Join-Path $folder '*') -DestinationPath $zip
    $packages[$id]=$zip
}
Copy-Item -LiteralPath $packages['dev.moshi.wordlyrics'] -Destination (Join-Path $source 'noctis-plugin\WordLyrics-for-Noctis.zip')
$appZip = Join-Path $fixtureRoot 'app.zip'; Compress-Archive -LiteralPath $source -DestinationPath $appZip
$global:WordLyricsSetupFixture = @{ Case=''; Root=''; Answers=$null; Starts=0; HashChecks=0; Requests=$null; App=$appZip; Packages=$packages }

function Invoke-WebRequest {
    param($Uri,$OutFile,$TimeoutSec,[switch]$UseBasicParsing)
    $state=$global:WordLyricsSetupFixture; $state.Requests.Add([string]$Uri)
    if ($Uri -match 'Noctis-v1.5.9-Setup.exe$') {
        if ($state.Case -eq 'network-failure') { throw [TimeoutException]::new('secret proxy details') }
        'never executable' | Set-Content -LiteralPath $OutFile -Encoding ascii
    } elseif ($Uri -match 'fixture.invalid') { Copy-Item -LiteralPath $state.App -Destination $OutFile }
    elseif ($Uri -match '/LyricMotion/') { Copy-Item -LiteralPath $state.Packages['dev.moshi.lyricmotion'] -Destination $OutFile }
    elseif ($Uri -match '/noctic-download-plugin/') { Copy-Item -LiteralPath $state.Packages['dev.moshi.freemusicfinder'] -Destination $OutFile }
    elseif ($Uri -match '/TrueShuffle/') { Copy-Item -LiteralPath $state.Packages['dev.moshi.trueshuffle'] -Destination $OutFile }
    else { throw 'Unexpected network call' }
}
function Get-Item {
    param($LiteralPath,[switch]$Force,$ErrorAction)
    if ([IO.Path]::GetFileName($LiteralPath) -eq 'Noctis-Setup.exe') {
        return [pscustomobject]@{ Length=$(if($global:WordLyricsSetupFixture.Case -eq 'bad-size'){1}else{152405346}) }
    }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -Force:$Force -ErrorAction SilentlyContinue
}
function Get-FileHash {
    param($LiteralPath,$Algorithm)
    if ([IO.Path]::GetFileName($LiteralPath) -eq 'Noctis-Setup.exe') {
        $global:WordLyricsSetupFixture.HashChecks++
        return [pscustomobject]@{ Hash=$(if($global:WordLyricsSetupFixture.Case -eq 'bad-hash'){'bad'}else{'e40f38246a05b40624769ee8e538bcc419cd52be08ebd334b776c15bf9aebcdf'}) }
    }
    Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm
}
function Start-Process {
    param($FilePath,$ArgumentList,[switch]$PassThru,[switch]$Wait,$WindowStyle)
    $state=$global:WordLyricsSetupFixture; $state.Starts++
    if ([IO.Path]::GetFileName($FilePath) -ne 'Noctis-Setup.exe' -or $WindowStyle -ne 'Hidden' -or -not $Wait -or '/VERYSILENT' -notin $ArgumentList) { throw 'Unsafe/unexpected process request' }
    if ($state.Case -eq 'install-failure') { return [pscustomobject]@{ExitCode=7} }
    $mockNoctisHome=Join-Path $env:LOCALAPPDATA 'Programs\Noctis'; New-Item -ItemType Directory -Force -Path $mockNoctisHome | Out-Null
    'not executable' | Set-Content -LiteralPath (Join-Path $mockNoctisHome 'Noctis.exe') -Encoding ascii
    return [pscustomobject]@{ExitCode=0}
}
function Read-Host {
    param($Prompt)
    $state=$global:WordLyricsSetupFixture
    if ($state.Answers.Count -eq 0) { throw 'Unexpected input prompt' }
    return $state.Answers.Dequeue()
}
$names=@('WORDLYRICS_SOURCE','WORDLYRICS_DIR','WORDLYRICS_NO_START','WORDLYRICS_SHORTCUT_DIR','NOCTIS_DATA_DIR','WORDLYRICS_NO_NOCTIS','WORDLYRICS_NO_EXTRAS','WORDLYRICS_NONINTERACTIVE','WORDLYRICS_INSTALL_NOCTIS','WORDLYRICS_NOCTIS_EXE','WORDLYRICS_TELEGRAM_DEFAULTS_FILE','WORDLYRICS_PACKAGE','WORDLYRICS_FINDER_PACKAGE','WORDLYRICS_EXTRAS_DIR','LOCALAPPDATA','APPDATA','ProgramFiles')
$saved=@{}; foreach($name in $names){$saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$passed=0
function Assert-Setup($condition,$message) { if(-not $condition){throw $message}; $script:passed++ }
try {
    foreach($case in @('decline','install','bad-size','bad-hash','network-failure','install-failure','noninteractive','portable','private-defaults','private-kept','private-invalid','local-bundle','older-noctis','newer-program')) {
        $state=$global:WordLyricsSetupFixture; $state.Case=$case; $state.Starts=0; $state.HashChecks=0
        $state.Requests=[Collections.Generic.List[string]]::new(); $state.Answers=[Collections.Generic.Queue[string]]::new()
        $root=Join-Path $fixtureRoot $case; $state.Root=$root
        $env:LOCALAPPDATA=Join-Path $root 'local'; $env:APPDATA=Join-Path $root 'roaming'; $env:ProgramFiles=Join-Path $root 'programs'
        $env:WORDLYRICS_DIR=Join-Path $root 'app'; $env:WORDLYRICS_SOURCE='https://fixture.invalid/app.zip'; $env:WORDLYRICS_NO_START='1'; $env:WORDLYRICS_SHORTCUT_DIR=Join-Path $root 'desktop'
        New-Item -ItemType Directory -Force -Path $env:WORDLYRICS_SHORTCUT_DIR | Out-Null
        foreach($name in @('NOCTIS_DATA_DIR','WORDLYRICS_NO_NOCTIS','WORDLYRICS_NO_EXTRAS','WORDLYRICS_NONINTERACTIVE','WORDLYRICS_INSTALL_NOCTIS','WORDLYRICS_NOCTIS_EXE','WORDLYRICS_TELEGRAM_DEFAULTS_FILE','WORDLYRICS_PACKAGE','WORDLYRICS_FINDER_PACKAGE','WORDLYRICS_EXTRAS_DIR')){[Environment]::SetEnvironmentVariable($name,$null,'Process')}
        if($case -eq 'noninteractive'){$env:WORDLYRICS_NONINTERACTIVE='1'}
        elseif($case -eq 'newer-program') {
            New-Item -ItemType Directory -Force -Path (Join-Path $env:WORDLYRICS_DIR 'wordlyrics') | Out-Null
            '__version__ = "2.0.0"' | Set-Content -LiteralPath (Join-Path $env:WORDLYRICS_DIR 'wordlyrics\__init__.py') -Encoding ascii
            'newer program preserved' | Set-Content -LiteralPath (Join-Path $env:WORDLYRICS_DIR 'wordlyrics\__main__.py') -Encoding ascii
            '@echo off' | Set-Content -LiteralPath (Join-Path $env:WORDLYRICS_DIR 'WordLyrics.bat') -Encoding ascii
            $state.Answers.Enqueue('0')
        }
        elseif($case -eq 'older-noctis') {
            $fakeCompiler=Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
            if (-not (Test-Path -LiteralPath $fakeCompiler)) { throw 'Existing Windows .NET Framework compiler required for the Noctis-version fixture; no downloads performed.' }
            $oldExe=Join-Path $env:LOCALAPPDATA 'Programs\Noctis\Noctis.exe'
            New-Item -ItemType Directory -Force -Path (Split-Path $oldExe) | Out-Null
            $codeFile=Join-Path $root 'FakeNoctis.cs'
            '[assembly:System.Reflection.AssemblyFileVersion("1.4.0.0")] [assembly:System.Reflection.AssemblyInformationalVersion("1.4.0")] class FakeNoctis { static void Main() {} }' | Set-Content -LiteralPath $codeFile -Encoding ascii
            & $fakeCompiler /nologo /target:exe ("/out:"+$oldExe) $codeFile
            if ($LASTEXITCODE -ne 0) { throw 'Version fixture compilation failed' }
            $state.Answers.Enqueue('1')
        }
        elseif($case -eq 'decline'){$state.Answers.Enqueue('0')}
        elseif($case -eq 'portable') {
            $portable=Join-Path $root 'portable\Noctis.exe'; New-Item -ItemType Directory -Force -Path (Split-Path $portable) | Out-Null
            'not executable' | Set-Content -LiteralPath $portable -Encoding ascii
            $state.Answers.Enqueue('2');$state.Answers.Enqueue($portable)
        } elseif($case -like 'private-*') {
            $exe=Join-Path $env:LOCALAPPDATA 'Programs\Noctis\Noctis.exe';New-Item -ItemType Directory -Force -Path (Split-Path $exe) | Out-Null
            'not executable' | Set-Content -LiteralPath $exe -Encoding ascii
            $env:WORDLYRICS_TELEGRAM_DEFAULTS_FILE=Join-Path $root 'telegram-defaults.private.json'
            $(if($case -eq 'private-invalid'){'{"api_id":0,"api_hash":"secret"}'}else{'{"api_id":12345,"api_hash":"0123456789abcdef0123456789abcdef"}'}) | Set-Content -LiteralPath $env:WORDLYRICS_TELEGRAM_DEFAULTS_FILE -Encoding ascii
        } elseif($case -eq 'local-bundle') {
            $env:WORDLYRICS_PACKAGE=$appZip; $env:WORDLYRICS_FINDER_PACKAGE=$packages['dev.moshi.freemusicfinder']
            $state.Answers.Enqueue('1')
        } else {$state.Answers.Enqueue('1')}
        $privateTarget=Join-Path $env:APPDATA 'Noctis\plugin-data\dev.moshi.freemusicfinder\telegram-defaults.private.json'
        if($case -eq 'private-kept'){New-Item -ItemType Directory -Force -Path (Split-Path $privateTarget) | Out-Null; 'custom defaults' | Set-Content -LiteralPath $privateTarget -Encoding ascii}
        $output=(& $Installer 6>&1 | Out-String)
        Assert-Setup (Test-Path -LiteralPath (Join-Path $env:WORDLYRICS_DIR 'wordlyrics\__main__.py')) "$case`: WordLyrics installs even when Noctis fails/is declined"
        Assert-Setup (-not $output.Contains('0123456789abcdef0123456789abcdef') -and -not $output.Contains('secret proxy details')) "$case`: no credentials/proxy details in output"
        if($case -in @('install','install-failure','local-bundle','older-noctis')){Assert-Setup ($state.Starts -eq 1 -and $state.HashChecks -eq 1) "$case`: verified installer launched once"}
        else{Assert-Setup ($state.Starts -eq 0) "$case`: no executable launch"}
        if($case -in @('install','portable','local-bundle','older-noctis') -or $case -like 'private-*') {
            if (-not (Test-Path -LiteralPath (Join-Path $env:APPDATA 'Noctis\plugins\dev.moshi.wordlyrics\plugin.json'))) { Write-Host $output }
            Assert-Setup (Test-Path -LiteralPath (Join-Path $env:APPDATA 'Noctis\plugins\dev.moshi.wordlyrics\plugin.json')) "$case`: plugins installed for detected/new Noctis"
        }
        if($case -in @('bad-size','bad-hash')){Assert-Setup ($output.Contains('NOCTIS_DOWNLOAD_CHECK_FAILED')) "$case`: integrity failure explained"}
        if($case -eq 'network-failure'){Assert-Setup ($output.Contains('TIMEOUT')) 'network failure has precise code'}
        if($case -eq 'private-defaults'){Assert-Setup (Test-Path -LiteralPath $privateTarget) 'private defaults installed outside plugin package'}
        if($case -eq 'private-kept'){Assert-Setup ((Get-Content -LiteralPath $privateTarget -Raw).Trim() -eq 'custom defaults') 'existing private settings preserved'}
        if($case -eq 'private-invalid'){Assert-Setup (-not (Test-Path -LiteralPath $privateTarget)) 'invalid defaults rejected';Assert-Setup ($output.Contains('TELEGRAM_DEFAULTS_INVALID')) 'invalid defaults explained'}
        if($case -eq 'local-bundle'){Assert-Setup (-not ($state.Requests | Where-Object {$_ -match 'fixture.invalid|noctic-download-plugin'})) 'friend bundle uses supplied app/Finder without fetching older public packages'}
        if($case -eq 'older-noctis'){Assert-Setup ($output.Contains('1.4.0 is older')) 'old player offers a compatible update'}
        if($case -eq 'newer-program') {
            Assert-Setup ((Get-Content -LiteralPath (Join-Path $env:WORDLYRICS_DIR 'wordlyrics\__main__.py') -Raw).Contains('newer program preserved')) 'newer private program never downgraded by public setup'
            Assert-Setup ($output.Contains('WORDLYRICS_NEWER_INSTALLED')) 'program version preservation explained'
        }
        Assert-Setup ($state.Answers.Count -eq 0) "$case`: no hidden prompts"
    }
    "PASS: $passed setup assertions across 14 scenarios; no network, executable/model launch or real credentials."
} finally {
    foreach($name in $names){[Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')}
    Remove-Variable -Name WordLyricsSetupFixture -Scope Global
}
