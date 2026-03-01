@echo off
where uv >nul 2>&1
if errorlevel 1 (
    echo Error: uv is not installed. Run 0-install-prereqs.bat first.
    pause
    exit /b 1
)
uv run --script "%~dp01-import-pam-recordings.py"
pause
