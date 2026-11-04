# Running the GPS-loss failsafe test in SITL (Windows + WSL2)

This guide has three parts:

- **Part A: First-time setup.** Done once, takes about 30-45 minutes.
- **Part B: Your first test run.** Includes the one-time SITL restart.
- **Part C: Every time after that.** Opening it again after you've closed everything, and the edit, run, look loop.

If you're new to Ubuntu or terminals, read **Part 0** first.

---

## Part 0: Ubuntu and terminal basics

### 0.1 Opening Ubuntu

Once WSL is installed (A1) and you've rebooted, any of these opens an Ubuntu terminal:

- **Start menu:** press the **Windows key**, type **Ubuntu**, and press **Enter**.
- **Win+R:** press **Windows key + R**, type `wsl`, and press **Enter**.
- **Windows Terminal:** click the **˅** arrow next to the tab and pick **Ubuntu-24.04**.
- **From PowerShell or Command Prompt:** type `wsl -d Ubuntu-24.04` (or just `wsl`).

You're in Ubuntu when the prompt looks like `yourname@YOUR-PC:~$`.

If "Ubuntu" isn't in the Start menu, run `wsl -l -v` in PowerShell. If nothing is listed, install it with `wsl --install -d Ubuntu-24.04` and reboot.

### 0.2 The first start: creating your Linux user

The very first time Ubuntu opens, it takes a minute to set up, then asks:

1. **`Enter new UNIX username:`** Type a short name in lowercase, with no spaces (for example `romet`), and press Enter.
2. **`New password:`** Type a password and press Enter. **Nothing appears while you type, not even dots.** That's normal.
3. **`Retype new password:`** Type the same password again.

When you see `Installation successful!` and a prompt with your name, you're done. This is your Linux login, separate from Windows. You'll need the password whenever a command asks `[sudo] password for ...`.

**If that went wrong** (the window closed, or the prompt says `root@...` instead of your name), create the user yourself. In Ubuntu, run:

```bash
adduser romet
usermod -aG sudo romet
```

Then, in **PowerShell**:

```
ubuntu2404.exe config --default-user romet
```

Close Ubuntu and open it again. Use your own name in place of `romet` in all three commands.

### 0.3 You need TWO Ubuntu windows

SITL keeps one terminal busy the whole time it runs, so the test runs in a **second** Ubuntu window:

| Window | What runs in it | Notes |
|---|---|---|
| **Terminal 1: "SITL"** | `sitl`, then the MAVProxy prompt (`MANUAL>`) | **Don't close it** while testing; closing it stops the simulator |
| **Terminal 2: "TEST"** | `silmtest ...`, `git`, `nano`, `explorer.exe` | Use it for everything else |

**There's no second terminal until you open one.** Open Ubuntu again, the same way as in 0.1:

- the **Start menu** again (Windows key, type Ubuntu, Enter) gives a second, separate window,
- or **Win+R**, then `wsl`, then Enter,
- or, if your window has tabs at the top (Windows Terminal), press **Ctrl+Shift+T**, or click **˅** and choose **Ubuntu-24.04**. If it has no tabs, use one of the first two.

Both windows are the same Ubuntu with the same files. To check the new one:

```bash
cd ~/uav-silm/onboard
```

The prompt should change to something like `romet@YOUR-PC:~/uav-silm/onboard$`.

### 0.4 Which window do I type into?

After `sitl` starts, you'll have up to three windows:

| Window | What it is | Type in it? |
|---|---|---|
| **Ubuntu Terminal 1** | Scrolling messages, then a `MANUAL>` prompt (the MAVProxy prompt) | **Yes.** `param set ...` commands go here |
| **Console** (grey window with status lines: mode, GPS, battery) | Opened by `--console` | **No**, only for watching |
| **Map** (only if you added `--map`) | The plane on a map | **No**, only for watching |
| **Ubuntu Terminal 2** | Normal `$` prompt | **Yes.** `silmtest ...` and everything else |

