# SILM Ground Station - the laptop program

The Ground Station is where you do everything on the ground: describe the
aircraft, plan the mission, choose what the camera looks for, send it all to
the aircraft, launch, and look at the pictures afterwards. It runs on your
laptop (Windows, macOS or Linux) and opens in your web browser, but it does
not need the internet except for the map background.

- [1. Start it](#1-start-it)
- [2. The screen](#2-the-screen)
- [3. The tabs, one by one](#3-the-tabs-one-by-one)
- [4. Moving data between the laptop and the aircraft](#4-moving-data-between-the-laptop-and-the-aircraft)
- [5. Where things are saved](#5-where-things-are-saved)
- [6. When something doesn't work](#6-when-something-doesnt-work)

---

## 1. Start it

**First time only:** install **Python 3** from <https://www.python.org/downloads/>.
On Windows, tick **"Add python.exe to PATH"** on the first installer screen.
Then get the software: on GitHub, **Code → Download ZIP**, and unzip it (or
`git clone https://github.com/rometlehari-maker/uav-silm`).

**Every time:**

| Computer | Do this |
|---|---|
| **Windows** | Open the `uav-silm\onboard` folder and **double-click `Start Ground Station.bat`** |
| **macOS / Linux** | In a terminal: `cd uav-silm/onboard && ./start_ground_station.sh` |

The first start sets itself up (it needs internet once and takes about a
minute). Then your browser opens at **http://127.0.0.1:8765**. A black window
stays open: that is the Ground Station running. **Keep it open** while you use
it; close it when you're done.

Starting it again while it's already running just opens the browser page.

---

## 2. The screen

```
+--------------------------------------------------------------------------+
| SILM Ground Station           Mission [Field A     ]  [Save]  [Ready]    |
+--------------+---------------------------------------+-------------------+
| 1 Start      |                                       | Before you fly    |
| 2 Aircraft   |   the step you are on                 |  - problems to    |
| 3 Mission    |   (questions, map, buttons)           |    fix (red)      |
| 4 Camera     |                                       |  - notes          |
| 5 Send       |                                       |    (orange)       |
| 6 Launch     |                                       |                   |
| 7 Simulator  |                                       |                   |
| 8 Flights    |                                       |                   |
| 9 Teach      |                                       |                   |
+--------------+---------------------------------------+-------------------+
```

- **Left:** the steps, top to bottom. A green tick appears on a step once it is done.
- **Middle:** every value you can change has a **question above it** in bold
  ("What is the center of gravity of your aircraft?"), the box to answer in,
  and an explanation underneath. Questions with a red **\*** must be answered.
- **Right, "Before you fly":** everything that is still wrong (**red**: must be
  fixed before anything can be sent or launched) or worth knowing
  (**orange**). The pill at the top right says **Ready**, **N notes** or
  **N to fix**. A wrong answer is also outlined in red at its question.
- **Mission name + Save:** everything is saved automatically as you type;
  **Save** just confirms it.

---

## 3. The tabs, one by one

### 1 · Start

- **Your progress:** the six things to do before a flight, ticked as you go.
  Click one to jump there.
- **Missions on this laptop:** open a saved mission, delete one, or **New
  mission** (it copies your aircraft and camera answers, so you only draw the
  new area). **Save to a file… / Open a file…** move a mission between laptops.

### 2 · Aircraft

Answer once per aircraft. The answers become the flight controller's settings.

| Question | What it sets |
|---|---|
| What do you call this aircraft? | Name on the files |
| What is the wingspan? / How much does it weigh? | Your records |
| Where is the propeller? | Pusher: the motor waits 0.3 s after the throw so the propeller clears your hand |
| How deep is the wing? / **What is the center of gravity of your aircraft?** | The **balance bar** shows the usual range (25-33 % of the wing depth) in green and your balance point as a line. Far outside it is an error: re-measure before flying |
| How many cells does the battery have? / capacity | Battery warnings: low = 3.5 V per cell or 25 % left, critical = 3.3 V per cell or 15 % left; both make it land |
| How long can it fly on a full battery? | The planner warns when a mission uses more than 75 % of it |
| At what speed does it stall? / survey speed / fastest | Minimum speed = 1.3 × stall; survey and maximum speeds |
| How steeply may it bank in turns? | Turn radius and the planner's line order |
| Does it have a working airspeed sensor? | **Required** - without GPS it navigates on airspeed |
| How is it launched? | Hand: motor starts after the throw. Bungee/catapult: same, with a harder push. Runway: the motor runs at once (needs ground steering - not recommended without a pilot) |
| How does it land? | Belly (slightly nose-up) or on wheels |

### 3 · Mission

The big map, with tools above it:

1. **① Set launch point** - click where you will throw it. This is "home".
2. **② Draw survey area** - click the corners of the area one after another.
   **Undo corner** removes the last one; **Clear area** starts again.
3. **③ Set landing point** - a large, flat, open field (it defaults to the launch point).
4. **Landing direction** - click a point *ahead* of the touchdown point, or
   answer the wind question and press **Land into the wind**.
5. **Go to** (under the map): type `latitude, longitude` to jump to your field.
   The layer button (top right of the map) switches between map and satellite.

The orange line is the planned flight with numbered waypoints, the thick red
line the final approach, and the dashed red circle the geofence. The tiles at
the top right show the flight time, battery use, distance, number of lines and
the geofence that will be set. Under them: how many pixels the smallest object
will be at your height, and the highest altitude that still gives ~10 pixels.

Questions on this tab: smallest object, survey height, line overlap and
direction, takeoff height, final approach length and start height, the
legal height limit, and **today's wind** (speed and where it comes FROM).
**Above 8 m/s the Ground Station refuses to launch**: in the simulator, a
landing without GPS in stronger wind could miss the strip.

### 4 · Camera

What the aircraft's computer (the Raspberry Pi 4) looks for and when it takes
pictures:

- **Which recognition model?** The standard one (people, cars, trucks, buses,
  boats, cattle, horses, sheep), or one you trained on tab 9.
- **What should it look for?** Click the objects (green = on). Farm objects
  need a trained model.
- **Fire and smoke?** A colour check that needs no training; it also flags
  sunsets and red roofs, so look at its pictures.
- **How sure must it be?** Lower finds more but also saves more mistakes.
- **From what height should it start taking pictures?** Nothing is saved below it.
- **Also save ordinary pictures every few seconds?** For looking at the area
  afterwards and for teaching it new objects.
- **Camera address / gimbal**, **how the Pi is wired to the flight controller**,
  and **what the Pi does when switched on**: *Fly* (pictures only in the air,
  armed, in AUTO) or *Bench test* (pictures whenever it sees something - for
  checking the camera on the ground; never fly in this mode).

### 5 · Send to aircraft

See [section 4](#4-moving-data-between-the-laptop-and-the-aircraft). Three
cards: **A** flight controller over USB, **B** aircraft computer over Wi-Fi,
**C** aircraft computer with a USB stick.

### 6 · Launch

- **Wind today:** green if within the limit.
- **Checklist:** nine lines (simulator passed, everything sent, battery and
  balance, GPS OK, strip walked, wind, permission, propeller clear). **ARM for
  launch** stays greyed out until every line is ticked and there are no red
  problems.
- **ARM for launch** re-checks the mission stored on the aircraft, switches to
  AUTO and arms. Then: unplug the USB cable, hold it level, nose into the wind,
  throw firmly. **Disarm** if you change your mind.

### 7 · Simulator

Flies **this exact mission** with your aircraft's settings and today's wind in
ArduPilot's simulator; halfway through the GPS is jammed (or lost) for as long
as you choose. Results: six checks (turned back in time, headed for the strip,
stayed inside the geofence, knew where it was again, landed and switched off,
landed near the planned point), the touchdown distance and a picture of the
track.

The simulator runs on **Linux**. On Windows, run the Ground Station inside
Ubuntu (WSL2) for this tab - set up once as in [`SITL_HOWTO.md`](SITL_HOWTO.md) -
or copy the commands the tab shows.

### 8 · Flights & pictures (image analysis)

- **Bring a flight to this laptop:** **From the aircraft (Wi-Fi)…** lists the
  flights on the aircraft - **Download**; **From the USB stick…**; or **Import**
  any folder (the Pi's SD card, a copied folder).
- **Flights on this laptop:** click one. You see the number of pictures,
  objects found, GPS problems; **filter buttons** per object type; a **map**
  with the flight track (red where GPS was bad) and a dot for every picture;
  and all pictures in a grid **with their coordinates**.
- **Click a picture** (or a dot on the map): it opens large, with the found
  object boxed, and shows what it is, how sure, the **coordinates** (with a
  **Copy** button), where the position comes from, the time, height and
  heading, and links to OpenStreetMap and Google Maps. ← / → step through the
  pictures, Esc closes.
- **Where the coordinates come from:** a found object gets the position of the
  **object on the ground** (worked out from the camera angle and height). An
  ordinary picture gets the **aircraft's position** at the moment it was taken
  (from the once-a-second flight track).
- **Export map (GeoJSON)** for QGIS or Google Earth; **Export list (CSV)** for Excel.

### 9 · Teach new objects

The standard model doesn't know tractors or combines. To teach it:

1. **Object types** - choose the type you are marking; **Add** a new one if
   needed (e.g. *sprayer*).
2. **Add pictures** - drop JPEG/PNG files on the box or choose them. Best:
   pictures **from above, at your flying height** - the ordinary pictures from
   your own flights are ideal. The same picture twice is stored once.
3. **Mark the objects** - **drag a box** tightly around every object of that
   type. Click a box to select it (**Delete** removes it). Pictures with nothing
   to find: press **Nothing here** - they teach the model too. ← / → move
   between pictures. Every change is saved at once.
4. **Train** - the table shows pictures and boxes per type and what is still
   missing (at least 30 pictures per type; 150+ is good). Choose **Quick test**,
   **Good** or **Best** and **Start training**. When it's done, the model
   appears under "Trained models" with how many objects it finds; press **Use
   on the aircraft**, then send it (tab 5 - the model goes with the settings).

Training needs the training tools on the laptop (about 2 GB; the tab tells you
the command). A computer with an NVIDIA graphics card is 10-50 times faster.
[`training/README.md`](training/README.md) explains training in depth.

---

## 4. Moving data between the laptop and the aircraft

The aircraft has two computers, and each gets your answers differently:

| | Flight controller (Matek H743, ArduPilot) | Aircraft computer (Raspberry Pi 4) |
|---|---|---|
| Does | flies, launches, lands | camera, recognition, pictures |
| Gets | **parameters** (settings), the **GPS-loss script**, the **mission** | **camera settings**, the mission copy, a trained **model** |
| How | **USB cable** to the flight controller | its own **Wi-Fi**, or a **USB stick** |
| Gives back | - | the **flights** (pictures, track, logs) |

**After every change, send to both.** The aircraft must be **disarmed**: both
computers refuse changes while it is armed or in the air.

### A. Flight controller - USB cable

1. Plug the battery into the aircraft and the USB cable from the flight
   controller into the laptop.
2. Tab 5, card A: **Find ports**, pick the port (Windows: COM3, COM4…; Linux:
   /dev/ttyACM0; macOS: /dev/cu.usbmodem…), **Check connection**. It shows the
   firmware, GPS, battery voltage and whether the GPS-loss script is running.
3. **1. Send the aircraft settings (parameters)** - about a minute; every
   value is written and read back.
4. **2. Copy the GPS-loss script** - copied to the flight controller's SD card
   (`APM/scripts/silm_gps_loss.lua`) and read back.
5. **The first time:** unplug the battery and USB for 10 s (reboot), plug back
   in, **Check connection** → "GPS-loss script is running".
6. **3. Send the mission** - uploaded, read back, compared: "identical".

Prefer Mission Planner or QGroundControl? Open **"Or use Mission Planner…"** on
card A and download the `.param`, `.waypoints` and `settings.yaml` files.

### B. Aircraft computer - Wi-Fi

The Pi makes its own Wi-Fi network on the ground (set up once by
`pi/install.sh`, see [`FLIGHT_READY.md`](FLIGHT_READY.md) B2):

1. Power the aircraft. Wait about a minute.
2. On the laptop, join the Wi-Fi network **SILM-…** (the password you chose at
   set-up). The laptop has no internet while joined; that's normal.
3. Tab 5, card B: address **10.42.0.1**, **Check connection**. It shows the Pi's
   software version, whether the flight program is running, GPS, camera, the
   settings on the Pi and how many flights it has stored.
4. **Send camera settings and mission** - checked on the Pi exactly as the
   flight program will check them, installed, and the flight program restarted
   with them. With a trained model chosen, the model goes first.
5. **Flights back:** tab 8, **From the aircraft (Wi-Fi)…**, **Download**.

The Pi switches its Wi-Fi **off as soon as the aircraft is armed** (nothing
transmits in flight) and back on 20 s after it has landed and disarmed.

### C. Aircraft computer - USB stick

For when Wi-Fi isn't available.

1. Format a USB stick as **FAT32** or **exFAT** and name it **SILM**.
2. Plug it into the laptop. Tab 5, card C: **Find sticks**, pick it,
   **Write settings to the stick**. This writes `SILM_TRANSFER/to_aircraft/`
   (settings, mission, and the trained model if one is chosen). Eject it safely.
3. Plug it into the aircraft's Pi and power on (or leave it on). Within a few
   seconds the Pi imports the settings and writes `import_result.json` next to
   them. **Finished flights are copied onto the stick** at the same time
   (`SILM_TRANSFER/from_aircraft/`).
4. Back in the laptop: **Read the stick** says whether the aircraft accepted
   the settings (or why not). Tab 8, **From the USB stick…** imports the flights.

You can leave the stick in the aircraft: after each flight, once it has landed
and disarmed, the new flight is copied onto it.

### D. Without either: the SD card

Power off, take the Pi's SD card out, put it in the laptop, tab 8: **Import**
the folder `silm_captures` (or one `mission_…` folder in it).

---

## 5. Where things are saved

On the laptop, in your home folder (`C:\Users\<you>\SILM` on Windows):

| Folder | What |
|---|---|
| `SILM/projects/` | your missions (`<name>.json`) |
| `SILM/flights/` | flights brought from the aircraft (`mission_YYYYmmdd_HHMMSS/`) |
| `SILM/training/my_objects/` | training pictures (`images/`), boxes (`labels/`), object types |
| `SILM/training/models/` | trained models (`.tflite` + labels) |

On the aircraft's Pi: settings in `~/silm-settings/` (`settings.yaml`,
`mission.waypoints`, `project.json`, `models/`), flights in `~/silm_captures/`.

---

## 6. When something doesn't work

| What you see | What to do |
|---|---|
| The browser doesn't open | Open <http://127.0.0.1:8765> yourself. Check the black window for an error |
| "Python was not found" | Install Python 3 (tick "Add to PATH"), double-click again |
| Map is grey | No internet: the map background needs it. Everything else works; plan at home and save |
| "No serial ports found" | Battery and USB cable in? Another program (Mission Planner) holding the port? Close it |
| "no answer on COM…" | Wrong port, or the flight controller is still starting: wait 10 s, try again |
| "the aircraft is ARMED" | Disarm (tab 6) first |
| Pi: "no answer from http://10.42.0.1…" | Is the laptop joined to the SILM-… Wi-Fi? Aircraft powered for a minute? Armed (Wi-Fi is off)? |
| Pi: "settings rejected" | The message says which value; fix it and send again (nothing on the Pi changed) |
| Stick: "has not read this stick yet" | Is the stick named SILM? Was the Pi powered with it in for 10 s? |
| "Not answered: …" | Answer that question (outlined in red) |
| ARM greyed out | Fix the red problems on the right and tick every checklist line |
| Simulator tab: "runs on Linux" | Run the Ground Station in Ubuntu (WSL2), or copy the commands shown |
| Training: "tools are not installed" | Run the command the tab shows, once (`python -m pip install ultralytics`) |
