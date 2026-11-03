# SILM — flight-ready guide (failsafe + recognition, end to end)

This is the rigorous, do-it-yourself guide: **where every file goes, which
command to run where, how to see for yourself it works, how to iterate, and the
flight checklist.** Follow it top to bottom the first time.

Two independent subsystems, neither needing an operator link in flight:

- **A. GPS-loss failsafe** — lives in **ArduPilot on the Matek H743**. Keeps the
  aircraft safe (dead-reckon home / loiter / land) if GPS is lost, jammed or
  spoofed. Works even if the Pi is off.
- **B. Recognition + photo** — lives on the **Raspberry Pi 3**. Reads telemetry,
  watches the camera, recognises farm/forestry objects and fire, saves
  geo-tagged pictures to the SD card. Never commands the aircraft.

> The one hardware item the current build likely lacks: an **AHD→USB capture
> dongle** so the Pi can see the analog SIYI A2 Mini. Everything else is parts
> you already have. A Coral USB stick is optional (makes recognition real-time).

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
    camera.py                 frame capture (USB dongle / CSI / file)
    detector.py               TFLite detector (SSD or YOLO)
    fire.py                   no-training fire/smoke heuristic
    georef.py                 pixel -> ground lat/lon
    failsafe.py               GPS-health monitor + optical-flow aid
    store.py                  save photos + metadata + de-dup
    recon.py                  main loop
    tiles.py                  optional offline basemap
  ardupilot/
    gps_denied_failsafe.param ArduPilot failsafe params  <-- load in Mission Planner
  training/
    classes.yaml              the class list
    make_synthetic.py         synthetic bootstrap dataset
    train.py / export.py      train and export to TFLite
    README.md                 data + training guide
  tools/
    eval_model.py             run a model over images (onboard code path)
    make_geojson.py           turn a flight log into a map layer
    download_tiles.py         pre-cache an offline basemap
  models/                     .tflite models live here (not committed)
  systemd/silm-recon.service  auto-start on the Pi
