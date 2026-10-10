# WordLyrics installer. Paste this one line into PowerShell:
#
#     irm https://raw.githubusercontent.com/Alfamin/WordLyrics/main/install.ps1 | iex
#
# It downloads the small program (under 1 MB) into a "WordLyrics" folder in your user folder, puts a
# shortcut on the desktop and starts it. The first start then sets itself up inside that folder.
# WordLyrics stays portable. If Noctis is missing, you can choose its official per-user installer.
# Running the line again later updates the program and keeps the models, reports and settings.
#
# Optional, set before running:
#   $env:WORDLYRICS_DIR      = 'D:\Tools\WordLyrics'    another folder to install into
#   $env:WORDLYRICS_NO_START = '1'                      install only, do not start
#   $env:WORDLYRICS_SHORTCUT_DIR = 'D:\Tools'           put the shortcut there instead of on the desktop
#   $env:WORDLYRICS_NO_NOCTIS = '1'                     do not put anything into the Noctis player
#   $env:WORDLYRICS_NO_EXTRAS = '1'                     only the WordLyrics plugin, not the three others
#   $env:WORDLYRICS_NONINTERACTIVE = '1'                skip optional Noctis questions
#   $env:WORDLYRICS_PACKAGE = 'C:\path\WordLyrics.zip'  use an existing program package
#   $env:WORDLYRICS_FINDER_PACKAGE = 'C:\path\Finder.zip' use an existing Free Music Finder package
#   $env:WORDLYRICS_EXTRAS_DIR = 'C:\path\plugins'     local optional ZIPs named by plugin id
#   $env:WORDLYRICS_TELEGRAM_DEFAULTS_FILE = 'C:\path\telegram-defaults.private.json'  private defaults, never public
#   $env:WORDLYRICS_OFFLINE_INSTALL = '1'  use installed/supplied files, no network or automatic first-start downloads
#   $env:WORDLYRICS_REPAIR_INSTALL = '1'   explicitly refetch same-version program/plugin files for repair
#
# If the Noctis music player is on this computer, four plugins are put into Noctis' plugins folder and
# kept up to date by running the line again: WordLyrics (times new songs by itself), Lyric Motion
# (animated lyrics), Free Music Finder (finds and downloads songs) and True Shuffle (shuffle modes).
# Noctis keeps a new plugin switched
# off until you switch it on in Settings -> Plugins.

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $Repo = 'Alfamin/WordLyrics'
    $ProgramVersion = '1.6.0'
    $Sources = @(
        "https://github.com/$Repo/archive/refs/heads/main.zip",
        "https://codeload.github.com/$Repo/zip/refs/heads/main"
    )
    if ($env:WORDLYRICS_SOURCE) { $Sources = @($env:WORDLYRICS_SOURCE) }

    $Dest = $env:WORDLYRICS_DIR
    if (-not $Dest) { $Dest = Join-Path $HOME 'WordLyrics' }
    $Work = Join-Path ([IO.Path]::GetTempPath()) ('wordlyrics-install-' + [Guid]::NewGuid().ToString('N'))
    $Zip = Join-Path $Work 'WordLyrics.zip'
    $batPath = Join-Path $Dest 'WordLyrics.bat'

    # the other Noctis plugins this line keeps up to date (each from its own repository)
    $Extras = @(
        @{ Name = 'Lyric Motion'; Id = 'dev.moshi.lyricmotion'; Manifest = 'https://raw.githubusercontent.com/Alfamin/LyricMotion/main/src/plugin.json'; Urls = @(
            'https://github.com/Alfamin/LyricMotion/raw/main/LyricMotion-for-Noctis.zip',
            'https://raw.githubusercontent.com/Alfamin/LyricMotion/main/LyricMotion-for-Noctis.zip') },
        @{ Name = 'Free Music Finder'; Id = 'dev.moshi.freemusicfinder'; Manifest = 'https://raw.githubusercontent.com/Alfamin/noctic-download-plugin/main/FreeMusicFinder/plugin.json'; Urls = @(
            'https://github.com/Alfamin/noctic-download-plugin/raw/main/dist/FreeMusicFinder.zip',
            'https://raw.githubusercontent.com/Alfamin/noctic-download-plugin/main/dist/FreeMusicFinder.zip') },
        @{ Name = 'True Shuffle'; Id = 'dev.moshi.trueshuffle'; Manifest = 'https://raw.githubusercontent.com/Alfamin/TrueShuffle/main/src/plugin.json'; Urls = @(
            'https://github.com/Alfamin/TrueShuffle/raw/main/dist/TrueShuffle-for-Noctis.zip',
            'https://raw.githubusercontent.com/Alfamin/TrueShuffle/main/dist/TrueShuffle-for-Noctis.zip') }
    )
    $noctis = $env:NOCTIS_DATA_DIR
    if (-not $noctis) { $noctis = Join-Path $env:APPDATA 'Noctis' }
    $noctisThere = -not $env:WORDLYRICS_NO_NOCTIS -and (Test-Path -LiteralPath $noctis -PathType Container)
    $NoctisNotes = @()
    $NoctisRelease = @{
        Version = '1.5.9'; Size = 152405346
        Url = 'https://github.com/heartached/Noctis/releases/download/v1.5.9/Noctis-v1.5.9-Setup.exe'
        Sha256 = 'e40f38246a05b40624769ee8e538bcc419cd52be08ebd334b776c15bf9aebcdf'
    }
    $installedNoctis = $false
    $noctisExe = $null
    $InstalledNoctisVersion = $null

    function Test-InstalledProgram {
        if ($env:WORDLYRICS_REPAIR_INSTALL -eq '1' -or $env:WORDLYRICS_SOURCE) { return $false }
        try {
            $stamp = Join-Path $Dest 'installation.json'
            if (-not (Test-Path -LiteralPath $stamp -PathType Leaf) -or (Get-Item -LiteralPath $stamp).Length -gt 65536 -or ((Get-Item -LiteralPath $stamp).Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
            $record = [IO.File]::ReadAllText($stamp) | ConvertFrom-Json
            if ([version]$record.version -lt [version]$ProgramVersion) { return $false }
            $required = @('WordLyrics.bat','requirements.txt','wordlyrics\__main__.py','wordlyrics\runner.py','wordlyrics\menu.py','wordlyrics\repair.py','wordlyrics\drafts.py','wordlyrics\search.py','noctis-plugin\WordLyrics-for-Noctis.zip')
            $properties = @($record.files.PSObject.Properties)
            if ($properties.Count -gt 100 -or $properties.Count -lt $required.Count) { return $false }
            foreach ($name in $required) { if (-not ($properties.Name -contains $name)) { return $false } }
            $prefix = [IO.Path]::GetFullPath($Dest).TrimEnd('\') + '\'
            foreach ($item in $properties) {
                $path = [IO.Path]::GetFullPath((Join-Path $Dest $item.Name))
                if (-not $path.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or
                    -not (Test-Path -LiteralPath $path -PathType Leaf) -or
                    ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                    (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $item.Value) { return $false }
            }
            return $true
        } catch { return $false }
    }

    function Save-InstalledProgram {
        $hashes = [ordered]@{}
        $paths = @('WordLyrics.bat','requirements.txt','install.ps1','noctis-plugin\WordLyrics-for-Noctis.zip')
        $paths += @(Get-ChildItem -LiteralPath (Join-Path $Dest 'wordlyrics') -File | Where-Object { $_.Extension -in @('.py','.ps1') } | ForEach-Object { 'wordlyrics\' + $_.Name })
        foreach ($name in $paths) {
            $path = Join-Path $Dest $name
            if (Test-Path -LiteralPath $path -PathType Leaf) { $hashes[$name] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash }
        }
        $version = [regex]::Match([IO.File]::ReadAllText((Join-Path $Dest 'wordlyrics\__init__.py')), '__version__\s*=\s*"([0-9.]+)"').Groups[1].Value
        $json = @{ version=$version; files=$hashes } | ConvertTo-Json -Depth 4
        [IO.File]::WriteAllText((Join-Path $Dest 'installation.json'),$json,(New-Object Text.UTF8Encoding($false)))
    }

    function Keep-InstalledExtra($extra) {
        if ($env:WORDLYRICS_REPAIR_INSTALL -eq '1') { return $false }
        $pluginFolder = Join-Path (Join-Path $noctis 'plugins') $extra.Id
        try {
            $old = Get-Content -LiteralPath (Join-Path $pluginFolder 'plugin.json') -Raw | ConvertFrom-Json
            $entry = [string]$old.entry
            if ($old.id -ne $extra.Id -or -not (Test-PluginFiles $pluginFolder $old)) { return $false }
            if ($env:WORDLYRICS_OFFLINE_INSTALL -eq '1') { Write-Host " $($extra.Name): installed $($old.version); offline mode keeps it."; return $true }
            $metadata = Join-Path $Work ($extra.Id + '-version.json')
            try {
                Invoke-WebRequest -UseBasicParsing -Uri $extra.Manifest -OutFile $metadata -TimeoutSec 8
                if ((Get-Item -LiteralPath $metadata).Length -gt 65536) { throw 'metadata too large' }
                $new = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json
                if ($new.id -ne $extra.Id) { throw 'unexpected metadata' }
                if ([version]$old.version -ge [version]$new.version) { Write-Host " $($extra.Name): $($old.version) is current; ZIP download skipped."; return $true }
                return $false
            } catch {
                Write-Host (" $($extra.Name): installed $($old.version) kept. Update check unavailable: " + (Explain-Download $_)) -ForegroundColor Yellow
                return $true
            }
        } catch { return $false }
    }

    function Test-PluginFiles($pluginFolder,$info) {
        try {
            $entry = [string]$info.entry
            if (-not $entry -or $entry -notlike '*.dll' -or [IO.Path]::GetFileName($entry) -ne $entry -or -not (Test-Path -LiteralPath (Join-Path $pluginFolder $entry) -PathType Leaf)) { return $false }
            $stamp = Join-Path $pluginFolder 'wordlyrics-installed-files.json'
            if (-not (Test-Path -LiteralPath $stamp)) { return $true } # installed directly by Noctis: at least check the entry DLL
            $file = Get-Item -LiteralPath $stamp
            if ($file.Length -gt 65536 -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
            $record = Get-Content -LiteralPath $stamp -Raw | ConvertFrom-Json
            if ($record.version -ne $info.version) { return $true } # another installer updated it; stale hashes must not force a downgrade
            $items = @($record.files.PSObject.Properties)
            if ($items.Count -gt 100 -or -not ($items.Name -contains $entry)) { return $false }
            $prefix = [IO.Path]::GetFullPath($pluginFolder).TrimEnd('\') + '\'
            foreach ($item in $items) {
                $path = [IO.Path]::GetFullPath((Join-Path $pluginFolder $item.Name))
                if (-not $path.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $path -PathType Leaf) -or
                    ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $item.Value) { return $false }
            }
            return $true
        } catch { return $false }
    }

    function Noctis-Version($path) {
        if (-not $path) { return $null }
        try {
            $value = [Diagnostics.FileVersionInfo]::GetVersionInfo($path).ProductVersion
            $number = [regex]::Match([string]$value,'^\d+(\.\d+){1,3}').Value
            if ($number) { return [version]$number }
        } catch { }
        return $null
    }

    function Show-Step($title) {
        Write-Host ''
        Write-Host ('=' * 64) -ForegroundColor Cyan
        Write-Host ('  WORDLYRICS | ' + $title) -ForegroundColor Cyan
        Write-Host ('=' * 64) -ForegroundColor Cyan
    }

    function Explain-Download($errorRecord) {
        $exception = $errorRecord.Exception
        $status = $null
        try { $status = [int]$exception.Response.StatusCode } catch { }
        if ($status -in 401,403) { return "ACCESS_REFUSED (HTTP $status): the host or this connection may be blocked. Check your VPN/proxy." }
        if ($status -eq 429) { return 'RATE_LIMIT (HTTP 429): too many requests. Try again later.' }
        if ($status -ge 500) { return "SERVER_ERROR (HTTP $status): the download host is unavailable. Try again later." }
        if ($status -ge 400) { return "HTTP_$status`: the file could not be downloaded. Try again later." }
        $message = [string]$exception.Message
        if ($exception -is [TimeoutException] -or $exception -is [OperationCanceledException] -or $message -match 'timed out|timeout|canceled') { return 'TIMEOUT: the connection did not answer. Check internet access or your VPN/proxy.' }
        if ($message -match 'name resolution|resolve|No such host|known host') { return 'DNS_FAILED: the download host could not be found. Check DNS, internet access or your VPN.' }
        if ($message -match 'SSL|TLS|certificate|secure channel') { return 'TLS_FAILED: a secure connection could not be verified. Check your clock or connection.' }
        return ('DOWNLOAD_FAILED (' + $exception.GetType().Name + '): check internet access or your VPN/proxy.')
    }

    function Find-Noctis {
        $candidates = @($env:WORDLYRICS_NOCTIS_EXE,
            (Join-Path $env:LOCALAPPDATA 'Programs\Noctis\Noctis.exe'),
            (Join-Path $env:ProgramFiles 'Noctis\Noctis.exe'))
        foreach ($path in $candidates) { if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) { return $path } }
        return $null
    }

    function Offer-Noctis {
        if ($env:WORDLYRICS_NO_NOCTIS) { return }
        $found = Find-Noctis
        if ($env:WORDLYRICS_OFFLINE_INSTALL -eq '1') {
            Write-Host ' Noctis: offline setup keeps the installed player; no installer download.'
            return $found
        }
        $oldVersion = Noctis-Version $found
        if ($found -and (-not $oldVersion -or $oldVersion -ge [version]$NoctisRelease.Version)) { return $found }
        # An explicitly named portable/test data folder is already a user choice.
        if (-not $found -and $env:NOCTIS_DATA_DIR -and (Test-Path -LiteralPath $noctis -PathType Container)) { return }
        Show-Step 'NOCTIS MUSIC PLAYER'
        if ($found) { Write-Host " Noctis $oldVersion is older than the tested plugin bundle ($($NoctisRelease.Version))." }
        else { Write-Host ' Noctis was not found in the usual locations.' }
        Write-Host ' [1] Download/install the compatible Noctis version (about 153 MB, official GitHub release)'
        Write-Host ' [2] Noctis is installed elsewhere - choose its Noctis.exe'
        Write-Host ' [0] Continue with WordLyrics only'
        Write-Host ' Official downloads: https://github.com/heartached/Noctis/releases/latest'
        if ($env:WORDLYRICS_INSTALL_NOCTIS -eq '1') { $choice = '1' }
        elseif ($env:WORDLYRICS_NONINTERACTIVE -eq '1') { $choice = '0' }
        else { $choice = Read-Host ' WORDLYRICS INPUT | Choose 1, 2 or 0, then Enter' }
        if ($choice -eq '2') {
            $path = (Read-Host ' Drop/type the path to Noctis.exe').Trim().Trim('"')
            if ([IO.Path]::GetFileName($path) -ieq 'Noctis.exe' -and (Test-Path -LiteralPath $path -PathType Leaf)) { return [IO.Path]::GetFullPath($path) }
            Write-Host ' NOCTIS_NOT_FOUND: that is not an existing Noctis.exe. WordLyrics will still be installed.' -ForegroundColor Yellow
            return
        }
        if ($choice -ne '1') { return $found }
        $setup = Join-Path $Work 'Noctis-Setup.exe'
        Write-Host " Downloading Noctis $($NoctisRelease.Version) from github.com..."
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $NoctisRelease.Url -OutFile $setup -TimeoutSec 300
            if ((Get-Item -LiteralPath $setup).Length -ne $NoctisRelease.Size -or
                (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash -ine $NoctisRelease.Sha256) {
                throw 'NOCTIS_DOWNLOAD_CHECK_FAILED: installer size/fingerprint does not match the official release. It was not run.'
            }
            Write-Host ' Download verified. Installing for this Windows user...'
            $installAt = Join-Path $env:LOCALAPPDATA 'Programs\Noctis'
            $process = Start-Process -FilePath $setup -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',("/DIR=`"$installAt`"")) -PassThru -Wait -WindowStyle Hidden
            if ($process.ExitCode -ne 0) { throw "NOCTIS_INSTALL_FAILED: installer returned $($process.ExitCode). WordLyrics will still be installed." }
            $found = Join-Path $installAt 'Noctis.exe'
            if (-not (Test-Path -LiteralPath $found -PathType Leaf)) { throw 'NOCTIS_INSTALL_NOT_FOUND: setup exited, but Noctis.exe is missing.' }
            return $found
        } catch {
            if ($_.Exception.Message -like 'NOCTIS_*') { Write-Host $_.Exception.Message -ForegroundColor Yellow }
            else { Write-Host (Explain-Download $_) -ForegroundColor Yellow }
            Write-Host ' Continue with WordLyrics. You can install Noctis later and rerun this installer.'
            return $found
        }
    }

    function Install-TelegramDefaults {
        $privateFile = $env:WORDLYRICS_TELEGRAM_DEFAULTS_FILE
        if (-not $privateFile) { return }
        try {
            $item = Get-Item -LiteralPath $privateFile
            if ($item.Length -gt 4096 -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'invalid private defaults file' }
            $config = Get-Content -LiteralPath $privateFile -Raw | ConvertFrom-Json
            if ([int]$config.api_id -le 0 -or [string]$config.api_hash -notmatch '^[a-fA-F0-9]{32}$') { throw 'invalid Telegram application credentials' }
            $target = Join-Path $noctis 'plugin-data\dev.moshi.freemusicfinder\telegram-defaults.private.json'
            if (Test-Path -LiteralPath $target) {
                Write-Host ' Telegram defaults: existing private configuration kept.'
                return
            }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $privateFile -Destination $target
            Write-Host ' Telegram defaults installed privately. Each friend signs in with their own phone number.'
        } catch {
            Write-Host ' TELEGRAM_DEFAULTS_INVALID: the private defaults could not be installed. You can still enter your own API id/hash in Free Music Finder.' -ForegroundColor Yellow
        }
    }

    # Puts one plugin package into Noctis' plugins folder and says in one line what happened. A plugin
    # that is already there in the same or a newer version is left alone; the plugin's own data (logins,
    # settings) is kept by Noctis elsewhere and is not touched.
    function Add-ToNoctis($name, $package, $wantId) {
        try {
            $unpacked = Join-Path $Work ('plugin-' + [Guid]::NewGuid().ToString('N'))
            Expand-Archive -LiteralPath $package -DestinationPath $unpacked -Force
            $about = Join-Path $unpacked 'plugin.json'
            if (-not (Test-Path -LiteralPath $about)) { throw 'the plugin package is not complete' }
            $info = Get-Content -LiteralPath $about -Raw | ConvertFrom-Json
            if ($info.id -ne $wantId) { throw 'the plugin package is not the expected one' }
            if ($InstalledNoctisVersion -and $info.minAppVersion -and $InstalledNoctisVersion -lt [version]$info.minAppVersion) {
                return " Noctis plugin `"$name`": requires Noctis $($info.minAppVersion), installed $InstalledNoctisVersion. Update Noctis and run this installer again; existing plugin files were kept."
            }
            $entry = [string]$info.entry
            if (-not $entry -or [IO.Path]::GetFileName($entry) -ne $entry -or $entry -notlike '*.dll' -or
                -not (Test-Path -LiteralPath (Join-Path $unpacked $entry) -PathType Leaf)) {
                throw 'the plugin package is missing its entry DLL'
            }
            $pluginHome = Join-Path (Join-Path $noctis 'plugins') $wantId
            $old = $null
            $oldAbout = Join-Path $pluginHome 'plugin.json'
            if (Test-Path -LiteralPath $oldAbout) {
                try { $old = [string](Get-Content -LiteralPath $oldAbout -Raw | ConvertFrom-Json).version } catch { $old = '0' }
            }
            if ($null -ne $old) {
                $newer = $true
                try { $newer = [version]$info.version -gt [version]$old } catch { $newer = $info.version -ne $old }
                $oldEntryPresent = $false
                try {
                    $oldInfo = Get-Content -LiteralPath $oldAbout -Raw | ConvertFrom-Json
                    $oldEntry = [string]$oldInfo.entry
                    $oldEntryPresent = Test-PluginFiles $pluginHome $oldInfo
                } catch { }
                if (-not $newer -and ($info.version -ne $old -or ($oldEntryPresent -and $env:WORDLYRICS_REPAIR_INSTALL -ne '1'))) { return " Noctis plugin `"$name`": $old is in place, nothing to update." }
            }
            New-Item -ItemType Directory -Force -Path $pluginHome | Out-Null
            # plugin.json goes last: if a file is in use (Noctis is open), the old version number stays
            # and the next run tries again
            $files = @(Get-ChildItem -LiteralPath $unpacked -File -Recurse | Where-Object { $_.FullName -ne $about })
            try {
                foreach ($f in $files) {
                    $to = Join-Path $pluginHome $f.FullName.Substring($unpacked.Length + 1)
                    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
                    Copy-Item -LiteralPath $f.FullName -Destination $to -Force
                }
                $installedFiles = [ordered]@{}
                foreach ($f in $files) { $installedFiles[$f.FullName.Substring($unpacked.Length + 1)] = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash }
                $installedFiles['plugin.json'] = (Get-FileHash -LiteralPath $about -Algorithm SHA256).Hash
                $record = @{ version=$info.version;files=$installedFiles } | ConvertTo-Json -Depth 4
                [IO.File]::WriteAllText((Join-Path $pluginHome 'wordlyrics-installed-files.json'),$record,(New-Object Text.UTF8Encoding($false)))
                Copy-Item -LiteralPath $about -Destination $oldAbout -Force
            } catch {
                if ($null -ne $old) { return " Noctis plugin `"$name`": $($info.version) could not replace $old (is Noctis open? close it and run this line again)." }
                throw
            }
            if ($null -ne $old) { return " Noctis plugin `"$name`": updated $old -> $($info.version). It is used from the next start of Noctis." }
            return " Noctis plugin `"$name`": $($info.version) is in place. In Noctis: Settings -> Plugins, turn on `"Community plugins`"," +
                   "`n" + "   then switch `"$name`" on (restart Noctis first if it is not in the list yet)."
        } catch {
            return " Noctis plugin `"$name`": could not be put into Noctis ($($_.Exception.Message))." +
                   "`n" + '   Use "Install from file" in Noctis -> Settings -> Plugins with its zip instead.'
        }
    }

    Show-Step 'SETUP'
    Write-Host ' Word-by-word lyrics for your music'
    Write-Host " Installing into $Dest"
    Write-Host ''

    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    try {
        $previousExe = Find-Noctis
        $noctisExe = Offer-Noctis
        $InstalledNoctisVersion = Noctis-Version $noctisExe
        $installedNoctis = -not $previousExe -and [bool]$noctisExe
        $noctisThere = -not $env:WORDLYRICS_NO_NOCTIS -and ([bool]$noctisExe -or $noctisThere)
        if ($noctisThere) { New-Item -ItemType Directory -Force -Path $noctis | Out-Null }
        Show-Step 'INSTALL WORDLYRICS'
        $reuseProgram = Test-InstalledProgram
        $got = $reuseProgram
        if ($reuseProgram) { Write-Host ' WordLyrics is already installed and its program files verify. Program ZIP download skipped.'; $src = $Dest }
        if (-not $reuseProgram -and $env:WORDLYRICS_PACKAGE) {
            $localPackage = Get-Item -LiteralPath $env:WORDLYRICS_PACKAGE
            if ($localPackage.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'LOCAL_PACKAGE_INVALID: choose an original ZIP file rather than a link.' }
            Copy-Item -LiteralPath $env:WORDLYRICS_PACKAGE -Destination $Zip
            Write-Host ' Using the program package supplied with this installer.'
            $got = $true
        }
        foreach ($u in $(if ($got -or $env:WORDLYRICS_OFFLINE_INSTALL -eq '1') { @() } else { $Sources })) {
            try {
                Write-Host " Downloading the program from $(([Uri]$u).Host) ..."
                Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $Zip -TimeoutSec 120
                $got = $true
                break
            } catch {
                Write-Host ('   ' + (Explain-Download $_)) -ForegroundColor Yellow
            }
        }
        if (-not $got) {
            Write-Host ' SETUP_FILES_MISSING: no verified installed program or supplied package is available. Use the private/offline ZIP, or reconnect and run setup again. Existing files were kept.'
            return
        }
        if (-not $reuseProgram) {
        Expand-Archive -LiteralPath $Zip -DestinationPath (Join-Path $Work 'x') -Force
        $bat = Get-ChildItem -LiteralPath (Join-Path $Work 'x') -Recurse -Filter 'WordLyrics.bat' | Select-Object -First 1
        if (-not $bat -or -not (Test-Path -LiteralPath (Join-Path $bat.Directory.FullName 'wordlyrics\__main__.py'))) {
            Write-Host ' The download does not contain WordLyrics. Nothing was installed.'
            return
        }
        $src = $bat.Directory.FullName
        }
        $keepNewerProgram = $reuseProgram
        try {
            $incoming = [regex]::Match([IO.File]::ReadAllText((Join-Path $src 'wordlyrics\__init__.py')), '__version__\s*=\s*"([0-9.]+)"').Groups[1].Value
            $existing = [regex]::Match([IO.File]::ReadAllText((Join-Path $Dest 'wordlyrics\__init__.py')), '__version__\s*=\s*"([0-9.]+)"').Groups[1].Value
            $keepNewerProgram = $reuseProgram -or ($incoming -and $existing -and [version]$existing -gt [version]$incoming)
            if ($keepNewerProgram -and -not $reuseProgram) { Write-Host " WORDLYRICS_NEWER_INSTALLED: keeping $existing; the supplied program is older ($incoming)." -ForegroundColor Yellow }
        } catch { }

        New-Item -ItemType Directory -Force -Path (Join-Path $Dest 'wordlyrics') | Out-Null
        # the program's own files are replaced by the new ones; models, reports and the portable Python stay
        foreach ($f in 'WordLyrics.bat', 'requirements.txt', 'README.md', 'install.ps1', 'LICENSE.txt', 'lyrics-providers.example.json') {
            $p = Join-Path $src $f
            if (-not $keepNewerProgram -and (Test-Path -LiteralPath $p)) { Copy-Item -LiteralPath $p -Destination (Join-Path $Dest $f) -Force }
        }
        Get-ChildItem -LiteralPath (Join-Path $src 'wordlyrics') -File | Where-Object { $_.Extension -eq '.py' -or $_.Name -eq 'repair-permissions.ps1' } | ForEach-Object {
            if (-not $keepNewerProgram) { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Dest ('wordlyrics\' + $_.Name)) -Force }
        }
        # the plugin for the Noctis player, ready for its "Install from file..." button
        $plugin = Join-Path $src 'noctis-plugin\WordLyrics-for-Noctis.zip'
        if (Test-Path -LiteralPath $plugin) {
            New-Item -ItemType Directory -Force -Path (Join-Path $Dest 'noctis-plugin') | Out-Null
            if (-not $keepNewerProgram) { Copy-Item -LiteralPath $plugin -Destination (Join-Path $Dest 'noctis-plugin\WordLyrics-for-Noctis.zip') -Force }
            # ... and, when Noctis is on this computer, put into its plugins folder. Noctis keeps a new
            # plugin switched off until you switch it on yourself; nothing in Noctis' settings is touched.
            if ($noctisThere) { $NoctisNotes += Add-ToNoctis 'WordLyrics' $plugin 'dev.moshi.wordlyrics' }
        }
        # the three other Noctis plugins of the same family: fetched and brought up to date as well
        if ($noctisThere -and -not $env:WORDLYRICS_NO_EXTRAS) {
            Show-Step 'INSTALL NOCTIS PLUGINS'
            foreach ($x in $Extras) {
                Write-Host (' Installing/updating ' + $x.Name + '...')
                $hasLocal = ($env:WORDLYRICS_EXTRAS_DIR -and (Test-Path -LiteralPath (Join-Path $env:WORDLYRICS_EXTRAS_DIR ($x.Id + '.zip')) -PathType Leaf)) -or ($x.Id -eq 'dev.moshi.freemusicfinder' -and $env:WORDLYRICS_FINDER_PACKAGE)
                if (-not $hasLocal -and (Keep-InstalledExtra $x)) { continue }
                $file = Join-Path $Work ($x.Id + '.zip')
                $got = $false
                if ($env:WORDLYRICS_EXTRAS_DIR) {
                    $localExtra = Join-Path $env:WORDLYRICS_EXTRAS_DIR ($x.Id + '.zip')
                    if (Test-Path -LiteralPath $localExtra -PathType Leaf) {
                        try {
                            if ((Get-Item -LiteralPath $localExtra).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'linked package' }
                            Copy-Item -LiteralPath $localExtra -Destination $file
                            $got = $true
                        } catch { Write-Host ' LOCAL_EXTRA_PACKAGE_INVALID: using the public fallback.' -ForegroundColor Yellow }
                    }
                }
                if ($x.Id -eq 'dev.moshi.freemusicfinder' -and $env:WORDLYRICS_FINDER_PACKAGE) {
                    try {
                        $localFinder = Get-Item -LiteralPath $env:WORDLYRICS_FINDER_PACKAGE
                        if ($localFinder.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'linked plugin package' }
                        Copy-Item -LiteralPath $env:WORDLYRICS_FINDER_PACKAGE -Destination $file
                        $got = $true
                    } catch { Write-Host ' LOCAL_FINDER_PACKAGE_INVALID: the supplied plugin ZIP could not be read. Trying its public download.' -ForegroundColor Yellow }
                }
                foreach ($u in $(if ($got -or $env:WORDLYRICS_OFFLINE_INSTALL -eq '1') { @() } else { $x.Urls })) {
                    try {
                        Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $file -TimeoutSec 120
                        $got = $true
                        break
                    } catch { Write-Host ('   ' + (Explain-Download $_)) -ForegroundColor Yellow }
                }
                if ($got) { $NoctisNotes += Add-ToNoctis $x.Name $file $x.Id }
                else { $NoctisNotes += " Noctis plugin `"$($x.Name)`": could not be downloaded, left as it is." }
            }
        }
        if ($noctisThere -and -not $env:WORDLYRICS_NO_EXTRAS) { Install-TelegramDefaults }
        # a file you may have edited is never replaced
        $extra = Join-Path $Dest 'extra-links.txt'
        if (-not (Test-Path -LiteralPath $extra) -and (Test-Path -LiteralPath (Join-Path $src 'extra-links.txt'))) {
            Copy-Item -LiteralPath (Join-Path $src 'extra-links.txt') -Destination $extra
        }
        # a .bat file needs Windows line ends, whatever the download did to them
        $text = [IO.File]::ReadAllText($batPath)
        $text = $text.Replace("`r`n", "`n").Replace("`n", "`r`n")
        [IO.File]::WriteAllText($batPath, $text, (New-Object Text.UTF8Encoding($false)))
        if (-not $reuseProgram) { Save-InstalledProgram }

        try {
            $desk = [Environment]::GetFolderPath('Desktop')
            if ($env:WORDLYRICS_SHORTCUT_DIR) { $desk = $env:WORDLYRICS_SHORTCUT_DIR }
            $lnk = Join-Path $desk 'WordLyrics.lnk'
            $sh = New-Object -ComObject WScript.Shell
            $s = $sh.CreateShortcut($lnk)
            $s.TargetPath = $batPath
            $s.WorkingDirectory = $Dest
            $s.Description = 'Word-by-word lyrics for your music'
            $s.Save()
            Write-Host " A shortcut `"WordLyrics`" is in $desk. You can also drag a music folder onto it."
        } catch {
            # Windows' shortcut maker cannot handle some folder names (letters outside the system's own
            # alphabet): a small starter file does the same job
            try {
                $starter = "@echo off`r`nchcp 65001 >nul`r`nset `"WORDLYRICS_NO_PAUSE=1`"`r`ncall `"$batPath`" %*`r`npause`r`n"
                [IO.File]::WriteAllText((Join-Path $desk 'WordLyrics.cmd'), $starter, (New-Object Text.UTF8Encoding($false)))
                Write-Host " A starter `"WordLyrics`" is in $desk. You can also drag a music folder onto it."
            } catch {
                Write-Host ' (No desktop shortcut could be made; start WordLyrics.bat in the folder above.)'
            }
        }
    } catch {
        Write-Host " The installation did not finish: $($_.Exception.Message)"
        return
    } finally {
        # Clean only this installer's own temporary directory, never a linked or unexpected path.
        $fullWork = [IO.Path]::GetFullPath($Work)
        $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/')
        $workInfo = Get-Item -LiteralPath $fullWork -Force -ErrorAction SilentlyContinue
        if ($workInfo -and [IO.Path]::GetDirectoryName($fullWork) -eq $tempRoot -and
            [IO.Path]::GetFileName($fullWork) -match '^wordlyrics-install-[0-9a-f]{32}$' -and
            -not ($workInfo.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Remove-Item -LiteralPath $fullWork -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Show-Step 'SETUP RESULT'
    Write-Host " WordLyrics installed into $Dest" -ForegroundColor Green
    foreach ($n in $NoctisNotes) { Write-Host $n }
    Write-Host ''
    if ($noctisThere) {
        Write-Host ' NEXT: Noctis > Settings > Plugins > enable Community plugins and approve the plugins.' -ForegroundColor Yellow
        Write-Host ' Add your music folders in Noctis. In WordLyrics, choose [1] Generate missing timestamps.'
    } else { Write-Host ' NEXT: choose [1] Generate missing timestamps, then choose your music folder.' -ForegroundColor Yellow }
    Write-Host ' First use downloads Python, packages and AI models; later starts reuse them.'
    if ($env:WORDLYRICS_OFFLINE_INSTALL -eq '1') {
        Write-Host ' OFFLINE_SETUP: program/plugin files were kept or installed locally. Automatic startup downloads are disabled. Start WordLyrics when its Python/packages/models are available.' -ForegroundColor Yellow
        return
    }
    if ($installedNoctis -and -not $env:WORDLYRICS_NO_START) { Start-Process -FilePath $noctisExe -WindowStyle Hidden | Out-Null }
    if (-not $env:WORDLYRICS_NO_START) {
        $previousNoPause = $env:WORDLYRICS_NO_PAUSE
        $env:WORDLYRICS_NO_PAUSE = '1'
        try { & $batPath } finally { [Environment]::SetEnvironmentVariable('WORDLYRICS_NO_PAUSE', $previousNoPause, 'Process') }
    }
}
