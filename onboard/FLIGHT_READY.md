# SILM — flight-ready guide (failsafe + recognition, end to end)

This is the rigorous, do-it-yourself guide: **where every file goes, which
command to run where, how to see for yourself it works, how to iterate, and the
flight checklist.** Follow it top to bottom the first time.

Two independent subsystems, neither needing an operator link in flight:

- **A. GPS-loss failsafe** — lives in **ArduPilot on the Matek H743** (parameters
  + a small Lua script). Dead-reckons, turns for home and stays inside a
  geofence if GPS is lost, jammed or spoofed. Works even if the Pi is off.
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
    recon.py                  main loop
    tiles.py                  optional offline basemap
  ardupilot/
    gps_denied_failsafe.param ArduPilot failsafe params  <-- load in Mission Planner
    scripts/silm_gps_loss.lua GPS-loss -> RTL script     <-- copy to the H743 SD card
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
    sitl_failsafe_test.py     fly + grade a GPS-loss/jamming test in the simulator
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

On the **H743**: the parameters from `ardupilot/gps_denied_failsafe.param`, and
`silm_gps_loss.lua` in `APM/scripts/` on its SD card.

---

## A. The GPS-loss failsafe (do this first — it's the safety net)

### A1. Load the parameters and the script
1. Optional but recommended: check the file against **your** firmware version
   first (it is verified against ArduPlane 4.7.2):
   ```bash
   git clone --depth 1 --branch ArduPlane-stable https://github.com/ArduPilot/ardupilot
   python3 tools/check_params.py --src ardupilot ardupilot/gps_denied_failsafe.param
   ```
   It reports unknown names (typos, renamed or Copter-only parameters),
   duplicates and out-of-range values.
2. Connect the H743 to Mission Planner. **CONFIG → Full Parameter List → Load
   from file** → `onboard/ardupilot/gps_denied_failsafe.param`.
3. **Read every changed line** against the comments in that file. Adjust
   `FENCE_RADIUS` (must contain the whole mission), `FENCE_ALT_MAX`,
   `RTL_ALTITUDE` and the `AIRSPEED_*` limits for your site and airframe.
4. **Write Params**, then reboot.
5. Copy `onboard/ardupilot/scripts/silm_gps_loss.lua` to the flight
   controller's SD card at `APM/scripts/` (Mission Planner: CONFIG → MAVFtp, or
   take the card out). Reboot. The Messages tab shows
   `SILM GPS-loss failsafe loaded`. Reboot once more and the `SLM_` parameters
   appear (`SLM_GPS_LOSS_S` etc.).

### A2. What each piece does
- **Dead reckoning** (`ARSPD_USE=1`): ArduPlane keeps navigating on airspeed,
  heading and the wind it learnt while GPS was good. This is automatic in
  ArduPlane; it needs a working, calibrated airspeed sensor.
- **`silm_gps_loss.lua`**: after `SLM_GPS_LOSS_S` (5) seconds of bad GPS in AUTO
  it switches to RTL, so the aircraft heads home instead of carrying on outward.
  GPS counts as bad without a 3D fix, below `SLM_MIN_SATS` (6) satellites or
  above `SLM_MAX_HDOP` (2.5). An episode only ends after `SLM_GPS_OK_S` (10)
  seconds of good GPS, so a jammer's flickering lock can't reset the timer. It
  never overrides a pilot flying in a manual mode.
- **`EK3_OPTIONS=1` (JammingExpected)**: after a GPS loss the EKF re-checks GPS
  quality before trusting it again, so a bad or faked signal can't snap the
  position to the wrong place.
- **`FENCE_*`**: a geofence around home forces RTL if the (dead-reckoned)
  position leaves it, bounding the worst case.
- **`FS_SHORT_ACTN=0`, `FS_LONG_ACTN=0`**: losing the ExpressLRS link **in AUTO
  continues the mission** (that is the point of a link-free mission); in manual
  modes it still circles, then RTLs. Do **not** set `FS_LONG_ACTN=1`: the
  aircraft would turn home the moment it left radio range.

