# SILM — flight-ready guide (failsafe + recognition, end to end)

This is the rigorous, do-it-yourself guide: **where every file goes, which
command to run where, how to see for yourself it works, how to iterate, and the
flight checklist.** Follow it top to bottom the first time.

**There is no RC link on this aircraft** (no ExpressLRS receiver, no telemetry
radio). Every flight is a fully automatic mission planned on the laptop
(section M), uploaded over USB on the ground, and flown from a hand launch to an
automatic belly landing and disarm. Nobody can take over in flight, so every
failure path in this guide ends in an automatic landing.

Two independent subsystems, neither needing an operator link in flight:

- **A. Flight + GPS-loss failsafe** — lives in **ArduPilot on the Matek H743** (parameters
  + a small Lua script). Launches, flies and lands on its own; if GPS is lost,
  jammed or spoofed it dead-reckons to the mission's landing sequence and lands,
  inside a geofence. Works even if the Pi is off.
- **B. Recognition + photo** — lives on the **Raspberry Pi 3**. Reads telemetry,
  watches the camera, recognises farm/forestry objects and fire, saves
  geo-tagged pictures to the SD card. Never commands the aircraft.

> Everything here uses parts already in the build: the SIYI A2 mini sends
> digital video over Ethernet straight into the Pi 3. Whether the Pi 3 is fast
> enough for your altitude is measured by `tools/preflight.py` (B4); if it
> isn't, a Coral USB accelerator is the one addition.

---

## 0. File map — what lives where

On your **dev machine / GPU box** (the repo):
```
onboard/
  planner/                    LAPTOP mission planner (GUI in the browser)
    app.py                    python planner/app.py -> http://127.0.0.1:8765
    survey.py                 survey lines, takeoff, landing sequence, checks
    static/index.html         the map page
    README.md                 how to plan, upload, test
  run.py                      entry point for the Pi app
  config.yaml                 all runtime settings
  requirements.txt            Pi Python deps
  silm_recon/                 the onboard app (runs on the Pi)
    telemetry.py              read-only MAVLink from the H743
    camera.py                 frame capture (RTSP over Ethernet / USB / CSI / file)
    siyi.py                   SIYI gimbal: point down, read its angle (UDP)
    detector.py               TFLite detector (SSD or YOLO)
    fire.py                   no-training fire/smoke heuristic
    georef.py                 pixel -> ground lat/lon
    failsafe.py               GPS-health monitor + optical-flow aid
    confirm.py                multi-frame confirmation (fewer false alarms)
    health.py                 Pi temperature / throttling / storage monitor
    store.py                  save photos + metadata + de-dup
    mavmission.py             .waypoints files, mission upload/download, no-RC check
    recon.py                  main loop
    tiles.py                  optional offline basemap
  ardupilot/
    gps_denied_failsafe.param no-RC + failsafe params   <-- load once (Mission Planner / QGC)
    scripts/silm_gps_loss.lua GPS-loss -> landing script <-- copy to the H743 SD card
  training/
    ADDING_OBJECTS.md         tutorial: add a new object and train on it
    objects/TEMPLATE.md       object card to fill in per new object (deer.md = example)
    classes.yaml              the class list
    make_synthetic.py         synthetic bootstrap / practice dataset
    prelabel.py               draft labels with the current model
    merge_datasets.py         merge datasets, remapping classes by name
    check_dataset.py          find label problems, preview boxes
    train.py / export.py      train and export to TFLite
    README.md                 data + training guide
  tools/
    preflight.py              does it work on THIS aircraft? PASS/WARN/FAIL per part
    check_params.py           check a .param file against the ArduPlane source
    sitl_failsafe_test.py     fly + grade a no-RC mission with GPS loss/jamming in the simulator
    calibrate_camera.py       measure the lens from checkerboard photos
    pixels_on_target.py       is an object big enough to detect at an altitude?
    threshold_sweep.py        precision / recall per class at each threshold
    make_review.py            post-flight map page with every detection + photo
    eval_model.py             run a model over images (onboard code path)
    make_geojson.py           turn a flight log into a map layer
    download_tiles.py         pre-cache an offline basemap
  tests/                      automated tests (python3 -m pytest)
  models/                     .tflite models live here (not committed)
  systemd/silm-recon.service  auto-start on the Pi
```

