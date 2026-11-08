#!/usr/bin/env python3
"""Build a post-flight review page for one mission folder.

    python3 tools/make_review.py /path/to/mission_YYYYmmdd_HHMMSS
    -> /path/to/mission_.../review.html   (open it in a browser)

The page shows the flight track (red where GPS was degraded or lost), every
saved detection as a coloured marker with its photo, filters by class, a photo
grid, and the GPS-health and companion-health events. The map tiles come from
OpenStreetMap, so the map needs internet when you view it; everything else
(photos, list, events) works offline.
"""

from __future__ import annotations

import html
import json
import os
import sys
from collections import Counter

PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6",
           "#bfef45", "#469990", "#9a6324", "#800000", "#808000", "#000075", "#a9a9a9", "#dcbeff"]


def read_ndjson(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # a line cut off by power loss
    return out


def collect(mission_dir):
    recs = read_ndjson(os.path.join(mission_dir, "detections.ndjson"))
    dets = []
    for r in recs:
        ac = r.get("aircraft", {})
        for d in r.get("detections", []):
            lat, lon = d.get("ground_lat"), d.get("ground_lon")
            src = "ground"
            if lat is None or lon is None:
                lat, lon, src = ac.get("lat"), ac.get("lon"), "aircraft"
            dets.append({"seq": r.get("seq"), "time": r.get("time"), "img": r.get("jpg"),
                         "label": d.get("label"), "score": d.get("score"),
                         "lat": lat, "lon": lon, "pos_src": src,
                         "alt": ac.get("rel_alt_m"), "mode": ac.get("mode")})
    track = [t for t in read_ndjson(os.path.join(mission_dir, "track.ndjson"))
             if t.get("lat") is not None]
    gps = read_ndjson(os.path.join(mission_dir, "gps_health.ndjson"))
    health = [h for h in read_ndjson(os.path.join(mission_dir, "health.ndjson")) if h.get("warnings")]
    return dets, track, gps, health


def build(mission_dir) -> str:
    dets, track, gps, health = collect(mission_dir)
    labels = sorted({d["label"] for d in dets})
    colors = {lab: PALETTE[i % len(PALETTE)] for i, lab in enumerate(labels)}
    counts = Counter(d["label"] for d in dets)
    name = os.path.basename(os.path.abspath(mission_dir))
    dur = ""
    if len(track) > 1:
        m = int((track[-1]["t"] - track[0]["t"]) // 60)
        dur = f"{m} min logged"
    gps_bad = sum(1 for g in gps if "LOST" in g.get("event", "") or "DEGRADED" in g.get("event", ""))

    data = {"dets": dets, "track": track, "colors": colors}
    data_json = json.dumps(data).replace("</", "<\\/")
    chips = "".join(
        f'<button class="chip" aria-pressed="true" data-label="{html.escape(l)}">'
        f'<i style="background:{colors[l]}"></i>{html.escape(l)} <b>{counts[l]}</b></button>'
        for l in labels)
    gps_rows = "".join(
        f"<li><time>{html.escape(str(g.get('t', '')))}</time> {html.escape(str(g.get('event', '')))}"
        f" &middot; sats {g.get('satellites', '?')}, hdop {g.get('hdop', '?')}</li>" for g in gps) \
        or "<li>No GPS events recorded.</li>"
    health_rows = "".join(
        f"<li><time>{html.escape(str(h.get('t', '')))}</time> {html.escape('; '.join(h['warnings']))}</li>"
        for h in health) or "<li>No companion warnings.</li>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight review {html.escape(name)}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root {{ --bg:#f6f7f8; --panel:#fff; --text:#1d2327; --dim:#5d6a71; --line:#d9dee1; --accent:#0b7285; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161b1e; --panel:#1f262a; --text:#e6ebee; --dim:#9aa7ae; --line:#334047; --accent:#66d9e8; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
header {{ padding:20px 16px 8px; max-width:1200px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:28px 0 10px; }}
.meta {{ color:var(--dim); }}
main {{ padding:0 16px 40px; max-width:1200px; margin:0 auto; }}
.stats {{ display:flex; flex-wrap:wrap; gap:10px; margin:12px 0; }}
.stat {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px 14px; }}
.stat b {{ display:block; font-size:20px; }}
#map {{ height:min(62vh,560px); border-radius:12px; border:1px solid var(--line); background:var(--panel); }}
#nomap {{ display:none; padding:16px; color:var(--dim); }}
.chips {{ display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }}
.chip {{ display:inline-flex; align-items:center; gap:7px; border:1px solid var(--line); background:var(--panel); color:var(--text); border-radius:999px; padding:5px 12px; cursor:pointer; font:inherit; }}
.chip i {{ width:10px; height:10px; border-radius:50%; display:inline-block; }}
.chip[aria-pressed="false"] {{ opacity:.4; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:12px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
.card img {{ width:100%; aspect-ratio:16/10; object-fit:cover; display:block; background:#0002; }}
.card div {{ padding:8px 10px; font-size:13px; }}
.card b {{ font-size:14px; }}
.card a {{ color:var(--accent); }}
ul.ev {{ list-style:none; padding:0; margin:0; background:var(--panel); border:1px solid var(--line); border-radius:10px; }}
ul.ev li {{ padding:8px 12px; border-top:1px solid var(--line); font-size:13px; }} ul.ev li:first-child {{ border-top:0; }}
time {{ color:var(--dim); font-variant-numeric:tabular-nums; margin-right:6px; }}
.popup img {{ width:220px; display:block; border-radius:6px; margin-bottom:6px; }}
</style></head><body>
<header><h1>Flight review</h1><div class="meta">{html.escape(name)} {('&middot; ' + dur) if dur else ''}</div>
<div class="stats">
 <div class="stat"><b>{len(dets)}</b>detections</div>
 <div class="stat"><b>{len(labels)}</b>classes</div>
 <div class="stat"><b>{gps_bad}</b>GPS degraded/lost events</div>
 <div class="stat"><b>{len(health)}</b>companion warnings</div>
</div></header>
<main>
<div class="chips" id="chips">{chips}</div>
<div id="map"></div><div id="nomap">Map needs internet (OpenStreetMap tiles). The photo list below works offline.</div>
<h2>Detections</h2><div class="grid" id="grid"></div>
<h2>GPS events</h2><ul class="ev">{gps_rows}</ul>
<h2>Companion health warnings</h2><ul class="ev">{health_rows}</ul>
</main>
<script>const DATA = {data_json};</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const on = new Set(Object.keys(DATA.colors));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[c]));
function cardHTML(d) {{
  const osm = `https://www.openstreetmap.org/?mlat=${{d.lat}}&mlon=${{d.lon}}#map=18/${{d.lat}}/${{d.lon}}`;
  return `<a href="${{esc(d.img)}}" target="_blank"><img loading="lazy" src="${{esc(d.img)}}" alt="${{esc(d.label)}}"></a>
  <div><b style="color:${{DATA.colors[d.label]}}">${{esc(d.label)}}</b> ${{(d.score*100).toFixed(0)}}% &middot; #${{d.seq}}<br>
  <time>${{esc(d.time)}}</time><br>${{d.lat?.toFixed(6)}}, ${{d.lon?.toFixed(6)}} (${{d.pos_src}})
  &middot; <a href="${{osm}}" target="_blank">map</a></div>`;
}}
function renderGrid() {{
  const g = document.getElementById("grid");
  g.innerHTML = "";
  DATA.dets.filter(d => on.has(d.label)).forEach(d => {{
    const c = document.createElement("div"); c.className = "card"; c.innerHTML = cardHTML(d); g.appendChild(c);
  }});
  if (!g.children.length) g.innerHTML = '<p class="meta">No detections.</p>';
}}
let markers = [];
if (window.L) {{
  const map = L.map("map");
  L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",
    {{maxZoom: 19, attribution: "&copy; OpenStreetMap contributors"}}).addTo(map);
  const pts = [];
  let seg = [], segBad = null;
  const flush = () => {{ if (seg.length > 1) L.polyline(seg, {{color: segBad ? "#e03131" : "#495057", weight: segBad ? 4 : 2}}).addTo(map); }};
  DATA.track.forEach(t => {{
    const bad = t.gps && t.gps !== "OK";
    if (segBad !== null && bad !== segBad) {{ flush(); seg = seg.slice(-1); }}
    segBad = bad; seg.push([t.lat, t.lon]); pts.push([t.lat, t.lon]);
  }});
  flush();
  DATA.dets.forEach(d => {{
    if (d.lat == null) return;
    const m = L.circleMarker([d.lat, d.lon], {{radius: 8, color: "#fff", weight: 1.5,
      fillColor: DATA.colors[d.label], fillOpacity: .95}})
      .bindPopup(`<div class="popup">${{cardHTML(d)}}</div>`);
    m.label = d.label; m.addTo(map); markers.push(m); pts.push([d.lat, d.lon]);
  }});
  if (pts.length) map.fitBounds(pts, {{padding: [30, 30]}}); else map.setView([58.6, 25.0], 7);
  window._map = map;
}} else {{
  document.getElementById("map").style.display = "none";
  document.getElementById("nomap").style.display = "block";
}}
document.querySelectorAll(".chip").forEach(b => b.addEventListener("click", () => {{
  const l = b.dataset.label, p = b.getAttribute("aria-pressed") === "true";
  b.setAttribute("aria-pressed", String(!p)); p ? on.delete(l) : on.add(l);
  markers.forEach(m => on.has(m.label) ? m.addTo(window._map) : m.remove());
  renderGrid();
}}));
renderGrid();
</script></body></html>
"""


def main() -> int:
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        print(__doc__)
        return 2
    out = os.path.join(sys.argv[1], "review.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build(sys.argv[1]))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
