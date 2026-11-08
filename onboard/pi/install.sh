#!/usr/bin/env bash
# =============================================================================
# SILM - one-time setup of the aircraft's Raspberry Pi 4 Model B (2 GB)
# =============================================================================
# Run ON THE PI, with internet (Ethernet to your router, or your home Wi-Fi),
# from the onboard/ folder of the cloned repository:
#
#     cd ~/uav-silm/onboard
#     bash pi/install.sh                 # asks for the hotspot password
#     bash pi/install.sh --password 'my-secret-123' --ssid SILM-aircraft
#
# Options:
#     --ssid NAME        Wi-Fi hotspot name            (default SILM-<hostname>)
#     --password PASS    Wi-Fi hotspot password, 8-63 characters
#     --no-hotspot       don't set up the Wi-Fi hotspot (USB stick only)
#     --country CC       Wi-Fi country if none is set yet (default EE); the
#                        hotspot can't start without one
#     --no-camera-net    don't reserve the Ethernet port for the SIYI camera
#     --skip-apt         don't install system packages (re-runs)
#     --dry-run          print every command, change nothing
#
# What it does (safe to run again; each step checks first):
#   1. system packages: OpenCV with GStreamer (hardware video decoding), Python
#   2. a Python environment ~/silm-venv with pymavlink, the TFLite runtime ...
#   3. the detection model (COCO SSD MobileNet, checked by SHA-256)
#   4. the two services: silm-recon (flight program) and silm-agent (transfer)
#   5. permission for the agent to restart the flight program and switch Wi-Fi
#   6. the Wi-Fi hotspot the laptop joins on the ground (10.42.0.1)
#   7. the camera network on the Ethernet port (Pi 192.168.144.20, camera
#      .25; active after the reboot) and the serial port to the flight controller
#   8. automatic mounting of a USB stick labelled SILM at /mnt/silm_usb
#   9. a configuration check (run.py --check)
# Expects 64-bit Raspberry Pi OS (Bookworm or later) on a Raspberry Pi 4.
# =============================================================================
set -euo pipefail

SSID=""
PASSWORD=""
HOTSPOT=1
CAMNET=1
COUNTRY="EE"
APT=1
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ssid) SSID="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --no-hotspot) HOTSPOT=0; shift ;;
    --country) COUNTRY="$2"; shift 2 ;;
    --no-camera-net) CAMNET=0; shift ;;
    --skip-apt) APT=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
done

ONBOARD="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
HOME_DIR="$(eval echo "~$USER_NAME")"
VENV="$HOME_DIR/silm-venv"
MODEL_URL="https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip"
MODEL_SHA="a809cd290b4d6a2e8a9d5dad076e0bd695b8091974e0eed1052b480b2f21b6dc"
MODEL_FILE="$ONBOARD/models/coco_ssd_mobilenet_v1_quant.tflite"
MODEL_FILE_SHA="e4b118e5e4531945de2e659742c7c590f7536f8d0ed26d135abcfe83b4779d13"   # detect.tflite in the zip
[ -z "$SSID" ] && SSID="SILM-$(hostname)"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32mOK\033[0m %s\n' "$*"; }
warn() { printf '    \033[33mWARNING\033[0m %s\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }
# write a root-owned file from stdin
put()  { if [ "$DRY" = 1 ]; then printf '    [dry-run] write %s\n' "$1"; cat >/dev/null; else sudo tee "$1" >/dev/null; fi; }

if [ "$(id -u)" = 0 ] && [ "$DRY" = 0 ]; then
  echo "Run this as your normal user (not with sudo); it asks for sudo when needed."; exit 2
fi
[ -f "$ONBOARD/run.py" ] || { echo "run.py not found next to pi/ - run from the onboard folder"; exit 2; }

# ---------------------------------------------------------------- 0. checks
step "Checking the Raspberry Pi"
MODEL_TXT="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unknown)"
case "$MODEL_TXT" in
  *"Raspberry Pi 4"*) ok "$MODEL_TXT" ;;
  *) warn "this is '$MODEL_TXT', not a Raspberry Pi 4 - continuing anyway" ;;
esac
ARCH="$(uname -m)"
[ "$ARCH" = aarch64 ] && ok "64-bit OS" || warn "64-bit Raspberry Pi OS is expected (this is $ARCH)"
MEM_MB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
ok "memory ${MEM_MB} MB"

if [ "$HOTSPOT" = 1 ] && [ -z "$PASSWORD" ] && [ "$DRY" = 0 ]; then
  echo
  echo "The Pi will run its own Wi-Fi network '$SSID' on the ground."
  read -r -s -p "Choose its password (8-63 characters): " PASSWORD; echo
