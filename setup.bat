@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo.
echo ================================================
echo   Claude WeChat Bot - Setup
echo ================================================
echo.

:: ── Python Detection and Auto-Install ────────────────────────────
echo [1/4] Checking Python 3.11+ ...

set "VERFILE=%TEMP%\pyver_check_%RANDOM%.txt"
set PYCMD=

:: ── Helper: test a python command and set PYCMD if version >= 311
goto :helpers_end

:try_python
    %~1 --version >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        %~1 -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" > "%VERFILE%" 2>&1
        set /p PYVER=<"%VERFILE%"
        if !PYVER! GEQ 311 set PYCMD=%~1
    )
    exit /b

:try_python_path
    :: Same as try_python but arg is a quoted path to python.exe
    if exist "%~1" (
        "%~1" -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" > "%VERFILE%" 2>&1
        if !ERRORLEVEL! equ 0 (
            set /p PYVER=<"%VERFILE%"
            if !PYVER! GEQ 311 set PYCMD=%~1
        )
    )
    exit /b

:helpers_end

:: Phase 1 - Try common commands
call :try_python "python"
if "!PYCMD!"=="" call :try_python "py -3.11"
if "!PYCMD!"=="" call :try_python "python3"

:: Phase 2 - Try known install locations
if "!PYCMD!"=="" call :try_python_path "%LocalAppData%\Programs\Python\Python311\python.exe"
if "!PYCMD!"=="" call :try_python_path "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe"
if "!PYCMD!"=="" call :try_python_path "%ProgramFiles%\Python311\python.exe"
if "!PYCMD!"=="" call :try_python_path "C:\Python311\python.exe"

:: Phase 3 - Auto-install bundled Python if still not found
if "!PYCMD!"=="" (
    if exist "%~dp0python-3.11.9-amd64.exe" (
        echo.
        echo Python 3.11+ not found. Installing from bundled installer...
        echo This may take 1-2 minutes. Please wait...
        echo.
        "%~dp0python-3.11.9-amd64.exe" /passive InstallAllUsers=0 PrependPath=1 Include_test=0 2>&1
        echo.
        if !ERRORLEVEL! equ 0 (
            echo Python 3.11.9 installation complete.
            echo.
            call :try_python "python"
            if "!PYCMD!"=="" call :try_python "py -3.11"
            if "!PYCMD!"=="" call :try_python_path "%LocalAppData%\Programs\Python\Python311\python.exe"
            if "!PYCMD!"=="" call :try_python_path "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe"
            if "!PYCMD!"=="" call :try_python_path "%ProgramFiles%\Python311\python.exe"
            if "!PYCMD!"=="" call :try_python_path "C:\Python311\python.exe"
        ) else (
            echo WARNING: Installer exited with code !ERRORLEVEL!.
            echo If installation failed, try manually running:
            echo   python-3.11.9-amd64.exe
        )
    ) else (
        echo Bundled installer python-3.11.9-amd64.exe not found in project directory.
    )
)

del "%VERFILE%" 2>nul

if "!PYCMD!"=="" (
    echo.
    echo ================================================
    echo   ERROR: Python 3.11+ not found or not installed.
    echo.
    echo   Please manually run: python-3.11.9-amd64.exe
    echo   Make sure to check "Add Python to PATH"
    echo   during installation.
    echo ================================================
    pause
    exit /b 1
)

echo   Python found: !PYCMD!
echo   Version:
!PYCMD! --version

:: ── Virtual Environment ──────────────────────────────────────────
echo.
echo [2/4] Creating virtual environment...
if exist .venv (
    echo   .venv already exists, skipping.
) else (
    !PYCMD! -m venv .venv
    if !ERRORLEVEL! neq 0 (
        echo   ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo   Virtual environment created.
)

:: ── Install Dependencies ─────────────────────────────────────────
call .venv\Scripts\activate.bat
echo.
echo [3/4] Installing dependencies...
pip install -r requirements.txt -q
if !ERRORLEVEL! neq 0 (
    echo   WARNING: Some dependencies may have failed to install.
    echo   You can retry later with: pip install -r requirements.txt
) else (
    echo   Dependencies installed.
)

:: ── Configuration ────────────────────────────────────────────────
echo.
echo [4/4] Setting up configuration...
if not exist .env (
    copy .env.example .env >nul
    echo   Created .env from .env.example
    echo   IMPORTANT: Edit .env to set your ANTHROPIC_API_KEY
    echo             or configure it in the admin panel later.
) else (
    echo   .env already exists, skipping.
)

:: Create data directories and sample stickers
if not exist data mkdir data

:: Try to run sticker generation (Pillow needed)
.venv\Scripts\python.exe generate_stickers.py 2>nul
if !ERRORLEVEL! neq 0 (
    echo   Sticker generation skipped - image library not available
    for %%e in (happy sad angry love surprised neutral) do (
        if not exist data\stickers\%%e mkdir data\stickers\%%e
    )
)

:: ── Done ─────────────────────────────────────────────────────────
echo.
echo ================================================
echo   Setup complete!
echo.
echo   Next:
echo   1. Run: start.bat
echo   2. Open http://localhost:8080
echo   3. Configure API Key in the admin panel
echo   4. Click "Generate QR Code" to log in
echo ================================================
echo.

pause
exit /b 0