On the **aircraft Raspberry Pi 3**: a clone of `onboard/` at
`/home/pi/uav-silm/onboard`, plus your `models/*.tflite`.

On the **H743**: the parameters from `ardupilot/gps_denied_failsafe.param`,
`silm_gps_loss.lua` in `APM/scripts/` on its SD card, and the mission from the
planner.

On the **laptop**: a clone of the repo; `pip install -r planner/requirements.txt`.

---

## A. Flight without RC, and the GPS-loss failsafe (do this first)

### A1. Load the parameters and the script
1. Optional but recommended: check the file against **your** firmware version
   first (it is verified against ArduPlane 4.7.2):
   ```bash
   git clone --depth 1 --branch ArduPlane-stable https://github.com/ArduPilot/ardupilot
   python3 tools/check_params.py --src ardupilot ardupilot/gps_denied_failsafe.param
   ```
   It reports unknown names (typos, renamed or Copter-only parameters),
   duplicates and out-of-range values.
2. Connect the H743 by USB to Mission Planner (or QGroundControl) — only for
   this one-time parameter load; day-to-day missions come from the SILM planner.
   **CONFIG → Full Parameter List → Load from file** →
   `onboard/ardupilot/gps_denied_failsafe.param`.
3. **Read every changed line** against the comments in that file. Adjust
   `FENCE_RADIUS` (must contain the whole mission — the planner tells you the
   value), `FENCE_ALT_MAX`, `RTL_ALTITUDE`, the `AIRSPEED_*` limits and the
   `LAND_*` flare values for your site and airframe.
4. **Write Params**, then reboot.
5. Copy `onboard/ardupilot/scripts/silm_gps_loss.lua` to the flight
   controller's SD card at `APM/scripts/` (Mission Planner: CONFIG → MAVFtp, or
   take the card out). Reboot. The Messages tab shows
   `SILM GPS-loss failsafe loaded`. Reboot once more and the `SLM_` parameters
   appear (`SLM_GPS_LOSS_S` etc.).

### A2. What each piece does
No RC receiver:
- **`INITIAL_MODE=10`**: boots into AUTO (nothing else could select a mode).
  You arm from the laptop planner just before the throw.
- **`THR_FAILSAFE=0`, `ARMING_SKIPCHK=64`**: there is no receiver, so no RC
  failsafe (it would trigger at once) and no RC pre-arm check (it would never
  arm). `FS_GCS_ENABL=0`: no ground-station link to lose either.
- **`ARMING_MIS_ITEMS=13`**: it refuses to arm unless the mission has TAKEOFF,
  DO_LAND_START and LAND — a mission that could not end in a landing can't fly.
- **Hand launch — `TKOFF_THR_MINACC=15`, `TKOFF_THR_DELAY=3`**: armed in AUTO
  the motor stays off until the throw (15 m/s² forward), then waits 0.3 s more so
  the pusher prop clears your hand. The TAKEOFF item climbs at 15° to 50 m.
- **Landing — `RTL_AUTOLAND=2`, `LAND_*`**: the planner puts `DO_LAND_START` →
  final-approach waypoint → `LAND` at the end of every mission. Any RTL (GPS
  loss, fence) jumps straight to that sequence instead of circling over home
  until the battery dies. `LAND_FLARE_SEC/ALT` and `LAND_PITCH_DEG` set the
  flare; `LAND_DISARMDELAY=10` disarms 10 s after touchdown.
- **Battery — `BATT_FS_LOW_ACT=2`, `BATT_FS_CRT_ACT=2`**: on a plane "Land"
  means "jump to the landing sequence", so a low battery also ends in the
  planned landing.

GPS loss:
- **Dead reckoning** (`ARSPD_USE=1`): ArduPlane keeps navigating on airspeed,
  heading and the wind it learnt while GPS was good. This is automatic in
  ArduPlane; it needs a working, calibrated airspeed sensor.
- **`silm_gps_loss.lua`**: after `SLM_GPS_LOSS_S` (5) seconds of bad GPS in AUTO
  it switches to RTL, which (with `RTL_AUTOLAND=2`) flies the mission's landing
  sequence — the aircraft heads for the landing strip instead of carrying on
  outward. It never interrupts the takeoff, and never restarts a landing that
  is already under way.
  GPS counts as bad without a 3D fix, below `SLM_MIN_SATS` (6) satellites or
  above `SLM_MAX_HDOP` (2.5). An episode only ends after `SLM_GPS_OK_S` (10)
  seconds of good GPS, so a jammer's flickering lock can't reset the timer. It
  only acts in AUTO.
