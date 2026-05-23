@echo off
setlocal
cd /d "%~dp0"

echo Building IA Batch Downloader GUI for Windows...

echo Checking Python...
py --version >nul 2>&1
if errorlevel 1 (
    echo Python launcher "py" was not found. Install Python 3.10+ from python.org.
    pause
    exit /b 1
)

echo Installing/updating build dependencies...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m pip install pyinstaller

echo Cleaning old build folders...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Running PyInstaller...
py -m PyInstaller IA_Batch_Downloader.spec --clean --noconfirm

if exist dist\IA_Batch_Downloader.exe (
    echo.
    echo Build complete: dist\IA_Batch_Downloader.exe
) else (
    echo.
    echo Build failed. Check the output above.
)

pause
