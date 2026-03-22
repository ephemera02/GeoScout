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
pip install flask pillow numpy pyinstaller --quiet
echo.

:: Build
echo   Building GeoScout.exe...
pyinstaller --onedir --noconsole --name GeoScout --icon=Geoscout_icon.ico app.py 2>nul
if not exist "dist\GeoScout" (
    pyinstaller --onedir --noconsole --name GeoScout app.py
)

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