```

On the **aircraft Raspberry Pi 3**: a clone of `onboard/` at
`/home/pi/uav-silm/onboard`, plus your `models/*.tflite`.

On the **H743**: just the parameters from `ardupilot/gps_denied_failsafe.param`.

---

## A. The GPS-loss failsafe (do this first — it's the safety net)

### A1. Load the parameters
1. Connect the H743 to Mission Planner (USB or your normal telemetry).
2. **CONFIG → Full Parameter List → Load from file** →
   `onboard/ardupilot/gps_denied_failsafe.param`.
3. **Read every changed line** against the comments in that file. Adjust
   `FENCE_RADIUS`, `FENCE_ALT_MAX`, `ALT_HOLD_RTL`, airspeed limits for your site.
4. **Write Params**, then reboot the FC.

### A2. What each piece does
- `FS_DR_ENABLE=1` + `FS_LONG_ACTN=1`: on GPS loss the EKF **dead-reckons**
  (airspeed + heading + learnt wind) and flies an RTL path home instead of
  flying away. If GPS returns, it resumes normal nav automatically.
- `EK3_POS_I_GATE` / `EK3_GPS_CHECK`: a spoofed "teleport" is rejected by the
  EKF innovation checks rather than believed.
- `FENCE_*`: hard geofence backstop so a worst-case dead-reckoning drift still
  can't leave your operating area — it forces RTL/land at the boundary.
- `THR_FAILSAFE=1`: independent RC-loss failsafe for the manual return phase.

### A3. Prove it without flying (SITL — strongly recommended)
Install ArduPilot SITL once on your PC, then:
```bash
sim_vehicle.py -v ArduPlane --console --map
# load your params, plan a small AUTO mission, arm, takeoff, then simulate
# GPS loss mid-mission:
param set SIM_GPS1_ENABLE 0        # kill GPS in the sim
# watch: the plane should switch to dead-reckoning and track home, not fly away.
param set SIM_GPS1_ENABLE 1        # restore; it should re-acquire and resume
```
Also simulate a spoof-like jump (`SIM_GPS1_POS` offsets) and confirm the EKF
rejects it. **Do not fly the real aircraft until SITL behaves correctly.**

### A4. Honest limits
Dead-reckoning drifts with wind-estimate error — it's a controlled recovery,
not infinite blind flight. The geofence bounds the worst case. This is exactly
how commercial fixed-wing autopilots handle GPS loss; it is a safety feature,
not GPS-denied precision navigation.

### A5. (Optional) companion optical-flow backup
The Pi can send the FC a backup ground-velocity estimate from the camera
(`failsafe.send_to_fc`). **Leave it off until** you've done the bench check in
step B6 and confirmed sane values, then enable `EK3_SRC2_VELXY` per the comments
in the param file. Flight safety must never depend on the Pi.

---

## B. The recognition + photo system (the Pi)

### B1. Hardware
- Plug the **AHD→USB dongle** into the Pi; connect the SIYI A2 Mini's AHD output
  to it. (Or use a Pi CSI camera and set `camera.source: picamera`.)
- Wire the Pi to a **spare H743 UART** (e.g. TELEM2) or the H743 USB. In
  ArduPilot set that port `SERIALx_PROTOCOL=2`, `SERIALx_BAUD=115`.
- Optional: Coral USB accelerator.

### B2. Install (on the Pi, with internet on the bench)
```bash
git clone <your repo> ~/uav-silm         # or copy onboard/ over
cd ~/uav-silm/onboard
sudo apt update && sudo apt install -y python3-opencv python3-numpy python3-yaml
python3 -m pip install -r requirements.txt
# get a detection model (see models/README.md); start with the COCO baseline
# or your trained models/silm_farm.tflite from section C.
```

### B3. Configure
Edit `config.yaml`:
- `telemetry.connection` → your serial port (`/dev/serial0`, `/dev/ttyACM0`…).
- `camera.device` → usually `0` (the dongle).
- `detector.model/labels/format` → your model (COCO SSD, or `format: yolo` +
  `silm_farm.tflite`).
- `georef.hfov_deg` → your lens FOV (calibrate, step B5); `forward_tilt_deg` →
  your real camera mount angle (90 = straight down).
- `storage.output_dir` → a path on the SD card / USB stick.

### B4. See it work on the bench — no flight controller needed
```bash
# point camera.source at a folder of test JPEGs, or leave 'usb' with the dongle:
python3 run.py --config config.yaml --selftest
```
You'll see per-frame detections and a demo geo-reference printed. This proves the
capture path + model + fire detector + geo-ref all work.

### B5. Calibrate the geo-reference (one time)
1. Put a known object in frame at a known distance, or fly a quick test and
   compare a saved detection's `ground_lat/lon` against the object's real GPS.
2. Adjust `georef.hfov_deg` until the projected position matches. (Too-small
   HFOV → positions too close to the aircraft; too-large → too far.)
3. Set `forward_tilt_deg` to your mount (measure it; 90° = nadir).

### B6. Verify the failsafe companion aids on the bench
```bash
python3 run.py --config config.yaml    # with the FC connected and GPS indoors
```
- Cover/uncover the GPS or walk indoors: the log prints
  `GPS state OK -> DEGRADED/LOST` and writes `gps_health.ndjson`.
- Wave the camera over a textured surface: `optical_flow.ndjson` fills with
  velocity estimates. Only once these look sane should you consider
  `failsafe.send_to_fc: true` + the EKF source param.

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

### C3. Iterate (the loop that actually makes it good)
Set `storage.save_all_frames: true`, fly, pull the card, label the frames the
model got wrong, add them to the dataset, retrain (`--model <previous best.pt>`
to continue), re-export, re-fly. Each loop improves it on *your* fields.

---

## D. Flight-day checklist
1. SITL failsafe test passed (A3). Params written to the FC (A1).
2. Model on the Pi; `--selftest` clean (B4); geo-ref calibrated (B5).
3. Plan the mission in Mission Planner; set a sensible RTL point and geofence.
4. Pre-cache an offline basemap if you want map context (`tools/download_tiles.py`).
5. Power on; confirm the `silm-recon` service is running and sees telemetry
   (`journalctl -u silm-recon -f` shows `idle ... gps=OK`).
6. Arm, hand-launch, AUTO. The Pi starts saving once airborne above `min_alt_m`.
7. On return, switch out of AUTO on ExpressLRS and land manually.
8. Pull the SD card. `python3 tools/make_geojson.py <mission>/detections.ndjson`
   → drop the GeoJSON on a map to review every target + photo. Check
   `gps_health.ndjson` for any GPS events during the flight.

---

## E. Legal / safety
- GPS-denied **failsafe** is a safety feature (anti-flyaway); it is not a licence
  to fly beyond what your local rules allow. In Estonia follow EASA /
  Transpordiamet rules for autonomous/BVLOS flight and keep within your
  authorised area and the geofence.
- Don't pre-cache Google imagery (against their terms) — use a permitted
  satellite/terrain source for the offline basemap.
- Review privacy rules before photographing people or private property.