- The map and console windows only appear *after* you run `sitl`. So `sitl` itself always goes in a plain Ubuntu terminal.
- If messages scroll over the `MANUAL>` prompt, press **Enter** once and it shows again. You can type even while messages scroll.
- `MANUAL>` commands (`param set ...`) only work in Terminal 1. Ubuntu commands (`silmtest`, `cd`, `git`) **don't** work at the `MANUAL>` prompt; they go in Terminal 2.

### 0.5 Terminal survival kit

| Want to | Do this |
|---|---|
| Paste a command | **Right-click** in the window, or **Ctrl+Shift+V** (plain Ctrl+V often doesn't work) |
| Copy text from the terminal | Select it with the mouse, then **Ctrl+Shift+C** (or right-click in the classic window) |
| Stop whatever is running | **Ctrl+C** (that's "stop", not "copy", in a terminal) |
| Repeat or edit an earlier command | **↑** / **↓** arrows, then **Enter** |
| Finish typing a file or folder name | **Tab** |
| Go to your home folder | `cd ~` (`~` means `/home/yourname`) |
| See what's in the current folder | `ls` |
| See where you are | `pwd` |
| Clear the screen | `clear`, or **Ctrl+L** |
| Run a block of several lines | Paste it all at once; each line runs in turn |

- Password prompts never show what you type.
- Lines starting with `#` in this guide are comments. Pasting them is harmless.
- Commands are case-sensitive: `Sitl` is not `sitl`.

### 0.6 Your Ubuntu files from Windows

- **From Ubuntu, open a folder in Windows Explorer:** `explorer.exe .` (the current folder) or `explorer.exe sitl_jam`.
- **From Windows:** type this in the File Explorer address bar:
  `\\wsl$\Ubuntu-24.04\home\<yourname>\uav-silm\onboard`
  Ubuntu also shows up as **Linux → Ubuntu-24.04** in Explorer's left panel.
- **Windows' own drives inside Ubuntu:** `C:\` is `/mnt/c/`. Keep the project in your Ubuntu home folder (`~`), not under `/mnt/c`: it's much faster there.

### 0.7 Editing files

- **In the terminal:** `nano <file>`. Save with **Ctrl+O** then **Enter**; exit with **Ctrl+X**.
- **VS Code (nicer):** install VS Code on Windows with the **WSL** extension. Then in Ubuntu, run `cd ~/uav-silm && code .`.
- Notepad via `\\wsl$\...` also works for quick edits.

---

## Part A: First-time setup (once)

### A1. Install Ubuntu (WSL2)

In **PowerShell (Run as administrator)**:

```
wsl --install -d Ubuntu-24.04
```

Reboot, then open **Ubuntu** from the Start menu (0.1) and create your Linux user (0.2).

### A2. Get the two repositories (Terminal 1)

```bash
cd ~
git clone -b claude/blissful-lovelace-rnmruk https://github.com/rometlehari-maker/uav-silm
git clone --recursive https://github.com/ArduPilot/ardupilot
```

The ArduPilot clone is large and takes a few minutes.

### A3. Install ArduPilot's tools and build the plane simulator

```bash
cd ~/ardupilot
Tools/environment_install/install-prereqs-ubuntu.sh -y
. ~/.profile
```

The install takes 10-20 minutes and asks for your password once. It creates a Python environment that activates automatically in every new terminal; you'll see `(venv-ardupilot)` at the start of the prompt.

**Close Ubuntu and open it again**, then build:

```bash
cd ~/ardupilot
./waf configure --board sitl
./waf plane
```

The build takes 5-10 minutes. It's done when you see `'plane' finished successfully`.

Then add the two Python packages the test needs:

```bash
pip install pymavlink matplotlib
```

### A4. Make the folder SITL runs from

SITL stores its settings (`eeprom.bin`) and loads Lua scripts from the folder you start it in. Always start it from the same folder:

```bash
mkdir -p ~/sitlrun/scripts
cp ~/uav-silm/onboard/ardupilot/scripts/silm_gps_loss.lua ~/sitlrun/scripts/
```

### A5. Add two shortcuts (strongly recommended)

These save typing every session. `sitl` also copies the newest Lua script into place each time, so you can't forget to.

```bash
cat >> ~/.bashrc <<'EOF'

# --- SILM SITL shortcuts ---
alias sitl='cd ~/sitlrun && cp ~/uav-silm/onboard/ardupilot/scripts/silm_gps_loss.lua scripts/ && ~/ardupilot/Tools/autotest/sim_vehicle.py -v ArduPlane --console --custom-location=59.40,24.70,30,0'
alias silmtest='cd ~/uav-silm/onboard && python3 tools/sitl_failsafe_test.py'
EOF
source ~/.bashrc
```

- `sitl` starts the simulator, with Tallinn (59.40, 24.70) as home. To use your own field, change the numbers. The format is `lat,lon,ground altitude in m,heading`.
- `silmtest ...` runs the test; you add the options after it.

The rest of this guide uses the shortcuts. Without them, use the full commands from the `alias` lines.

---

## Part B: Your first test run

### B1. Start SITL (Terminal 1)

Open Ubuntu (0.1). This window is **Terminal 1**. At the normal `$` prompt, type:

```bash
sitl
```

The first start takes about 30 seconds. Terminal 1 fills with messages and ends at a **MAVProxy prompt** (something like `MANUAL>`; press Enter if it's hidden). A grey **console** window may open too; that one is only for watching (see 0.4). Let it sit until the messages slow down, about 30 more seconds.

If you get an error about the console or a display, start it without the window instead (the prompt in the terminal still works):

```bash
cd ~/sitlrun && ~/ardupilot/Tools/autotest/sim_vehicle.py -v ArduPlane --custom-location=59.40,24.70,30,0
```

### B2. Fix the simulated battery (once, at the `MANUAL>` prompt in Terminal 1, not in the console or map window)

Your param file has **4S** battery thresholds (critical at 13.2 V), but the simulated plane has a **3S** battery (12.6 V). Without this fix it refuses to arm with "Battery 1 critical voltage failsafe". Type this at the MAVProxy prompt:

```
param set BATT_VOLT_MULT 13.4667
```

This makes the simulator report 16.8 V, the same as a full 4S pack. It is saved in `~/sitlrun/eeprom.bin`, so you only do it once. **Never put this line in `gps_denied_failsafe.param`**: it's a simulator-only trick.

### B3. First test run (Terminal 2)

**Open a second Ubuntu window now** (0.3): Start menu, then Ubuntu, then Enter. Leave Terminal 1 running. In the new window:

```bash
silmtest --fault jam --wind 6 --out sitl_jam
```

The first time, you'll most likely see this:

```
The silm_gps_loss.lua script is not running in SITL (no SLM_ENABLE).
...
SCR_ENABLE is now 1 - restart SITL once, then re-run this test.
```

That's expected: the test just switched scripting on, and the script only loads on the next start.

### B4. Restart SITL once (Terminal 1)

1. Press **Ctrl+C** in Terminal 1 and wait for the prompt to come back. If it hangs, press Ctrl+C again.
2. Start it again: type `sitl`, or press **↑** then **Enter**.
3. Watch for this line, which means the script loaded:
   ```
   SILM GPS-loss failsafe loaded
   ```

### B5. The real run (Terminal 2)

```bash
silmtest --fault jam --wind 6 --out sitl_jam
```

A run takes about **3-5 minutes**. You'll see it go through these steps:

```
connected to SITL
mission uploaded (13 items; ...)
armed, AUTO - no RC from here on
[ap] Mission: 1 Takeoff
[ap] Takeoff complete at 50m
t=44s  702 m from home: GPS JAM ON
[ap] SILM: GPS bad 5s -> RTL/landing on dead reckoning     <- the failsafe working
[ap] Landing sequence start
t=135s GPS restored, ...
[ap] SILM: GPS recovered
[ap] Mission: 13 Land
t=185s landed and disarmed

=== RESULT ===
  PASS  rtl_in_time
  PASS  headed_home_while_blind
  PASS  stayed_inside_fence
  PASS  estimate_reconverged
  PASS  landed
  PASS  landed_near_touchdown
PASSED - details in sitl_jam/
```

### B6. Look at the results

The results go to `~/uav-silm/onboard/sitl_jam/`:

| File | What it is |
|---|---|
| `track.png` | Map: true flight path (black), path while GPS was bad (red), what the autopilot *thought* (blue dashes) |
| `result.json` | All the numbers and every autopilot message |
| `track.csv` | The raw track, 5 rows per second; opens in Excel |

To open the folder in Windows Explorer from Terminal 2:

```bash
explorer.exe sitl_jam
```

What each check means:

| Check | It passes when |
|---|---|
| `rtl_in_time` | The failsafe switched to RTL/landing within `SLM_GPS_LOSS_S` (+ a margin) of the GPS going bad |
| `headed_home_while_blind` | The plane was really closer to the landing point when GPS came back than when it was lost |
| `stayed_inside_fence` | The *true* position never went outside the geofence |
| `estimate_reconverged` | After GPS returned, the autopilot's position matched the true position again |
| `landed` | It ended on the ground and disarmed by itself |
| `landed_near_touchdown` | Touchdown was within `--land-radius-m` (default 150 m) of the planned point |

**Done with the first run.** You don't need to repeat B2 or B4 again, unless you delete `~/sitlrun/eeprom.bin` or start SITL with `-w`, which wipes the saved settings.

---

## Part C: Every time after that

### C1. Coming back after closing everything

The Ubuntu windows are closed, or the PC was restarted.

**Terminal 1** (open Ubuntu):
```bash
sitl
```
Wait until it settles (about 30 s) and you've seen `SILM GPS-loss failsafe loaded`.

**Terminal 2** (open Ubuntu a second time; see 0.3):
```bash
cd ~/uav-silm && git pull
silmtest --fault jam --wind 6 --out run_01
```

That's all. The battery setting, scripting and the Lua script are already in place.

### C2. Restart SITL between runs

After a run, the simulated plane is sitting wherever it landed. For a clean, repeatable start, **restart SITL before every run**: in Terminal 1, press **Ctrl+C**, then **↑**, then **Enter**. This takes about 30 seconds.

### C3. The edit, run, look loop

| What you changed | What to do before running again |
|---|---|
| `onboard/ardupilot/gps_denied_failsafe.param` (any normal setting, or `SLM_*`) | Nothing extra; the test loads the file at the start of every run |
| `onboard/ardupilot/scripts/silm_gps_loss.lua` | Restart SITL (`sitl` copies the new script in) |
| Test options (`--wind`, `--fault-s`, ...) | Nothing; just pass the new options |
| Simulated airframe (`-f plane-heavy`, a JSON model) | Restart SITL with the new `-f` option |

A typical loop looks like this:

```bash
# Terminal 2: edit a parameter
nano ~/uav-silm/onboard/ardupilot/gps_denied_failsafe.param     # Ctrl+O Enter = save, Ctrl+X = exit

# Terminal 1: Ctrl+C, then up-arrow + Enter (fresh SITL)

# Terminal 2: run, using a NEW output folder each time so you can compare
silmtest --fault jam --wind 6 --out run_02_flare4m
explorer.exe run_02_flare4m
```

To edit in VS Code instead: install VS Code on Windows with the **WSL** extension, then in Ubuntu run `cd ~/uav-silm && code .`.

### C4. Scenarios worth running

```bash
silmtest --fault jam     --wind 6                      --out jam_w6         # baseline
silmtest --fault disable --wind 6                      --out off_w6         # GPS simply gone
silmtest --fault jam     --wind 10 --wind-dir 270      --out jam_w10        # strong wind
silmtest --fault disable --wind 8  --fault-s 240       --out off_long       # lands blind: worst case
silmtest --fault jam     --wind 6  --trigger-m 1000 --out-m 1400 --out jam_far   # fault further out
```

Restart SITL between each one (C2). Use `--fault-s` long enough that GPS is still bad at touchdown: that shows how far off the landing gets when it's flown on dead reckoning alone. `blind_at_touchdown: True` in the results confirms it happened.

All options:

```bash
silmtest --help
```

### C5. Save what works

When a parameter change flies well:

```bash
cd ~/uav-silm
git diff                          # see exactly what you changed
git add onboard/ardupilot/gps_denied_failsafe.param
git commit -m "Raise LAND_FLARE_ALT to 4 m (SITL jam/wind 10 OK)"
git push
```

### C6. Shutting down

1. Terminal 1: **Ctrl+C** (stops SITL).
2. Close the windows.
3. Optional, frees the memory WSL is using. In PowerShell:
   ```
   wsl --shutdown
   ```

Nothing is lost when you do this. Settings stay in `~/sitlrun/eeprom.bin`, and results stay in the `--out` folders.

---

## Troubleshooting

| Symptom | Cause, and the fix |
|---|---|
| `could not arm` + `Battery 1 critical voltage failsafe` | B2 wasn't done, or the settings were wiped. Run `param set BATT_VOLT_MULT 13.4667` at the MAVProxy prompt |
| `could not arm` + another PreArm message | Wait 30-60 s after SITL starts (the EKF and airspeed calibration need time), then run again. If it repeats, the PreArm text says which setting to look at |
| `ConnectionRefusedError` | SITL isn't running, or was started without MAVProxy. Start it with `sitl` in Terminal 1 first |
| `script is not running ... restart SITL` | Restart SITL (B4). Check that `~/sitlrun/scripts/silm_gps_loss.lua` exists, and that you start SITL from `~/sitlrun` |
| `Lua: ... error` in Terminal 1 | A typo in your edited `.lua`. The message gives the line number |
| `it landed before the fault was triggered` | The mission was too short for the trigger distance. Lower `--trigger-m` or raise `--out-m` |
| `timeout` | The plane never got back down. Look at `track.png`, and increase `--timeout-s` for very long missions |
| `Address already in use` when starting SITL | An old SITL is still running. Run `pkill -f arduplane`, then `sitl` |
| `sitl: command not found` | The shortcuts weren't added (A5), or the terminal predates them. Run `source ~/.bashrc` |
| "There's no second terminal" | You open it yourself: Start menu, then Ubuntu, then Enter again (0.3) |
| Typed `silmtest` at the `MANUAL>` prompt and got `Unknown command` | That's MAVProxy, in Terminal 1. Run `silmtest` in Terminal 2 (0.4) |
| Typed `param set ...` at a `$` prompt and got `command not found` | That's a plain Ubuntu terminal. `param set` goes at the `MANUAL>` prompt in Terminal 1 (0.4) |
| Ctrl+V doesn't paste | Use right-click, or **Ctrl+Shift+V** (0.5) |
| The prompt says `root@...` | The user wasn't created on first start. See 0.2 |
| The console or map window doesn't open | Your Windows doesn't support GUI apps in WSL (needs Windows 11, or `wsl --update`). It's not needed: use the start command without `--console` from B1 |
| You want a completely fresh simulator | `rm ~/sitlrun/eeprom.bin`, then redo B2 and B4 |

## Notes

- `-L 59.40,24.70,30,0` in older instructions is wrong for `sim_vehicle.py`: `-L` takes a *named* location. Use `--custom-location=...`.
- The test also sets `TKOFF_THR_MINACC=0` in the simulator only, because SITL's plane takes off from a runway, not a hand launch. Your file keeps its real value.
- SITL's default plane is a generic 2 kg model, not your airframe. Results show that the *logic* works and roughly how far dead reckoning drifts; they are not a promise about the real aircraft's exact numbers.
