#!/usr/bin/env python3
"""Pre-cache an offline satellite/terrain basemap for a mission area.

Run this ON THE GROUND before the flight (the aircraft has no link in the air).
It downloads XYZ tiles covering a bounding box at one zoom level into an XYZ
folder that silm_recon/tiles.py can read offline.

    python3 tools/download_tiles.py \
        --url "https://{s}.example-permitted-source/{z}/{x}/{y}.jpg" \
        --bbox 24.40 58.33 24.62 58.45 \
        --zoom 17 \
        --out /home/pi/basemap_xyz

LICENCE: use a source whose terms permit offline caching. Do NOT point this at
Google Maps/Earth tiles -- that violates Google's terms. Esri World Imagery,
Mapbox (with an offline plan), national ortho/WMS services, or your own imagery
are appropriate. Set a descriptive, honest User-Agent and respect the source's
rate limits and attribution requirements.
"""

from __future__ import annotations

import argparse
import math
import os
import time
import urllib.request


def deg2tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="XYZ template with {z}/{x}/{y} (and optional {s})")
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    ap.add_argument("--zoom", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--subdomains", default="a,b,c")
    ap.add_argument("--user-agent", default="SILM-UAV-basemap/0.1 (student project)")
    ap.add_argument("--delay", type=float, default=0.1, help="seconds between requests")
    args = ap.parse_args()

    minlon, minlat, maxlon, maxlat = args.bbox
    z = args.zoom
    x0, y1 = deg2tile(maxlat, minlon, z)  # NW
    x1, y0 = deg2tile(minlat, maxlon, z)  # SE
    xlo, xhi = sorted((x0, x1))
    ylo, yhi = sorted((y0, y1))
    subs = args.subdomains.split(",")

    total = (xhi - xlo + 1) * (yhi - ylo + 1)
    print(f"downloading {total} tiles at z={z} into {args.out}")
    done = 0
    for x in range(xlo, xhi + 1):
        for y in range(ylo, yhi + 1):
            s = subs[(x + y) % len(subs)]
            url = (args.url
                   .replace("{s}", s).replace("{z}", str(z))
                   .replace("{x}", str(x)).replace("{y}", str(y)))
            ext = url.rsplit(".", 1)[-1].split("?")[0][:4] or "png"
            dest_dir = os.path.join(args.out, str(z), str(x))
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, f"{y}.{ext}")
            done += 1
            if os.path.exists(dest):
                continue
            req = urllib.request.Request(url, headers={"User-Agent": args.user_agent})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read()
                with open(dest, "wb") as fh:
                    fh.write(data)
            except Exception as exc:
                print(f"  {done}/{total} FAIL {x}/{y}: {exc}")
                continue
            if done % 25 == 0:
                print(f"  {done}/{total}")
            time.sleep(args.delay)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
