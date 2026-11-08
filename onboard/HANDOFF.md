# Handoff: continue the SILM rewrite in a new session

Paste this into a new Claude Code session (or say "read onboard/HANDOFF.md
on branch claude/blissful-lovelace-rnmruk and continue"). Repository:
`rometlehari-maker/uav-silm`, branch **`claude/blissful-lovelace-rnmruk`**
(everything below is committed and pushed there).

## What the user asked for (latest request, in full)

1. Rewrite the whole system for a **Raspberry Pi 4 Model B, 2 GB** (was Pi 3).
2. Re-run **all** tests done before (unit tests, SITL GPS-jam scenarios); test
   rigorously; **every test must pass** - no battery errors, no camera mishaps.
3. A **simple, intuitive laptop GUI** where mission, aircraft details, run
   command/mode, launch type etc. can be changed, with a **question above every
   editable value** (e.g. "What is the center of gravity of your aircraft?").
   Easy to launch and understand.
4. A tab for **image analysis**: pictures from the flight with coordinates.
5. **Rewrite all instruction docs**, including how to **transfer the mission
   and flight data from the laptop to the aircraft** (and data back).
6. (Added mid-way) A place in the laptop program to **upload training images
   and label them** ("tractor", "combine", ...), and **rigorously train the
   Pi's model on higher-quality images**, then test it.

User's answers to questions asked:
- Laptop <-> Pi transfer: **both** Wi-Fi (Pi hotspot) and USB stick.
- The 10 m/s-wind blind-landing FAIL: **try to improve it first, then turn
  what's left into a hard limit** (GUI refuses to launch above the limit; the
  test suite covers exactly that envelope). **Current limit in code: 10 m/s**
  (`groundstation/project.py` MAX_WIND_MPS), decided from the final matrix below.

## Done (committed)

- **Pi 4 adaptation** (`silm_recon/`): camera that isn't ready at boot no longer
  kills the program (`Camera(strict=False)`), geo-ref adapts to real frame
  size, one bad frame can't stop capture, health monitor has Pi 4 temps (75 C)
  + free-memory warning, status file `/run/silm/status.json`
  (`silm_recon/status.py`), config = `config.yaml` + `~/silm-settings/settings.yaml`
  overlay (`silm_recon/config.py`, validated; bad overlay falls back to
  defaults), `run.py --check`, `program.mode: flight|bench`, `~` paths.
- **Real bug fixed**: the COCO SSD model's class/score outputs were swapped
  (every detection "person", scores ~86). `detector.py` now reads outputs by
  name/value; `models/coco_labels.txt` = the model's 90 COCO ids; model file
  renamed `coco_ssd_mobilenet_v1_quant.tflite`. `tests/test_real_model.py`.
- **Ground agent on the Pi** (`silm_recon/agent.py`, `systemd/silm-agent.service`):
  HTTP on :8080 (status, settings in, flights list/zip, log, trained model in),
  Wi-Fi off while armed/airborne, USB stick labelled SILM at /mnt/silm_usb
  (imports `SILM_TRANSFER/to_aircraft/`, copies flights to `from_aircraft/`).
- **Pi installer** `pi/install.sh` (packages, venv, model w/ SHA-256, both
  services, sudo rule, NetworkManager hotspot 10.42.0.1, fstab automount,
  `--dry-run`, shellcheck-clean). `preflight.py` reads the settings overlay.
- **Ground Station** (`groundstation/`, launchers `Start Ground Station.bat`,
  `start_ground_station.sh`): tabs Start, Aircraft (CG bar), Mission (map),
  Camera, Send to aircraft (FC over USB: params/Lua via MAVFTP/mission, all
  read back; Pi over Wi-Fi; USB stick), Launch (checklist + wind gate),
  Simulator (runs SITL on the user's mission), Flights & pictures (map,
  filters, lightbox with coordinates; frames get interpolated track
  positions), Teach new objects (upload, draw boxes, train, send model).
  Every question lives in `groundstation/project.py` QUESTIONS; it generates
  the .param file, mission and Pi settings. Leaflet vendored (offline OK).
- **Simulator**: `tools/sitl_failsafe_test.py` matches SITL's 3S battery to the
  param file's cell count automatically (no manual BATT_VOLT_MULT).
  `groundstation/fc.send_script` fixed to drive MAVFTP to completion
  (`process_ftp_reply`) and use `scripts/` on SITL, `/APM/scripts` on boards.

## Test status (last run)

- `python3 -m pytest -q` in `onboard/`: **394 passed**, 2 skipped (ArduPilot
  source check: set `ARDUPILOT_SRC=/home/user/ardupilot`; SITL: set
  `SILM_SITL_TESTS=1 SILM_ARDUPILOT=/home/user/ardupilot`).
