@echo off
REM Builds dist\DizzyDoctor.exe - run on any Windows PC with Python 3.10+ installed (python.org).
cd /d "%~dp0"
python -m pip install --upgrade -r requirements.txt pyinstaller || goto :fail
python -m unittest || goto :fail
python deploy\version_info.py build\version_info.txt || goto :fail
REM The icon goes on the .exe (Explorer, taskbar, shortcuts) and is bundled for the window title bars.
python -m PyInstaller --noconfirm --onefile --windowed --icon "assets\DizzyDoctor.ico" --version-file build\version_info.txt --add-data "assets\DizzyDoctor.ico;assets" --collect-submodules keyring --name DizzyDoctor --collect-all webview --hidden-import webview.platforms.edgechromium --collect-all winrt --collect-all pymupdf --collect-all faster_whisper --collect-all ctranslate2 --collect-all av --add-data "recordfinder/charting/web;recordfinder/charting/web" --add-data "recordfinder/charting/templates;recordfinder/charting/templates" --add-data "recordfinder/charting/note_schema.json;recordfinder/charting" --add-data "recordfinder/superbill_form.json;recordfinder" --add-data "recordfinder/superbill_form.png;recordfinder" --add-data "recordfinder/superbill_form_older.json;recordfinder" --add-data "recordfinder/superbill_form_older.png;recordfinder" --hidden-import win32timezone main.py || goto :fail
echo.
echo Built: %~dp0dist\DizzyDoctor.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
