# SILM - flight-ready guide (from a new aircraft to the pictures)

The do-it-yourself procedure, top to bottom the first time: **what goes where,
what to run, how to check it works, and the flight checklist.** The laptop
program has its own guide: [`GROUND_STATION.md`](GROUND_STATION.md).

**There is no RC link and no telemetry radio.** Every flight is a fully
automatic mission planned on the laptop, sent to the aircraft on the ground,
and flown from the launch to an automatic landing and disarm. Nobody can take
over in flight, so every failure path ends in an automatic landing.

The aircraft has two computers, and neither needs a link in flight:

| | **Flight controller** - Matek H743, ArduPilot | **Aircraft computer** - Raspberry Pi 4 Model B, 2 GB |
|---|---|---|
| Does | launches, flies the mission, lands, disarms; GPS-loss failsafe | camera, recognition, geo-tagged pictures |
| Software | ArduPlane + `ardupilot/gps_denied_failsafe.param` + `ardupilot/scripts/silm_gps_loss.lua` | this folder: `run.py` + `silm_recon/` (two services) |
| Gets its settings | from the Ground Station over its **USB cable** | from the Ground Station over its **own Wi-Fi** or a **USB stick** |
| If the other one fails | flies and lands anyway | keeps recording; never commands the aircraft |

---

## Contents

