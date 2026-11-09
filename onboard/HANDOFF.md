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

- `python3 -m pytest -q` in `onboard/`: **455 passed**, 10 skipped (after task 14; skips include the real-SSH test without SILM_SSH_TEST) (ArduPilot
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
- **"Make it much easier, like an app"** (user chose: guided setup wizard +
  real installer with icon; NOT "Pi set up from the app"):
  - **Wizard** (committed 058d8f5): tab "✦ Setup" (`groundstation/static/wizard.js`,
    `/api/setup` -> `~/SILM/setup.json`), 10 self-checking steps (need, aircraft,
    mission, fc_connect [auto-finds USB port, fc/info], fc_setup [one button:
    params -> script -> waits for replug -> mission], bench, pi_install [Imager +
    one copy-paste line], pi_connect [polls /api/pi/status], pi_check [camera
    frames, telemetry_ok, send settings], sim). First start opens it.
    New Camera-tab question `link.fc_serial_for_pi` -> make_params sets
    SERIALn_PROTOCOL=2/BAUD. `pi/install.sh` now 9 steps: Wi-Fi country
    (--country, default EE), camera network siyi-camera on eth0 (from reboot,
    --no-camera-net), UART via raspi-config. e2e covers the wizard.
  - **Desktop app** (committed c788201, v2.1.0): `groundstation/desktop.py`
    (pywebview window, browser+tk fallback, `--selftest` incl. MAVLink
    loopback), `groundstation/runtime.py` (frozen app finds a system Python
    for simulator/training), `packaging/build_app.py` (PyInstaller onedir,
    numpy/lxml excluded; Linux build 48 MB / 22 MB tar.gz, built + selftested
    here and the packaged UI loaded without errors), Inno Setup
    `packaging/windows/installer.iss` (per-user, desktop icon), Linux
    `packaging/linux/install.sh`, icons from the bird logo in `packaging/icons/`.
    Workflow `.github/workflows/ground-station-app.yml`: builds + selftests
    Windows/macOS(arm64)/Linux on push; tag `gs-v*` publishes a release with
    fixed-name copies (UAV-SILM-Ground-Station-Setup.exe, -macos.zip,
    -linux.tar.gz) + SHA256SUMS. Website Downloads has an app block linking to
    releases/latest/download/... (dead until a release exists).
  - **CI built all three and they pass their self-test** (runs 42494097927,
    42494485902): Windows installer UAV-SILM-Ground-Station-Setup-2.1.0.exe
    (20.8 MB), macOS ~20 MB, Linux 22 MB. The self-test also loads the window
    component (Edge WebView2 / Cocoa) and Windows/macOS builds fail without it
    (SILM_REQUIRE_WINDOW=1). The window itself has not been seen on a real
    screen (CI is headless): the user should try it once.
  - downloads/ rebuilt for 2.1.0; `tools/build_downloads.py` now deletes files
    of older versions and rewrites the software zip's name everywhere on the
    page (the checksum commands). Website app block checked: no errors, phone OK.
  - **PUBLISHED (8 Nov):** PR rometlehari-maker/uav-silm#2 merged into main
    (fe453a3); release **gs-v2.1.0** created by the workflow with
    UAV-SILM-Ground-Station-Setup(-2.1.0).exe (20.8 MB), -windows.zip,
    -macos.zip (20.4 MB), -linux.tar.gz (36 MB, CI build) and SHA256SUMS.txt;
    website live (pages deploy succeeded). The website's app buttons use
    releases/latest/download/<fixed name>. Next version: bump onboard/VERSION
    and merge to main -> a new release is made automatically.
    Still not done: seeing the app window on a real Windows/macOS screen (the
    user should install it once and report).
  - **Download zips moved off the website** (user agreed): the software zip
    and model zip had been committed on every rebuild (51 MB of the 65 MB git
    history). Now `downloads/` holds only the .param and .lua (+ sums), and the
    release job runs `build_downloads.py --release` to attach both zips to each
    GitHub release. VERSION 2.1.1 so main publishes a complete release.
    **Done:** PR rometlehari-maker/uav-silm#3 merged (3e5fbcd); release
    gs-v2.1.1 published with installers + uav-silm-software-2.1.1.zip +
    silm-oi-farm-model-v1.zip + SHA256SUMS.txt; website live with 2 cards.
    The old zips are still in git history (51 MB); removing them needs a
    history rewrite of main + force push - only if the user asks.
  - **User said yes** to publishing the release and putting the site live.
    Branch rebased onto main (PR #1 had merged) and force-with-lease pushed.
    Pushing a tag from this session is refused (HTTP 403: only the working
    branch may be pushed), so the workflow now publishes the release itself:
    on `main`, if tag gs-v<onboard/VERSION> doesn't exist yet, the release job
    creates it with the installers. So: PR -> merge -> release appears a few
    minutes later (website buttons work from then on).
  - Intermittent test failure seen twice (both in unusually slow runs: once in
    test_groundstation.py, once in the full suite), never reproduced in 8+
    runs, also not under full CPU load. Name not captured yet; if it shows up
    again, run with `-rf` and note it here.
- **"Absolutely everything from the app"** (latest request; tasks 10-14):
  plan = (10) Pi tools: preflight, live camera, lens calibration [DONE];
  (11) Pi software update + Wi-Fi settings from the app; (12) Pi install over
  SSH; (13) flight-controller tools: servo reversing, level/accel/compass/
  airspeed calibration, battery voltage calibration, log download, firmware
  update via bootloader; (14) training-tools installer + detailed in-app
  guides for what can't be done from an app (hardware, first firmware flash
  via DFU, Pi card, simulator on Windows, Python, legal).
  - (10) done: agent `/api/camera.jpg` (recon writes preview.jpg next to the
    status file, 1/s, only on the ground, only while the agent's
    preview_request is <15 s old), `/api/calibration{,/capture,/compute,/reset}`
    (uses tools/calibrate_camera.py; shots in ~/silm-settings/calibration),
    `/api/preflight` (stops silm-recon, runs preflight.py --json, restarts it;
    new sudo rules stop/start). Ground Station: new tab "10 Tools"
    (static/tools.js), calibration stored in project camera.calibration and
    sent as georef.calibration. Bug caught by tests: capture result's "ok"
    was overwritten by the state's "ok": True.
  - (13) flight-controller tools done (Tools tab, `groundstation/fctools.py`):
    servo outputs + reversing (SERVOn_REVERSED), battery voltage calibration
    (BATT_VOLT_MULT from a multimeter reading), log list + download
    (LOG_REQUEST_LIST/DATA), and background jobs (`FCJobs`, one at a time,
    polled via GET /api/fc/job): level (PREFLIGHT_CALIBRATION p5=2),
    airspeed+baro (p3=1), 6-position accel (p5=1 + ACCELCAL_VEHICLE_POS
    handshake, "Done, it's in place" button), compass (DO_START_MAG_CAL,
    MAG_CAL_PROGRESS/REPORT), firmware update (ArduPilot's BSD-3 uploader
    vendored in groundstation/vendor/, run as its own process; the packaged
    app runs itself with --ardupilot-uploader; firmware from
    firmware.ardupilot.org/Plane/stable/<board>/arduplane.apj or a chosen
    .apj; stops at once on "not suitable for this board").
    Tests: test_fctools.py (fake bootloader on a pty: real upload, wrong
    board refused; scripted-FC job logic), test_fctools_sitl.py (6 tests vs
    real ArduPlane SITL, all pass), and the UI was driven against SITL.
    NOT possible from the app: the first ArduPilot install on a new board
    (DFU) -> explained in task 14's guides.
  - (11) done: Pi software update + Wi-Fi from the app.
    `groundstation/pisoftware.py` makes a reproducible zip of what the Pi runs
    (PI_FILES/PI_DIRS; the packaged app carries it prebuilt as
    `pi_software.zip`, made by `packaging/build_app.py prebuild_pi_package()`).
    Agent `POST /api/software` {zip_b64} -> `install_software`: refuses while
    armed, rejects unsafe/incomplete zips, unpacks to `onboard.new`, copies
    models the package lacks (the downloaded COCO model), runs the NEW
    `run.py --check` with the Pi's settings, swaps (old kept as `onboard.old`),
    restarts silm-recon and exits (systemd restarts the agent). USB: stick
    `SILM_TRANSFER/to_aircraft/software/silm-pi-software.zip`, installed once
    (sha marker), result in `aircraft_status.json` -> shown by the Send tab's
    "Read the stick's answer". Wi-Fi: root helper `pi/silm-wifi` (installed
    to /usr/local/sbin, sudoers allows only it; every value validated) with
    list / hotspot NAME PASS / home-add / home-remove / country CC; agent
    `GET/POST /api/wifi`. Ground Station: `POST /api/pi/software`
    {how: info|wifi|usb}, `POST /api/pi/wifi`; Tools-tab cards "Aircraft
    software" and "Aircraft Wi-Fi". Docs: GROUND_STATION.md got a "10 · Tools"
    section (it had none), FLIGHT_READY 3.2 update notes + roll-back command.
    Bug found by the browser test: the agent reported its module's VERSION,
    not its software folder's -> `software_version(self.root)`.
    **Verified for real (not only fakes):** a real agent process started from
    a folder unpacked from the real package (VERSION set to 0.9.0) was updated
    through the Ground Station's HTTP route: the real `run.py --check` ran,
    the agent exited 0, restarted (as systemd would) reporting 2.1.1, `.old`
    kept, downloaded model kept; a package with a broken config.yaml was
    refused by its own check and nothing changed (script:
    scratchpad real_update.py, not committed). The exact nmcli commands of
    install.sh and silm-wifi were run through real nmcli 1.46 `--offline`
    (apt network-manager): accepted, keyfiles right (AP, 10.42.0.1/24
    shared, priority -10 hotspot / 0 home -> home preferred in range). A real
    PyInstaller build (`packaging/build_app.py --smoke`) passes its self-test,
    which now also checks the bundled Pi package. Full suite 439 passed,
    8 skipped.
  - (12) done: install the Pi from the app over SSH (`groundstation/piinstall.py`,
    paramiko). Job `PiInstall` (one at a time, polled `GET /api/pi/install`,
    `POST /api/pi/install/{start,cancel}`): connect (key remembered in
    ~/SILM/known_hosts, changed key refused with an explanation), `sudo -v`
    (answers sudo's password prompt on a pty), internet check (pypi.org),
    SFTP the app's Pi package, unpack + swap with a python3 snippet (UNPACK;
    keeps downloaded models, old -> onboard.old), `bash pi/install.sh
    --password/--ssid/--country [--no-hotspot] [--no-camera-net]`, progress
    from its "==> N/9" lines, passwords masked in the log, reboot. Host field
    accepts name:port. UI: one shared form (`piInstallForm/piInstallBind` in
    tools.js) in Setup step 7 (marks the step done) and the Tools tab.
    install.sh now also checks the SHA-256 of an already-present model
    (re-downloads a damaged one; SHA of detect.tflite e4b118e5...).
    `setResult` no longer wipes an element's own classes (bug found while
    driving the new form in a browser).
    **Verified for real:** sshd in this container (user silmpi, sudo asks
    for the password): wrong password explained; dry-run install all 9 steps
    via the Ground Station HTTP route and via the browser UI; a REAL install
    ran apt, the venv + pip, the model download+check, and stopped at step 4
    only because the container has no systemd (shown clearly in the UI);
    re-install keeps the model and onboard.old; cancel works. CI now starts
    an sshd on 127.0.0.1:2222 with a throw-away sudo user and runs
    `test_install_over_a_real_ssh_server` (SILM_SSH_TEST). paramiko added to
    groundstation/requirements.txt, build_app hidden imports, the desktop
    self-test, THIRD_PARTY_NOTICES (also a new section listing what the
    desktop installers bundle - that was missing). Full suite 447 passed.
  - (14) done: training-tools installer + guides.
    `groundstation/toolsetup.py` `ToolInstall`: makes ~/SILM/training-venv with
    the found Python, pip-installs ultralytics litert-torch ai-edge-litert
    ai-edge-quantizer (on Linux without nvidia-smi first CPU-only torch from
    download.pytorch.org, tolerated if unreachable), verifies the imports;
    `runtime.training_python()` prefers that venv; routes GET /api/train/tools,
    POST /api/train/tools/{install,cancel}; Teach tab "Install the training
    tools" box. Tests: real venv + pip install of a small package, re-use,
    cancel. The FULL install was run for real here in the background (see the
    progress log below for the result).
    `GUIDES.md` (12 guides: parts, wiring with the H743-WING SERIAL map from
    ArduPilot's hwdef, firmware-first via STM32CubeProgrammer + _with_bl.hex,
    sd-card, usb-stick, python, simulator, camera, servos-esc, cg, airspeed,
    legal/Transpordiamet) is the one source: `groundstation/guides.py`
    converts its Markdown subset (escaped) for the new **Guides** tab;
    `data-guide="<id>"` links anywhere open a guide (wizard steps, Tools,
    Teach, Send C, Launch, question help via Q(..., guide=)). Test: every
    data-guide id exists. Facts checked: SIYI A2 mini 12 V (web), DFU
    procedure (web search; ardupilot.org itself is blocked here),
    Transpordiamet routes + 10 EUR yearly operator registration (web search;
    transpordiamet.ee blocked here), ARSPD_TUBE_ORDR and I2C bus order from
    the ArduPilot source.
    Also new in Tools (the guides promised them): each output's job
    (SERVOn_FUNCTION select; Throttle sets 1000-2000/trim 1000) and an
    Airspeed sensor card (ARSPD_TYPE/ARSPD_BUS, found/healthy from SYS_STATUS
    bit 0x20, VFR_HUD airspeed). Both verified against ArduPlane SITL
    (test_fctools_sitl.py 7/7). Wizard fc_connect text no longer sends people
    to Mission Planner. Full suite 455 passed, 10 skipped.
  - **CI was red from a0af800 to dde6144** (4 pushes, unnoticed at the time):
    `onboard tests` installed a hand-written package list without pyserial,
    which test_fctools.py imports. Now it installs `groundstation/requirements.txt`
    plus the test extras. Reproduced and verified in a clean venv with exactly
    CI's packages: 456 passed (incl. the real-SSH install test). Check CI
    after every push.
  - CI after the fix (509d7bf): **green** (onboard tests run 69 incl. the
    real-SSH install test; app build green). Full real training-tools
    install (started 20:35 into scratchpad/training-venv): download.pytorch.org
    is blocked here, so pip fell back to the standard CUDA torch 2.14.1
    (pinned via litert-torch) + nvidia libs, ~1 MB/s here; at 21:01 still
    downloading nvidia_cudnn. Result to be recorded when it ends.
