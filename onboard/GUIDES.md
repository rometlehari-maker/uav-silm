# SILM guides: what the app can't do for you

The Ground Station does everything it can over a USB cable, the aircraft's
Wi-Fi, SSH or a USB stick. These guides cover the rest: buying the parts,
wiring them, the few things that need another program, and the paperwork.
The same guides are in the app (**Guides**, the last item in its menu), and
the app links to them wherever a step needs one.

Each guide says **what you need**, **the steps**, and **how to check it
worked**.

**Almost nothing here needs a terminal.** Where a step does (a few Linux and
Mac steps, marked), it is written for someone who has never used one:
what to open, what to type, what you will see, and how to tell it worked.
The short version of "a terminal": a window where you type one line, press
**Enter** (Return on a Mac), and the computer does it and answers in text.
Open it with **Ctrl+Alt+T** on Ubuntu, or **Cmd+Space**, type `Terminal`,
Return on a Mac. The line ending in `$` or `%` is where you type. Every
command in these guides has a **Copy** button in the app: copy, paste
(**Ctrl+Shift+V** on Ubuntu, **Cmd+V** on a Mac), Enter, and wait until the
`$` line comes back. If a command starts with `sudo`, it asks for your
computer's password: **nothing shows while you type it**, not even dots.
Type it and press Enter. Close the terminal when the guide says you are done.

## Shopping list {#parts}

What to buy, and what to look for. Prices change; the brands named are the
ones this software is built and tested for.

