@echo off
REM ============================================================
REM  Who is the Undercover (Project-17) - one-click start.
REM  Starts the local server on port 8092.
REM ============================================================
cd /d "%~dp0"
echo Starting Undercover server on port 8092 ...
py server.py
pause
