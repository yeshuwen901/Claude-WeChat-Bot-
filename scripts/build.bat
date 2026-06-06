@echo off
cd /d "%~dp0.."
setlocal enabledelayedexpansion

title Build WeChatBot

echo ================================================
echo   WeChatBot PyInstaller Build
echo ================================================
echo.

:: ── Check PyInstaller ─────────────────────────────────────────────────
python -c "import PyInstaller" >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo   Installing PyInstaller...
    python -m pip install pyinstaller --quiet
    if !ERRORLEVEL! neq 0 (
        echo   ERROR: Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

:: ── Set output paths ──────────────────────────────────────────────────
set "OUTDIR=D:\yu\项目"
set "EXENAME=clawbot"

:: Create output directory if not exists
if not exist "!OUTDIR!" mkdir "!OUTDIR!"

:: ── Clean previous build ──────────────────────────────────────────────
echo   [1/3] Cleaning previous build...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "!OUTDIR!\!EXENAME!.exe" del /q "!OUTDIR!\!EXENAME!.exe"
for /r %%f in (*.spec) do del /q "%%f" 2>nul

:: ── Build ─────────────────────────────────────────────────────────────
echo   [2/3] Building !EXENAME!.exe...
:: --clean: remove cache, --noconfirm: overwrite output
pyinstaller ^
    --onefile ^
    --name="!EXENAME!" ^
    --add-data "src\static;static" ^
    --clean ^
    --noconfirm ^
    --distpath "!OUTDIR!" ^
    --workpath "build\!EXENAME!" ^
    --specpath "build" ^
    src\main.py

if !ERRORLEVEL! neq 0 (
    echo.
    echo   ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

:: ── Clean build artifacts ─────────────────────────────────────────────
echo   [3/3] Cleaning build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
for /r %%f in (*.spec) do del /q "%%f" 2>nul

:: ── Verify output ─────────────────────────────────────────────────────
echo.
echo ================================================
if exist "!OUTDIR!\!EXENAME!.exe" (
    for %%A in ("!OUTDIR!\!EXENAME!.exe") do (
        set "SIZE=%%~zA"
        set /a "SIZEMB=!SIZE!/1048576"
    )
    echo   Build complete: !OUTDIR!\!EXENAME!.exe
    echo   Size: !SIZEMB! MB
) else (
    echo   ERROR: !EXENAME!.exe not found at !OUTDIR!
)
echo ================================================
echo.
pause
exit /b 0