- **[0. What lives where](#0-what-lives-where)**
- **[1. The laptop: Ground Station](#1-the-laptop-ground-station)**
- **[2. The flight controller](#2-the-flight-controller-once)** - parameters, script, what they do, proving it in the simulator, honest limits
- **[3. The Raspberry Pi 4](#3-the-raspberry-pi-4-once)** - hardware, install, check, calibrate
- **[4. Moving data: laptop ⇄ aircraft](#4-moving-data-laptop--aircraft)**
- **[5. Teaching it your objects](#5-teaching-it-your-objects)**
- **[6. Flight day](#6-flight-day)** - the checklist
- **[7. After the flight: the pictures](#7-after-the-flight-the-pictures)**
- **[8. Legal and safety](#8-legal-and-safety)**
- **[9. Automated tests](#9-automated-tests)**

---

## 0. What lives where

```
onboard/
  Start Ground Station.bat     Windows: double-click to start the laptop program
  start_ground_station.sh      macOS / Linux / WSL: same
  groundstation/               LAPTOP program (tabs, transfer, simulator, pictures, training)
  GROUND_STATION.md            its guide
  run.py, config.yaml          the Pi's flight program and its defaults
  silm_recon/                  the Pi's code: camera, detector, geo-referencing,
                               storage, health, agent.py (Wi-Fi/USB transfer), ...
  pi/install.sh                one-time Raspberry Pi 4 setup
  systemd/                     the Pi's two services (silm-recon, silm-agent)
  ardupilot/
    gps_denied_failsafe.param  no-RC + GPS-loss parameters (the Ground Station fills in your aircraft)
    scripts/silm_gps_loss.lua  GPS-loss failsafe script for the flight controller
  models/                      recognition models (.tflite, downloaded by pi/install.sh)
  training/                    training tools (also behind the Ground Station's tab 9)
  tools/                       preflight.py, sitl_failsafe_test.py, calibrate_camera.py, ...
  tests/                       automated tests (section 9)
```

| Where | What is there |
|---|---|
| **Laptop** | the repository; your missions, flights and training pictures in `~/SILM/` |
| **Flight controller** | parameters; `APM/scripts/silm_gps_loss.lua` on its SD card; the mission |
| **Pi** | the repository at `~/uav-silm`; `~/silm-venv`; your settings in `~/silm-settings/`; flights in `~/silm_captures/` |

---

## 1. The laptop: Ground Station

Install Python 3 (<https://www.python.org/downloads/>; on Windows tick **"Add
python.exe to PATH"**), get the repository (GitHub **Code → Download ZIP**, or
`git clone https://github.com/rometlehari-maker/uav-silm`), then:

- **Windows:** double-click `onboard\Start Ground Station.bat`
- **macOS / Linux:** `cd uav-silm/onboard && ./start_ground_station.sh`

It opens in your browser at <http://127.0.0.1:8765>. Answer the questions on
tabs 2-4 (aircraft, mission, camera). Details: [`GROUND_STATION.md`](GROUND_STATION.md).

---

## 2. The flight controller (once)

### 2.1 Load the parameters and the script

With the Ground Station (recommended):

1. Answer tab **2 Aircraft** (battery, speeds, launch type, balance point...)
   and draw a mission on tab 3 (the geofence is set from it).
2. Battery in, USB from the flight controller to the laptop. Tab **5 Send**,
   card A: pick the port, **Check connection**.
3. **1. Send the aircraft settings (parameters)**: every value is written and
   read back.
4. **2. Copy the GPS-loss script**: to the SD card at `APM/scripts/`, read back.
5. **Reboot**: battery and USB out for 10 s, back in. **Check connection** must
   say "GPS-loss script is running".
6. **3. Send the mission**: "identical".

Without the Ground Station: card A → **"Or use Mission Planner…"** downloads the
`.param` file (load it in Mission Planner: CONFIG → Full Parameter List → Load
from file → Write Params, reboot); copy `ardupilot/scripts/silm_gps_loss.lua`
to the SD card's `APM/scripts/` (Mission Planner: CONFIG → MAVFtp), reboot
twice (the second time the `SLM_` parameters appear).

Optional, against **your** firmware version: `python3 tools/check_params.py
--src <ardupilot checkout> your.param` reports unknown names and out-of-range values.

### 2.2 What each part does

**No RC receiver**
- `INITIAL_MODE=10`: boots into AUTO (nothing else could select a mode). You
  arm from the Ground Station just before the launch.
- `THR_FAILSAFE=0`, `ARMING_SKIPCHK=64`, `FS_GCS_ENABL=0`: no receiver and no
  ground link, so no failsafes waiting for them.
- `ARMING_MIS_ITEMS=13`: refuses to arm unless the mission has TAKEOFF,
  DO_LAND_START and LAND - a mission that can't end in a landing can't fly.
- **Launch** (from your answer): hand/bungee: `TKOFF_THR_MINACC` (motor off
  until the throw), `TKOFF_THR_DELAY` (pusher: 0.3 s to clear your hand);
  runway: motor runs at once, `TKOFF_ROTATE_SPD`.
- **Landing:** `RTL_AUTOLAND=2` - every RTL (GPS loss, geofence, battery) flies
  the mission's landing sequence instead of circling. `LAND_FLARE_*`,
  `LAND_PITCH_DEG` (belly: nose slightly up), `LAND_DISARMDELAY=10`.
- **Battery** (from your cell count and capacity): low = 3.5 V/cell or 25 % left,
  critical = 3.3 V/cell or 15 %; both → land (`BATT_FS_*_ACT=2`).
- **Speeds** (from your stall/cruise/max): `AIRSPEED_MIN` = 1.3 × stall, ...

**GPS lost, jammed or spoofed**
- **Dead reckoning** (`ARSPD_USE=1`): without GPS it navigates on airspeed,
  heading and the wind it learnt. Needs a working, calibrated airspeed sensor.
- **`silm_gps_loss.lua`**: after `SLM_GPS_LOSS_S` (5) s of bad GPS in AUTO it
  switches to RTL = fly the landing sequence. Never interrupts a takeoff or a
  landing already under way. GPS counts as bad without a 3D fix, below
  `SLM_MIN_SATS` (6) satellites, above `SLM_MAX_HDOP` (2.5) - or, during an
  episode, more than `SLM_GPS_DIFF_M` (50 m) from the aircraft's GPS-free
  position (a jammer can fake a perfect-looking fix). An episode only ends
  after `SLM_GPS_OK_S` (10) s of good GPS.
- **EKF source set 2 = "no GPS"** (`EK3_SRC2_*`): for the whole episode the
  script makes the position estimator ignore GPS completely (barometer height,
  compass heading), so a jammer's fake positions and climb rates can't steer it
  or trigger the landing flare in mid-air.
- **`AHRS_OPTIONS=1`**: no fallback to the simple DCM estimator, which steers on
  the raw (jammed) GPS position.
- **Arming gate** (in the script): refuses to arm (`PreArm: SILM: waiting for
  EKF to use GPS`) until the position estimator has GPS - otherwise heights
  could be ~20 m off for the whole flight. After power-on, wait for this
  message to clear.
- **`EK3_OPTIONS=1`**: after a GPS loss, GPS is quality-checked before it is trusted again.
- **Geofence** `FENCE_*`: crossing it → RTL → landing. The Ground Station sets
  `FENCE_RADIUS` from the mission.

### 2.3 Prove it in the simulator - before the first flight

Fly your mission in ArduPilot's simulator, with the GPS jammed halfway:

- **Ground Station tab 7 Simulator** (needs Linux, or Ubuntu in WSL2 on Windows,
  with the simulator installed once: [`SITL_HOWTO.md`](SITL_HOWTO.md) part A):
  **Start the simulated flight**. It sets up the simulator, flies, and reports
  seven checks plus the track.
- By hand: [`SITL_HOWTO.md`](SITL_HOWTO.md).

**Every check must pass before a real flight.** The simulator's 3S battery is
matched to your cell count automatically (simulator only).

### 2.4 Honest limits

- **A landing without GPS lands long.** Without GPS the aircraft can't re-measure
  the wind; near the ground the real wind is weaker than the wind it learnt at
  height, so it travels further than it thinks. In the simulator, blind
  landings into 4-8 m/s wind touched down 55-95 m past the planned point and up
  to ~26 m to the side. **Keep at least 150 m of clear, flat ground beyond the
  touchdown point.** **Above 8 m/s wind the Ground Station refuses to launch.**
- **The simulator flies a generic 2 kg model**, not your airframe: it proves the
  logic, not your exact numbers.
- **No RC means no tuning flights with a pilot.** Start from good defaults for
  your airframe, check control directions on the bench (AUTO on the ground: tilt
  the aircraft, the surfaces must correct), keep the first missions short and
  close. If you can borrow a receiver for the first tuning flights, do.
- An automatic belly landing can't avoid obstacles: the strip must be clear,
  long (≥ 200 m plus the margin above), wide (≥ 50 m), nothing tall on the approach.

---

## 3. The Raspberry Pi 4 (once)

### 3.1 Hardware

- **Pi 4 Model B, 2 GB**, 64-bit Raspberry Pi OS (Bookworm or later, "Lite" is
  enough), a good 32 GB+ microSD card (A1/A2 class).
- **Power:** 5.1 V, 3 A from a BEC that can deliver it continuously. Under-voltage
  makes the Pi slow itself down (the health log and preflight show it).
- **Cooling:** a heatsink; the Pi 4 slows down from 80 °C. Air flow in the fuselage helps.
- **Camera video:** the SIYI A2 mini's Ethernet into the Pi's Ethernet port.
  The camera needs **12 V** (a regulated 12 V output or BEC).
- **Flight controller:** a spare H743 UART (e.g. TELEM2) to the Pi's UART pins
  (GPIO 14/15, `/dev/serial0`), or USB (`/dev/ttyACM0`). In ArduPilot set that
  port `SERIALx_PROTOCOL=2`, `SERIALx_BAUD=115`. Enable the Pi's UART with
  `sudo raspi-config` → Interface Options → Serial Port: login shell **No**,
  hardware **Yes**.
- **Camera network:** the camera is at 192.168.144.25. Give the Pi an address
  on that network (no gateway, so Wi-Fi stays the default route):
  ```bash
  sudo nmcli con add type ethernet ifname eth0 con-name siyi-camera \
      ipv4.method manual ipv4.addresses 192.168.144.20/24 ipv6.method disabled
  sudo nmcli con up siyi-camera
  ping -c 3 192.168.144.25
  ```

### 3.2 Install (on the Pi, with internet - Ethernet to your router, or your home Wi-Fi)

Set the Wi-Fi country first (`sudo raspi-config` → Localisation → WLAN Country).
Then:

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/rometlehari-maker/uav-silm.git ~/uav-silm
cd ~/uav-silm/onboard
bash pi/install.sh
```

It asks for the password of the aircraft's Wi-Fi network and then, step by
step: installs the system packages (OpenCV with GStreamer for the Pi's hardware
video decoder), a Python environment `~/silm-venv`, the recognition model
(checked by its SHA-256), the two services (`silm-recon`: the flight program;
`silm-agent`: settings in and flights out), a permission for the agent to
restart the flight program and switch Wi-Fi on/off (nothing else), the **Wi-Fi
hotspot** `SILM-<hostname>` at 10.42.0.1, and automatic mounting of a USB stick
named **SILM**. It ends with a configuration check. Then `sudo reboot`.

Options: `--ssid NAME`, `--password PASS`, `--no-hotspot`, `--skip-apt`,
`--dry-run` (print what it would do). Safe to run again.

Your home Wi-Fi (if you set one up) is preferred when in range - handy for
updates (`cd ~/uav-silm && git pull`). At the field the hotspot comes up instead.

### 3.3 Check it

```bash
cd ~/uav-silm/onboard
~/silm-venv/bin/python3 tools/preflight.py --alt-m 100 --speed-mps 19
```

PASS/WARN/FAIL for: the settings file, the camera stream (resolution, frame
rate), the gimbal, the flight controller link (GPS, failsafe parameters,
script loaded, the mission on board), the model (labels, and the **measured**
recognition speed on this Pi against what your height and speed need),
storage, temperature, clock. Fix every FAIL. Then:

```bash
~/silm-venv/bin/python3 run.py --selftest      # camera + recognition, prints what it sees
journalctl -u silm-recon -f                    # the flight program's log
journalctl -u silm-agent -f                    # the transfer agent's log
```

The flight program waits ("idle ... gps=OK") until the aircraft is **armed, in
AUTO, above the picture height** - nothing is recorded on the bench (unless
you choose *Bench test* on the Camera tab). It keeps running if the camera
starts late or drops out, and reconnects by itself.

### 3.4 Calibrate the lens (once)

The A2 mini's 160° fisheye bends straight lines; without calibration positions
away from the picture centre are off.

1. Print a checkerboard (A3, 9×6 inner corners, ~35 mm squares), mount it flat.
2. On the Pi, from the live stream at your flying resolution:
   ```bash
   ~/silm-venv/bin/python3 tools/calibrate_camera.py --fisheye \
       --device rtsp://192.168.144.25:8554/main.264 --capture 30 --cols 9 --rows 6 --square-mm 35
   ```
   Cover the whole frame, especially edges and corners, at different tilts.
3. RMS under ~0.5 px is good. Paste the printed `calibration:` block under
   `georef:` in `config.yaml`.
4. First flight: compare a picture's coordinates with the object's real position.

---

## 4. Moving data: laptop ⇄ aircraft

Full detail, step by step: [`GROUND_STATION.md` §4](GROUND_STATION.md#4-moving-data-between-the-laptop-and-the-aircraft).
Short version - **after every change, send to both computers, aircraft disarmed**:

| What | Route | Ground Station |
|---|---|---|
| parameters, GPS-loss script, mission → flight controller | USB cable | tab 5, card A, buttons 1-2-3 |
| camera settings, mission copy, trained model → Pi | **Wi-Fi**: join `SILM-…`, Pi at 10.42.0.1 | tab 5, card B, **Send** |
| the same → Pi | **USB stick** named SILM | tab 5, card C, **Write** → stick into the Pi, power on → back: **Read the stick** |
| flights (pictures, track, logs) → laptop | Wi-Fi | tab 8, **From the aircraft (Wi-Fi)…** → Download |
| flights → laptop | USB stick (left in the Pi, flights are copied onto it after landing) | tab 8, **From the USB stick…** |
| flights → laptop | the Pi's SD card | tab 8, **Import** the folder `silm_captures` |

What the Pi does with them: settings are checked exactly as the flight program
will check them (a bad file changes nothing and the reason is shown), installed
in `~/silm-settings/settings.yaml` on top of `config.yaml`, and the flight
program restarts with them. Wi-Fi switches **off** when the aircraft is armed
and back on 20 s after it has landed and disarmed. Nothing is accepted while
the aircraft is armed or in the air.

---

## 5. Teaching it your objects

The standard model recognises people and vehicles (car, truck, bus, boat,
cattle, horse, sheep). Tractors, combines and other farm objects need a model
trained on **your** pictures:

1. **Ground Station tab 9 "Teach new objects"**: add pictures (best: the
   ordinary pictures from your own flights - from above, at your height), draw
   a box around every object, mark empty pictures "Nothing here". At least 30
   pictures per object type, 150+ is good.
2. **Start training**, then **Use on the aircraft**, then send (section 4).

A starting point trained on real photos: `training/openimages_dataset.py`
downloads ~1,800 labelled photos of tractors, cars, trucks and livestock from
Google's Open Images (no account needed). Those are mostly **ground-level**
photos: add your aerial pictures for real use. Training in depth,
including GPU training and evaluating a model with the onboard code:
[`training/README.md`](training/README.md).

---

## 6. Flight day

**At home (with internet):**
1. Ground Station: mission drawn, no red problems; the Simulator tab passed
   for **this** mission.
2. Battery charged.

**At the field:**
1. Enter **today's wind** (Mission tab) and press **Land into the wind**. Above
   8 m/s: don't fly.
2. Walk the landing strip and the approach: clear, flat, nobody on it.
3. Power the aircraft. Send everything (tab 5, A then B or C) if anything changed.
4. Wait for GPS: tab 5, **Check connection**: GPS fix, no `PreArm` message.
5. Tab 6 **Launch**: tick every line of the checklist, **ARM for launch**.
6. Unplug the USB cable. Hold it level, nose into the wind, **throw firmly**,
   slightly nose-up. The motor starts after the throw.
7. It flies the survey and lands itself. Stay clear of the approach. If anything
   goes wrong in the air, there is nothing to switch: GPS loss, low battery and
   the geofence all end in the same landing.
8. Wait until it has stopped and disarmed (10 s after touchdown) before walking up.

---

## 7. After the flight: the pictures

Bring the flight to the laptop (section 4) and open it on tab **8 Flights &
pictures**: the track on a map (red where GPS was bad), a dot for every
picture, every picture with its coordinates, filters per object type, a large
view with the object boxed, the coordinates (copy), time, height and links to
OpenStreetMap / Google Maps, and exports to GeoJSON (QGIS, Google Earth) and
CSV (Excel).

Without the Ground Station: `python3 tools/make_review.py <flight folder>`
writes a `review.html` page; `tools/make_geojson.py` exports the detections.

---

## 8. Legal and safety

- **Fully automatic flight with no RC link is outside the EASA "open"
  category**, which requires that the remote pilot can always intervene. It
  needs the **"specific" category**: an operational authorisation from
  Transpordiamet (SORA-based, or a standard scenario if one fits). Expect
  questions on containment (geofence, `FENCE_ACTION=1`), how every flight ends
  (automatic landing, battery → land) and monitoring.
- The GPS-loss failsafe is a safety feature against fly-aways, not permission
  to fly beyond your authorisation.
- Map imagery: don't pre-cache Google imagery (against their terms).
- Check the rules for photographing people and private property.

---

## 9. Automated tests

On your computer, from `onboard/`:

```bash
python3 -m pip install pytest lupa opencv-python-headless pymavlink pyyaml numpy ai-edge-litert
python3 -m pytest -q
```

| Test file | What it proves |
|---|---|
| `test_gps_loss_lua.py` | the real Lua script with mocked ArduPilot: loss in AUTO, takeoff/landing never interrupted, flickering lock, fake-but-good-looking fixes, source switching, arming gate |
| `test_check_params.py` | the parameter checker; guards on the no-RC and GPS-loss settings |
| `test_groundstation.py` | every question; parameter, mission and settings generation (incl. checked against ArduPlane's source when `ARDUPILOT_SRC` is set); MAVLink to a fake flight controller; **Wi-Fi and USB-stick round trips against a real agent**; trained models to the Pi; pictures with coordinates |
| `test_gui_browser.py` | **the Ground Station in a real browser** (needs Node.js + Playwright): every tab like a user, sending to a real agent |
| `test_agent.py` | the Pi's agent over real HTTP: settings, flights, Wi-Fi off when armed, USB stick |
| `test_pi4_robustness.py`, `test_config.py` | late/dropped camera, crashing frame, status file, memory, settings overlay and validation |
| `test_real_model.py` | the **real** recognition model through the onboard code |
| `test_install.py` | the Pi installer (dry run) and the service files |
| `test_planner.py`, `test_sitl_analysis.py` | survey geometry, mission checks, simulator grading |
| `test_geometry.py`, `test_calibration.py`, `test_fisheye.py`, `test_siyi.py`, `test_camera_reader.py`, `test_realcam.py` | geo-referencing, lens calibration, the 160° fisheye, the SIYI gimbal protocol, camera reading, preflight |
| `test_integration.py`, `test_misc_paths.py`, `test_pipeline.py`, `test_confirm.py`, `test_health.py`, `test_store_fire.py`, `test_review.py`, `test_training_tools.py` | telemetry against a fake ArduPlane, the main loop, storage, fire detection, review page, training tools |
| `test_sitl_integration.py` (slow, `SILM_SITL_TESTS=1`) | a **factory-fresh simulated flight controller set up only by the Ground Station** (parameters, script over MAVFTP, reboot, mission), then flown with GPS jamming: every check must pass |

GitHub runs them on every push that touches `onboard/` (with the real model
and the parameter check against the current ArduPlane source).
