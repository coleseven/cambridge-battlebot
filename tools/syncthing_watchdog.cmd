@echo off
rem Syncthing watchdog (scheduled task "CAMBC Syncthing watchdog", every 15 minutes): start Syncthing if it is not
rem running. Syncthing carries the distributed-training queue (.arena/dist) to the training PC; on 19, 20 and 21 Oct
rem Windows' hang detector closed it on this PC mid-run and the training PC sat idle for the rest of the night.
rem Remove with:  schtasks /delete /tn "CAMBC Syncthing watchdog" /f
tasklist /fi "imagename eq syncthing.exe" | find /i "syncthing.exe" >nul && exit /b 0
start "" /min "%LOCALAPPDATA%\Microsoft\WinGet\Links\syncthing.exe" serve --no-browser --no-console
echo %date% %time% watchdog started syncthing >> "%~dp0..\.arena\syncthing-watchdog.log"
