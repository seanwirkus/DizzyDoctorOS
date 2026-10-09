@echo off
setlocal
rem Installs DizzyDoctor for the person signed in to this PC (no admin rights needed) and starts it.
rem From then on it updates itself from this folder and loads the clinic data the main PC publishes here.
rem (DIZZY_TEST=1 installs into %LOCALAPPDATA% with the shortcuts next to the program and doesn't start it.)
set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\DizzyDoctor"
echo.
echo   Installing DizzyDoctor on %COMPUTERNAME% for %USERNAME%...
if defined DIZZY_TEST goto copyfiles
tasklist /FI "IMAGENAME eq DizzyDoctor.exe" 2>nul | find /I "DizzyDoctor.exe" >nul
if not errorlevel 1 (
    echo.
    echo   DizzyDoctor is open on this PC. Close it, then run this again.
    echo.
    pause
    exit /b 1
)
:copyfiles
if not exist "%DEST%" mkdir "%DEST%"
copy /y "%SRC%DizzyDoctor.exe" "%DEST%\DizzyDoctor.exe" >nul || goto fail
copy /y "%SRC%config.json" "%DEST%\config.json" >nul || goto fail
set "DIZZY_DEST=%DEST%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "$dirs = if ($env:DIZZY_TEST) { @($env:DIZZY_DEST) } else { @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')) };" ^
  "foreach ($dir in $dirs) {" ^
  "  foreach ($old in 'DizzyDoctorOS.lnk', 'dizzdocOs.lnk') { Remove-Item -LiteralPath (Join-Path $dir $old) -ErrorAction SilentlyContinue };" ^
  "  $l = $w.CreateShortcut((Join-Path $dir 'DizzyDoctor.lnk'));" ^
  "  $l.TargetPath = (Join-Path $env:DIZZY_DEST 'DizzyDoctor.exe'); $l.WorkingDirectory = $env:DIZZY_DEST;" ^
  "  $l.Description = 'DizzyDoctor - clinic progress notes'; $l.Save() }" || goto fail
echo.
echo   Done. DizzyDoctor is on the Desktop and in the Start menu.
echo   The first start copies the clinic data from the server - give it a minute or two.
echo.
if defined DIZZY_TEST exit /b 0
start "" "%DEST%\DizzyDoctor.exe"
exit /b 0

:fail
echo.
echo   The install didn't finish. Is the V: drive connected on this PC?
echo.
if not defined DIZZY_TEST pause
exit /b 1
