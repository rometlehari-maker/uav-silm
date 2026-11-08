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
  test suite covers exactly that envelope). Current limit in code: 8 m/s
  (`groundstation/project.py` MAX_WIND_MPS).

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

- `python3 -m pytest -q` in `onboard/`: **370 passed**, 1 skipped (ArduPilot
  source check; set `ARDUPILOT_SRC=/home/user/ardupilot`).
- Browser end-to-end `tests/test_gui_browser.py` (Node Playwright, real agent):
  passes. Screenshots with `SILM_E2E_SHOTS=<dir>`.
- `tests/test_sitl_integration.py` (`SILM_SITL_TESTS=1 SILM_ARDUPILOT=/home/user/ardupilot`):
  **passes** - factory-fresh SITL set up only by Ground Station code, flown with
  GPS jam: 6/6 checks, touchdown 55.9 m, failsafe 5.2 s, battery auto-matched.
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

## Still to do (in this order)

1. **Strong-wind landing** (task 4): try shorter/steeper final approach
   (mission `approach_len_m` / `approach_alt_m`, e.g. 200 m from 25 m) in
   10 m/s wind via the SITL runner; if it passes keep it as the default,
   otherwise keep MAX_WIND_MPS = 8 and make the scenario list = envelope.
   Then re-run the full SITL scenario matrix one at a time (sim_vehicle kills
   other SITL instances - never run two in parallel).
2. **Training on real images** (task 7): Open Images is reachable
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
3. **Docs** (task 6): rewrite `README.md`, `FLIGHT_READY.md`, `SITL_HOWTO.md`
   for the Pi 4 + Ground Station; new `GROUND_STATION.md` (every tab) and a
   clear "Transfer" section: FC over USB (params, Lua, mission), Pi over Wi-Fi
   hotspot (join SILM-..., 10.42.0.1:8080), Pi over USB stick (label SILM,
   SILM_TRANSFER/to_aircraft & from_aircraft), flights back to the laptop.
   Update `models/README.md` (new model name), `planner/README.md` (planner
   is superseded by the Ground Station).
4. **Final full test run** (task 5) + add the new test files' deps to
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
