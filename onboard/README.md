# SILM onboard reconnaissance

Link-free, fully onboard recognition for the UAV SILM airframe, for
agriculture / forestry / wildfire survey. The aircraft flies a pre-planned
Mission Planner route on its own, recognises ground subjects (farm machinery,
vehicles, livestock, forest fires…) as it goes, geo-references them and saves
tagged pictures to its own SD card — **with no radio, fibre or any other link to
the operator** — and has a GPS-loss **failsafe** so a jammed/lost GPS brings it
home instead of losing it. On return, the pilot takes over manually on
ExpressLRS.

> **New here? Read [`FLIGHT_READY.md`](FLIGHT_READY.md)** — the rigorous,
> step-by-step guide covering where every file goes, the GPS-loss failsafe, how
> to train/test the detector, how to verify it yourself, and the flight
> checklist. This file is the component overview; FLIGHT_READY is the procedure.

Key parts added beyond basic recognition:
- **GPS-loss failsafe** — `ardupilot/gps_denied_failsafe.param` (the real safety
  net, in ArduPilot) + `silm_recon/failsafe.py` (companion GPS-health monitor and
  optical-flow backup). See FLIGHT_READY section A.
- **Trainable ag/forestry detector** — `training/` has the full
  train → export → deploy pipeline and a synthetic bootstrap; drop your
  `.tflite` into `models/` and set `detector.format: yolo`. See
  `training/README.md`.

This runs on the hardware already in the build. The only likely addition is a
~$15 analog-to-USB video capture dongle (see *Camera*), because the SIYI A2 Mini
is an analog camera and a Raspberry Pi can't ingest analog over USB without it.

---

## How the "no link" requirement is actually met

The autonomy splits cleanly into two independent pieces, and **neither needs an
operator link in flight**:

| Job | What does it | Needs a link? |
|-----|--------------|---------------|
| Fly the planned route | **ArduPilot on the Matek H743.** You draw waypoints in Mission Planner, upload them on the ground, arm, hand-launch. `AUTO` mode flies the whole route and `RTL` brings it home. | No |
| See + recognise + photograph targets | **This software on the Raspberry Pi 3.** It only *reads* GPS/attitude from the H743 and watches the camera, then saves pictures to the SD card. | No |
| Take over on return | **You, on the ExpressLRS radio**, by switching out of `AUTO`. | Only your normal manual RC |

The companion computer **never commands the aircraft** and never transmits
anything. If the Pi crashed mid-flight, the plane would still fly its mission
and come home — you'd just lose that flight's photos. This separation is
deliberate: it's what makes the system safe and link-free.

> The project's existing 4G/WireGuard idea is a *separate, optional* convenience
> for live telemetry. It is **not required** by anything here and is never used
> by this software.

---

## Hardware — kept as-is

Everything maps onto parts already on your component list:

- **Matek H743 (ArduPilot)** — flies the mission; this code reads telemetry from
  a spare UART (set `SERIALx_PROTOCOL=2`, MAVLink2) or the H743 USB port.
- **Raspberry Pi 3** — the onboard "brain" that runs recognition. Passive.
- **SIYI A2 Mini camera** — the eye. See the one caveat below.
- **M10Q GPS / compass, airspeed, battery** — unchanged; the Pi just reads the
  resulting position/attitude estimate over MAVLink.
- **ExpressLRS** — unchanged; your manual-control path on return.

### Camera — the one honest caveat

The SIYI A2 Mini outputs **analog AHD** video. A Raspberry Pi has no analog
video input, so to get frames into the Pi for computer vision you need a small
**AHD→USB capture dongle** (~$10–15, ~15 g). With it, the camera appears as a
normal `/dev/video0` device and `camera.source: usb` just works. This is the
only part the current build probably lacks. (Alternatively, a Pi CSI camera
would plug straight in — set `camera.source: picamera` — but that changes the
camera, which you asked to avoid.)

### Performance — honest numbers

A **Raspberry Pi 3 does ~1–3 FPS** on a quantised 320×320 detector. That is
enough for large, slow-moving targets (combines, vehicles, fires) seen from
80–120 m **if** you fly high/slow enough that a target stays in frame for a
second or two — the de-dup and `detect_every_n_frames` settings are tuned for
this. If you want real-time (30+ FPS), add a **$60 Coral USB accelerator**; the
code detects it and uses it automatically. No other change.

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

```bash
sudo apt update
sudo apt install -y python3-opencv python3-numpy python3-yaml
cd ~/uav-silm/onboard
python3 -m pip install -r requirements.txt     # pymavlink, tflite-runtime, ...
# download a detection model:
#   see models/README.md
```

Wire the Pi to the H743 (UART or USB) and plug in the AHD→USB dongle, then edit
`config.yaml` (`telemetry.connection`, `camera.device`, `georef.hfov_deg`,
`georef.forward_tilt_deg`, `storage.output_dir`).

### Bench test (no flight controller needed)

```bash
# point camera.source at a folder of test JPEGs, or leave as usb with the dongle:
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

1. **Plan** the route in Mission Planner; upload waypoints to the H743. Pre-cache
   an offline basemap for the area if you want map context
   (`tools/download_tiles.py`, see *legal note* below).
2. **Launch.** Power on; the Pi service starts and sits idle (it won't save
   anything until the plane is armed, in `AUTO`, and above `min_alt_m`). Arm and
   hand-launch.
3. **Fly.** ArduPilot flies the route. The Pi watches the camera, recognises
   targets, geo-references them and saves pictures to the SD card. No link is
   used. The de-dup logic stops the same combine being saved dozens of times.
4. **Return.** `RTL` or the last waypoint brings it home. Switch out of `AUTO`
   on ExpressLRS and land under manual control — the recon loop simply stops
   saving when it sees you've taken over.
5. **Review.** Pull the SD card. Run `tools/make_geojson.py detections.ndjson`
   and drop the GeoJSON onto a map to see every target and its photo.

---

## Configuration map

Everything lives in `config.yaml`:

- `telemetry` — how the Pi reaches the H743.
- `camera` — frame source (`usb` dongle / `picamera` / `file`).
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

## Legal note on map imagery

Google Maps/Earth tiles may **not** be pre-downloaded or cached under Google's
terms. For the optional offline basemap, use a source whose licence permits
offline use (e.g. Esri World Imagery for internal/eval use, Mapbox with an
offline plan, national ortho/WMS layers, or your own imagery). The project
brief's "Google Maps data" is satisfied by any equivalent satellite/terrain
basemap.

Also check local UAV rules (in Estonia, EASA/Transpordiamet) for autonomous/BVLOS
flight and for photographing people or property before operating.