| Part | What to look for | Why this one |
|---|---|---|
| **Fixed-wing plane** | Hand-launch or runway airframe with room for a Pi 4, a camera looking down and a 4S battery; flies steadily at 15-22 m/s | Not a multicopter: the software flies ArduPlane missions |
| **Flight controller** | **Matek H743-WING** (V2 or V3), or another H7/F7 board that runs ArduPlane **with Lua scripting** | The GPS-loss script needs Lua; the app's firmware updater and defaults know this board |
| **GPS with compass** | u-blox M10 GPS with a compass, e.g. **Matek M10Q-5883** | Position, and the compass for flying without GPS |
| **Airspeed sensor** | Digital I2C sensor with pitot tube, e.g. **Matek ASPD-4525** or **ASPD-DLVR** | **Required**: without GPS the aircraft navigates on airspeed |
| **Aircraft computer** | **Raspberry Pi 4 Model B, 2 GB** (more memory works), heatsink case or heatsinks | Runs the camera, recognition and pictures |
| **Memory card** | microSD **32 GB or more, A1 or A2 class** (e.g. SanDisk, Samsung) | Cheap cards slow the Pi and corrupt on power loss |
| **Camera** | **SIYI A2 mini** (Ethernet version, 160° lens, pitch gimbal) | Video over Ethernet; the gimbal points it straight down |
| **Power for the Pi** | A BEC giving **5.1-5.2 V, at least 3 A continuous** | A weak supply makes the Pi slow itself down or restart |
| **Power for the camera** | **12 V** regulated (the flight controller's 12 V pad if it has one, or a 12 V BEC) | The A2 mini works on 12 V |
| **Battery** | 4S (14.8 V nominal) Li-ion or LiPo; the app asks for its capacity | The defaults are for 4S |
| **ESC + motor + propeller** | ESC with a fixed throttle range (BLHeli_S, BLHeli_32 or AM32), sized for your motor | No ESC calibration needed (see "ESC and servos") |
| **Servos** | Digital servos sized for the control surfaces | - |
| **USB stick** | Any USB stick, 8 GB or more | Name it **SILM**: the second way to move settings and pictures |
| **Cables** | USB cable for the flight controller (USB-C on the H743-WING V3), a short Ethernet cable or the camera's adapter cable, JST-GH leads, heat-shrink | - |
| **Tools** | Multimeter, soldering iron, a kitchen scale and a ruler (balance point) | Battery calibration and the balance point need them |
| **Laptop** | Windows 10/11, macOS 12+ or Linux; ~10 GB free; an NVIDIA graphics card only if you will train much | Runs the Ground Station |

**Not needed:** an RC transmitter and receiver, a telemetry radio. This
aircraft flies by itself from launch to landing.

Where to buy: Matek and SIYI parts from their authorised dealers (listed on
mateksys.com and siyi.biz); the Raspberry Pi from an approved reseller
(raspberrypi.com, "Buy"). Avoid copies of the airspeed sensor and GPS: bad
sensors cause exactly the failures this system is meant to survive.

## Wiring {#wiring}

**What you need:** the parts above, soldering iron, the board's own manual
(mateksys.com → H743-WING → manual) for pad positions.

### Flight controller ports (Matek H743-WING)

From ArduPilot's definition of this board. Pads are labelled TX/RX with a
number on the board.

| Board pads | ArduPilot port | Use in SILM |
|---|---|---|
| USB | SERIAL0 | The laptop (Ground Station) |
| TX7 / RX7 | SERIAL1 (TELEM1) | Free |
| **TX1 / RX1** | **SERIAL2 (TELEM2)** | **The Raspberry Pi** (the app's default) |
| TX2 / RX2 | SERIAL3 (GPS1) | **GPS** |
| TX3 / RX3 | SERIAL4 (GPS2) | Free |
| TX8 / RX8 | SERIAL5 | Free |
| TX4 / RX4 | SERIAL6 | Free |
| RX6 | SERIAL7 | Free (RC input; not used) |
| SDA / SCL (I2C) | - | **Compass** (from the GPS) and **airspeed sensor** |
| VBAT / CURR | battery monitor | Built in: voltage ×11, current 40 A/V by default |
| S1, S2, … | SERVO1, SERVO2, … | Motor and servos (see "ESC and servos") |

If you use another board, look up its port list on ardupilot.org (search
the board's name) and answer the app's question "Which flight-controller port
are the Pi's wires connected to?" (Camera tab) with the matching SERIAL number.

### Raspberry Pi ↔ flight controller

Three wires, 3.3 V on both sides (no level converter):

| Raspberry Pi 4 (40-pin header) | Flight controller |
|---|---|
| pin 8 (GPIO 14, TX) | **RX1** |
| pin 10 (GPIO 15, RX) | **TX1** |
| pin 6 (GND) | **G** next to those pads |

TX always goes to RX. Do **not** connect the 5 V pins of the two boards
together. The installer switches the Pi's serial port on; the app sets the
flight controller's port (protocol MAVLink 2, 115200 baud by default).

### Power

- **Pi:** the 5.1 V / 3 A BEC to the Pi's USB-C power socket (a short USB-C
  lead cut to the BEC), or to pins 2 (5 V) and 6 (GND). Not from the flight
  controller's 5 V pad: it can't supply 3 A.
- **Camera:** 12 V and ground to the camera's power lead.
- **Servos:** from the flight controller's servo rail (the H743-WING has a
  BEC for it; its voltage is set by a jumper or pad - see the manual).
- **Everything shares one ground.**

### Camera

The SIYI A2 mini's Ethernet lead into the Pi's Ethernet socket. The camera's
address is 192.168.144.25; the installer gives the Pi 192.168.144.20 on that
socket.

**Check it worked:** Tools tab → **Check everything** (camera, gimbal, flight
controller link and GPS) and **Camera → Show the live picture**.

## First ArduPilot install on a new flight controller {#firmware-first}

A new board comes with other firmware (often INAV or Betaflight) and no
ArduPilot "bootloader", so the app's **Update ArduPlane** can't reach it the
first time. Once ArduPilot is on it, every later update is done from the app.

**What you need:** the laptop, the USB cable, and **STM32CubeProgrammer**
(free from st.com; Windows, macOS and Linux; it asks for an e-mail address to
download).

1. **Get the right file.** Open firmware.ardupilot.org → **Plane** →
   **stable** → your board's folder (**MatekH743** for the H743-WING). Download
   the file ending in **`_with_bl.hex`** (for example
   `arduplane_with_bl.hex`). It contains ArduPilot *and* its bootloader.
   Not the `.apj` file (that is for updates), and not `*_bl.hex` alone (that is
   only the bootloader).
2. **Start the board in DFU mode.** Unplug everything. **Hold the BOOT
   button** on the flight controller, plug in the USB cable, then let go. The
   board's lights stay dark or dim: that is right.
3. **STM32CubeProgrammer:** on the right choose **USB** (not ST-LINK), press
   the refresh arrow until a port such as **USB1** appears, then **Connect**.
   - Windows and no port appears: the DFU driver is missing. Install
     STM32CubeProgrammer's driver (in its installation folder,
     `Drivers\DFU_Driver`), or use Zadig (zadig.akeo.ie): Options → List all
     devices → "STM32 BOOTLOADER" → WinUSB → Install.
