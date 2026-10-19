@echo off
rem Night-window distributed worker for this PC (00:00-12:00). Started by the Windows scheduled task
rem "CAMBC night worker" at 00:00; stops claiming 10 minutes before 12:00, finishes its tasks, and exits.
rem --adopt-base: this PC builds a byte-different wasm from the same source (install path), so play with the
rem coordinator's published build to keep results identical to the trainer's.
rem Restarts the worker if it dies (18 Oct: python314.dll access violation at 01:58 lost the rest of the night).
rem Remove the schedule with:  schtasks /delete /tn "CAMBC night worker" /f
rem Runs from a temp copy: the `git pull` below may rewrite this file, and cmd reads batch files as it goes.
if not "%~1"=="--run" (
  copy /y "%~f0" "%TEMP%\cambc_night_worker.cmd" >nul
  "%TEMP%\cambc_night_worker.cmd" --run "%~dp0.."
  exit /b
)
cd /d "%~2"
if not exist .arena\dist mkdir .arena\dist
echo === %date% %time% night worker start >> .arena\dist-night.log
git pull -q --ff-only origin main >> .arena\dist-night.log 2>&1
rem Syncthing carries the dist queue to the training PC; it was found not running on 19 Oct (the night's
rem claims could not sync and real1 hung). Start it if it is not running.
tasklist /fi "imagename eq syncthing.exe" | find /i "syncthing.exe" >nul || start "" /b "%LOCALAPPDATA%\Microsoft\WinGet\Links\syncthing.exe" serve --no-browser
rem Progress curve first (~minutes; snapshots already scored are cached): new NEAT snapshots vs fixed opponents.
.venv\Scripts\python -u tools\progress.py n2v2 --jobs 12 >> .arena\progress-night.log 2>&1
.venv\Scripts\python -u tools\progress.py n3v3 --team-size 3 --warm n2v2 --cgp c2v2 --jobs 12 >> .arena\progress-night.log 2>&1
set /a tries=0
:run
rem Mode switch: with .arena\night_real.flag present, the night runs real-map fighter training on this PC
rem (tools/real_train.py) instead of being a worker for the training PC. From 19 Oct: run real2, the fighters
rem trained inside the director (bots/team-director-train = tools/team_bot.py --macro-src bots/director/src
rem --set NN_HEROES=4095 --runtime-params tools/space-director.json), warm-started from real1, with the director's
rem params CMA-ES'd on the same games (--cma; docs/DIRECTOR.md).
if exist .arena\night_real.flag (
  .venv\Scripts\python -u tools\real_train.py run real2 --warm-run real1 --team team-director-train --cma tools/space-director.json --pop 16 --games 6 --jobs 12 --until 11:50 --dist .arena/dist >> .arena\real-night.log 2>&1
) else (
  .venv\Scripts\python -u tools\dist_worker.py --root .arena/dist --jobs 12 --name nightpc --adopt-base --until 12:00 --margin 600 >> .arena\dist-night.log 2>&1
)
set code=%errorlevel%
echo === %date% %time% night worker exit %code% >> .arena\dist-night.log
if "%code%"=="0" goto done
set /a tries+=1
if %tries% geq 20 goto done
ping -n 31 127.0.0.1 >nul
goto run
:done
