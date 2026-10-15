# Training-PC setup prompt

Paste everything below the line into Claude Code on the training PC.

---

You are setting up this Windows PC as a dedicated, always-on **training machine** for our Cambridge Battlecode 2026a bot. The main development happens on another PC (hostname `DESKTOP-MN3IVFC`, the "main PC"), which will control this machine over SSH and receive its results through Syncthing. Your job is setup only: don't change bot strategy code (`bots/main`, `bots/duel`, `tools/duel_train.py`, `tools/arena.py`, `tools/tune.py`). Ask me before anything that needs admin rights, and tell me exactly what to run when it's something only I can do. At the end, give me the short report described in step 9.

**1. Prerequisites.** Check for Git, the GitHub CLI (`gh`) and Python 3.12+. Install any that are missing with winget (`Git.Git`, `GitHub.cli`, `Python.Python.3.12`). If `gh` isn't signed in, ask me to run `gh auth login` myself (account `coleseven`). Don't handle my credentials.

**2. Repo.** Clone the private repo `coleseven/cambridge-battlebot` to `D:\cambridge battlebot` (or `C:\cambridge battlebot` if there's no D: drive). Work on `main`, but never commit or push from this machine except on branches named `trainer/...`. Antigravity (the other coding agent on this PC) uses `ag/...` branches. Neither of you pushes to `main`.

**3. Python environment and game toolkit.** In the repo root:
- `python -m venv .venv`
- `.venv\Scripts\python -m pip install --upgrade pip cambc cma numpy`
- `.venv\Scripts\cambc init --yes .` This downloads the WASI toolchain (about 600 MB) and pulls the maps. `cambc --version` should print 2.1.6.
- If `cambc init` modified tracked files, discard those changes with `git checkout -- .` so the checkout stays clean.

**4. Files that are git-ignored and must be recreated.**
- Sparring opponent: clone `https://github.com/FHL-08/Tarot-Club-Cambc2026` into a temporary folder, check out commit `9997f59`, and copy its `bots/foundry/src` folder to `bots/sparring-tarot/src` in our repo. Copy `bots/main/.gitignore` and `bots/main/.clangd` into `bots/sparring-tarot/` as well. Only copy that source folder; don't run anything from their repo.
- Duel maps: `.venv\Scripts\python tools\duel_maps.py --size 1 --out maps_duel/1v1`, then the same with `--size 2 --out maps_duel/2v2` and `--size 3 --out maps_duel/3v3`. The last two produce many more maps; that's expected.
- Tuning map sets: create `.arena\maps_tune` (copy of `maps\*.map26a` plus `maps_gen\gen1_00` to `gen1_10`) and `.arena\maps_holdout` (`maps_gen\gen1_11` to `gen1_39`).

**5. Smoke tests**, all from the repo root:
- `.venv\Scripts\cambc build main`, then `duel`, `v1` and `sparring-tarot`. All must build.
- `.venv\Scripts\python tools\eval.py v1 --vs starter-cpp sparring-tarot --jobs 4`. Expect v1 to win almost every game against the starter and roughly 15/18 against sparring-tarot.
- `.venv\Scripts\python tools\duel_train.py matrix prior --jobs 4`. Should finish with a matchup table.

**6. Throughput calibration.** Report the CPU model, physical and logical core counts, and RAM. Then time `tools\eval.py v1 --vs starter-cpp sparring-tarot` at `--jobs` values from about half to the full logical core count. Write the fastest value to `.arena\machine.json` as `{"jobs": N, "cpu": "...", "cores": C, "threads": T, "ram_gb": R}`. On the main PC, hyperthreads didn't help past roughly 75% of logical cores.

**7. SSH access for the main PC.**
- Ask me to run these in an **admin** PowerShell:
  - `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`
  - `Start-Service sshd; Set-Service -Name sshd -StartupType Automatic`
- Authorise this public key for key-only login. It belongs to the main PC.
  - If my Windows account is an administrator, the key goes in `C:\ProgramData\ssh\administrators_authorized_keys`, with permissions fixed by `icacls "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"`. That needs admin, so ask me.
  - Otherwise it goes in `%USERPROFILE%\.ssh\authorized_keys`.
  ```
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFdrPPtgK+fc/Ah1Mfm5PPfsxYX/mXJCOooJcGa0HWnf main-pc-claude-cambc
  ```
- Check that the Windows firewall allows inbound TCP 22 on the private network profile. If a rule is missing, tell me the command; don't change firewall settings yourself.
- Report this PC's hostname, Windows username and LAN IPv4 address (`ipconfig`).

**8. Always-on services.**
- **Syncthing:** install it with winget (`Syncthing.Syncthing`) and start it. I'll pair the two PCs in its web UI (http://127.0.0.1:8384), so tell me this PC's device ID. Set up a shared folder for the repo's `.arena` directory, and create `.arena\.stignore` with these lines so only results sync, not build output:
  ```
  duelbots
  bots
  tmp
  dbg
  probe
  duel_test
  jobs_test
  ```
  Each PC writes its own results cache (`results-<hostname>.jsonl`), and run names from this PC should start with `t-`, so the two machines never write the same file.
- **Job runner:** test `.venv\Scripts\python tools\jobrunner.py --root .arena/jobs`. Drop a job file `{"name":"hello","cmd":"echo hi"}` into `.arena\jobs\queue\` and check it ends up in `done\`.
  - Then register a Windows scheduled task, "CAMBC job runner", that starts at logon (with an option to restart on failure). It should run:
    ```
    .venv\Scripts\python tools\jobrunner.py --root .arena/jobs --on-commit ".venv\Scripts\python tools\eval.py main --vs starter-cpp sparring-tarot v1 --maps .arena/maps_holdout"
    ```
    from the repo root, writing its own output to `.arena\jobs\runner.log`. Show me the task definition before creating it.
- **Things I'll do myself:** remind me to add Windows Defender exclusions for the repo folder and `%LOCALAPPDATA%\cambc`, and to set the power plan so the PC never sleeps. Don't change security or power settings yourself.

**9. Final report:** hostname, username, LAN IP, CPU, cores and threads, RAM, chosen jobs value, Syncthing device ID, smoke-test results (with the eval summary lines), and anything that failed or still needs me.
