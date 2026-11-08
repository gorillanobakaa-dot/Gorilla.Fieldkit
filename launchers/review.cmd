@echo off
rem Opens a terminal and runs the one review: everything waiting on the owner, answered once.
cd /d "%~dp0.."
python -m fieldkit review
echo.
pause
