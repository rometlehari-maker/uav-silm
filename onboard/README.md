# SILM onboard reconnaissance

Link-free, fully onboard recognition for the UAV SILM airframe, for
agriculture / forestry / wildfire survey. The aircraft has **no RC receiver
(no ExpressLRS) and no telemetry radio**: every flight is a fully automatic
mission planned in the **laptop GUI** (`planner/`), uploaded over USB, then
hand-launched — automatic takeoff, survey, automatic belly landing, automatic
disarm. On the way it recognises ground subjects (farm machinery, vehicles,
livestock, forest fires…), geo-references them and saves tagged pictures to
its own SD card. A GPS-loss **failsafe** turns a jammed/lost GPS into a landing
at the planned landing point instead of a lost aircraft. Nobody can take over
in flight, so every failure path ends in an automatic landing.

> **New here? Read [`FLIGHT_READY.md`](FLIGHT_READY.md)** — the rigorous,
> step-by-step guide covering where every file goes, the GPS-loss failsafe, how
> to train/test the detector, how to verify it yourself, and the flight
> checklist. This file is the component overview; FLIGHT_READY is the procedure.

Key parts added beyond basic recognition:
- **Laptop mission planner** — `planner/app.py`: draw the area, launch and
  landing on a map; it builds the complete no-RC mission (takeoff, survey
  lines, `DO_LAND_START`, final approach, `LAND`), checks it, and uploads it
  over USB or saves a `.waypoints` file. See [`planner/README.md`](planner/README.md).
- **GPS-loss failsafe** — `ardupilot/gps_denied_failsafe.param` (verified
  against ArduPlane 4.7.2) + `ardupilot/scripts/silm_gps_loss.lua` (turns for
  the landing sequence after GPS loss, jamming-proof; never interrupts a takeoff
  or a landing), both on the flight controller, plus
  `silm_recon/failsafe.py` (companion GPS-health monitor and optical-flow
  backup). Desk-test it with `tools/sitl_failsafe_test.py`. See FLIGHT_READY
  section A.
- **Reliability** — multi-frame confirmation (`confirm.py`), Pi health and
  storage monitoring (`health.py`), lens calibration
  (`tools/calibrate_camera.py`), a post-flight review page
  (`tools/make_review.py`) and an automated test suite (`tests/`, section F).
- **Trainable ag/forestry detector** — `training/` has the full
  train → export → deploy pipeline and a synthetic bootstrap; drop your
  `.tflite` into `models/` and set `detector.format: yolo`. See
  `training/README.md`.

This runs on the hardware already in the build. The SIYI A2 mini sends digital
video over Ethernet, which plugs straight into the Pi 3. Whether the Pi 3 alone
is fast enough is the open question: `tools/preflight.py` measures it on the
aircraft (see *Performance*).

---

## How the "no link" requirement is actually met

The autonomy splits cleanly into two independent pieces, and **neither needs an
operator link in flight**:

| Job | What does it | Needs a link? |
|-----|--------------|---------------|
| Plan the mission | **The laptop planner** (`planner/app.py`), on the ground. Upload over USB. | USB cable, before flight only |
| Launch, fly, land | **ArduPilot on the Matek H743.** Arm, throw: `TAKEOFF` starts the motor after the throw, `AUTO` flies the survey, the `DO_LAND_START` sequence and `LAND` bring it down into the wind and it disarms itself. Every failsafe (GPS loss, battery, fence) jumps to that same landing sequence. | No |
| See + recognise + photograph targets | **This software on the Raspberry Pi 3.** It only *reads* GPS/attitude from the H743 and watches the camera, then saves pictures to the SD card. | No |

The companion computer **never commands the aircraft** and never transmits
anything. If the Pi crashed mid-flight, the plane would still fly its mission
and come home — you'd just lose that flight's photos. This separation is
deliberate: it's what makes the system safe and link-free.

**No RC means no manual takeover, ever.** What that requires (all in
`ardupilot/gps_denied_failsafe.param`, checked by `tools/preflight.py`):
`INITIAL_MODE=10` (boot into AUTO), `THR_FAILSAFE=0` and `ARMING_SKIPCHK=64`
(no receiver to wait for),
`ARMING_MIS_ITEMS=13` (it will not arm without TAKEOFF, DO_LAND_START and LAND
in the mission), `TKOFF_THR_MINACC=15` + `TKOFF_THR_DELAY=3` (motor starts after
the throw), `RTL_AUTOLAND=2` (RTL flies the landing sequence instead of
circling forever), battery failsafes = land, `LAND_DISARMDELAY=10`.

> The project's existing 4G/WireGuard idea (SIM7600E) is a *separate, optional*
> way to WATCH the flight. It is **not required** by anything here and is never
> used by this software — but with no RC it is the only way to see what the
> aircraft is doing, and an authority may ask for it (see *Legal* in
> FLIGHT_READY.md).

