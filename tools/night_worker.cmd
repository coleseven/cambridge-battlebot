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
rem (Progress curves of the pre-patch arena runs n2v2 / n3v3 are off since the 20 Oct patch: with engine 1.0.0
rem nothing is cached and they would replay every snapshot. tools/progress.py still works by hand.)
set /a tries=0
:run
rem Mode switch: with .arena\night_real.flag present, the night runs real-map fighter training on this PC
rem instead of being a worker for the training PC. From 20 Oct (post-patch, engine 1.0.0): the learned director,
rem tools/director_train.py run d1 (bots/team-dirlearn = tools/team_bot.py --macro-src bots/director/src
rem --source neat:real1 --out team-dirlearn), fighters fixed. 21 Oct: run d2 (16 games/candidate/gen, half vs the rules,
rem lifetime fitness, d1's gen-100 network as warm start and as an opponent; the ladder's version 4).
rem 22 Oct: d2 drifted below its seed (winner's curse), so the night runs tools/director_climb.py run c1:
rem champion-gated hill climb from version 4 (paired screening, fresh-game confirmation before promotion).
rem 23 Oct: c1's 80 promotions drifted below version 4; run c2 needs a 2-standard-error lead on 64 fresh entries
rem and no deficit to version 4 (the anchor). Earlier: d1, real1 / real2 (tools/real_train.py).
if exist .arena\night_real.flag (
  .venv\Scripts\python -u tools\director_climb.py run c2 --seed-net .arena/director/hall/d1-gen0100.json --team team-dirlearn --fighters neat:real1 --lam 12 --games 16 --confirm 64 --margin 0.15 --z 2 --jobs 12 --until 11:50 --dist .arena/dist >> .arena\director-night.log 2>&1
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
