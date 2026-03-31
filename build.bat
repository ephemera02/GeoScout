@echo off
echo.
echo   GeoScout Build Script
echo   =====================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not found. Install Python 3.10+ and check "Add to PATH".
    pause
    exit /b 1
)

:: Install dependencies
echo   Installing dependencies...
python -m pip install flask pillow numpy pyinstaller --quiet
echo.

:: Sync the current root index into Flask's templates folder
echo   Syncing template files...
if not exist templates mkdir templates
copy /Y index.html templates\index.html >nul

:: Build single exe with templates and static bundled inside
echo   Building GeoScout.exe (single file)...
python -m PyInstaller ^
    --onefile ^
    --noconsole ^
    --noconfirm ^
    --name GeoScout ^
    --icon=Geoscout_icon.ico ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    app.py

if errorlevel 1 (
    echo   [ERROR] Build failed. Check the output above.
    pause
    exit /b 1
)

echo.
echo   =====================
echo   Done! Your exe is at dist\GeoScout.exe
echo   No extra folders needed; just run the exe.
echo   (uploads and results folders are created
echo    next to the exe at runtime)
echo   =====================
echo.
pause
