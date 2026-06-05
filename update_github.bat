@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

title WeChatBot - Update from GitHub
echo ================================================
echo   Update from GitHub
echo ================================================
echo.

:: ── CONFIGURE THIS ──────────────────────────────────────────────────
set "GITHUB_REPO=https://github.com/YOUR_USERNAME/YOUR_REPO.git"
set "GITHUB_BRANCH=main"
:: ────────────────────────────────────────────────────────────────────

:: ── Check git ───────────────────────────────────────────────────────
git --version >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo   ERROR: Git is not installed or not in PATH.
    echo   Install Git from: https://git-scm.com/download/win
    echo   Or download the latest ZIP manually from:
    echo   %GITHUB_REPO%
    pause
    exit /b 1
)

:: ── Read current version ────────────────────────────────────────────
set "OLD_VER=unknown"
if exist "%~dp0VERSION" (
    set /p OLD_VER=<"%~dp0VERSION"
)
echo   Current version: !OLD_VER!
echo.

:: ── Clone latest to temp ────────────────────────────────────────────
set "TEMP_CLONE=%TEMP%\wechatbot_update_%RANDOM%"
echo   Fetching latest version from GitHub...
echo   Repo: %GITHUB_REPO%
echo   Branch: %GITHUB_BRANCH%
echo.

git clone --depth 1 --branch %GITHUB_BRANCH% "%GITHUB_REPO%" "%TEMP_CLONE%" 2>&1
if !ERRORLEVEL! neq 0 (
    echo.
    echo   ERROR: Failed to clone repository.
    echo   Check your internet connection and the repo URL.
    rmdir /s /q "%TEMP_CLONE%" 2>nul
    pause
    exit /b 1
)
echo   Done.
echo.

:: ── Read new version ────────────────────────────────────────────────
set "NEW_VER=unknown"
if exist "%TEMP_CLONE%\VERSION" (
    set /p NEW_VER=<"%TEMP_CLONE%\VERSION"
)
echo   Latest version: !NEW_VER!

if "!OLD_VER!"=="!NEW_VER!" (
    echo.
    echo   Already up to date!
    rmdir /s /q "%TEMP_CLONE%" 2>nul
    pause
    exit /b 0
)
echo.

:: ── Confirm update ──────────────────────────────────────────────────
echo   ==============================================
echo     Update: !OLD_VER! --^> !NEW_VER!
echo   ==============================================
echo.
echo   This will:
echo     1. Back up your data ^(.env, database, configs^)
echo     2. Pull latest code from GitHub
echo     3. Replace code files, keep your data intact
echo     4. Update Python dependencies
echo.
choice /c yn /m "Continue with update"
if !ERRORLEVEL! equ 2 (
    rmdir /s /q "%TEMP_CLONE%" 2>nul
    exit /b 0
)
echo.

:: ── Step 1: Backup user data ────────────────────────────────────────
echo   [1/5] Backing up your data...

set "TIMESTAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "TIMESTAMP=!TIMESTAMP: =0!"
set "BACKUP_DIR=%~dp0backup_!OLD_VER!_!TIMESTAMP!"
mkdir "!BACKUP_DIR!\data" 2>nul

if exist "%~dp0data\conversations.db" (
    copy /y "%~dp0data\conversations.db" "!BACKUP_DIR!\data\" >nul
    echo     Saved: data\conversations.db
)
if exist "%~dp0data\.keyfile" (
    copy /y "%~dp0data\.keyfile" "!BACKUP_DIR!\data\" >nul
    echo     Saved: data\.keyfile
)
if exist "%~dp0data\wechat_account.json" (
    copy /y "%~dp0data\wechat_account.json" "!BACKUP_DIR!\data\" >nul
    echo     Saved: data\wechat_account.json
)
if exist "%~dp0.env" (
    copy /y "%~dp0.env" "!BACKUP_DIR!\" >nul
    echo     Saved: .env
)
if exist "%~dp0data\*.json" (
    for %%f in ("%~dp0data\*.json") do (
        copy /y "%%f" "!BACKUP_DIR!\data\" >nul 2>&1
    )
)
if exist "%~dp0data\*.md" (
    for %%f in ("%~dp0data\*.md") do (
        copy /y "%%f" "!BACKUP_DIR!\data\" >nul 2>&1
    )
)
if exist "%~dp0data\stickers" (
    xcopy /e /i /q /y "%~dp0data\stickers" "!BACKUP_DIR!\data\stickers" >nul 2>&1
)
echo     Backup saved to: !BACKUP_DIR!
echo.

:: ── Step 2: Stop running bot ────────────────────────────────────────
echo   [2/5] Stopping any running bot instance...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8080.*LISTENING"') do (
    taskkill /f /pid %%a 2>nul
    echo     Stopped process PID %%a
)
echo.

:: ── Step 3: Replace code files ──────────────────────────────────────
echo   [3/5] Installing new version...

:: Sync src/ directory (only new/changed files, skip data files)
robocopy "%TEMP_CLONE%\src" "%~dp0src" /e /njh /njs /np /ndl /xd __pycache__ >nul 2>&1

:: Copy root files (but NEVER .env from repo — user's .env is sacred)
for %%f in (
    "start.bat" "start.sh" "setup.bat" "setup.sh"
    "update.bat" "update_github.bat"
    "requirements.txt" "generate_stickers.py"
    "README.md" "REQUIREMENTS.md" "使用说明书.txt"
    "VERSION"
) do (
    if exist "%TEMP_CLONE%\%%~f" (
        copy /y "%TEMP_CLONE%\%%~f" "%~dp0\" >nul
    )
)

:: Copy data/ documentation files (but NOT database, keyfile, accounts, prompts)
for %%f in ("ISSUES.md" "REQUIREMENTS_REMINDER.md" "REQUIREMENTS_WEB_SEARCH.md" "REQUIREMENTS_CHAT_PROTECTION.md") do (
    if exist "%TEMP_CLONE%\data\%%~f" (
        copy /y "%TEMP_CLONE%\data\%%~f" "%~dp0data\" >nul
    )
)
echo.

:: ── Step 4: Cleanup temp clone ──────────────────────────────────────
echo   [4/5] Cleaning up...
rmdir /s /q "%TEMP_CLONE%" 2>nul
echo.

:: ── Step 5: Update dependencies ─────────────────────────────────────
echo   [5/5] Checking Python dependencies...

set "PYCMD="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYCMD=%~dp0.venv\Scripts\python.exe"
) else (
    python --version >nul 2>&1
    if !ERRORLEVEL! equ 0 set "PYCMD=python"
)
if "!PYCMD!"=="" (
    echo   WARNING: Python not found. Run setup.bat to install dependencies.
) else (
    echo   Updating dependencies via pip...
    "!PYCMD!" -m pip install -r "%~dp0requirements.txt" --quiet 2>&1
    echo   Dependencies updated.
)
echo.

:: ── Done ────────────────────────────────────────────────────────────
echo ================================================
echo   Update complete!
echo   !OLD_VER! --^> !NEW_VER!
echo ================================================
echo.
echo   Your data has been preserved:
echo     - .env ^(API keys^)
echo     - data\conversations.db ^(chat history^)
echo     - data\wechat_account.json ^(login state^)
echo     - data\character_*.json ^(custom prompts^)
echo     - data\stickers\ ^(custom stickers^)
echo.
echo   Backup saved to: !BACKUP_DIR!
echo.
echo   Run start.bat to launch the updated bot.
echo.
pause
exit /b 0