- **`EK3_OPTIONS=1` (JammingExpected)**: after a GPS loss the EKF re-checks GPS
  quality before trusting it again, so a bad or faked signal can't snap the
  position to the wrong place.
- **EKF source set 2 = "no GPS"** (`EK3_SRC2_*`): from the first bad GPS sample
  until GPS has been good for `SLM_GPS_OK_S`, `silm_gps_loss.lua` switches the
  EKF to source set 2: no GPS position or velocity, barometer height, compass
  heading. It dead-reckons on airspeed instead of fusing a jammed receiver,
  which still reports a flickering "3D fix" with fake positions and fake climb
  rates. In SITL, the fake climb rate made the landing flare fire 30–100 m up,
  and the aircraft touched down hundreds of metres short. `SLM_SRC_SW=0` turns
  the switch off.
- **`AHRS_OPTIONS=1` (no DCM fallback in forward flight)**: otherwise, when the
  EKF rejects GPS but the receiver claims a fix, the autopilot falls back to
  DCM, which steers on the **raw** GPS position, i.e. wherever the jammer says.
  In SITL this flew the aircraft out of the geofence.
- **Arming gate** (in `silm_gps_loss.lua`): `AHRS_OPTIONS=1` also removes
  ArduPilot's own "AHRS: not using configured AHRS type" pre-arm check, so the
  aircraft could be armed and thrown **before the EKF has a GPS position**. In
  SITL the EKF then set its origin during the climb, and every height after that
  was ~20 m off (flare 20 m up). The script therefore refuses to arm, with
  `PreArm: SILM: waiting for EKF to use GPS` / `waiting for home` / `GPS not
  good enough`, until all three are fine. **After power-on, wait for the GPS
  and for this message to clear before pressing Arm for launch.**
- **`FENCE_*`**: a geofence around home forces RTL → landing sequence if the
  (dead-reckoned) position leaves it, bounding the worst case.
- **`FS_SHORT_ACTN=0`, `FS_LONG_ACTN=0`**: with no link these never fire; kept at
  0 so a stray setting can't abort a mission.