4. Left side: **Erasing & Programming** (the second icon). Choose the
   `_with_bl.hex` file, tick **Verify programming**, press **Start
   Programming**. About a minute. "File download complete" = done.
5. **Disconnect**, unplug the USB cable, plug it in again (without BOOT).

**Check it worked:** in the Ground Station, Send tab → A → **Check
connection** shows ArduPlane and its version. Then continue in the setup
(step 5) or Send tab.

Mission Planner (Windows) can do later updates too, but not this first one.

## The Ground Station on Linux {#linux-install}

**What you need:** the file `UAV-SILM-Ground-Station-linux.tar.gz` from the
website's download button (Ubuntu 22.04 or newer, or another recent 64-bit
Linux).

1. In your Downloads folder, **right-click the file → Extract Here**. A
   folder named `UAV-SILM-Ground-Station` appears.
2. Open that folder and **double-click `UAV-SILM-Ground-Station`** (the one
   without a file ending). The Ground Station opens in your web browser,
   with a small window to quit it.
3. The first time it runs, it adds **UAV SILM Ground Station** to your
   applications menu by itself. From then on, start it from the menu.

**If double-clicking opens a text editor or does nothing:** your file manager
does not run programs by double-click. Then, once, in a terminal
(**Ctrl+Alt+T**; see the top of these guides):

```bash
cd ~/Downloads/UAV-SILM-Ground-Station && ./install.sh
```

`cd` goes into the extracted folder; `./install.sh` copies the program to
your home folder and adds the menu entry. It prints "Installed." when done.
Close the terminal; start it from the applications menu.

**Check it worked:** the Ground Station is in the applications menu and
opens in the browser.

**Camera and USB access:** to read a USB stick named SILM, Linux shows it
in the file manager by itself; the Ground Station finds it there.

## Writing the Raspberry Pi's memory card {#sd-card}

**What you need:** the microSD card, a card reader on the laptop, **Raspberry
Pi Imager** (raspberrypi.com/software; Windows, macOS, Linux).

1. Open Raspberry Pi Imager. **Device:** Raspberry Pi 4. **Operating
   system:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**.
   **Storage:** your card (check the size: everything on it is erased).
2. Press **Next**, then **Edit settings** when it asks about OS customisation:
   - **General:** hostname `silm`; a **username and password** (write them
     down - the app needs them once); **Configure wireless LAN** with your
     **home Wi-Fi** name and password and its **country** (e.g. EE); your time
     zone and keyboard.
   - **Services:** **Enable SSH** → **Use password authentication**.
3. **Save**, **Yes**, and wait for "Write successful".
4. Card into the Pi (under the board, contacts up), power it, wait **two
   minutes** for the first start.

Then: Ground Station → **Setup step 7** (or Tools tab → *Install the
aircraft computer*), with the Pi's name `silm.local` and the username and
password from step 2.

**If `silm.local` is not found:** your router's web page lists the connected
devices; use the Pi's address from there (e.g. `192.168.1.57`). Some Windows
PCs don't resolve `.local` names until Bonjour (from Apple) or iTunes is
installed - the address always works.

## The USB stick {#usb-stick}

The second way to move settings to the aircraft and pictures back, without
Wi-Fi. The stick must be **named SILM**.

- **Windows:** File Explorer → This PC → right-click the stick → **Format…** →
  File system **exFAT** (or FAT32 for sticks up to 32 GB) → Volume label
  **SILM** → Start. (Only renaming: right-click → Rename → `SILM`.)
