@echo off
echo Installing uv and ffmpeg...
winget install uv ffmpeg
echo.
echo Populating uv cache (this may take a while on first run)...
uv run --script "%~dp01-import-pam-recordings.py" --version
uv run --script "%~dp02-analyze-pam-recordings.py" --version
uv run --script "%~dp03-extract-top-detections.py" --version
echo Done.
pause