---

## Hardware — kept as-is

Everything maps onto parts already on your component list:

- **Matek H743 (ArduPilot)** — flies the mission; this code reads telemetry from
  a spare UART (set `SERIALx_PROTOCOL=2`, MAVLink2) or the H743 USB port.
- **Raspberry Pi 3** — the onboard "brain" that runs recognition. Passive.
- **SIYI A2 mini camera** — the eye: 1080p over Ethernet, 160° fisheye, pitch
  gimbal. See *Camera* below.
- **M10Q GPS / compass, airspeed, battery** — unchanged; the Pi just reads the
  resulting position/attitude estimate over MAVLink.
- **No RC receiver / no ExpressLRS** — none is fitted and none is needed.
  The laptop (USB) is the only interface, on the ground.

### Camera — SIYI A2 mini

Per retailer specifications (SIYI's own site could not be checked from the
build environment), the A2 mini is a 2 MP camera with a **160° fisheye** lens on
a **single-axis pitch gimbal** (−90° to +25°), with **Ethernet** video output,
powered from **12 V**. Defaults used here: video at
`rtsp://192.168.144.25:8554/main.264`, gimbal control on UDP port 37260.

- **Video:** plug the camera's Ethernet into the Pi's Ethernet port and give the
  Pi a fixed address on the camera's network (FLIGHT_READY.md B1). The app
  reads the RTSP stream, decoding with the Pi's hardware H.264 decoder through
  GStreamer (`camera.backend`).
- **Gimbal:** the app points the camera straight down and reads back its real
  angle over UDP (`silm_recon/siyi.py`), so geo-referencing uses the angle the
  camera actually has. SIYI's v1.1 manual says the SDK section was removed;
  community projects report it works on the A2 mini. `preflight.py` tests it.
  If it doesn't answer, set `gimbal.enabled: false` and use a fixed tilt.
- **Lens:** a 160° fisheye needs the fisheye lens model; calibrate it with
  `tools/calibrate_camera.py --fisheye` (FLIGHT_READY.md B5).

### Performance — honest numbers

**Object size in pixels is the hard limit**, and the fisheye makes it worse:
the whole 1920-px frame shrunk to the model's 320 px leaves a 10 × 3.5 m combine
at 100 m only ~11×4 px. So by default the model runs on the **central half of
the frame in 2×2 tiles** (`detector.roi`, `tiled`), where the fisheye's
resolution is best:

| At the model input (320 px), central half, 2×2 tiles | 100 m | 60 m |
|---|---|---|
| Combine, 10 × 3.5 m | 46 × 16 px, good | 76 × 27 px, good |
| Car, 4.5 × 1.8 m | 21 × 8 px, marginal | 34 × 14 px, good |
| Cow, 2.4 × 0.8 m | 11 × 4 px, too small | 18 × 6 px, marginal |

(`tools/pixels_on_target.py`; rule of thumb ≥10 px on the smaller side.)

That's **4 model runs per frame**, and an object crosses the detection region
in ~4.4 s at 100 m (2.6 s at 60 m), so to see it twice the Pi needs about
0.5-0.8 detection frames per second. **How fast a Pi 3 runs this has not been
measured** — run `python3 tools/preflight.py` on the aircraft: it measures
the speed on that Pi and says PASS or FAIL for your altitude and speed. If it
fails, a Coral USB accelerator (used automatically with an `_edgetpu.tflite`
model) is the fix that keeps the rest of the hardware.

---

## What it recognises

- **Out of the box (bundled COCO model): `car`, `truck`, `person`, `boat`, …**
- **`fire` / `smoke`:** a built-in colour/texture heuristic (`fire.py`) that
  works with no training — it's conservative and meant to flag frames for human
  review, not to be a certified fire detector.
- **`harvester` / `combine` / `tractor`:** *not* COCO classes. Train a small
  custom model (recipe in `tools/train_notes.md`), drop the `.tflite` + labels
  into `models/`, and list the class names in `config.yaml → targets`. No code
  change needed — the pipeline is class-name driven.

Each saved event writes: a boxed JPEG, a JSON sidecar (class, score, pixel box,
full aircraft state, **geo-referenced ground lat/lon**), and appends to
`detections.ndjson` + `detections.csv`. After the flight,
`tools/make_geojson.py` turns the log into a map layer.

---

## Install (on the Pi, on the bench with internet)

64-bit Raspberry Pi OS; full steps in FLIGHT_READY.md B2.

```bash
sudo apt update
sudo apt install -y python3-opencv python3-numpy python3-yaml python3-venv \
    gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav
python3 -m venv --system-site-packages ~/silm-venv
cd ~/uav-silm/onboard
~/silm-venv/bin/pip install -r requirements.txt     # pymavlink, ai-edge-litert, ...
# download a detection model: see models/README.md
```

