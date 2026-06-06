@echo off
cd /d "%~dp0.."
setlocal enabledelayedexpansion
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

:: ── Port Conflict Detection ────────────────────────────────────────
netstat -ano 2>nul | findstr ":8080.*LISTENING" >nul
if !ERRORLEVEL! equ 0 (
    echo ================================================
    echo   WARNING: Port 8080 is already in use!
    echo   Another bot instance may be running.
    echo ================================================
    echo.
    choice /c yn /m "Kill existing instance and continue"
    if !ERRORLEVEL! equ 2 exit /b 1
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING"') do (
        taskkill /f /pid %%a 2>nul
    )
    timeout /t 2 /nobreak >nul
    echo Port 8080 released.
    echo.
)

:: ── Python Detection and Auto-Install ──────────────────────────────
set "VERFILE=%TEMP%\pyver_check_%RANDOM%.txt"
set PYCMD=

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
    if exist "%~1" (
        "%~1" -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" > "%VERFILE%" 2>&1
        if !ERRORLEVEL! equ 0 (
            set /p PYVER=<"%VERFILE%"
            if !PYVER! GEQ 311 set PYCMD=%~1
        )
    )
    exit /b

:helpers_end

:: Prefer .venv if available
if exist .venv\Scripts\python.exe (
    set PYCMD=.venv\Scripts\python.exe
    goto :found
)

:: Try common commands
call :try_python "python"
if "!PYCMD!"=="" call :try_python "py -3.11"
if "!PYCMD!"=="" call :try_python "python3"

:: Try known install locations
if "!PYCMD!"=="" call :try_python_path "%LocalAppData%\Programs\Python\Python311\python.exe"
if "!PYCMD!"=="" call :try_python_path "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe"
if "!PYCMD!"=="" call :try_python_path "%ProgramFiles%\Python311\python.exe"
if "!PYCMD!"=="" call :try_python_path "C:\Python311\python.exe"

:: Auto-install bundled Python
if "!PYCMD!"=="" (
    if exist "python-3.11.9-amd64.exe" (
        echo.
        echo Python 3.11+ not found. Installing from bundled installer...
        echo This may take 1-2 minutes. Please wait...
        echo.
        "python-3.11.9-amd64.exe" /passive InstallAllUsers=0 PrependPath=1 Include_test=0 2>&1
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
        )
    )
)

del "%VERFILE%" 2>nul

if "!PYCMD!"=="" (
    echo ERROR: Python 3.11+ not found.
    echo Please run setup.bat first, or manually install:
    echo   python-3.11.9-amd64.exe
    pause
    exit /b 1
)

:found
echo ================================================
echo   Claude WeChat Bot
echo ================================================
echo.
echo   Using Python: !PYCMD!
echo   Admin panel:  http://localhost:8080
echo.

:restart
!PYCMD! -X utf8 main.py
set EXITCODE=%ERRORLEVEL%
if %EXITCODE% equ 42 (
    echo.
    echo Scheduled restart triggered. Relaunching...
    echo.
    goto restart
)
pause
exit /b %EXITCODE%
