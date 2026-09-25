@echo off
rem Runs the daily routine (called by the Task Scheduler). Log in logs\reminders.log.

cd /d "%~dp0.."
if not exist logs mkdir logs
echo. >> logs\reminders.log
echo ===== %date% %time% ===== >> logs\reminders.log
uv run settlement-reminders >> logs\reminders.log 2>&1
