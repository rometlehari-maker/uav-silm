# Running the GPS-loss failsafe test in SITL (Windows + WSL2)

This guide has these parts (plus Part 0, basics, a Troubleshooting table and Notes):

- **Part A: First-time setup.** Done once, takes about 30-45 minutes.
- **Part B: Your first test run.** Includes the one-time SITL restart.
- **Part C: Every time after that.** Opening it again after you've closed everything, and the edit, run, look loop.
- **Part D: What you're looking at.** Every window, field, message, command and output file explained, plus a **Glossary**.
- **Part E: What the tests found.** Expected results for 8 scenarios, the problems that were fixed, and how to update (E3).

If you're new to Ubuntu or terminals, read **Part 0** first.

> **Shortcut once Part A is done: the Ground Station does Parts B and C for you.**
> Start the Ground Station *inside Ubuntu* (the simulator runs there):
> ```bash
> cd ~/uav-silm/onboard && ./start_ground_station.sh --no-browser
> ```
> then open <http://127.0.0.1:8765> in your normal Windows browser, go to tab
> **7 Simulator** and press **Start the simulated flight**. It starts the
> simulator at your mission's launch point, sets the battery, restarts once
> for the script, flies your mission with the GPS jammed and shows the six
> checks and the track. The rest of this guide is the manual way, and explains
> what you see.

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

### B2. The simulated battery: nothing to do

The simulated plane has a **3S** battery (12.6 V); your parameter file may be for 4S or 6S, whose low-voltage failsafe would then refuse to arm. **The test does this for you**: it works out your cell count from the parameter file and scales the simulator's voltage reading to a full pack of that size. You'll see a line like:

```
simulator battery read 12.6 V; scaled to a full 4S pack (16.8 V) for your battery failsafes (simulator only)
```

Nothing in your files changes. (Older versions of this guide had you type `param set BATT_VOLT_MULT ...` here - no longer needed.)

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
[ap] SILM: GPS lost/degraded, dead reckoning
[ap] SILM: EKF ignoring GPS (source set 2)
[ap] SILM: GPS bad 5s -> RTL/landing on dead reckoning     <- the failsafe working
[ap] Landing sequence start
t=135s GPS restored, ...
[ap] SILM: GPS recovered
[ap] SILM: EKF using GPS again (source set 1)
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

**Done with the first run.** You don't need to repeat B4 again, unless you delete `~/sitlrun/eeprom.bin` or start SITL with `-w`, which wipes the saved settings.

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

What to expect from each is in **E1**.