- Browser end-to-end `tests/test_gui_browser.py` (Node Playwright, real agent):
  passes. Screenshots with `SILM_E2E_SHOTS=<dir>`.
- `tests/test_sitl_integration.py` (`SILM_SITL_TESTS=1 SILM_ARDUPILOT=/home/user/ardupilot`):
  **passes** - factory-fresh SITL set up only by Ground Station code, flown with
  GPS jam: 6/6 checks, touchdown 55.9 m, failsafe 5.2 s, battery auto-matched.
- **Final run (after training, all current files):** SITL integration test
  + ArduPilot parameter check: 7 passed (3 min); full suite 394 passed in a
  clean CI-like venv.
- Earlier 8-scenario SITL matrix (before the GUI work): 7/8 pass; FAIL =
  wind 10 m/s, blind touchdown 160 m long (limit 150).

## Progress log (newest last - updated as work goes on)

- **Environment:** a personal cloud environment **`uav-silm`** now exists for
  this repo: its setup script installs the Python test tools, builds ArduPlane
  SITL into `ardupilot/` next to the repo and downloads the detection model
  (~5 min at session start). Allowed hosts added: `github.com`,
  `release-assets.githubusercontent.com`, `objects.githubusercontent.com`
  (needed for Ultralytics' pretrained weights `yolo11n.pt`, which otherwise
  answer 504). Pick this environment for a new session.
- **Docs (task 6) mostly done and pushed:** new `GROUND_STATION.md` (every tab,
  transfer laptop <-> aircraft: FC USB / Pi Wi-Fi / USB stick / SD card,
  troubleshooting); `FLIGHT_READY.md`, `README.md` rewritten for Pi 4 +
  Ground Station; `SITL_HOWTO.md` (Ground Station shortcut, battery step now
  automatic); `models/README.md`; `planner/README.md` (superseded note).
  Still to update after results: wind numbers in FLIGHT_READY 2.4 /
  SITL_HOWTO E1, and `training/README.md` (Open Images + Ground Station tab).
- **Training data (task 7):** `training/openimages_dataset.py` (committed) built
  `training/datasets/openimages/` (git-ignored): 1800 photos - tractor 450 photos
  / 719 boxes, car 626 / 1582, truck 466 / 668, livestock 452 / 989. Visual
  check: tractor boxes ~8 in 10 correct; Open Images "Harvester" photos are
  mostly NOT combines, so `combine` is excluded by default (`--combine` to add).
  Ultralytics 8.4 + torch installed system-wide; `yolo11n.pt` downloaded.
  NEXT: train yolo11n imgsz 320 on CPU (after SITL runs - training uses all
  cores and would slow the real-time simulator), export, evaluate with
  `tools/eval_model.py` on the val split through the onboard detector, write
  results into training/README.md, add a regression test.
- **Strong wind (task 4):** experiment running: Ground Station mission, 10 m/s
  from 250°, GPS jammed until after touchdown, approach A 300 m/30 m, B 200 m/25 m,
  C 150 m/20 m (script: scratchpad `wind_exp.py`, uses `groundstation.sim.SimRunner`).

- **Safety bug found + fixed (wind experiment A, 10 m/s, GPS jammed past
  touchdown):** after touchdown the Lua script ended the GPS-loss episode
  ("not flying") and put the EKF back on the STILL-JAMMED GPS; the fake
  movement made the aircraft look airborne, the episode restarted ~70 times
  and it stayed ARMED on the ground for >3 min. Fix: on the ground the
  episode stays (EKF off GPS) until GPS is good for SLM_GPS_OK_S
  (`silm_gps_loss.lua`, 2 new Lua tests). New SITL grading check
  `disarmed_soon_after_landing` (armed on the ground <= 30 s), shown in the
  Simulator tab. Variant A otherwise passed: blind touchdown 129.6 m long
  (limit 150), flare at 2.9 m.

- **Wind experiment results (10 m/s, blind landing):** final approach A 300 m/30 m
  -> 129.6 m long; B 200 m/25 m -> 106.7 m; C 150 m/20 m -> 100.6 m.
  **New default: 200 m from 25 m** (`planner/survey.py`, `groundstation/project.py`).
- **Second safety bug (run C):** even with the EKF kept off GPS, a blind
  aircraft on the ground "moves" at the learnt wind speed (dead reckoning),
  ArduPlane's is_flying flickers and it never disarms until GPS returns.
  Fix in `silm_gps_loss.lua`: during an episode it disarms itself after
  `SLM_LAND_DIS_S` (15 s) of: armed, AUTO, LAND item, airspeed < 0.8 x stall
  (AIRSPEED_MIN/1.3), gyros quiet (< 0.1 rad/s), < 5 m above home. 8 new Lua
  tests incl. the never-disarm-in-flight cases (44 Lua tests pass).
