@echo off
cd /d "%~dp0.."
setlocal enabledelayedexpansion

title WeChatBot Updater
echo ================================================
echo   WeChatBot Update Tool
echo ================================================
echo.

:: ── Read new version ───────────────────────────────────────────────
set "NEW_VER=unknown"
if exist "VERSION" (
    set /p NEW_VER=<"VERSION"
    echo   New version: !NEW_VER!
) else (
    echo   WARNING: VERSION file not found in this package.
    echo   If this is a fresh install, use setup.bat instead.
)
echo.

:: ── Detect / ask for old installation ──────────────────────────────
set "OLD_PATH="

:: Auto-detect: check common locations
for %%d in (
    "D:\yu\AI陪聊\AI陪聊"
    "C:\WeChatBot"
    "%USERPROFILE%\WeChatBot"
    "%USERPROFILE%\Desktop\WeChatBot"
) do (
    if exist "%%~d\data\conversations.db" (
        set "OLD_PATH=%%~d"
    )
)

if not "!OLD_PATH!"=="" (
    echo   Detected existing installation at:
    echo   !OLD_PATH!
    echo.
    choice /c yn /m "Is this correct"
    if !ERRORLEVEL! equ 2 set "OLD_PATH="
    echo.
)

if "!OLD_PATH!"=="" (
    echo   Please enter the path to your current bot installation:
    echo   ^(the folder that contains start.bat and the data\ folder^)
    echo.
    set /p OLD_PATH="  Path: "
    echo.
    if not exist "!OLD_PATH!\data\conversations.db" (
        echo   ERROR: data\conversations.db not found at that path.
        echo   Make sure you entered the correct bot installation folder.
        pause
        exit /b 1
    )
)

:: Remove trailing backslash
if "!OLD_PATH:~-1!"=="\" set "OLD_PATH=!OLD_PATH:~0,-1!"

:: ── Read old version ───────────────────────────────────────────────
set "OLD_VER=unknown"
if exist "!OLD_PATH!\VERSION" (
    set /p OLD_VER=<"!OLD_PATH!\VERSION"
)

:: ── Confirm update ─────────────────────────────────────────────────
echo   ==============================================
echo     Update Summary
echo   ==============================================
echo     From: !OLD_VER!  at  !OLD_PATH!
echo     To:   !NEW_VER!
echo   ==============================================
echo.
echo   This will:
echo     1. Back up your current data ^(database, config, accounts^)
echo     2. Replace code files with the new version
echo     3. Restore your data and settings
echo.
choice /c yn /m "Continue with update"
if !ERRORLEVEL! equ 2 exit /b 0
echo.

:: ── Step 1: Backup old data ────────────────────────────────────────
echo   [1/4] Backing up your data...
set "BACKUP_DIR=!OLD_PATH!\backup_!OLD_VER!_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "BACKUP_DIR=!BACKUP_DIR: =0!"
mkdir "!BACKUP_DIR!\data" 2>nul

for %%f in (
    "conversations.db"
    ".keyfile"
    "wechat_account.json"
) do (
    if exist "!OLD_PATH!\data\%%~f" (
        copy /y "!OLD_PATH!\data\%%~f" "!BACKUP_DIR!\data\" >nul
        echo     Saved: data\%%~f
    )
)
if exist "!OLD_PATH!\.env" (
    copy /y "!OLD_PATH!\.env" "!BACKUP_DIR!\" >nul
    echo     Saved: .env
)
:: Backup custom prompts
if exist "!OLD_PATH!\data\*.txt" (
    for %%f in ("!OLD_PATH!\data\*.txt") do (
        copy /y "%%f" "!BACKUP_DIR!\data\" >nul 2>&1
    )
)
:: Backup sticker data
if exist "!OLD_PATH!\data\stickers" (
    xcopy /e /i /q /y "!OLD_PATH!\data\stickers" "!BACKUP_DIR!\data\stickers" >nul 2>&1
)
echo     Backup saved to: !BACKUP_DIR!
echo.

:: ── Step 2: Stop running bot ───────────────────────────────────────
echo   [2/4] Stopping any running bot instance...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8080.*LISTENING"') do (
    taskkill /f /pid %%a 2>nul
    echo     Stopped process PID %%a
)
echo.

:: ── Step 3: Copy new code files ────────────────────────────────────
echo   [3/4] Installing new version...
:: Copy source
robocopy "src" "!OLD_PATH!\src" /e /njh /njs /np /ndl >nul 2>&1
:: Copy root files
for %%f in (
    "start.bat" "start.sh" "setup.bat" "setup.sh" "update.bat"
    "requirements.txt" "generate_stickers.py"
    "README.md" "REQUIREMENTS.md" "使用说明书.txt"
    "VERSION"
) do (
    if exist "%%~f" (
        copy /y "%%~f" "!OLD_PATH!\" >nul
    )
)
:: Copy data docs (but not the database)
for %%f in ("ISSUES.md" "REQUIREMENTS_REMINDER.md" "REQUIREMENTS_WEB_SEARCH.md") do (
    if exist "data\%%~f" (
        copy /y "data\%%~f" "!OLD_PATH!\data\" >nul
    )
)
echo.

:: ── Step 4: Install/update dependencies ────────────────────────────
echo   [4/4] Checking Python dependencies...

set "PYCMD="
if exist "!OLD_PATH!\.venv\Scripts\python.exe" (
    set "PYCMD=!OLD_PATH!\.venv\Scripts\python.exe"
) else (
    python --version >nul 2>&1
    if !ERRORLEVEL! equ 0 set "PYCMD=python"
)
if "!PYCMD!"=="" (
    echo   WARNING: Python not found. Run setup.bat to install dependencies.
) else (
    echo   Updating dependencies via pip...
    "!PYCMD!" -m pip install -r "!OLD_PATH!\requirements.txt" --quiet 2>&1
    echo   Dependencies updated.
)

echo.

:: ── Done ───────────────────────────────────────────────────────────
echo ================================================
echo   Update complete!
echo   !OLD_VER! --^> !NEW_VER!
echo ================================================
echo.
echo   Your data has been preserved.
echo   Backup saved to: !BACKUP_DIR!
echo.
echo   Run start.bat to launch the updated bot.
echo.
pause
exit /b 0
