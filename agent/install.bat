@echo off
REM ─────────────────────────────────────────────────────────
REM  Fetchr Agent — Windows installer
REM ─────────────────────────────────────────────────────────
setlocal

set SCRIPT_DIR=%~dp0
set VENV_DIR=%SCRIPT_DIR%.venv

echo.
echo  ╔══════════════════════════════════════╗
echo  ║     Fetchr Agent Installer (Win)     ║
echo  ╚══════════════════════════════════════╝
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] python not found. Install Python 3.10+ and retry.
  pause
  exit /b 1
)

REM ── Install aria2c (primary download engine) ──────────────
echo Checking for aria2c...
where aria2c >nul 2>&1
if errorlevel 1 (
  echo aria2c not found. Installing via winget...
  winget install --id=aria2.aria2 -e --silent
  REM Refresh PATH so aria2c is visible immediately
  for /f "tokens=*" %%i in ('where aria2c 2^>nul') do set ARIA2C_PATH=%%i
  if defined ARIA2C_PATH (
    echo aria2c installed successfully.
  ) else (
    echo [WARN] aria2c could not be installed automatically.
    echo        Download it from https://github.com/aria2/aria2/releases
    echo        and place aria2c.exe in your PATH.
    echo        Fetchr will fall back to yt-dlp without aria2c.
  )
) else (
  echo aria2c already installed.
)

REM ── Install ffmpeg (needed for video+audio merging) ───────
echo Checking for ffmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo ffmpeg not found. Installing via winget...
  winget install --id=Gyan.FFmpeg -e --silent
  where ffmpeg >nul 2>&1
  if errorlevel 1 (
    echo [WARN] ffmpeg could not be installed automatically.
    echo        Download it from https://ffmpeg.org/download.html
    echo        and place ffmpeg.exe in your PATH.
  ) else (
    echo ffmpeg installed successfully.
  )
) else (
  echo ffmpeg already installed.
)

REM Create venv
if not exist "%VENV_DIR%" (
  echo Creating virtual environment...
  python -m venv "%VENV_DIR%"
)

call "%VENV_DIR%\Scripts\activate.bat"

REM Install deps
echo Installing Python packages...
pip install --upgrade pip --quiet
pip install -r "%SCRIPT_DIR%requirements.txt" --quiet
echo Dependencies installed.

REM Generate icons
echo Generating extension icons...
python "%SCRIPT_DIR%generate_icons.py"

REM Create a startup shortcut via Task Scheduler
set TASK_NAME=FetchrAgent
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if errorlevel 1 (
  schtasks /create /tn "%TASK_NAME%" /tr "\"%VENV_DIR%\Scripts\python.exe\" \"%SCRIPT_DIR%main.py\"" /sc ONLOGON /ru %USERNAME% /f >nul
  echo Auto-start task registered: %TASK_NAME%
) else (
  echo Auto-start task already exists: %TASK_NAME%
)

REM Start agent now
echo Starting Fetchr agent...
start "" /B "%VENV_DIR%\Scripts\python.exe" "%SCRIPT_DIR%main.py"

echo.
echo ════════════════════════════════════════
echo   Fetchr agent running at:
echo   http://127.0.0.1:9876
echo.
echo   Next: load the extension\ folder in
echo   Chrome -^> chrome://extensions (dev mode)
echo ════════════════════════════════════════
echo.
pause
