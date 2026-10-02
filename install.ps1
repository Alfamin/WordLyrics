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
        foreach ($f in 'WordLyrics.bat', 'requirements.txt', 'README.md', 'LICENSE.txt') {
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
        Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host " Installed. To remove it later, delete the folder $Dest and the shortcut."
    Write-Host ''
    if (-not $env:WORDLYRICS_NO_START) {
        $env:WORDLYRICS_NO_PAUSE = '1'
        try { & $batPath } finally { Remove-Item Env:\WORDLYRICS_NO_PAUSE -ErrorAction SilentlyContinue }
    }
}
