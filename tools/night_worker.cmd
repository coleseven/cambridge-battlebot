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
rem Progress curve first (~minutes; snapshots already scored are cached): new NEAT snapshots vs fixed opponents.
.venv\Scripts\python -u tools\progress.py n2v2 --jobs 12 >> .arena\progress-night.log 2>&1
.venv\Scripts\python -u tools\progress.py n3v3 --team-size 3 --warm n2v2 --cgp c2v2 --jobs 12 >> .arena\progress-night.log 2>&1
set /a tries=0
:run
rem Mode switch: with .arena\night_real.flag present, the night runs real-map fighter training on this PC
rem (tools/real_train.py, run real1) instead of being a worker for the training PC.
if exist .arena\night_real.flag (
  .venv\Scripts\python -u tools\real_train.py run real1 --warm-run n3v3 --pop 16 --games 6 --jobs 12 --until 11:50 >> .arena\real-night.log 2>&1
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
