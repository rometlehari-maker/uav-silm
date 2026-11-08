#!/usr/bin/env bash
# Install the UAV SILM Ground Station for this user: a menu entry with its icon
# and the command uav-silm-ground-station. Run from the unpacked folder:
#     ./install.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/uav-silm-ground-station"
APP="UAV-SILM-Ground-Station"
if [ "$HERE" != "$DEST" ]; then
  rm -rf "$DEST"
  mkdir -p "$DEST"
  cp -r "$HERE"/. "$DEST"/
fi
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/bin"
cat >"$HOME/.local/share/applications/uav-silm-ground-station.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=UAV SILM Ground Station
Comment=Plan, send and review UAV SILM survey flights
Exec="$DEST/$APP"
Icon=$DEST/icon.png
Terminal=false
Categories=Science;Utility;
DESKTOP
ln -sf "$DEST/$APP" "$HOME/.local/bin/uav-silm-ground-station"
echo "Installed. Start it from your applications menu (UAV SILM Ground Station)"
echo "or with the command: uav-silm-ground-station"