- **2.2.0 merged and released** (user: "merge it now" / "merge it but do the
  testing regardless"): PR #4 squash-merged as 4b39006 after green CI; on main
  onboard tests + app build green; release gs-v2.2.0 published 23:11 with
  Setup .exe, windows/macos zips, linux tar.gz, source + model zips,
  SHA256SUMS, and also `pi_software.zip` (the Pi update package from the
  build folder; harmless, usable for a manual USB update). Branch restarted
  from main afterwards.
  - After the merge (on the branch, not yet in a release): the first full
    training-tools install ran out of disk here ("No space left on device":
    pip's cache doubled the space). Fix: `--no-cache-dir`, a disk-space
    message, honest sizes. **Re-run succeeded for real:** the app's
    ToolInstall made the venv and installed ultralytics 8.4.208, litert-torch,
    ai-edge-litert, ai-edge-quantizer (+ CUDA torch 2.14.1, as the CPU index
    is blocked here) = **7.0 GB** -> sizes now "2-7 GB, keep ~10 GB free".
    Then a real Quick training through the Ground Station HTTP routes with
    that venv (40 Open Images tractor pictures uploaded + boxed via
    /api/train/upload and /boxes): training 47 s, INT8 export (body INT8, head
    float, 4.4 MB), confidence check exported/original 0.98, model listed
    (mAP50 0.28 - expected for 40 pictures / 15 epochs). The whole
    install -> train -> export path works. Test venv deleted afterwards.
- **2.2.1 merged and released** (user: "if everything is good, merge"): PR #5
  squash-merged as 41c9075; main tests + app build green; release gs-v2.2.1
  published 06:48 with all installers, zips, SHA256SUMS. Before merging, one
  unexplained failure (454/455, slow run) did not reproduce in 8 further full
  runs (incl. under CPU load); the PyPI-dependent cancel test was replaced by
  a network-free one. If an intermittent failure shows up again, run with
  `-rf` and record its name here.
- **Simulator "Nothing goes wrong" option** (user: "add the option in the app
  that nothing goes wrong in the simulator"): `--fault none` in
  tools/sitl_failsafe_test.py + `analyse_normal` (checks: flew_whole_survey,
  no_failsafe, stayed_inside_fence, position_estimate_good <= 25 m, landed,
  landed_near_touchdown, disarmed_soon_after_landing; reports flight_time_s).
  Simulator tab: third choice, hides the duration, explains; a normal-flight
  pass does NOT mark "sim" done (the launch checklist needs the GPS-loss
  test). Verified for real: tests/test_sitl_integration.py
  ::test_normal_flight_option_through_the_ground_station flew the Test field
  mission in ArduPlane SITL through the Ground Station's SimRunner: all 7
  checks PASS, survey item 10 reached, estimate error max 3.5 m, touchdown
  21.4 m from the planned point, disarmed 20.5 s after touchdown, flight
  4 min 47 s, wind 5 m/s. Not merged/released yet.
- **2.2.2 merged and released** (user: "keep only the app download file on
  the website. put in the new update"): PR #6 squash-merged as ff3c640.
  Website: the download section offers only the app (buttons ->
  releases/latest); `downloads/` (param, lua, SHA256SUMS, downloads.json)
  removed from the repo; build_downloads.py website mode now only writes the
  version line (`site_meta`/`site_block`), release mode unchanged (param + lua
  still attached to releases and inside the software zip). Page text updated
  (Pi installed from the app, checksum examples use the app files). Main:
  tests, app build, pages deploy green; release gs-v2.2.2 published 07:37.
  Note: another session also edits index.html styling on main (red accent,
  light blue) - rebase before touching the site.
- **Recognition from above** (user: "can you train the app uploaded model on
  the birdseye view images further", then "can you make anything better?"):
  - New tools: `tools/eval_detections.py` (P/R per class via the onboard
    detector with tiling; tiny boxes and "ignore" regions not counted) and
    `training/visdrone_to_yolo.py` (VisDrone for MEASURING only: its licence
    is CC BY-NC-SA 3.0 per mirrors - can't go into the AGPL silm_oi).
  - Baseline silm_oi on VisDrone val (548 pictures, score 0.35, boxes >= 16 px),
    car recall/precision: 1x1 2.4 %/0.84, 2x2 12.9 %/0.84, 3x3 23.5 %/0.79,
    4x4 31.7 %/0.73; trucks ~1-3 % (no top-down trucks in the training).
    Time per frame here (4-core): 13/48/106/215 ms.
  - New Camera-tab question recognition.tiles (2/3/4, default 2) ->
    detector.tile_grid and the planner's pixels-on-target (user chose "add a
    setting"). Test: test_finer_tiles_reach_the_pi_and_the_planner.
  - runtime.has_module now uses importlib.util.find_spec in-process instead
    of importing (importing ultralytics inside /api/train/state could exceed
    the 20 s test timeout under load - likely the long-standing intermittent
    failure, test_http_training_endpoints).
  - Roboflow: the user connected the Roboflow connector and approved forking
    top-down sets into workspace salvadors-workspace-of0ak. Forked
    `cow-aerial-hzo5z` (Public Domain, 524 pictures, v1 generated, yolov8
    export ready) - but the export link is on app.roboflow.com, blocked here;
    the approval prompt for adding app.roboflow.com was declined twice -
    the user says they DO want to give access but "can't update on this
    device" (the prompt can't be approved there). They were told to add
    app.roboflow.com, api.roboflow.com, universe.roboflow.com,
    source.roboflow.com under the environment's Network access > Allowed
    domains from a computer (claude.ai/code, environment menu > Edit). Next:
    check reachability with curl; when reachable, download the cow-aerial
    export (versions_export gives an app.roboflow.com/ds/... link), fork the
    other candidates, merge with the Open Images set (cow/sheep -> livestock,
    tractor/combine kept), retrain, measure with tools/eval_detections.py
    (VisDrone for cars/trucks; held-out top-down livestock/tractor splits;
    Open Images val for no regression), ship only if better. Candidates found (not forked): riis/aerial-sheep
    (PD), cattle-psyty/aerial-cattle-detection, arthurmessias/counting-cattle,
    ivan-slukin/project-isui (tractor/combine/car/truck), bolo-4yv3d/gcdp1
    (tractor/harvester/animal); avoid VisDrone / Stanford-drone re-uploads
    (non-commercial originals). Path that works without this environment:
    export on the user's PC (training/fetch_real_data.py --source roboflow),
    or their own flight pictures labelled in the Teach tab.
- **Top-down retraining, in progress** (Roboflow reachable after the user
  added the hosts to the uav-silm environment). Forked into
  salvadors-workspace-of0ak, each with a plain v1 (auto-orient only) and a
  yolov8 export: cow-aerial-hzo5z (PD; barn ceiling camera, single cows,
  baked-in cutout/noise -> NOT used), aerial-sheep-7mzre (PD; 4K nadir drone
  over pasture -> used), aerial-cattle-detection-wr4ey (CC BY 4.0; drone,
  nadir+oblique pasture -> used), counting-cattle-7rpmz (CC BY 4.0; nadir
  feedlot, many boxes, some animals unlabelled -> used), project-isui-wzhot
  (stock ground photos with misplaced boxes -> REJECTED), harvester-px266-9r5id
  (forked, not yet checked). No usable top-down tractor set found.
  New tool training/tile_dataset.py (cuts big pictures into 640 px tiles with
  boxes clipped, like the Pi's tiling). Data in the scratchpad (not in git):
  sheep 350/80/80 4K pictures -> 4589/1046/1096 tiles; counting cattle tiled
  9191/2440/1083; training set training/datasets/aerial_v2 = Open Images
  (1502/298) + aerial cattle (720/209) + 1500/300 sampled tiles each of
  feedlot cattle and sheep; test splits held out. Baseline silm_oi (2x2,
  score 0.35, boxes >= 8 px): aerial cattle test recall 4.5 % / P 0.71,
  feedlot cattle test recall 25.6 % / P 0.50. Training: fine-tune from
  runs/oi_v1/weights/best.pt, 30 epochs, 320 px, CPU (runs/aerial_v2).
  Next: export, measure on held-out aerial tests + VisDrone cars/trucks +
  Open Images val (no regression on tractor/car/truck), ship only if better.
  **Kept safe** (user: "keep safe"): a scheduled check-in every 30 min runs
  scratchpad/save_ckpt.sh, which pushes onboard/training/checkpoints/aerial_v2/
  last_weights.pt (optimizer stripped, ~5.5 MB) + results.csv; the exact
  training set is listed in onboard/training/manifests/aerial_v2.txt.
  RECOVERY on a fresh machine: re-export the Roboflow copies (versions_export,
  yolov8), re-tile, rebuild the set from the manifest, then
  `train.py --model training/checkpoints/aerial_v2/last_weights.pt
  --epochs <remaining>` (same data/imgsz/batch). Remove training/checkpoints/
  before merging to main (or keep only the final model in models/).
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