- **Final SITL matrix (Ground Station mission, 200 m/25 m approach):** w4 from E
  PASS 60 m; w6 PASS 55 m; w8 from NW PASS 72 m; w8 blind PASS 85 m; w10 blind
  PASS 107 m (armed on ground 17 s); GPS-off w6 PASS 55 m (14 s); **w12 blind
  FAIL 151 m** (limit 150). => MAX_WIND_MPS = 10 (question asks for gusts).
  Docs updated (FLIGHT_READY 2.4, README, GROUND_STATION, SITL_HOWTO E1).
- **Training DONE (task 7):** `training/runs/oi_v1` (git-ignored), yolo11n,
  320 px, 40 epochs CPU (~50 min). Val mAP50 0.533. Exported -> committed
  `models/silm_oi.tflite` (4.4 MB, INT8 body + float head) + labels + `silm_oi.json`.
  Held-out 298 photos via the onboard detector, precision/recall at 0.35:
  tractor 0.73/0.38, car 0.72/0.37, truck 0.70/0.58, livestock 0.75/0.52
  (COCO SSD: no tractors; car 0.21/0.45, truck 0.30/0.51, livestock 0.51/0.46).
  Results + caveats in `training/README.md` ("Results: the ready-made model").
  `tests/test_bundled_model.py` + 4 CC BY 2.0 sample photos with attribution
  (`tests/data/oi_samples/samples.json`). Licence note (YOLO11n AGPL-3.0) in
  `models/README.md`.
- **Aerial data:** the user asked why ground-level photos. Answer: the only
  labelled source reachable here. Roboflow Universe has aerial sets but the
  user declined both the credential form and adding Roboflow hosts to the
  environment (do NOT propose again); they pasted a key in chat and then
  regenerated it (old key dead, never stored). Route for them: run
  `training/fetch_real_data.py --source roboflow` on their PC, or fly with
  "save ordinary pictures" and label in tab 9.
- **Export bug found + fixed (`training/export.py`):** Ultralytics 8.4 now exports
  through LiteRT (needs `litert-torch`, which pins torch<2.15 - in this container
  it lives in a scratchpad venv `exportenv` installed with `--no-deps` so the
  system torch 2.15 used by training is untouched). Its fully-INT8 export clips
  every class score at ~0.50 (also when only the decode ops are float). Fix:
  export float, then `ai_edge_quantizer` static INT8 for the body with the whole
  `head.Detect` kept float (HEAD_REGEX) -> scores match the .pt (ratio 1.01),
  4.4 MB, ~2x faster than float on x86. If the confidence check still fails,
  export.py falls back to the float model. Model input is NCHW (detector handles it).
- **Ground Station: ready-made models** - `onboard/models/<name>.tflite` with a
  `<name>.json` (title, finds, classes) + labels is offered on the Camera tab as
  "Ready-made: ..." and sent to the Pi like a trained model (committed 610f0d1).
  Plan: commit `models/silm_oi.tflite` (+labels, .json; .gitignore exceptions
  added) and `tests/test_bundled_model.py` (blank picture -> no detections,
  labels = classes.yaml, sample photos `tests/data/oi_samples/` CC BY 2.0 with
  attribution from Open Images' image list - all 298 val photos are CC BY 2.0;
  metadata in scratchpad `val_meta.csv`). Evaluation script: scratchpad
  `eval_models.py` (also runs COCO SSD with cow/sheep/horse -> livestock).
  Licence note to add: weights derive from Ultralytics YOLO11n (AGPL-3.0).
- **GUI restyled like www.uavsilm.eu** (user request): the website lives in
  this repo's root (`index.html`, `fonts/`, `images/`). `groundstation/static/style.css`
  rewritten with the site's tokens (bg #3a494d, cyan #a1e6f3, amber, coral),
  Bricolage Grotesque + IBM Plex Mono (copied to `static/fonts/`, OFL), bird logo
  (`static/logo.jpg`), pill buttons, site-style section labels ("eyebrow"),
  dark OSM tiles (`tiles-dark` CSS filter; satellite untouched), map overlays
  in site colours. No ids changed; e2e browser test passes. OSM tiles are
  blocked in this sandbox, so the dark street map was not seen here.