Wire the Pi to the H743 (UART or USB) and the camera's Ethernet to the Pi (see
FLIGHT_READY.md B1 for the network address), then edit `config.yaml`
(`telemetry.connection`, `camera.url`, `storage.output_dir`) and run
`python3 tools/preflight.py`.

### Bench test (no flight controller needed)

```bash
# camera.source: rtsp with the camera connected, or file + a folder of test JPEGs:
python3 run.py --config config.yaml --selftest
```
This runs the camera + detector + fire + geo-ref and prints what it sees, so you
can validate the capture path and model before flying.

### Run on every boot

```bash
sudo cp systemd/silm-recon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now silm-recon.service
journalctl -u silm-recon -f
```

---

## A mission, end to end

1. **Plan** on the laptop: `python planner/app.py`, draw launch, area and
   landing (into the wind), check the messages, plug in the H743 by USB and
   **Upload mission** (it reads it back to verify). Pre-cache an offline
   basemap for the Pi if you want map context (`tools/download_tiles.py`, see
   *legal note* below).
2. **Preflight.** Power on; the Pi service starts and sits idle (it won't save
   anything until the plane is armed, in `AUTO`, and above `min_alt_m`). Run
   `tools/preflight.py` once per field day: it also downloads the mission from
   the H743 and refuses one without TAKEOFF / DO_LAND_START / LAND.
3. **Launch.** It boots into `AUTO` (`INITIAL_MODE=10`). Press **Arm for
   launch** in the planner (USB; it re-checks the mission first), unplug, throw
   firmly into the wind. The motor starts only after the throw
   (`TKOFF_THR_MINACC`, `TKOFF_THR_DELAY`).
4. **Fly.** ArduPilot flies the survey. The Pi watches the camera, recognises
   targets, geo-references them and saves pictures to the SD card. No link is
   used. The de-dup logic stops the same combine being saved dozens of times.
5. **Land.** After the last line the mission flies `DO_LAND_START` → final
   approach → `LAND`: flare, belly landing, disarm after 10 s. A GPS loss,
   low battery or fence breach jumps straight to the same landing sequence.
   Nobody needs to (or can) take over; the recon loop stops saving below
   `min_alt_m` on the approach.
6. **Review.** Pull the SD card. Run `tools/make_geojson.py detections.ndjson`
   and drop the GeoJSON onto a map to see every target and its photo.

---

## Configuration map

Everything lives in `config.yaml`:

- `telemetry` — how the Pi reaches the H743.
- `camera` — frame source (`rtsp` over Ethernet / `usb` / `picamera` / `file`).
- `gimbal` — SIYI gimbal pointing and angle read-back.
- `detector` — model + labels + confidence threshold.
- `targets` — which classes to keep and photograph.
- `fire` — the no-training fire/smoke heuristic.
- `georef` — lens FOV + camera mounting, for pixel→lat/lon.
- `activation` — when it's allowed to save (mode/armed/altitude gates).
- `storage` — where photos go, and the de-dup window.
- `tiles` — optional offline basemap.

---

## Accuracy & limits (so numbers aren't oversold)

- **Geo-referencing** assumes flat ground at launch elevation and a calibrated
  lens FOV. Expect a few metres' error near frame centre at 100 m AGL — enough
  to find the object again, not survey-grade. Calibrate `hfov_deg` and set
  `forward_tilt_deg` to your real mount angle.
- **Detection** quality depends entirely on your model and the altitude/angle it
  was trained for. The COCO baseline is generic; custom targets need custom data.
- **Fire heuristic** will false-alarm on sunset-coloured or bright scenes; treat
  its hits as "look at this frame", not ground truth.
- **Automatic landing** has no pilot to correct it. Pick a landing strip much
  larger than you think (at least 50 × 200 m, nothing tall on the approach),
  and plan into the wind. After a GPS loss the landing is flown on dead
  reckoning and can be tens of metres off — the simulator test
  (`tools/sitl_failsafe_test.py`) shows how far for your wind.

## Legal note on map imagery

Google Maps/Earth tiles may **not** be pre-downloaded or cached under Google's
terms. For the optional offline basemap, use a source whose licence permits
offline use (e.g. Esri World Imagery for internal/eval use, Mapbox with an
offline plan, national ortho/WMS layers, or your own imagery). The project
brief's "Google Maps data" is satisfied by any equivalent satellite/terrain
basemap.

**Flight without an RC link is not allowed in the EASA "open" category**: the
remote pilot must always be able to intervene. A fully automatic flight with no
link needs the **"specific" category** — an operational authorisation from
Transpordiamet (an SORA-based application, or a standard scenario if one fits).
Expect them to ask how you keep the aircraft inside the area (geofence),
how it ends every flight (automatic landing), and how you monitor it — the
optional 4G link is the obvious answer to the last one. Also check the rules
for photographing people or property before operating.
