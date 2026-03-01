@echo off
where uv >nul 2>&1
if errorlevel 1 (
    echo Error: uv is not installed. Run 0-install-prereqs.bat first.
    pause
    exit /b 1
)
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo Error: ffmpeg is not installed. Run 0-install-prereqs.bat first.
    pause
    exit /b 1
)
uv run --script "%~dp02-analyze-PAM-recordings.py"
pause
