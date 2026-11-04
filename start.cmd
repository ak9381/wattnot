@echo off
rem WattNot on Windows: double-click or run  start.cmd [replay|check|keys|live|baseten|modal|gpu]
rem Runs start.ps1 with a process-only execution policy bypass (no admin, no system change).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