- **Downloads on the website** (user request): licence chosen by the user =
  **MIT** for the team's code (`onboard/LICENSE`); `onboard/THIRD_PARTY_NOTICES.md`
  credits everything else (Leaflet BSD-2, fonts OFL with full texts also in the
  site's `fonts/`, silm_oi AGPL-3.0 with `models/LICENSE-silm_oi-AGPL-3.0.txt`,
  Open Images CC BY, OSM/Esri, ArduPilot GPL, Python libs).
  `onboard/tools/build_downloads.py` packs a COMMIT (git ls-tree/show, never
  stray files; HANDOFF.md excluded; stops on secret-looking content),
  reproducibly (fixed timestamps), into `downloads/` (software zip, model zip,
  .param, .lua, SHA256SUMS.txt, downloads.json) and rewrites the cards in
  `index.html` between `downloads:begin/end` markers. New site section
  `#downloads` (nav, menu, rail, ids list, footer, Resources "next"), new
  sources in the numbered list, nav breakpoint 1080 -> 1120 px.
  `tests/test_build_downloads.py` (6). Unpacked zip passes its own tests.
  Added a "Will it work on my aircraft?" checklist above the cards (fixed-wing
  ArduPlane with scripting-capable H7/F7 board + airspeed sensor; NOT
  multirotors, DJI/PX4/iNav, F4 boards). **User said "put it live": merged
  into `main` via a pull request (the site deploys from `main`; repo has
  `.nojekyll`, so files are served as-is, including `onboard/`).**
  To refresh after onboard changes: commit, `python3 onboard/tools/build_downloads.py`,
  commit `downloads/` + `index.html`.
- The user asked to "use the desktop computer remotely": DESKTOP-G18N2DO is in
  their Remote Control list but offers no folders to cloud sessions
  (`not_available`; folders `uavsilm_softsim1`, `System32`). Told them how to
  continue there instead.

## Still to do (in this order)

1. ~~Strong-wind landing~~ (done: 200 m/25 m approach, limit 10 m/s).
2. ~~Training on real images~~ (done, see the progress log). Was (task 7): Open Images is reachable
   (storage.googleapis.com + open-images-dataset.s3.amazonaws.com); Hugging
   Face, LVIS and download.pytorch.org are BLOCKED by the network policy.
   Open Images has no tractor *boxes*; plan: images human-verified as
   Tractor (/m/013xlm, ~7,900) or Harvester (/m/01_cf6) / Agricultural
   machinery (/m/0475mb), using their Land vehicle (/m/01prls) / Vehicle
   (/m/07yv9) boxes as the tractor box (check a sample visually for label
   quality); plus Car (/m/0k4j), Truck (/m/07r04), Cattle (/m/01xq0k1).
   Filtered CSVs were in the old session's scratchpad (re-stream them: the
   train bbox file is 2.3 GB, the image-label file 2.7 GB with `\r\n` line
   ends). Download ~300-600 images/class, train yolo11n imgsz 320 on CPU
   (`training/train.py`, `--project` supported), export with
   `training/export.py`, evaluate through the ONBOARD detector
   (`tools/eval_model.py`), add tests, report precision/recall honestly and
   the viewpoint gap (ground-level photos vs aerial). `pip install ultralytics`
   was being installed system-wide.
3. ~~Docs~~ (done except training/README results). Was: rewrite `README.md`, `FLIGHT_READY.md`, `SITL_HOWTO.md`
   for the Pi 4 + Ground Station; new `GROUND_STATION.md` (every tab) and a
   clear "Transfer" section: FC over USB (params, Lua, mission), Pi over Wi-Fi
   hotspot (join SILM-..., 10.42.0.1:8080), Pi over USB stick (label SILM,
   SILM_TRANSFER/to_aircraft & from_aircraft), flights back to the laptop.
   Update `models/README.md` (new model name), `planner/README.md` (planner
   is superseded by the Ground Station).
4. **Final full test run** (task 5): pytest 394 passed in a clean CI-like venv; CI now also installs Playwright so the browser test runs there. Was: add the new test files' deps to
   `.github/workflows/onboard-tests.yml` (CI already downloads the model and
   installs ai-edge-litert + shellcheck).

## Environment notes (this cloud container)

- ArduPilot SITL built at `/home/user/ardupilot` (`build/sitl/bin/arduplane`);
  run dir `/home/user/sitlrun`. Use `--custom-location=lat,lon,alt,hdg`
  (not `-L`). Without MAVProxy, connect to `tcp:127.0.0.1:5760`.
- Node Playwright is installed globally (`NODE_PATH=$(npm root -g)`); do not
  install Python Playwright.
- Killing processes: never `pkill -f <pattern>` with a pattern that appears in
  your own shell command line (it kills the shell); use `pgrep -x arduplane`.
- Nothing has been tested on real Pi 4 hardware or a real flight controller;
  the installer is tested with `--dry-run` only.
