@echo off
rem Double-click: guided GitHub setup (fieldkit github menu). Nothing changes on GitHub unless you type y.
cd /d "%~dp0.."
where python >nul 2>nul || (echo Python is not installed. Install it from https://www.python.org/downloads/ and try again. & pause & exit /b 1)
python -m fieldkit github menu
echo.
echo Done. Press any key to close this window.
pause >nul