- **macOS:** Disk Utility → the stick → **Erase** → Name **SILM**, Format
  **ExFAT** → Erase.
- **Linux:** the **Disks** program (in the applications menu) → click the
  stick in the left list → the ⚙ button under the picture of the stick →
  **Format Partition…** → Volume name **SILM**, type **exFAT** (choose "Other"
  if it is not listed) → Next → Format. No terminal needed.

**Check it worked:** Send tab → C → the stick appears in the list.

## Installing Python {#python}

The Ground Station app runs without Python. Python is needed for:
**training** your own objects, the **simulator** test, and running the
Ground Station from the source folder.

- **Windows:** python.org → Downloads → the newest Python 3 → run the
  installer and **tick "Add python.exe to PATH"** on the first screen → Install
  Now. Check: open PowerShell, type `python --version`.
- **macOS:** python.org → Downloads → the macOS installer → run it. Then in
  the Applications/Python 3.x folder double-click **Install Certificates**.
  Check: Terminal, `python3 --version`.
- **Linux (Ubuntu/Debian):** in a terminal (**Ctrl+Alt+T**; see the top of
  these guides), one line:

  ```bash
  sudo apt install python3 python3-venv python3-pip
  ```

  It asks for your password (nothing shows while you type), then
  `Do you want to continue? [Y/n]`: press **Enter**. A minute later the `$`
  line is back. Check: `python3 --version` prints a version such as
  `Python 3.12.3`. Close the terminal.

Then close and reopen the Ground Station. The Teach tab's **Install the
training tools** puts everything else in its own folder (`~/SILM/training-venv`,
2-7 GB depending on the computer, so keep about 10 GB free); deleting that folder
removes it again.

## The simulator {#simulator}

The Ground Station's **Simulator** tab flies your mission on the laptop in
ArduPilot's simulator, on a live map, with the GPS jammed halfway or a normal
flight. **On Windows and Linux nothing needs setting up:** open the tab and
press **Install the simulator**. It downloads ArduPilot's own ready-made
simulator program (7-25 MB) into your SILM folder, once. Then **Fly it in
the simulator**.

**Check it worked:** the status line goes "Starting the simulator…", then
"Taking off", and the blue line starts moving on the map. At the end:
**Passed** with every check green.

**If the download fails:** the computer needs the internet for this one step,
and the file comes from firmware.ardupilot.org. Company or school networks
sometimes block downloads of programs; try at home or on a phone's hotspot.

### The simulator on a Mac {#simulator-mac}

ArduPilot does not publish a ready-made simulator for Apple computers, so
on a Mac it has to be built once (about 30-45 minutes, mostly waiting). You
only need to type things in **Terminal** during this one-time build; the
Ground Station finds the built simulator by itself afterwards. If you have a
Windows or Linux computer, use that instead and skip this.

**What you need:** a Mac with macOS 12 or newer, about 5 GB free, the
internet, and the Mac's administrator password.

