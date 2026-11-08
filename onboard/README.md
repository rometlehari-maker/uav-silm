# SILM onboard - automatic survey flights with recognition

Link-free, fully automatic survey flights for the UAV SILM airframe
(agriculture, forestry, wildfire). The aircraft has **no RC receiver and no
telemetry radio**: you plan on a laptop, send it to the aircraft on the
ground, launch it, and it flies, recognises and photographs ground objects
(vehicles, farm machinery, livestock, fire), lands itself and switches off.
Afterwards the laptop shows every picture on a map with its coordinates.

| Start here | |
|---|---|
| **[`GROUND_STATION.md`](GROUND_STATION.md)** | the laptop program: every tab, sending to the aircraft, the pictures |
| **[`FLIGHT_READY.md`](FLIGHT_READY.md)** | the procedure from a new aircraft to the pictures, and the flight-day checklist |
| [`SITL_HOWTO.md`](SITL_HOWTO.md) | flying your mission in ArduPilot's simulator on Windows (WSL2) / Linux |
| [`training/README.md`](training/README.md) | training the recognition model on your objects |

---

## The parts

```
      LAPTOP                                    AIRCRAFT
 +-------------------+   USB cable    +------------------------------+
 |  Ground Station   |--------------->| Flight controller (H743,     |
 |  (browser app)    |  parameters,   | ArduPlane): launch, fly,     |
 |                   |  script,       | land; GPS-loss failsafe      |
 |  1 Start          |  mission       +--------------^---------------+
 |  2 Aircraft       |                               | telemetry (read-only)
 |  3 Mission (map)  |  Wi-Fi hotspot +--------------+---------------+
 |  4 Camera         |<-------------->| Raspberry Pi 4 (2 GB)        |
 |  5 Send           |  or USB stick  |  silm-recon: camera, recog-  |
 |  6 Launch         |  settings in,  |   nition, geo-tagged pictures|
 |  7 Simulator      |  flights out   |  silm-agent: transfer        |
 |  8 Flights        |                +--------------^---------------+
 |  9 Teach objects  |                               | Ethernet video
 +-------------------+                     SIYI A2 mini camera (160° fisheye, gimbal)
```

- **Ground Station** (`groundstation/`, start with `Start Ground Station.bat` or
  `./start_ground_station.sh`): every value has a question above it ("What is
  the center of gravity of your aircraft?"); it builds the flight controller's
  parameters, the mission and the Pi's settings from your answers, checks
  them, sends them, arms for the launch, flies the mission in the simulator,
  shows the flight's pictures with coordinates, and trains new objects.
- **Flight controller**: ArduPlane with `ardupilot/gps_denied_failsafe.param`
  (no-RC settings, battery and geofence failsafes, all ending in the landing)
  and `ardupilot/scripts/silm_gps_loss.lua` (GPS lost/jammed/spoofed → stop
  trusting GPS, fly the landing sequence on dead reckoning; refuses to arm
  before the position estimator has GPS).
- **Raspberry Pi 4** (`run.py`, `silm_recon/`, set up by `pi/install.sh`):
  - `silm-recon` - reads position and attitude from the flight controller,
    watches the camera, runs the recognition model and the fire/smoke check,
    works out each object's ground position, and saves boxed pictures +
    metadata. Only records when armed, in AUTO, above the picture height.
    **Never commands the aircraft**: if it crashed, the aircraft would still
    fly and land.
  - `silm-agent` - its own Wi-Fi hotspot on the ground (switched off while
    armed) and a USB stick named SILM: settings and trained models in,
    flights out.

## Hardware

- **Matek H743** with ArduPlane 4.7, GPS/compass (M10Q), **airspeed sensor
  (required)**, battery monitor.
- **Raspberry Pi 4 Model B, 2 GB** with 64-bit Raspberry Pi OS; 5.1 V / 3 A supply; heatsink.
- **SIYI A2 mini** camera: 1080p over Ethernet (RTSP), 160° fisheye, pitch
  gimbal (UDP), 12 V. Video is decoded by the Pi 4's hardware H.264 decoder.
- No RC receiver, no telemetry radio. The optional 4G link (SIM7600E) is not
  used by this software.

## What it recognises

- **Standard model** (COCO SSD MobileNet, downloaded by `pi/install.sh`):
  person, car, truck, bus, boat, cattle, horse, sheep, ...
- **Fire and smoke**: a colour/texture check that needs no training. It
  flags sunsets and red roofs too: treat its hits as "look at this picture".
- **Tractor, combine and other farm/forestry objects**: a model trained on
  your pictures (Ground Station tab 9; `training/`). `training/openimages_dataset.py`
  fetches real labelled photos as a starting point.

**Object size in pixels is the hard limit.** By default the model looks at the
central half of the frame in 2×2 tiles (where the fisheye is sharpest). At
100 m a car is ~8 px at the model's input (marginal), a combine ~46 px (good).
The Ground Station's Mission tab tells you, for your smallest object, the
highest altitude that still gives ~10 pixels. `tools/preflight.py` measures
the recognition speed on the Pi itself against what your height and speed need.

## Each saved picture

A boxed JPEG, a JSON sidecar (object, score, box, the aircraft's full state,
the object's ground latitude/longitude), and a line in `detections.ndjson` /
`detections.csv`. Plus `track.ndjson` (position once a second), GPS-health,
Pi-health and optional ordinary pictures every few seconds (`frames/`).

## Accuracy and limits

- **Geo-referencing** assumes flat ground at launch height and a calibrated
  lens: a few metres near the picture centre at 100 m. Enough to find the
  object again, not survey grade.
- **Recognition** is only as good as the model and the pictures it was trained
  on; ground-level training photos recognise objects from above less well
  than aerial ones.
- **A landing without GPS lands long** (55-107 m past the planned point in 4-10
  m/s wind in the simulator). Above 10 m/s wind the Ground Station refuses to
  launch. See [`FLIGHT_READY.md` 2.4](FLIGHT_READY.md#24-honest-limits).
- **Not yet flown on the real aircraft**: everything is tested in software and
  in ArduPilot's simulator (`tests/`, [`FLIGHT_READY.md` §9](FLIGHT_READY.md#9-automated-tests)).

## Legal

Fully automatic flight with no RC link needs the EASA **"specific" category**
(an operational authorisation from Transpordiamet). Don't pre-cache Google map
imagery. Check the rules for photographing people and property.
[`FLIGHT_READY.md` §8](FLIGHT_READY.md#8-legal-and-safety).
