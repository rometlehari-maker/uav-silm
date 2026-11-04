# SILM mission planner (laptop GUI)

The aircraft has **no RC link** (no ExpressLRS) and no telemetry radio. Every
flight is a fully automatic mission planned here on the laptop, uploaded over
USB on the ground, and flown from a hand launch to an automatic belly landing.

```
cd uav-silm/onboard
python -m pip install -r planner/requirements.txt
python planner/app.py              # browser opens http://127.0.0.1:8765
```

Options: `--port 8800`, `--config config.yaml` (camera / detection region, used
for the swath and pixels-on-target), `--params ardupilot/gps_denied_failsafe.param`
(fence radius, altitude limit, cruise speed), `--no-browser`. The page runs only
on this computer (127.0.0.1). The map tiles (OpenStreetMap, Esri satellite) need
internet; planning, checking and upload work offline once the page is open.

## Planning a mission

1. **Set launch point** – click where you will throw it. This becomes home.
2. **Draw survey area** – click the corners of the field / forest block, then
   pick another tool to finish. Concave shapes are fine.
3. **Set landing point** and **Set landing direction** – a large, flat, open
   strip, landing **into the wind** (direction = the way the aircraft travels at
   touchdown). The planner adds a straight final approach (default 300 m long,
   starting 30 m high) and a `DO_LAND_START` before it.
4. **Smallest target** – the planner shows how many pixels that object gets at
   the model input at your altitude and suggests a lower altitude if it is
   marginal ("Use 83 m").
5. Read the **stats** (lines, distance, flight time, battery share, farthest
   point, fence needed, turn radius, glide slope) and the **messages**. Red =
   error: the mission cannot be downloaded or uploaded until it is fixed.

What the planner builds (and checks with `silm_recon/mavmission.check_mission`):

```
0  home (launch point)
1  TAKEOFF  pitch 15°, climb to 50 m   <- motor starts only after the throw
2  DO_CHANGE_SPEED  survey airspeed
3… WAYPOINTs  survey lines (interleaved so turns are never tighter than the bank limit)
n  DO_LAND_START  <- RTL, battery and GPS-loss failsafes all jump here
n+1 WAYPOINT  start of final approach
n+2 LAND      touchdown point, into the wind -> flare -> disarm after 10 s
```

## Getting it onto the aircraft

* **Upload mission** (USB): plug the Matek H743 into the laptop, pick its port
  (COM3 on Windows, /dev/ttyACM0 on Linux, /dev/cu.usbmodem… on macOS), click
  Upload. It refuses if the mission has errors or the aircraft is armed, then
  reads the mission back and compares it ("identical"). Tick **Also set
  FENCE_RADIUS** to write the fence the mission needs.
* **Download .waypoints**: the file opens in Mission Planner and QGroundControl
  too, if you prefer to upload with those.
* **Save plan / Load plan**: the plan (points, area, settings) as JSON, to
  reuse a field.

## Launch

The aircraft boots into AUTO (`INITIAL_MODE=10`). With the laptop on USB and the
aircraft in your hand, ready to throw, press **Arm for launch**: it downloads
the mission from the aircraft, refuses one without a landing sequence, switches
to AUTO and arms. Unplug USB and throw into the wind; the motor starts after
the throw. **Disarm** undoes it.

On the Pi, `tools/preflight.py` downloads the mission from the flight
controller and runs the same check before every flight.

## Test it on the simulator first

```
python tools/sitl_failsafe_test.py --mission silm_mission.waypoints --trigger-s 240
```

(start SITL with `-L` at your launch point; see the top of that file.) It
flies your mission, kills or jams GPS mid-survey, and checks it still lands and
disarms near the planned touchdown.
