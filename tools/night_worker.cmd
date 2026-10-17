@echo off
rem Night-window distributed worker for this PC (00:00-12:00). Started by the Windows scheduled task
rem "CAMBC night worker" at 00:00; stops claiming 10 minutes before 12:00, finishes its tasks, and exits.
rem Remove the schedule with:  schtasks /delete /tn "CAMBC night worker" /f
cd /d "%~dp0.."
if not exist .arena\dist mkdir .arena\dist
echo === %date% %time% night worker start >> .arena\dist-night.log
git pull -q --ff-only origin main >> .arena\dist-night.log 2>&1
.venv\Scripts\python -u tools\dist_worker.py --root .arena/dist --jobs 12 --name nightpc --until 12:00 --margin 600 >> .arena\dist-night.log 2>&1
echo === %date% %time% night worker exit %errorlevel% >> .arena\dist-night.log
