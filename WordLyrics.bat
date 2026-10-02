@echo off
rem WordLyrics launcher. Double-click it, drag a music folder onto it, or run it from a terminal:
rem     WordLyrics.bat "D:\Music"            WordLyrics.bat "D:\Music" --dry-run
rem     WordLyrics.bat undo "D:\Music"       WordLyrics.bat check
rem Nothing has to be installed first. The first start downloads a portable Python into the ".python"
rem folder next to this file and the packages into it. Nothing is put anywhere else on the computer,
rem and deleting the WordLyrics folder removes everything.
setlocal EnableExtensions
chcp 65001 >nul
set "APP=%~dp0"
if "%APP:~-1%"=="\" set "APP=%APP:~0,-1%"
set "CODE=0"
set "SYS=%SystemRoot%\System32"
rem ENVV goes up by one whenever requirements.txt changes, so that the setup runs again after an update
set "ENVV=1"
set "PYDIR=%APP%\.python"
set "PY=%PYDIR%\python.exe"
if defined WORDLYRICS_PYTHON (
    rem for development: another Python that already has the packages
    set "PY=%WORDLYRICS_PYTHON%"
    set "PYTHONPATH=%APP%"
    goto :run
)
if not exist "%PYDIR%\ready-%ENVV%.txt" (
    call :setup
    if errorlevel 1 goto :end
)
:run
"%PY%" -X utf8 -m wordlyrics %*
set "CODE=%ERRORLEVEL%"
goto :end

:setup
set "PYZIP=python-3.14.4-embed-amd64.zip"
set "PYSHA=cda80a9b1e75c0f1b4f9872ca1b417f0d19bce32facc811aea9180e70fad5fb9"
set "WORK=%APP%\.python-setup"
set "ZIP=%WORK%\python.zip"
set "UNZ=%WORK%\py"
echo.
echo  WordLyrics - first start
echo.
echo  It sets itself up now. This happens once, and only inside this folder:
echo    %APP%
echo  Needed: an internet connection and about 2.5 GB of free space in that folder.
echo.
if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 (
    echo  WordLyrics needs 64-bit Windows 10 or 11.
    set "CODE=1"
    exit /b 1
)
if exist "%PY%" goto :packages
echo  Step 1 of 3: a portable Python ^(12 MB^)
if not exist "%WORK%" mkdir "%WORK%"
set "GOT="
for %%U in (
    https://www.python.org/ftp/python/3.14.4/%PYZIP%
    https://registry.npmmirror.com/-/binary/python/3.14.4/%PYZIP%
    https://mirrors.huaweicloud.com/python/3.14.4/%PYZIP%
) do if not defined GOT call :fetch "%%U"
if not defined GOT (
    echo.
    echo  The portable Python could not be downloaded from any of the three servers.
    echo  Check the internet connection and start WordLyrics again.
    set "CODE=1"
    exit /b 1
)
if exist "%UNZ%" rmdir /s /q "%UNZ%"
mkdir "%UNZ%"
"%SYS%\tar.exe" -xf "%ZIP%" -C "%UNZ%" 2>nul
if not exist "%UNZ%\python.exe" "%SYS%\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Expand-Archive -LiteralPath $env:ZIP -DestinationPath $env:UNZ -Force"
if not exist "%UNZ%\python.exe" (
    echo  The portable Python could not be unpacked.
    set "CODE=1"
    exit /b 1
)
move "%UNZ%" "%PYDIR%" >nul
if not exist "%PY%" (
    echo  The portable Python could not be put into the .python folder.
    set "CODE=1"
    exit /b 1
)
echo    done
echo.
:packages
"%PY%" "%APP%\wordlyrics\firststart.py" %ENVV%
if errorlevel 1 (
    set "CODE=1"
    exit /b 1
)
if exist "%WORK%" rmdir /s /q "%WORK%"
exit /b 0

:fetch
echo    from %~1
if exist "%ZIP%" del /q "%ZIP%" >nul 2>nul
"%SYS%\curl.exe" --fail --location --progress-bar --connect-timeout 20 --retry 1 -o "%ZIP%" "%~1"
if errorlevel 1 (
    rem curl is missing or could not connect: Windows' own downloader, which also knows the system proxy
    if exist "%ZIP%" del /q "%ZIP%" >nul 2>nul
    "%SYS%\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; try { Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile $env:ZIP -TimeoutSec 180 } catch { exit 1 }"
)
if not exist "%ZIP%" (
    echo    that did not work
    exit /b 0
)
"%SYS%\certutil.exe" -hashfile "%ZIP%" SHA256 | "%SYS%\find.exe" /i "%PYSHA%" >nul
if errorlevel 1 (
    echo    the file that arrived is not the expected one
    exit /b 0
)
set "GOT=1"
exit /b 0

:end
rem keep the window open when started by double-click or drag and drop
if not defined WORDLYRICS_NO_PAUSE echo %CMDCMDLINE% | "%SYS%\find.exe" /i "%~nx0" >nul && pause
endlocal & exit /b %CODE%