### A3. Prove it without flying (SITL — do this before the first flight)
On your PC, with ArduPilot's simulator installed (instructions at the top of
`tools/sitl_failsafe_test.py`):
```bash
# in the folder you start SITL from:
mkdir -p scripts && cp ~/uav-silm/onboard/ardupilot/scripts/silm_gps_loss.lua scripts/
Tools/autotest/sim_vehicle.py -v ArduPlane --console --map -L 59.40,24.70,30,0

# in another terminal, from onboard/:
python3 tools/sitl_failsafe_test.py --fault jam --wind 6 --out sitl_jam
python3 tools/sitl_failsafe_test.py --fault disable --fault-s 120 --out sitl_off
```
The tool loads the parameters, uploads a mission 1.2 km out, arms, and at 700 m
from home turns on SITL's **GPS jamming simulation** (or switches GPS off). It
grades the run (PASS/FAIL): RTL in time, headed home while blind, stayed inside
the fence, estimate re-converged after GPS returned, ended near home. It also
saves `track.png` (true track vs the autopilot's estimate), `track.csv` and
`result.json`. Try stronger wind and longer outages to see how far dead
reckoning drifts. **Do not fly the real aircraft until these pass.**

### A4. Honest limits
Dead-reckoning drifts with wind-estimate error — it's a controlled recovery,
not infinite blind flight. The geofence bounds the worst case. This is exactly
how commercial fixed-wing autopilots handle GPS loss; it is a safety feature,
not GPS-denied precision navigation.

### A5. (Optional) companion optical-flow backup
The Pi can send the FC a backup ground-velocity estimate from the camera
(`failsafe.send_to_fc`). **Leave it off until** you've done the bench check in
step B6 and confirmed sane values, then set `EK3_SRC2_VELXY=6` (ExternalNav)
per the comments in the param file. Flight safety must never depend on the Pi.

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
1. `python3 -m pytest` passes (section F). SITL failsafe tests passed (A3).
2. Params written and `silm_gps_loss.lua` loaded on the FC (A1): Messages tab
   shows `SILM GPS-loss failsafe loaded`.
3. `tools/preflight.py` shows no FAIL (B4); `--selftest` clean; lens
   calibrated (B5).
4. Plan the mission in Mission Planner; the geofence radius must contain it.
5. Pre-cache an offline basemap if you want map context (`tools/download_tiles.py`).
6. Power on; confirm the `silm-recon` service is running and sees telemetry
   (`journalctl -u silm-recon -f` shows `idle ... gps=OK`).
7. Arm, hand-launch, AUTO. The Pi starts saving once airborne above `min_alt_m`.
8. On return, switch out of AUTO on ExpressLRS and land manually.
9. Pull the SD card and build the review page:
   ```bash
   # replace with your mission folder from the SD card:
   python3 tools/make_review.py /media/sd/silm_captures/mission_20261110_101500
   # -> writes review.html inside that folder
   ```
   It shows the flight track (red where GPS was degraded), every detection with
   its photo, and the GPS and Pi-health events. `tools/make_geojson.py` exports
   the detections for QGIS or other map tools.

## E. Legal / safety
- GPS-denied **failsafe** is a safety feature (anti-flyaway); it is not a licence
  to fly beyond what your local rules allow. In Estonia follow EASA /
  Transpordiamet rules for autonomous/BVLOS flight and keep within your
  authorised area and the geofence.
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
  bindings: GPS loss in AUTO, manual modes left alone, short dropouts, flickering
  lock (jamming), too few satellites, bad HDOP, parameter changes.
- `test_check_params.py`: the parameter checker, and guards on the settings
  that make link-free missions work (`FS_LONG_ACTN=0`, airspeed, scripting).
- `test_geometry.py`: geo-referencing and the optical-flow speed estimate
  against an independent camera model, including rendered ground images.
- `test_calibration.py`: the lens calibration recovers a known lens.
- `test_siyi.py`, `test_camera_reader.py`, `test_fisheye.py`,
  `test_realcam.py`: the real camera: gimbal protocol (checked byte for byte
  against published SIYI frames), newest-frame RTSP reading, the 160° fisheye
  model and its calibration, letterboxing, telemetry at the frame's time,
  gimbal tilt in geo-referencing, and the preflight tool.
- `test_training_tools.py`: the add-a-new-object tools (merging by class
  name, dataset checks, pre-labelling, per-class thresholds, size calculator).
- `test_pipeline.py`, `test_confirm.py`, `test_health.py`,
  `test_store_fire.py`, `test_review.py`, `test_sitl_analysis.py`: the
  onboard pipeline end to end and each part on its own.

GitHub runs the same tests (with the parameter check against the current
ArduPlane stable source) on every push that touches `onboard/`
(`.github/workflows/onboard-tests.yml`).