```bash
silmtest --fault jam     --wind 6                      --out jam_w6         # baseline
silmtest --fault disable --wind 6                      --out off_w6         # GPS simply gone
silmtest --fault none    --wind 6                      --out normal_w6      # nothing goes wrong: whole mission
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

## Part D: What you're looking at (every window, field and file)

### D1. Terminal 1 (MAVProxy): the messages while SITL starts

`sitl` first prints `SIM_VEHICLE: ...` lines (sim_vehicle.py checking the build and starting the simulator), then `RiTW: Starting ArduPlane ...` (the simulated flight controller starting), then MAVProxy's own lines, and finally the autopilot's messages. The exact set varies; these are the ones that matter:

| Message | Meaning |
|---|---|
| `Connect tcp:127.0.0.1:5760 ...` / `Detected vehicle 1:1 on link 0` | MAVProxy is connected to the simulated autopilot |
| `Received NNNN parameters` | It has downloaded all of the autopilot's parameters |
| `SILM GPS-loss failsafe loaded` | **Your Lua script is running.** If it's missing, see Troubleshooting |
| `EKF3 IMU0 initialised` / `EKF3 IMU1 initialised` | The position/attitude estimator has started (two copies, one per simulated IMU) |
| `EKF3 IMU0 is using GPS` | The EKF has accepted GPS; the aircraft knows where it is |
| `AHRS: EKF3 active` | The autopilot is using the EKF for navigation (good) |
| `Airspeed 1 calibrated` | Zero-airspeed calibration finished (needs the plane still) |
| `PreArm: ...` | A reason it *would* refuse to arm right now. Normal for the first ~30-60 s while things settle. **Missing mission item: land** is normal before the test uploads its mission |
| `PreArm: SILM: waiting for EKF to use GPS` (also `waiting for home`, `GPS not good enough`) | Your script's **arming gate**: it won't arm until the EKF has a GPS position. Normal for the first ~20-30 s after SITL starts (and after power-on on the real aircraft). Wait |
| `Lua: ...` / `Scripting: ...` | A Lua script problem. The line names the file and line number |

The prompt itself shows the current flight mode, for example `MANUAL>`, `AUTO>`, `RTL>`. It changes on its own while the test flies.

### D2. MAVProxy commands (typed at the `MANUAL>` prompt in Terminal 1)

You don't need any of these: they are for looking around. **Don't change modes or arm/disarm while a test is running**: the test is flying the aircraft.

| Command | What it does |
|---|---|
| `param show BATT*` | Show parameters matching a pattern (`*` = anything). E.g. `param show SLM*`, `param show EK3_SRC*`, `param show AHRS_OPTIONS` |
| `param set NAME VALUE` | Change a parameter in the simulator (saved in `eeprom.bin`) |
| `param fetch` | Re-download all parameters (e.g. after a Lua script created new ones) |
| `status` | Dump the latest of every message the autopilot sends (long) |
| `wp list` | Show the mission currently loaded in the autopilot |
| `fence list` | Show the geofence |
| `mode` | List the available flight modes |
| `mode RTL` | Change mode (e.g. to stop a stuck test; then Ctrl+C the test in Terminal 2) |
| `arm throttle` / `disarm` | Arm / disarm by hand. Not needed: the test arms |
| `module load map` | Open the map window if you started without `--map` |
| `help` | List every command |

Press **Tab** to complete command and parameter names. Press **↑** for earlier commands.

### D3. The console window (grey, opened by `--console`)

Read-only status display; you never need to click in it. Its rows, top to bottom:

**Row 1: status lights.** The colour code is the same for all of them:
**green** = present and healthy, **red** = present but unhealthy, **grey** = present but disabled, **black** = not fitted, **orange/yellow** = warning (EKF variance getting large / terrain data still loading).

| Field | Meaning |
|---|---|
| `Mode` (blue text, e.g. `AUTO`) | Current flight mode (see the Glossary) |
| `Sys:1` | Which aircraft (system ID) this is |
| `ARM` | **Green** = armed (motor live), **red** = disarmed |
| `GPS OK3 (14)` | GPS fix type (`OK3` = 3D fix) and number of satellites. **Red** with a number = no usable fix. Watch it flicker during the jam test |
| `Vcc` | Flight-controller supply voltage (~5 V) |
| `Radio` | RC/telemetry radio signal. Not used here (no radio in SITL) |
| `INS` | Inertial sensors (gyros, accelerometers) |
| `MAG` | Compass |
| `AS` | Airspeed sensor. Must be green: dead reckoning depends on it |
| `RNG` | Rangefinder (black/grey: not fitted) |
| `AHRS` | Attitude and heading estimate health. Turns red while the GPS is jammed |
| `EKF` | EKF health from its error estimates: green < 0.5, **orange** 0.5-1.0, **red** ≥ 1.0. Expect orange/red while jammed |
| `LOG` | Onboard logging |
| `RC` | RC receiver. **Red is normal** on this aircraft: it has none |
| `TERR` | Terrain data (yellow while downloading) |
| `PRE` | Pre-arm checks: **green = it could arm now**, red = a `PreArm:` reason exists |

**Row 2: links.** `Link 1 OK (...)` with packet counts and loss %. Red = MAVProxy lost the connection to SITL.

**Row 3: flight data.**

| Field | Meaning |
|---|---|
| `Hdg 045/045` | Heading now / heading it wants (degrees, 0 = north) |
| `Alt` | Height above home (m) |
| `AGL` | Height above the ground under the aircraft (terrain/rangefinder) |
| `AirSpeed` | Airspeed (m/s), from the simulated pitot tube |
| `GPSSpeed` | Ground speed from GPS (meaningless while jammed) |
| `Thr` | Throttle % |
| `Roll`, `Pitch` | Attitude (degrees) |
| `Wind 250/6.0` | Wind the autopilot has *estimated*: from-direction / speed. Compare with your `--wind-dir` / `--wind` |

**Row 4: mission.**

| Field | Meaning |
|---|---|
| `WP 3/12` | Current mission item / last item |
| `Distance` | Distance to the current waypoint |
| `Bearing` | Direction to the current waypoint |
| `AltError` | How far above (+) or below (−) its target height it is |
| `AspdError` | Airspeed error vs target |
| `FlightTime` | Time since takeoff |
| `ETR` | Estimated time to reach the waypoint |
| `Param 1482/1482` | Parameters downloaded / total |
| `Mission` | Mission items downloaded |

**Below the rows:** a scrolling copy of the autopilot's messages (the same `[ap]` lines the test prints).

**Menus:** **MAVProxy** → *Settings* (units, etc.), *Show Map*, *Show Map3d*, *Show HUD* (artificial horizon), *Show Checklist*. **Vehicle** → pick which aircraft if several are connected (only one here). None of them are needed for the test.

### D4. The map window (only if you started with `--map`, or `module load map`)

| What you see | Meaning |
|---|---|
| Plane icon | Where the autopilot **thinks** the aircraft is. With the current files it moves smoothly even while jammed; if it jumps around, the EKF is following the fake GPS (see E2) |
| Line behind the plane | Track flown (estimated position) |
| Numbered points + lines | The mission (loaded by the test) |
| Circle around home | The geofence (`FENCE_RADIUS`) |
| `H` | Home |

| Action | How |
|---|---|
| Zoom | Mouse wheel, or `+` / `-` keys |
| Pan | Drag with the left mouse button |
| Keep the plane centred | **View → Follow** (Ctrl+F) |
| Grid | **View → Grid** (Ctrl+G) |
| Jump to a coordinate | **View → Goto** (Ctrl+P) |
| Map style | **View → Tile Service** |
| Brighter/darker | Ctrl+B / Ctrl+Shift+B |

**Right-click** opens a menu with *Fly To*, *Set Home*, *Set Origin*, *Terrain Check*, *Show Position*, *Google Maps Link*, and editing tools for waypoints, fence and rally points. **Don't use them during a test**: they change what the aircraft is doing. *Show Position* and *Google Maps Link* are harmless.

The map needs internet to download map tiles. On a slow connection it may show grey squares for a while.

### D5. Terminal 2: the test's output, line by line

| Line | Meaning |
|---|---|
| `connected to SITL` | The test reached SITL on port 5762 (or 5760) |
| `WARNING: could not set [...]` | A parameter in your file doesn't exist in this ArduPilot version (or a `SLM_*` before the script loaded) |
| `waiting for GPS/EKF ...` | Waiting until the autopilot has a position |
| `mission uploaded (13 items; landing sequence from item 11, touchdown ...)` | The test built and loaded a mission: takeoff, survey lines, `DO_LAND_START` (item 11), approach, land. Touchdown = launch point |
| `[ap] ...` | A message **from the autopilot**, passed through (same as in Terminal 1) |
| `[ap] Arm: ...` / `[ap] PreArm: ...` | Why it can't arm *yet*. The test keeps trying for a while; if it ends in `could not arm`, the last reason is the one to fix |
| `armed, AUTO - no RC from here on` | Armed and in AUTO; from now on it flies on its own, like the real aircraft |
| `[ap] Mission: 1 Takeoff` | Mission item 1 (takeoff) started |
| `[ap] Takeoff complete at 50m` | Climb-out done; flying the survey |
| `t=44s  702 m from home: GPS JAM ON` | The test switched on SITL's GPS jamming (or `DISABLE ON` = GPS off). `t` = seconds since arming |
| `[ap] PreArm: SILM: waiting for EKF to use GPS` | The arming gate is holding (see D1). The test keeps retrying; it arms once the EKF has GPS |
| `[ap] SILM: GPS lost/degraded, dead reckoning` | Your script noticed bad GPS (first bad sample) |
| `[ap] SILM: EKF ignoring GPS (source set 2)` | Your script told the EKF to stop using GPS |
| `[ap] SILM: GPS bad 5s -> RTL/landing on dead reckoning` | **The failsafe acted**: RTL, which flies the landing sequence |
| `[ap] Landing sequence start` / `Mission: 11 LandStart` | Jumped to the landing part of the mission |
| `[ap] Circle fence breached` | The *estimated* position went outside the fence (also happens when the jam makes the estimate jump) |
| `t=135s GPS restored, true distance to touchdown 340 m` | The test switched the jamming off; where the aircraft really is |
| `[ap] SILM: GPS recovered` / `EKF using GPS again` | GPS good for `SLM_GPS_OK_S`; EKF back on GPS |
| `[ap] Mission: 13 Land` / `Landing approach start` / `Landing glide slope` | Final approach |
| `[ap] Flare 3.3m sink=1.7 speed=15.4 dist=53.8` | Flare started: at 3.3 m height, sinking 1.7 m/s, 15.4 m/s airspeed, 53.8 m before the land point. **A flare far above `LAND_FLARE_ALT` (3 m), or with an absurd sink, means the height estimate was wrong** |
| `[ap] SIM Hit ground at 0.5 m/s` | Simulated touchdown speed |
| `[ap] Distance from LAND point=31m` | How far from the planned point it stopped (autopilot's own number) |
| `t=185s  landed and disarmed` | The test saw it disarm by itself; flight over |
| `=== RESULT ===` and the PASS/FAIL list | The grades (Part B6) |

**Numbers under the grades:**

| Number | Meaning |
|---|---|
| `rtl_after_s` | Seconds from GPS going bad to the failsafe acting. Must be ≤ `SLM_GPS_LOSS_S` + 6 |
| `true_dist_at_fault_m` | Real distance from the landing point when GPS went bad |
| `true_dist_at_restore_m` | Real distance when GPS came back. Must be smaller than the one above |
| `max_true_dist_m` | Furthest real distance from home in the whole flight. Must be ≤ `FENCE_RADIUS` |
| `dead_reckoning_error_max_m` | Worst gap between where it *was* and where it *thought* it was while blind. Current files: roughly the touchdown overshoot (~70-170 m, mostly after touchdown while the EKF still has no GPS). Thousands of metres means it followed the jammer (E2) |
| `reconverged_after_s` | Seconds after GPS returned until the estimate was within 15 m of the truth (must be ≤ 40) |
| `touchdown_error_m` | Real touchdown distance from the planned point. Must be ≤ `--land-radius-m` (150) |
| `blind_at_touchdown` | `True` = it landed while GPS was still bad (a dead-reckoned landing) |

### D6. The files in the `--out` folder

**`track.png`**: a map in metres from the launch point (north up, east right).

| Line/marker | Meaning |
|---|---|
| Black line | True path (from the simulator) |
| Thick red | True path while GPS was bad |
| Blue dashes | Where the autopilot *thought* it was while GPS was bad. It should follow the red line closely and drift apart only near the end (the landing overshoot, E1). A fan of long jumps means the autopilot was navigating on the jammer's fake positions: the old problem in E2 |
| Black triangle | Launch |
| Green X | Planned touchdown |

**`result.json`**: every number above, all grades (`checks`), the scenario (`fault`, `fault_s`, `wind_mps`, `wind_dir_deg`, `touchdown_planned`) and **`ap_messages`**: every autopilot message in order. The first place to look when a run fails. To print just the messages:

```bash
python3 -c "import json; print('\n'.join(json.load(open('sitl_jam/result.json'))['ap_messages']))"
```

**`track.csv`**: 5 rows per second. Opens in Excel.

| Column | Meaning |
|---|---|
| `t` | Seconds since arming |
| `phase` | `survey` (before the fault), `gps_fault`, `gps_restored` |
| `mode` | Flight mode number: 10 = AUTO, 11 = RTL (0 MANUAL, 5 FBWA, 12 LOITER) |
| `seq` | Current mission item |
| `landing` | `True` once inside the landing sequence |
| `armed` | Motor armed |
| `true_lat`, `true_lon` | Real position (simulator) |
| `est_lat`, `est_lon` | Autopilot's position estimate |
| `alt` | Autopilot's height above home (m) |

### D7. Other files

| Path | What it is |
|---|---|
| `~/sitlrun/eeprom.bin` | SITL's saved parameters. Delete it for a factory-fresh simulator (then redo B4) |
| `~/sitlrun/scripts/` | Lua scripts SITL runs at boot |
| `~/sitlrun/logs/` | Onboard flight logs (`.BIN`), one per boot. Open them in Mission Planner or UAV Log Viewer (plot.ardupilot.org) to see everything the autopilot recorded |
| `~/sitlrun/terrain/` | Downloaded terrain height data |
| `~/sitlrun/mav.tlog` | MAVProxy's telemetry recording |

---

## Part E: What the tests found, and what results to expect

### E1. Expected results (current files, ArduPilot master, Nov 2026)

**Latest matrix** - the Ground Station's own mission (final approach 200 m long from 25 m, the default since Nov 2026), flown in SITL one at a time. "blind" means GPS stayed jammed until after touchdown, so the whole landing was flown without GPS.

| Scenario | Touchdown error | Armed on the ground | Result |
|---|---|---|---|
| jam, 4 m/s from the east | 60 m | - | PASS |
| jam, 6 m/s | 55 m | - | PASS |
| jam, 8 m/s from the north-west | 72 m | - | PASS |
| jam, 8 m/s, blind landing | 85 m | - | PASS |
| jam, 10 m/s, blind landing | 107 m | 17 s | PASS |
| GPS switched off, 6 m/s | 55 m | 14 s | PASS |
| jam, **12 m/s**, blind landing | **151 m** | 18 s | **FAIL** (limit 150 m) |

Because of that last row the Ground Station **refuses to launch above 10 m/s** (enter the gusts, not the average).

Earlier matrix (standard `silmtest` mission, older 300 m / 30 m approach), for comparison:

| Scenario | Failsafe after | Flare at | Touchdown error | Result |
|---|---|---|---|---|
| `--wind 6` (the standard test) | 5.4 s | 3.0 m | 73 m | PASS |
| `--wind 6 --wind-dir 200` | 5.4 s | 3.0 m | 75 m | PASS |
| `--wind 8 --wind-dir 300` | 5.1 s | 2.9 m | 93 m | PASS |
| `--wind 4 --wind-dir 90` | 5.4 s | 2.9 m | 67 m | PASS |
| `--wind 6 --trigger-m 900` | 5.5 s | 2.9 m | 83 m | PASS |
| `--wind 6 --fault-s 150` | 5.2 s | 3.0 m | 74 m | PASS |
| `--fault disable --wind 6 --fault-s 120` (lands blind) | 5.3 s | 2.9 m | 76 m | PASS |
| `--wind 10 --wind-dir 250` | 5.4 s | 2.9 m | **160 m** | **FAIL** (limit 150 m) |

Your numbers will differ a little. The jamming simulation is random, but repeatable for the same settings on the same ArduPilot version.

- **Failsafe ~5 s, flare at ~3 m** in every run: those are the parts the fixes below made reliable.
- **Touchdowns 55-107 m long** (past the planned point, along the landing direction), only 1-26 m sideways. Without GPS the EKF can't re-measure the wind, so it keeps the wind it learned at 100 m. Near the ground the wind is weaker (SITL simulates wind shear, like real air), so on the final approach the aircraft goes further than it thinks. The position error stays under ~7 m down to 30 m height, then grows. The simulated belly landing also slides on: with working GPS it stopped 10-30 m long. The shorter, lower final approach (200 m from 25 m instead of 300 m from 30 m) cut the 10 m/s overshoot from 160 m to 107 m.
- **The 12 m/s FAIL is a real limit, not a bug.** That wind is well over half the aircraft's airspeed. See FLIGHT_READY.md 2.4: keep ≥ 150 m of clear ground beyond the touchdown point; the Ground Station refuses wind above 10 m/s.
- **"Armed on the ground"** is the seventh check (`disarmed_soon_after_landing`, at most 30 s). After a blind landing the Lua script disarms the aircraft itself after 15 s of standing still on the landing item.

### E2. What was wrong before (fixed, but good to recognise in old results)

| Symptom in old results | Cause | Fix (all in `gps_denied_failsafe.param` / `silm_gps_loss.lua`) |
|---|---|---|
| Flew **away** while jammed and left the fence (`headed_home_while_blind` / `stayed_inside_fence` FAIL); lots of `AHRS: DCM active` in the messages | The EKF rejected the jammed GPS, but the receiver still claimed a fix, so the autopilot fell back to DCM, which steers on the raw (fake) GPS position | `AHRS_OPTIONS,1` |
| `Flare 89.2m sink=-69.77` or similar: flare far above 3 m, absurd sink, touchdown hundreds of metres short | The EKF kept fusing the jammed GPS's fake climb rate | The script switches the EKF to source set 2 (`EK3_SRC2_*`, no GPS) for the whole GPS-loss episode |
| Every height ~20 m off; `Flare 20.6m sink=0.01` | With `AHRS_OPTIONS=1` the aircraft could be armed before the EKF had GPS, so the EKF origin was set during the climb | Arming gate in the script: `PreArm: SILM: waiting for EKF to use GPS` |
| `rtl_in_time` FAIL in strong wind (failsafe after 12 s) | Between dropouts, the jammed receiver reported a perfect fix, so only the dropouts counted | During an episode, a fix > `SLM_GPS_DIFF_M` (50 m) from the GPS-free EKF position also counts as bad |

The test tool itself also had bugs, now fixed: `landed` was graded FAIL after a good landing; `estimate_reconverged` was graded FAIL after a blind landing; it hung after losing the connection.

### E3. Getting these fixes onto your PC

In **Terminal 2**:

```bash
cd ~/uav-silm && git pull
```

Then **restart SITL** in Terminal 1: Ctrl+C, then `sitl`. The `sitl` shortcut copies the new script into `~/sitlrun/scripts/`. The new `SLM_*` parameters appear after this restart. If the test then prints `script is not running ... restart SITL`, restart SITL once more (the script's parameter table grew, which needs a fresh start). Nothing else is needed: the test loads the new parameter file on every run.

### E4. Don't run two simulators at once

`sim_vehicle.py` kills **every** running ArduPlane simulator when it starts. Starting a second SITL (even with `-I1`) silently kills the first one mid-flight, and the test then reports a lost connection. Run one SITL and one test at a time.

---

## Glossary

| Term | Meaning |
|---|---|
| **ArduPilot / ArduPlane** | The autopilot software on your flight controller; ArduPlane is its fixed-wing version |
| **SITL** | Software In The Loop: ArduPlane compiled for your PC, flying a simulated aircraft. Same code as on the real board |
| **sim_vehicle.py** | ArduPilot's launcher that starts SITL (and MAVProxy) |
| **MAVProxy** | A text ground station: the `MANUAL>` prompt, the console and map windows |
| **MAVLink** | The message protocol between autopilot and ground station/test |
| **Port 5760 / 5762 / 5763** | SITL's network connections: 5760 = MAVProxy, 5762 = the test, 5763 = free (Mission Planner) |
| **Parameter / `.param` file** | A named setting in the autopilot (`NAME,value`). Your file is the aircraft's configuration |
| **eeprom.bin** | Where SITL stores parameters, like the flight controller's memory |
| **Lua script** | A small program running inside the autopilot (`silm_gps_loss.lua`) |
| **WSL / Ubuntu** | Linux running inside Windows |
| **Terminal / prompt** | The text window / the `$` (Ubuntu) or `MANUAL>` (MAVProxy) where you type |
| **AUTO** | Mode: fly the uploaded mission |
| **RTL** | Return To Launch. With `RTL_AUTOLAND=2`: fly the mission's landing sequence |
| **MANUAL, FBWA, LOITER** | Other modes (pilot control, assisted, circle). Not used without an RC link |
| **Mission / waypoint / item** | The flight plan, a list of numbered items (takeoff, waypoints, land...) |
| **DO_LAND_START** | Mission item that marks where the landing sequence begins |
| **Flare** | The final nose-up just before touchdown (`LAND_FLARE_ALT`, `LAND_FLARE_SEC`). Once it starts, the plane commits to landing where it is |
| **Arm / disarm** | Motor enabled / disabled |
| **PreArm check** | A safety check that must pass before arming |
| **Failsafe** | Automatic action when something goes wrong (GPS, battery, fence) |
| **Geofence (`FENCE_*`)** | Boundary around home; crossing it triggers RTL |
| **GPS fix (3D)** | GPS has a full position. Under jamming a receiver can claim a fix while giving garbage |
| **Satellites / HDOP** | GPS quality: more satellites and lower HDOP are better |
| **Jamming / spoofing** | Radio interference that blocks GPS / fakes GPS positions |
| **Dead reckoning** | Estimating position from airspeed, heading and learnt wind, without GPS. Error grows with time |
| **EKF (EKF3)** | Extended Kalman Filter: the autopilot's main estimator, mixing IMU, GPS, baro, compass, airspeed |
| **AHRS** | The layer that picks the estimate the autopilot flies on (EKF3 normally) |
| **DCM** | Older, simpler backup estimator. Takes position straight from GPS, which is dangerous under jamming (hence `AHRS_OPTIONS=1`) |
| **EKF source set** | Which sensors the EKF uses for position, velocity, height and yaw. Set 1 = normal (GPS); set 2 = GPS-loss (no GPS). `EK3_SRC1_*` / `EK3_SRC2_*` |
| **IMU / INS** | Gyros + accelerometers |
| **Baro** | Barometer: height from air pressure |
| **Compass / MAG** | Magnetometer: heading |
| **Airspeed sensor / pitot** | Measures speed through the air; essential for dead reckoning |
| **Innovation / variance** | How far a measurement disagrees with the EKF's prediction / how unsure the EKF is |
| **Home / origin** | Launch position the autopilot measures from |
| **Touchdown error** | Real distance between the landing spot and the planned point |
| **PASS / FAIL** | A grade from the test (Part B6) |

---

## Troubleshooting

| Symptom | Cause, and the fix |
|---|---|
| `could not arm` + `Battery 1 critical voltage failsafe` | The test scales the simulator's battery automatically; look for the "simulator battery" line in its output. If it says "could not read the simulator's battery voltage", restart SITL and run again |
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
| `could not arm` + `PreArm: SILM: waiting for EKF to use GPS` | The EKF never got a GPS position within the test's 2-minute arming window. Restart SITL (C2) and run again. If it repeats, check at the `MANUAL>` prompt that `param show SIM_GPS1_ENABLE` is 1 and `param show SLM_ENABLE` is 1 |
| You want a completely fresh simulator | `rm ~/sitlrun/eeprom.bin`, then redo B4 |

## Notes

- `-L 59.40,24.70,30,0` in older instructions is wrong for `sim_vehicle.py`: `-L` takes a *named* location. Use `--custom-location=...`.
- The test also sets `TKOFF_THR_MINACC=0` in the simulator only, because SITL's plane takes off from a runway, not a hand launch. Your file keeps its real value.
- SITL's default plane is a generic 2 kg model, not your airframe. Results show that the *logic* works and roughly how far dead reckoning drifts; they are not a promise about the real aircraft's exact numbers.