### A3. Prove it without flying (SITL — do this before the first flight)
On your PC, with ArduPilot's simulator installed. **Step-by-step guide
(Windows/WSL2, first run and repeat runs): [`SITL_HOWTO.md`](SITL_HOWTO.md).**
Short form:
```bash
# once: a folder to start SITL from, with the script in scripts/
mkdir -p ~/sitlrun/scripts
cp ~/uav-silm/onboard/ardupilot/scripts/silm_gps_loss.lua ~/sitlrun/scripts/

# terminal 1: start SITL (-L only takes a NAMED location; use --custom-location)
cd ~/sitlrun && ~/ardupilot/Tools/autotest/sim_vehicle.py -v ArduPlane --console \
    --custom-location=59.40,24.70,30,0
# once, at the MAVProxy prompt (SITL's battery is 3S, this file is for 4S):
#   param set BATT_VOLT_MULT 13.4667

# terminal 2, from onboard/ (first run enables scripting -> restart SITL once):
python3 tools/sitl_failsafe_test.py --fault jam --wind 6 --out sitl_jam
python3 tools/sitl_failsafe_test.py --fault disable --fault-s 120 --out sitl_off
```
The tool loads the parameters, builds a complete no-RC mission with the
planner's code (survey area 360–1200 m out, landing back at launch into the
wind), arms, flies it in AUTO with no RC, and at 700 m from home turns on SITL's
**GPS jamming simulation** (or switches GPS off). It grades the run (PASS/FAIL):
failsafe in time, headed for the landing point while blind, stayed inside the
fence, estimate re-converged after GPS returned, **landed and disarmed by
itself**, close to the planned touchdown. It saves `track.png` (true track vs
the autopilot's estimate, with the planned touchdown), `track.csv` and
`result.json`. Try stronger wind and longer outages (`--fault-s 300` makes it
land blind) to see how far dead reckoning drifts. Fly **your own** planned
mission the same way: `--mission silm_mission.waypoints --trigger-s 240` (start
SITL at your launch point). **Do not fly the real aircraft until these pass.**

### A4. Honest limits
- Dead-reckoning drifts with wind-estimate error — it's a controlled recovery,
  not infinite blind flight. A landing flown after GPS loss can be **tens of
  metres** off the strip; choose a landing area with that margin. The geofence
  bounds the worst case. This is how commercial fixed-wing autopilots handle
  GPS loss; it is a safety feature, not GPS-denied precision navigation.
- **No RC means no tuning flights with a pilot.** AUTOTUNE and trimming
  normally need manual flying. Start from well-chosen defaults for a 1.4 m
  pusher, check control directions on the bench (AUTO on the ground: tilt the
  aircraft, surfaces must correct), and keep the first missions short and close.
  If you can borrow any MAVLink link or receiver for the first tuning flights,
  do; it is the single biggest risk reduction for flight one.
- An automatic belly landing has no go-around for obstacles: the strip must be
  clear, long (≥ 200 m) and wide (≥ 50 m), with nothing tall under the approach.

### A5. (Optional) companion optical-flow backup
The Pi can send the FC a backup ground-velocity estimate from the camera
(`failsafe.send_to_fc`). **Leave it off until** you've done the bench check in
step B6 and confirmed sane values, then set `EK3_SRC2_VELXY=6` (ExternalNav)
per the comments in the param file. Flight safety must never depend on the Pi.

---

## M. Plan the mission on the laptop (planner GUI)

All mission planning is done in the SILM planner, a map page that runs on
your laptop. Details: [`planner/README.md`](planner/README.md).

### M1. Install and start (laptop, once)
```bash
cd uav-silm/onboard
python -m pip install -r planner/requirements.txt
python planner/app.py           # opens http://127.0.0.1:8765
```
It reads `config.yaml` (camera, detection region) and
`ardupilot/gps_denied_failsafe.param` (fence, altitude limit, cruise speed) so its
numbers match the aircraft. Map tiles need internet; plan at home and save the
plan (**Save plan**) if the field has no signal.

### M2. Plan
1. **Set launch point** — where you will throw. This is home.
2. **Draw survey area** — click the corners; pick another tool to finish.
3. **Set landing point** + **Set landing direction** — a big flat strip,
   landing **into the forecast wind**. Check the approach (red line) crosses
   nothing tall.
4. **Smallest target** — e.g. *car*: it shows the pixels on target and
   offers a lower altitude if marginal.
5. Read the stats and messages. **Red errors block download and upload.**
   Typical warnings: fence margin (tick *Also set FENCE_RADIUS* when uploading),
   battery share, distance from launch (permission / line of sight).

### M3. Upload and verify
Plug the H743 into the laptop by USB, choose its port, **Upload mission**. It
refuses an armed aircraft or a mission with errors, then reads the mission back
and must say **identical**. Or **Download .waypoints** and upload it with
Mission Planner / QGroundControl.

### M4. Check it on the aircraft
`tools/preflight.py` on the Pi downloads the stored mission and checks TAKEOFF
first, a DO_LAND_START landing sequence, LAND last, inside the fence and
altitude limit (part `mission`). Then fly it in SITL (A3, `--mission`).

### M5. Launch and land (no RC)
1. Aircraft powered, GPS fix (≥ 10 satellites; preflight shows it). It boots
   straight into **AUTO** (`INITIAL_MODE=10` — ArduPilot's documented setting
   for "AUTO on boot without a receiver").
2. Laptop on USB, planner open: hold the aircraft ready to throw, propeller
   clear, and press **Arm for launch**. It downloads the mission from the
   aircraft, refuses if it has no landing sequence, switches to AUTO and arms
   (the autopilot's own pre-arm checks still apply; a refusal shows its reason).
   **Unplug the USB cable.** (Disarm is next to it if you change your mind.)
3. Hold it level, nose into the wind, and **throw firmly**, slightly nose-up.
   The motor starts ~0.3 s after the throw; the TAKEOFF item climbs to 50 m.
4. It flies the survey, then the landing sequence: final approach, flare,
   belly landing, disarm after 10 s. Stay clear of the approach path.
5. If anything is wrong after launch there is nothing to switch — the failsafes
   (GPS loss, battery, fence) all end in the same landing. Plan accordingly.

---

## B. The recognition + photo system (the Pi)

### B1. Hardware
- **Camera video:** connect the SIYI A2 mini's Ethernet to the Pi 3's Ethernet
  port (with the cable/adapter supplied with the camera). The gimbal control
  uses the same cable.
- **Camera power:** the A2 mini needs **12 V**. The 4S battery is 14.8-16.8 V,
  so use a regulated 12 V output (check whether your power board has one; if
  not, a small 12 V BEC).
- **Network:** the camera is at 192.168.144.25. Give the Pi a fixed address on
  that network, with no gateway so Wi-Fi/4G stays the default route. On
  Raspberry Pi OS Bookworm (NetworkManager):
  ```bash
  sudo nmcli con add type ethernet ifname eth0 con-name siyi-camera \
      ipv4.method manual ipv4.addresses 192.168.144.20/24 ipv6.method disabled
  sudo nmcli con up siyi-camera
  ping -c 3 192.168.144.25
  ```
- **Flight controller:** wire the Pi to a **spare H743 UART** (e.g. TELEM2) or
  the H743 USB. In ArduPilot set that port `SERIALx_PROTOCOL=2`,
  `SERIALx_BAUD=115`.
- Optional: Coral USB accelerator (works on the Pi 3's USB 2.0 ports, slower
  than on USB 3).

### B2. Install (on the Pi, with internet on the bench)
Use **64-bit** Raspberry Pi OS.
```bash
git clone https://github.com/rometlehari-maker/uav-silm.git ~/uav-silm
cd ~/uav-silm/onboard
sudo apt update
sudo apt install -y python3-opencv python3-numpy python3-yaml python3-venv \
    gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav
python3 -m venv --system-site-packages ~/silm-venv
~/silm-venv/bin/pip install -r requirements.txt
# get a detection model (see models/README.md): the COCO baseline, or your
# trained models/silm_farm.tflite from section C.
```
OpenCV must come from apt, not pip: apt's build has GStreamer, which the camera
needs for the Pi's hardware video decoder (see `requirements.txt`). Run
everything with `~/silm-venv/bin/python3`.

Check the video path on its own first:
```bash
gst-launch-1.0 rtspsrc location=rtsp://192.168.144.25:8554/main.264 latency=0 ! \
    rtph264depay ! h264parse ! v4l2h264dec ! fakesink sync=false -v
```
Frames flowing (no errors, caps printed) means the hardware decoder path works.

### B3. Configure
Edit `config.yaml`:
- `telemetry.connection` → your serial port (`/dev/serial0`, `/dev/ttyACM0`…).
- `camera.url` → the RTSP address (default is the A2 mini's).
- `gimbal` → leave enabled; `preflight.py` tells you whether it answers.
- `detector.model/labels/format` → your model; `detector.roi`/`tiled` → the
  detection region (defaults: central half, 2×2 tiles; see README *Performance*).
- `storage.output_dir` → a path on the SD card / USB stick.

### B4. Preflight check, then see it work
```bash
~/silm-venv/bin/python3 tools/preflight.py --config config.yaml --alt-m 100 --speed-mps 19
```
It checks the camera stream (resolution, frame rate), the gimbal (answers,
points down; follow its instruction to confirm the angle is stabilised), the
flight controller (message rates, GPS, failsafe parameters, Lua script
loaded), the model (labels, and **measured** detection speed on this Pi against
what your altitude and speed need), storage, temperature and the clock. Fix
every FAIL. Then:
```bash
~/silm-venv/bin/python3 run.py --config config.yaml --selftest
```
prints per-frame detections and a demo geo-reference from the live camera.

### B5. Calibrate the lens (one time)
The A2 mini's 160° fisheye bends straight lines strongly; without calibration,
positions away from the image centre are off.
1. Print a large checkerboard (A3, 9×6 inner corners, ~35 mm squares) and fix it
   to something flat and rigid.
2. On the Pi, from the live stream at the resolution you fly with (keep the
   gimbal still):
   ```bash
   ~/silm-venv/bin/python3 tools/calibrate_camera.py --fisheye \
       --device rtsp://192.168.144.25:8554/main.264 --capture 30 \
       --cols 9 --rows 6 --square-mm 35
   ```
   Move the board so it covers the whole frame, **especially the edges and
   corners**, at different tilts. (Or take photos and use `--images folder/`.)
3. RMS error under ~0.5 px is good. Paste the printed `calibration:` block
   (it includes `model: fisheye`) under `georef:` in `config.yaml`.
4. If the gimbal can't be read (`gimbal.enabled: false`), measure the camera's
   fixed tilt and set `georef.forward_tilt_deg` (90 = straight down).
5. First flight: compare a saved detection's `ground_lat/lon` with the object's
   real position to confirm.

### B6. Verify the failsafe companion aids on the bench
```bash
python3 run.py --config config.yaml    # with the FC connected and GPS indoors
```
- Cover/uncover the GPS or walk indoors: the log prints
  `GPS state OK -> DEGRADED/LOST` and writes `gps_health.ndjson`.
- Wave the camera over a textured surface: `optical_flow.ndjson` fills with
  velocity estimates. Only once these look sane should you consider
  `failsafe.send_to_fc: true` + the EKF source param.
- `health.ndjson` records CPU temperature, throttling/under-voltage and free
  storage every 10 s; warnings also appear in `journalctl -u silm-recon`. Run it
  with the fuselage closed and the motor running on the bench to see whether the
  Pi gets too hot.

### B7. Auto-start on boot
```bash
sudo cp systemd/silm-recon.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now silm-recon.service
journalctl -u silm-recon -f
```
It sits idle until the aircraft is **armed, in AUTO, above `min_alt_m`** — so
nothing is logged on the bench or during the launch run-up.

---

## C. Train the recognition model on your objects

Full detail in `training/README.md`. Short version:

### C1. Prove the training chain now (synthetic, CPU — minutes)
```bash
cd onboard/training
python3 make_synthetic.py --out datasets/synth --n-train 350 --n-val 70
python3 train.py --data datasets/synth/data.yaml --model yolo11n.pt \
    --epochs 12 --imgsz 256 --device cpu --name synth_proof
python3 export.py --weights runs/detect/synth_proof/weights/best.pt \
    --imgsz 256 --model-name silm_synth
cd ..
python3 tools/eval_model.py --model models/silm_synth.tflite \
    --labels models/silm_synth_labels.txt --format yolo \
    --images training/datasets/synth/images/val --out /tmp/annot
```
Open `/tmp/annot/*.jpg` — you'll see boxes from a model **you just trained**,
running through the **exact onboard code**. (Synthetic shapes only — proof, not
field-ready.)

### C2. A real model (your data, GPU box)
1. Collect + label real aerial images (Roboflow is easiest) using the classes in
   `training/classes.yaml`.
2. `python3 train.py --data datasets/farm/data.yaml --model yolo11s.pt --epochs 150 --imgsz 640 --device 0 --name farm_v1`
3. Check `runs/detect/farm_v1/` metrics (mAP50, confusion matrix).
4. `python3 export.py --weights runs/detect/farm_v1/weights/best.pt --imgsz 320 --model-name silm_farm` (add `--edgetpu` for Coral).
5. Copy `models/silm_farm.tflite` + `_labels.txt` to the Pi; set `config.yaml`.

### C3. Adding a new object
Follow `training/ADDING_OBJECTS.md`: check it's big enough to see at your
altitude, add the class, collect and label (with `prelabel.py` drafting),
merge with the old data (`merge_datasets.py`), check (`check_dataset.py`),
retrain from your current model, compare, export, deploy.

### C4. Iterate (the loop that actually makes it good)
Set `storage.save_all_frames: true`, fly, pull the card, label the frames the
model got wrong, add them to the dataset, retrain (`--model <previous best.pt>`
to continue), re-export, re-fly. Each loop improves it on *your* fields.

---

## D. Flight-day checklist
1. `python3 -m pytest` passes (section F). SITL tests passed (A3), including
   your own mission (`--mission`).
2. Params written and `silm_gps_loss.lua` loaded on the FC (A1): Messages tab
   shows `SILM GPS-loss failsafe loaded`.
3. `tools/preflight.py` shows no FAIL (B4); `--selftest` clean; lens
   calibrated (B5).
4. Mission planned in the laptop planner (M2) with **no red errors**, landing
   into today's wind; uploaded and read back **identical** (M3); `FENCE_RADIUS`
   at least the planner's "Fence needed"; preflight part `mission` PASS.
5. Pre-cache an offline basemap if you want map context (`tools/download_tiles.py`).
6. Power on; confirm the `silm-recon` service is running and sees telemetry
   (`journalctl -u silm-recon -f` shows `idle ... gps=OK`).
7. Walk the landing strip and the approach: clear, nobody standing on it.
8. **Arm for launch** in the planner, unplug USB, throw into the wind (M5). The Pi starts saving once airborne above
   `min_alt_m`. Watch it climb out; note the time (battery endurance).
9. It lands and disarms itself on the strip. Wait for it to stop and the motor
   to be disarmed (≥ 10 s) before walking up; unplug the battery.
10. Pull the SD card and build the review page:
   ```bash
   # replace with your mission folder from the SD card:
   python3 tools/make_review.py /media/sd/silm_captures/mission_20261110_101500
   # -> writes review.html inside that folder
   ```
   It shows the flight track (red where GPS was degraded), every detection with
   its photo, and the GPS and Pi-health events. `tools/make_geojson.py` exports
   the detections for QGIS or other map tools.

## E. Legal / safety
- **No-RC autonomous flight is outside the EASA "open" category**, which
  requires that the remote pilot can always take control. Fully automatic
  flight with no link needs the **"specific" category**: an operational
  authorisation from Transpordiamet (SORA-based, or a standard scenario if one
  fits). Expect questions on containment (geofence, `FENCE_ACTION=1`), how every
  flight ends (automatic landing, battery failsafe → land) and monitoring; the
  optional 4G link (SIM7600E) is the obvious way to monitor.
- GPS-denied **failsafe** is a safety feature (anti-flyaway); it is not a licence
  to fly beyond what your authorisation allows. Keep within your authorised
  area and the geofence.
- Don't pre-cache Google imagery (against their terms) — use a permitted
  satellite/terrain source for the offline basemap.
- Review privacy rules before photographing people or private property.

---

## F. Automated tests

From `onboard/` on your PC (or the Pi):
```bash
python3 -m pip install pytest lupa opencv-python-headless pymavlink pyyaml numpy
python3 -m pytest -q
# include the parameter check against an ArduPilot checkout:
ARDUPILOT_SRC=../ardupilot python3 -m pytest -q
```
What they cover:
- `test_gps_loss_lua.py`: runs the real Lua script with mocked ArduPilot
  bindings: GPS loss in AUTO, other modes left alone, takeoff and an active
  landing sequence never interrupted, short dropouts, flickering
  lock (jamming), too few satellites, bad HDOP, parameter changes.
- `test_check_params.py`: the parameter checker, and guards on the settings
  that make no-RC missions work (no RC failsafe/arming check, mission must have
  takeoff + landing, `RTL_AUTOLAND=2`, hand-launch throttle, battery → land).
- `test_planner.py`: the laptop planner — survey geometry, landing sequence,
  warnings, pixels-on-target, `.waypoints` round trip, the no-RC mission check,
  upload/download against a fake flight controller, and the web API.
- `test_sitl_analysis.py`: the simulator grading (failsafe, landed, touchdown
  error) and that its built-in mission is a valid no-RC mission.
- `test_geometry.py`: geo-referencing and the optical-flow speed estimate
  against an independent camera model, including rendered ground images.
- `test_calibration.py`: the lens calibration recovers a known lens.
- `test_siyi.py`, `test_camera_reader.py`, `test_fisheye.py`,
  `test_realcam.py`: the real camera: gimbal protocol (checked byte for byte
  against published SIYI frames), newest-frame RTSP reading, the 160° fisheye
  model and its calibration, letterboxing, telemetry at the frame's time,
  gimbal tilt in geo-referencing, and the preflight tool.
- `test_integration.py`, `test_misc_paths.py`: telemetry against a fake
  ArduPlane speaking real MAVLink (including a link drop), the app's main loop
  end to end, offline map tiles and the tile downloader, the optical-flow
  MAVLink message.
- `test_training_tools.py`: the add-a-new-object tools (merging by class
  name, dataset checks, pre-labelling, per-class thresholds, size calculator).
- `test_pipeline.py`, `test_confirm.py`, `test_health.py`,
  `test_store_fire.py`, `test_review.py`, `test_sitl_analysis.py`: the
  onboard pipeline end to end and each part on its own.

GitHub runs the same tests (with the parameter check against the current
ArduPlane stable source) on every push that touches `onboard/`
(`.github/workflows/onboard-tests.yml`).