**Terminal, in short:** it is a program that comes with every Mac, in which
you type a command and press **Return** to run it. Open it with **Cmd+Space**,
type `Terminal`, press Return. A window with a line ending in `%` (or `$`)
appears: that is where you type. Copy each command below exactly (the Copy
button in the app's guide copies the whole line), paste it with **Cmd+V**,
press **Return**, and wait until the `%` line comes back before typing the
next. When a command asks `Password:`, type your Mac password: **nothing
shows while you type**, not even dots; press Return.

1. **Apple's build tools.** Type:

   ```bash
   xcode-select --install
   ```

   A window asks to install the command line developer tools: **Install**,
   agree, wait (5-15 minutes). If it says "already installed", good.

2. **Homebrew** (a program that installs other programs). Copy the one
   long command from <https://brew.sh> (it starts with `/bin/bash -c`), paste
   it, Return, type your password, Return. At the end it prints two or three
   lines starting with `echo` and `eval` under "Next steps": copy and run
   those too. Check: `brew --version` prints a version.

3. **The tools the build needs:**

   ```bash
   brew install git python@3.12 gcc
   ```

   ```bash
   pip3 install --user future pexpect empy==3.3.4 pymavlink
   ```

4. **Get ArduPilot** (about 1 GB, 5-10 minutes):

   ```bash
   cd ~ && git clone --recursive https://github.com/ArduPilot/ardupilot
   ```

   `cd ~` means "go to your home folder"; `git clone` copies ArduPilot's
   source code into a folder named `ardupilot` there.

5. **Build the simulator** (15-30 minutes; the Mac's fans will run):

   ```bash
   cd ~/ardupilot && ./waf configure --board sitl && ./waf plane
   ```

   It ends with lines like `'plane' finished successfully`. If it stops
   with an error, run the same command again once (a download may have
   failed); if it fails the same way, the text of the error is what to
   search for or ask about.

6. **Check it:**

   ```bash
   ls ~/ardupilot/build/sitl/bin/arduplane
   ```

   prints that path back (no "No such file"). Close and reopen the Ground
   Station: the Simulator tab now says **The simulator is installed**.

**To remove it later:** delete the `ardupilot` folder in your home folder.

## The camera (SIYI A2 mini) {#camera}

**Out of the box** it needs no setup: 12 V, its Ethernet lead into the Pi,
and the software finds it at **192.168.144.25** (video
`rtsp://192.168.144.25:8554/main.264`, gimbal commands on UDP port 37260).

- **Mounting:** under the fuselage, lens down, the gimbal's pitch axis
  across the aircraft (it tilts nose-to-tail). The software points it straight
  down at the start of each flight.
- **Lens calibration:** once per camera, from the app: Tools tab → **Lens
  calibration** (needs a printed checkerboard).
- **Firmware:** SIYI publishes firmware updates and its Windows program
  **SIYI PC Assistant** on siyi.biz → A2 mini → Downloads, with the steps in
  the A2 mini user manual (chapter on firmware upgrade). Update only when SIYI
  lists a fix you need; afterwards check the address is still 192.168.144.25.
- **Changed address?** If your camera was set to another address (with SIYI's
  program), the easy way is to set it back to **192.168.144.25** in SIYI's
  program. (Keeping another address means editing `config.yaml` on the Pi and
  the Pi's Ethernet address over SSH, which is for people who know Linux.)

**Check it worked:** Tools tab → **Show the live picture**, and **Check
everything** shows the camera and the gimbal PASS.

## ESC and servos {#servos-esc}

**What you need:** the aircraft with the **propeller OFF**, the battery, the
USB cable.

1. **Connect:** motor ESC signal to **S1** … and the servos to the next
   outputs, in any order you like - you tell ArduPilot which is which in
   step 2.
2. **Tell ArduPilot what each output does:** Tools tab → **Servos** → **Read
   the outputs** → choose each output's job (Aileron, Elevator, Rudder,
   Throttle, Elevon left/right, V-tail left/right…). Two ailerons on two
   outputs are both "Aileron". Unused outputs: "Disabled".
3. **Directions:** the bench check (setup step 6) - tilt the aircraft and
   watch each surface; wrong way → **Reversed** on that output.
4. **ESC throttle range:** choosing **Throttle** for an output sets it to
   1000-2000 µs, stopped at 1000. ESCs with BLHeli_S, BLHeli_32 or AM32 accept
   this range without calibration. Other ESCs: calibrate them once with a
   servo tester as their manual says (full throttle, power on, wait for the
   beeps, zero throttle).
5. **Motor direction:** before fitting the propeller, connect the ESC to a
   servo tester (a few euros) instead of the flight controller, power it and
   turn the knob slightly: the motor must turn so that the propeller will push
   air **backwards**. If not, swap any two of the three motor wires. Then
   back to its output on the flight controller.

**Check it worked:** the bench check in the setup passes, and nothing moves
the wrong way.

## Balance point (centre of gravity) {#cg}

The app asks "What is the center of gravity of your aircraft?" in mm behind
the wing's front edge, and the wing's depth (chord). It wants 25-33 % of the
chord for a normal wing.

1. Everything installed as it will fly: battery, Pi, camera, the lid on.
2. Measure the **wing depth** at the root (front edge to back edge), in mm.
3. Mark the target: 30 % of that depth behind the front edge, on both sides
   of the fuselage (a pencil mark under the wing).
4. Lift the aircraft with two fingertips (or a balancer) under the marks,
   upright. It should hang level or slightly nose-down.
   - Nose drops a lot: move the battery back. Tail drops: move it forward.
5. Find the point where it hangs level and measure it from the front edge:
   that number is your **center of gravity** answer. Enter it on the
   Aircraft tab; the app says if it is in the safe range.

**Never fly tail-heavy.** A slightly nose-heavy plane flies; a tail-heavy one
can't be recovered.

## Airspeed sensor {#airspeed}

1. **Mount** the pitot tube pointing straight forward, in clean air (out of
   the propeller's wash, away from the fuselage's nose), with the two tubes
   to the sensor short and without kinks. The tube from the pitot's tip
   (stagnation pressure) should go to the sensor's first (usually top) port;
   ArduPilot accepts either order by default (`ARSPD_TUBE_ORDR` = 2), but the
   right order lets it notice a stall.
2. **Connect** the sensor to the flight controller's I2C pads (SDA, SCL, 5 V,
   GND). On the Matek H743 the pads marked SDA1/SCL1 are bus 1, SDA2/SCL2 bus 0
   (if your board labels them without numbers, try bus 1 first).
3. **Tell ArduPilot which sensor:** Tools tab → **Airspeed sensor** → choose
   the type (MS4525 for ASPD-4525, DLVR for ASPD-DLVR) → **Set**, then restart
   the flight controller.
4. **Calibrate:** Tools tab → Calibrations → **Airspeed sensor** (cover the
   pitot tube against wind; it zeroes itself).

**Check it worked:** Tools tab → Airspeed sensor → **Check** says
"working". Blow gently into the pitot tube and press Check again: the speed
rises. In the first flight's log (Tools → Flight logs) the airspeed is close
to the GPS ground speed in calm air.

## Permission to fly: Estonia {#legal}

**Why this aircraft needs it:** it flies automatically from launch to
landing with no RC link. EASA's "open" category requires that the pilot can
always take control, so this operation is in the **"specific" category**,
which needs an authorisation from **Transpordiamet** (the Estonian Transport
Administration) before the first flight. Other EU countries: the same rules
(EU regulation 2019/947); apply to your own country's aviation authority.

1. **Register as a drone operator** with Transpordiamet (required in the
   specific category whatever the weight): Transpordiamet's e-service →
   **Applications** → **Create new application** → **Aviation certification**
   → **Unmanned aircraft operator registration**. State fee 10 €; the
   registration is renewed every year. Put your operator number on the
   aircraft.
2. **Choose how to get the authorisation** (Transpordiamet → Flying drones in
   Estonia → Specific category flights):
   - a **standard scenario** or **PDRA** (pre-defined risk assessment) if your
     flight fits one exactly;
   - otherwise a **SORA** risk assessment, which you prepare yourself and
     Transpordiamet reviews;
   - (a light UAS operator certificate, LUC, is for organisations flying a
     lot).
3. **What they will ask, and where the answers are:**
   - *How does every flight end?* Automatic landing on the mission's landing
     sequence; GPS loss, low battery and the geofence all lead to it
     (FLIGHT_READY.md part 2.2).
   - *Containment:* the geofence around the mission (`FENCE_*`, set from the
     mission by the app); the simulator results with the GPS jammed (Simulator
     tab; SITL_HOWTO.md).
   - *Where and how high:* the mission's area, height and the ground risk
     (people, roads) - the Mission tab's map and numbers.
   - *Pilot competence:* the training the authorisation requires (at least
     the A1/A3 online test; often A2 or the STS certificate).
4. **Before each flight:** check the airspace and any restrictions with
   EANS (Estonian Air Navigation Services, eans.ee → unmanned aviation) and
   follow the conditions of your authorisation.
5. **Insurance:** check what your authorisation and the airfield/landowner
   require; a liability insurance covering drones is advisable in any case.

The authorisation is valid in other EASA countries as long as their national
requirements are met. Rules change: Transpordiamet's pages are the
authority, these steps are a starting point.
