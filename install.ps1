# WordLyrics installer. Paste this one line into PowerShell:
#
#     irm https://raw.githubusercontent.com/Alfamin/WordLyrics/main/install.ps1 | iex
#
# It downloads the small program (under 1 MB) into a "WordLyrics" folder in your user folder, puts a
# shortcut on the desktop and starts it. The first start then sets itself up inside that folder.
# Nothing is installed into Windows, no administrator rights are needed, no setting is changed.
# Running the line again later updates the program and keeps the models, reports and settings.
#
# Optional, set before running:
#   $env:WORDLYRICS_DIR      = 'D:\Tools\WordLyrics'    another folder to install into
#   $env:WORDLYRICS_NO_START = '1'                      install only, do not start
#   $env:WORDLYRICS_SHORTCUT_DIR = 'D:\Tools'           put the shortcut there instead of on the desktop
#   $env:WORDLYRICS_NO_NOCTIS = '1'                     do not put anything into the Noctis player
#   $env:WORDLYRICS_NO_EXTRAS = '1'                     only the WordLyrics plugin, not the three others
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
        @{ Name = 'Lyric Motion'; Id = 'dev.moshi.lyricmotion'; Urls = @(
            'https://github.com/Alfamin/LyricMotion/raw/main/LyricMotion-for-Noctis.zip',
            'https://raw.githubusercontent.com/Alfamin/LyricMotion/main/LyricMotion-for-Noctis.zip') },
        @{ Name = 'Free Music Finder'; Id = 'dev.moshi.freemusicfinder'; Urls = @(
            'https://github.com/Alfamin/noctic-download-plugin/raw/main/dist/FreeMusicFinder.zip',
            'https://raw.githubusercontent.com/Alfamin/noctic-download-plugin/main/dist/FreeMusicFinder.zip') },
        @{ Name = 'True Shuffle'; Id = 'dev.moshi.trueshuffle'; Urls = @(
            'https://github.com/Alfamin/TrueShuffle/raw/main/dist/TrueShuffle-for-Noctis.zip',
            'https://raw.githubusercontent.com/Alfamin/TrueShuffle/main/dist/TrueShuffle-for-Noctis.zip') }
    )
    $noctis = $env:NOCTIS_DATA_DIR
    if (-not $noctis) { $noctis = Join-Path $env:APPDATA 'Noctis' }
    $noctisThere = -not $env:WORDLYRICS_NO_NOCTIS -and (Test-Path -LiteralPath $noctis -PathType Container)
    $NoctisNotes = @()

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
                if (-not $newer) { return " Noctis plugin `"$name`": $old is in place, nothing to update." }
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

    Write-Host ''
    Write-Host ' WordLyrics - word-by-word lyrics for your music'
    Write-Host " Installing into $Dest"
    Write-Host ''

    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
    New-Item -ItemType Directory -Force -Path $Work | Out-Null
    try {
        $got = $false
        foreach ($u in $Sources) {
            try {
                Write-Host " Downloading the program from $(([Uri]$u).Host) ..."
                Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $Zip -TimeoutSec 120
                $got = $true
                break
            } catch {
                Write-Host "   that did not work: $($_.Exception.Message)"
            }
        }
        if (-not $got) {
            Write-Host ' The program could not be downloaded. Check the internet connection and try again.'
            return
        }

        Expand-Archive -LiteralPath $Zip -DestinationPath (Join-Path $Work 'x') -Force
        $bat = Get-ChildItem -LiteralPath (Join-Path $Work 'x') -Recurse -Filter 'WordLyrics.bat' | Select-Object -First 1
        if (-not $bat -or -not (Test-Path -LiteralPath (Join-Path $bat.Directory.FullName 'wordlyrics\__main__.py'))) {
            Write-Host ' The download does not contain WordLyrics. Nothing was installed.'
            return
        }
        $src = $bat.Directory.FullName

        New-Item -ItemType Directory -Force -Path (Join-Path $Dest 'wordlyrics') | Out-Null
        # the program's own files are replaced by the new ones; models, reports and the portable Python stay
        foreach ($f in 'WordLyrics.bat', 'requirements.txt', 'README.md', 'LICENSE.txt', 'lyrics-providers.example.json') {
            $p = Join-Path $src $f
            if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination (Join-Path $Dest $f) -Force }
        }
        Get-ChildItem -LiteralPath (Join-Path $src 'wordlyrics') -Filter '*.py' | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Dest ('wordlyrics\' + $_.Name)) -Force
        }
        # the plugin for the Noctis player, ready for its "Install from file..." button
        $plugin = Join-Path $src 'noctis-plugin\WordLyrics-for-Noctis.zip'
        if (Test-Path -LiteralPath $plugin) {
            New-Item -ItemType Directory -Force -Path (Join-Path $Dest 'noctis-plugin') | Out-Null
            Copy-Item -LiteralPath $plugin -Destination (Join-Path $Dest 'noctis-plugin\WordLyrics-for-Noctis.zip') -Force
            # ... and, when Noctis is on this computer, put into its plugins folder. Noctis keeps a new
            # plugin switched off until you switch it on yourself; nothing in Noctis' settings is touched.
            if ($noctisThere) { $NoctisNotes += Add-ToNoctis 'WordLyrics' $plugin 'dev.moshi.wordlyrics' }
        }
        # the three other Noctis plugins of the same family: fetched and brought up to date as well
        if ($noctisThere -and -not $env:WORDLYRICS_NO_EXTRAS) {
            foreach ($x in $Extras) {
                $file = Join-Path $Work ($x.Id + '.zip')
                $got = $false
                foreach ($u in $x.Urls) {
                    try {
                        Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $file -TimeoutSec 120
                        $got = $true
                        break
                    } catch { }
                }
                if ($got) { $NoctisNotes += Add-ToNoctis $x.Name $file $x.Id }
                else { $NoctisNotes += " Noctis plugin `"$($x.Name)`": could not be downloaded, left as it is." }
            }
        }
        # a file you may have edited is never replaced
        $extra = Join-Path $Dest 'extra-links.txt'
        if (-not (Test-Path -LiteralPath $extra) -and (Test-Path -LiteralPath (Join-Path $src 'extra-links.txt'))) {
            Copy-Item -LiteralPath (Join-Path $src 'extra-links.txt') -Destination $extra
        }
        # a .bat file needs Windows line ends, whatever the download did to them
        $text = [IO.File]::ReadAllText($batPath)
        $text = $text.Replace("`r`n", "`n").Replace("`n", "`r`n")
        [IO.File]::WriteAllText($batPath, $text, (New-Object Text.UTF8Encoding($false)))

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

    Write-Host " Installed. To remove it later, delete the folder $Dest and the shortcut."
    foreach ($n in $NoctisNotes) { Write-Host $n }
    Write-Host ''
    if (-not $env:WORDLYRICS_NO_START) {
        $env:WORDLYRICS_NO_PAUSE = '1'
        try { & $batPath } finally { Remove-Item Env:\WORDLYRICS_NO_PAUSE -ErrorAction SilentlyContinue }
    }
}
