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

:: Build
echo   Building GeoScout.exe...
python -m PyInstaller --onedir --noconsole --noconfirm --name GeoScout --icon=Geoscout_icon.ico app.py
if errorlevel 1 (
    echo   [ERROR] Build failed. Check the output above.
    pause
    exit /b 1
)

:: Sync the current root index into Flask's templates folder
echo   Syncing template files...
if not exist templates mkdir templates
copy /Y index.html templates\index.html >nul

:: Copy templates and static into the dist folder
echo   Copying templates and static files...
xcopy /E /I /Y templates dist\GeoScout\templates >nul
xcopy /E /I /Y static dist\GeoScout\static >nul

:: Create uploads and results folders
mkdir dist\GeoScout\uploads 2>nul
mkdir dist\GeoScout\results 2>nul

echo.
echo   =====================
echo   Done! Your exe is in dist\GeoScout\
echo   Zip that whole folder for distribution.
echo   =====================
echo.
pause
