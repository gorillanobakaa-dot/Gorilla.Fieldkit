@echo off
rem Opens the inbox page (state\INBOX.md): what waits on you, why, the exact command, and what agents handled.
cd /d "%~dp0.."
python -m fieldkit review --inbox