fi
if [ "$HOTSPOT" = 1 ] && [ "$DRY" = 0 ]; then
  [ ${#PASSWORD} -ge 8 ] && [ ${#PASSWORD} -le 63 ] || { echo "password must be 8-63 characters"; exit 2; }
fi

# ---------------------------------------------------------------- 1. packages
if [ "$APT" = 1 ]; then
  step "1/9 System packages (OpenCV + GStreamer for hardware video decoding)"
  run sudo apt-get update
  run sudo apt-get install -y python3-opencv python3-numpy python3-yaml python3-venv \
      python3-serial gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
      gstreamer1.0-libav network-manager curl unzip exfat-fuse exfatprogs
  ok "packages installed"
else
  step "1/9 System packages: skipped (--skip-apt)"
fi

# ---------------------------------------------------------------- 2. venv
step "2/9 Python environment $VENV"
[ -d "$VENV" ] || run python3 -m venv --system-site-packages "$VENV"
run "$VENV/bin/pip" install --upgrade pip
run "$VENV/bin/pip" install -r "$ONBOARD/requirements.txt"
ok "Python packages installed"

# ---------------------------------------------------------------- 3. model
step "3/9 Detection model"
if [ -f "$MODEL_FILE" ] && echo "$MODEL_FILE_SHA  $MODEL_FILE" | sha256sum -c - >/dev/null 2>&1; then
  ok "already present and checked: $MODEL_FILE"
else
  [ -f "$MODEL_FILE" ] && warn "the model file is damaged or different: downloading it again"
  TMPZ="$(mktemp -d)"
  run curl -fsSL -o "$TMPZ/model.zip" "$MODEL_URL"
  if [ "$DRY" = 0 ]; then
    echo "$MODEL_SHA  $TMPZ/model.zip" | sha256sum -c - >/dev/null || { echo "model download corrupt"; exit 1; }
    unzip -p "$TMPZ/model.zip" detect.tflite >"$MODEL_FILE"
    echo "$MODEL_FILE_SHA  $MODEL_FILE" | sha256sum -c - >/dev/null || { echo "model file corrupt"; exit 1; }
  fi
  rm -rf "$TMPZ"
  ok "downloaded and checked"
fi

# ---------------------------------------------------------------- 4. services
step "4/9 Services silm-recon (flight program) and silm-agent (transfer)"
for svc in silm-recon silm-agent; do
  sed "s#@USER@#$USER_NAME#g; s#@ONBOARD@#$ONBOARD#g; s#@VENV@#$VENV#g" \
      "$ONBOARD/systemd/$svc.service" | put "/etc/systemd/system/$svc.service"
done
# /run/silm holds the live status file both services share
echo "d /run/silm 0755 $USER_NAME $USER_NAME -" | put /etc/tmpfiles.d/silm.conf
run sudo systemd-tmpfiles --create /etc/tmpfiles.d/silm.conf
run mkdir -p "$HOME_DIR/silm-settings" "$HOME_DIR/silm_captures"
run sudo systemctl daemon-reload
run sudo systemctl enable silm-recon.service silm-agent.service
ok "services installed and enabled (start at every power-on)"

# ---------------------------------------------------------------- 5. sudo rules
step "5/9 Permissions for the agent (pause/restart the program, switch Wi-Fi)"
SUDOERS_TMP="$(mktemp)"
cat >"$SUDOERS_TMP" <<EOF
# SILM ground agent: exactly these commands, nothing else
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl restart silm-recon
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl stop silm-recon
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/systemctl start silm-recon
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/nmcli radio wifi on
$USER_NAME ALL=(root) NOPASSWD: /usr/bin/nmcli radio wifi off
$USER_NAME ALL=(root) NOPASSWD: /usr/local/sbin/silm-wifi
EOF
if [ "$DRY" = 1 ]; then
  echo "    [dry-run] install /etc/sudoers.d/silm:"; sed 's/^/      /' "$SUDOERS_TMP"
else
  sudo visudo -cf "$SUDOERS_TMP" >/dev/null || { echo "sudoers rule invalid"; exit 1; }
  sudo install -m 0440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/silm
fi
rm -f "$SUDOERS_TMP"
# the Wi-Fi helper the agent may run as root (it checks every value itself)
run sudo install -m 0755 -o root -g root "$ONBOARD/pi/silm-wifi" /usr/local/sbin/silm-wifi
ok "rules installed (/etc/sudoers.d/silm) and the Wi-Fi helper (/usr/local/sbin/silm-wifi)"

# ---------------------------------------------------------------- 6. hotspot
if [ "$HOTSPOT" = 1 ]; then
  step "6/9 Wi-Fi hotspot '$SSID' (laptop joins it on the ground; the Pi is 10.42.0.1)"
  if command -v raspi-config >/dev/null && [ -z "$(sudo raspi-config nonint get_wifi_country 2>/dev/null || true)" ]; then
    run sudo raspi-config nonint do_wifi_country "$COUNTRY"
    ok "Wi-Fi country set to $COUNTRY (change with --country XX)"
  fi
  if nmcli -t -f NAME con show 2>/dev/null | grep -qx silm-hotspot; then
    run sudo nmcli con delete silm-hotspot
  fi
  # Low autoconnect priority: a known Wi-Fi (your home network, for updates)
  # is preferred when in range; at the field the hotspot comes up instead.
  run sudo nmcli con add type wifi ifname wlan0 con-name silm-hotspot autoconnect yes \
      ssid "$SSID" mode ap ipv4.method shared ipv4.addresses 10.42.0.1/24 \
      802-11-wireless.band bg wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASSWORD" \
      connection.autoconnect-priority -10
  ok "hotspot '$SSID' created (address 10.42.0.1, Ground Station port 8080)"
else
  step "6/9 Wi-Fi hotspot: skipped (--no-hotspot) - use the USB stick"
fi

# ---------------------------------------------------------------- 7. camera + FC port
step "7/9 Camera network and the serial port to the flight controller"
if [ "$CAMNET" = 1 ]; then
  if nmcli -t -f NAME con show 2>/dev/null | grep -qx siyi-camera; then
    ok "camera network already set up"
  else
    # Not switched on now (that could cut the internet this install uses);
    # it takes over the Ethernet port from the next boot.
    run sudo nmcli con add type ethernet ifname eth0 con-name siyi-camera autoconnect yes \
        ipv4.method manual ipv4.addresses 192.168.144.20/24 ipv6.method disabled \
        connection.autoconnect-priority 10
    ok "Ethernet port -> camera network after the reboot (Pi 192.168.144.20, camera 192.168.144.25)"
    ok "use Wi-Fi for internet from now on (--no-camera-net to keep Ethernet for internet)"
  fi
else
  ok "camera network: skipped (--no-camera-net)"
fi
# UART on GPIO 14/15 for the flight controller; no login console on it
if [ "$DRY" = 1 ]; then
  echo "    [dry-run] sudo raspi-config nonint do_serial_hw 0; sudo raspi-config nonint do_serial_cons 1"
elif command -v raspi-config >/dev/null; then
  { sudo raspi-config nonint do_serial_hw 0 && sudo raspi-config nonint do_serial_cons 1; } \
    || sudo raspi-config nonint do_serial 2 \
    || warn "could not set the serial port: sudo raspi-config > Interface Options > Serial Port (login shell No, hardware Yes)"
  ok "serial port /dev/serial0 for the flight controller (after the reboot)"
else
  warn "raspi-config not found: enable the serial port yourself (login shell No, hardware Yes)"
fi

# ---------------------------------------------------------------- 8. USB stick
step "8/9 USB stick labelled SILM -> /mnt/silm_usb (mounted automatically)"
run sudo mkdir -p /mnt/silm_usb
FSTAB_LINE="LABEL=SILM /mnt/silm_usb auto nofail,noauto,x-systemd.automount,x-systemd.idle-timeout=10,x-systemd.device-timeout=3,uid=$(id -u),gid=$(id -g),umask=022 0 0"
if grep -q "/mnt/silm_usb" /etc/fstab 2>/dev/null; then
  ok "already in /etc/fstab"
else
  if [ "$DRY" = 1 ]; then echo "    [dry-run] append to /etc/fstab: $FSTAB_LINE"
  else echo "$FSTAB_LINE" | sudo tee -a /etc/fstab >/dev/null; fi
  run sudo systemctl daemon-reload
  ok "added (format the stick as FAT32 or exFAT and name it SILM)"
fi

# ---------------------------------------------------------------- 9. check
step "9/9 Checking the configuration"
if [ "$DRY" = 1 ]; then
  echo "    [dry-run] $VENV/bin/python3 $ONBOARD/run.py --check"
else
  "$VENV/bin/python3" "$ONBOARD/run.py" --check
  sudo systemctl restart silm-recon silm-agent
  sleep 3
  for svc in silm-recon silm-agent; do
    systemctl is-active --quiet "$svc" && ok "$svc running" || warn "$svc not running: journalctl -u $svc -n 50"
  done
fi

cat <<EOF

Done. Next:
  * Reboot once (sudo reboot) so the hotspot, the camera network, the serial
    port and the USB mount are active.
  * On the laptop: join Wi-Fi '$SSID', open the Ground Station, tab
    "Send to aircraft" > Pi address 10.42.0.1 > Check connection.
  * Then run the full check on the bench: $VENV/bin/python3 tools/preflight.py
EOF
