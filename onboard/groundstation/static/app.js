"use strict";
/* SILM Ground Station - browser side. Talks only to the local Python server. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let META = null, P = null, CHECK = null, saveTimer = null, checkTimer = null;
const DONE = { fc: false, pi: false, sim: false };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const ct = r.headers.get("Content-Type") || "";
  if (ct.includes("application/json")) return r.json();
  return r.text();
}
function toast(msg, ms = 2600) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
function setResult(id, r, busy) {
  const el = $(id);
  if (busy) { el.className = "result busy"; el.textContent = busy; return; }
  el.className = "result " + (r.ok ? "ok" : "err");
  el.textContent = (r.ok ? "✓ " : "✗ ") + (r.message || (r.ok ? "done" : "failed"));
}
function getv(qid) { const [s, k] = qid.split("."); return (P[s] || {})[k]; }
function setv(qid, v) { const [s, k] = qid.split("."); P[s] = P[s] || {}; P[s][k] = v; changed(); }
function changed() {
  try { localStorage.setItem("silm-project", JSON.stringify(P)); } catch (e) {}
  clearTimeout(saveTimer); saveTimer = setTimeout(saveProject, 1500);
  clearTimeout(checkTimer); checkTimer = setTimeout(runCheck, 300);
}
function doneKey() { return "silm-done-" + (P && P.name); }
function loadDone() {
  try { Object.assign(DONE, { fc: false, pi: false, sim: false }, JSON.parse(localStorage.getItem(doneKey()) || "{}")); } catch (e) {}
}
function markDone(k) { DONE[k] = true; try { localStorage.setItem(doneKey(), JSON.stringify(DONE)); } catch (e) {} renderProgress(); }

/* ------------------------------------------------------------------ tabs */
function showTab(name) {
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "mission") setTimeout(() => { map.invalidateSize(); }, 50);
  if (name === "flights") { loadLibrary(); setTimeout(() => flMap && flMap.invalidateSize(), 50); }
  if (name === "sim") simPoll();
  if (name === "launch") renderLaunch();
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
}
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ------------------------------------------------------------------ forms */
function fieldHTML(q) {
  const id = "f-" + q.id.replace(".", "-");
  let input = "";
  const v = getv(q.id);
  if (q.type === "number") {
    input = `<div class="inline"><input type="number" id="${id}" ${q.min != null ? `min="${q.min}"` : ""} ${q.max != null ? `max="${q.max}"` : ""} step="${q.step || "any"}" value="${v ?? ""}" aria-label="${esc(q.q)}"> <span class="unit">${esc(q.unit)}</span></div>`;
  } else if (q.type === "text") {
    input = `<input type="text" id="${id}" value="${esc(v ?? "")}" aria-label="${esc(q.q)}">`;
  } else if (q.type === "select") {
    input = `<select id="${id}" aria-label="${esc(q.q)}">` + Object.entries(q.options).map(([k, l]) =>
      `<option value="${esc(k)}" ${String(v) === k ? "selected" : ""}>${esc(l)}</option>`).join("") + `</select>`;
  } else if (q.type === "bool") {
    input = `<div class="yesno" id="${id}" role="group" aria-label="${esc(q.q)}"><button type="button" data-v="true" class="${v === true ? "on" : ""}">Yes</button><button type="button" data-v="false" class="${v === false ? "on" : ""}">No</button></div>`;
  } else if (q.type === "multi") {
    const cur = new Set(v || []);
    input = `<div class="chips" id="${id}" role="group" aria-label="${esc(q.q)}">` + Object.entries(q.options).map(([k, l]) =>
      `<button type="button" class="chip ${cur.has(k) ? "on" : ""}" data-v="${esc(k)}" aria-pressed="${cur.has(k)}">${esc(l)}</button>`).join("") + `</div>`;
  }
  return `<div class="field" data-qid="${q.id}"><div class="q">${esc(q.q)}${q.required ? '<span class="req" title="must be answered">*</span>' : ""}</div>${input}${q.help ? `<div class="help">${esc(q.help)}</div>` : ""}<div class="msg"></div></div>`;
}
function renderForm(tab, el) {
  const qs = META.questions.filter((q) => q.tab === tab);
  const groups = [];
  qs.forEach((q) => { let g = groups.find((x) => x.name === q.group); if (!g) groups.push(g = { name: q.group, qs: [] }); g.qs.push(q); });
  el.innerHTML = groups.map((g) => `<div class="group"><h2>${esc(g.name)}</h2>${g.qs.map(fieldHTML).join("")}</div>`).join("");
  qs.forEach((q) => bindField(q, $("f-" + q.id.replace(".", "-"))));
}
function bindField(q, el) {
  if (!el) return;
  if (q.type === "number") el.addEventListener("input", () => { const t = el.value.trim(); setv(q.id, t === "" ? null : Number(t)); if (q.id === "aircraft.cg_mm" || q.id === "aircraft.chord_mm") renderCG(); });
  else if (q.type === "text") el.addEventListener("input", () => setv(q.id, el.value));
  else if (q.type === "select") el.addEventListener("change", () => setv(q.id, el.value));
  else if (q.type === "bool") el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    el.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b)); setv(q.id, b.dataset.v === "true"); }));
  else if (q.type === "multi") el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    b.classList.toggle("on"); b.setAttribute("aria-pressed", b.classList.contains("on"));
    setv(q.id, [...el.querySelectorAll("button.on")].map((x) => x.dataset.v)); }));
}
function renderForms() {
  renderForm("aircraft", $("form-aircraft"));
  renderForm("mission", $("form-mission"));
  renderForm("camera", $("form-camera"));
  $("project-name").value = P.name || "";
  $("fc-port").value = $("fc-port-2").value = P.link.fc_port || "";
  $("pi-address").value = P.link.pi_address || "10.42.0.1";
  $("usb-path").value = P.link.usb_path || "";
  renderCG();
}
function markFields() {
  document.querySelectorAll(".field").forEach((f) => { f.classList.remove("invalid"); f.querySelector(".msg").textContent = ""; });
  if (!CHECK) return;
  META.questions.forEach((q) => {
    const bad = CHECK.errors.find((e) => e.startsWith(q.q) || e === "Not answered: " + q.q);
    if (bad) { const f = document.querySelector(`.field[data-qid="${q.id}"]`); if (f) { f.classList.add("invalid"); f.querySelector(".msg").textContent = bad.replace(q.q + " - ", ""); } }
  });
}

/* ------------------------------------------------------------------ CG */
function renderCG() {
  const a = P.aircraft, box = $("cg-box"), ch = Number(a.chord_mm) || 0;
  if (!ch) { box.innerHTML = ""; return; }
  const lo = 0.25 * ch, hi = 0.33 * ch, cg = a.cg_mm;
  const pos = (mm) => Math.max(0, Math.min(100, (mm / ch) * 100));
  let verdict = "Enter where the aircraft balances (question below) to check it.";
  if (cg != null && cg !== "") {
    const pct = (100 * cg) / ch;
    verdict = pct >= 25 && pct <= 33 ? `✓ Balance point at ${pct.toFixed(0)} % of the wing depth: good.`
      : pct >= 18 && pct <= 38 ? `Balance point at ${pct.toFixed(0)} % of the wing depth: unusual - check it against your design.`
      : `✗ Balance point at ${pct.toFixed(0)} % of the wing depth: far from the usual 25-33 %. Do not fly before fixing it.`;
  }
  box.innerHTML = `<h2>Balance (centre of gravity)</h2>
    <div class="cgbar" aria-hidden="true"><span class="lbl" style="left:4px">front edge</span><span class="lbl" style="right:4px">back edge</span>
    <div class="good" style="left:${pos(lo)}%;width:${pos(hi) - pos(lo)}%"></div>
    ${cg != null && cg !== "" ? `<div class="mark" style="left:calc(${pos(cg)}% - 2px)"></div>` : ""}</div>
    <div class="help">Green: the usual range, ${Math.round(lo)}-${Math.round(hi)} mm behind the front edge of a ${ch} mm deep wing. ${esc(verdict)}</div>`;
}

/* ------------------------------------------------------------------ check */
async function runCheck() {
  try { CHECK = await api("/api/check", { project: P }); } catch (e) { return; }
  const ul = $("problem-list");
  const items = CHECK.errors.map((e) => `<li class="err">${esc(e)}</li>`).concat(CHECK.warnings.map((w) => `<li class="warn">${esc(w)}</li>`));
  ul.innerHTML = items.length ? items.join("") : '<li class="ok">✓ Everything is answered and the mission is valid.</li>';
  const pill = $("check-pill");
  pill.className = "pill" + (CHECK.errors.length ? " err" : CHECK.warnings.length ? " warn" : "");
  pill.textContent = CHECK.errors.length ? `${CHECK.errors.length} to fix` : CHECK.warnings.length ? `${CHECK.warnings.length} notes` : "Ready";
  markFields(); drawPlan(); renderProgress();
  if ($("tab-launch").classList.contains("active")) renderLaunch();
}
$("check-pill").addEventListener("click", () => { const p = $("problems"); p.style.display = "block"; p.scrollIntoView(); });

function renderProgress() {
  if (!P) return;
  const missionOK = CHECK && CHECK.plan && !CHECK.errors.some((e) => e.startsWith("Mission"));
  const aircraftOK = CHECK && !CHECK.errors.some((e) => META.questions.some((q) => q.tab === "aircraft" && e.includes(q.q)));
  const windOK = P.wind.speed_mps != null && P.wind.from_deg != null;
  const steps = [["Aircraft questions answered", aircraftOK, "aircraft"], ["Mission drawn on the map", missionOK, "mission"],
    ["Today's wind entered", windOK, "mission"], ["Flown in the simulator", DONE.sim, "sim"],
    ["Sent to the flight controller", DONE.fc, "send"], ["Sent to the aircraft computer", DONE.pi, "send"]];
  $("progress").innerHTML = steps.map(([t, d, tab]) => `<li class="${d ? "done" : ""}"><a href="#${tab}" data-go="${tab}">${esc(t)}</a></li>`).join("");
  $("progress").querySelectorAll("[data-go]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showTab(a.dataset.go); }));
  const tabDone = { aircraft: aircraftOK, mission: missionOK && windOK, send: DONE.fc && DONE.pi, sim: DONE.sim };
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("done", !!tabDone[b.dataset.tab]));
}

/* ------------------------------------------------------------------ projects */
async function saveProject() {
  if (!P) return;
  const r = await api("/api/projects", { project: P });
  if (r.ok) listProjects();
}
$("save").addEventListener("click", async () => { await saveProject(); toast("Saved: " + P.name); });
$("project-name").addEventListener("change", () => { P.name = $("project-name").value.trim() || "New mission"; loadDone(); changed(); });
async function listProjects() {
  const list = await api("/api/projects");
  $("project-list").innerHTML = list.length ? list.map((p) => `<li class="${p.name === P.name ? "active" : ""}"><span><b>${esc(p.name)}</b><br><span class="meta">saved ${esc(p.saved)}</span></span>
    <span><button class="btn ghost" data-open="${esc(p.name)}">Open</button> <button class="btn ghost" data-del="${esc(p.name)}" aria-label="Delete ${esc(p.name)}">Delete</button></span></li>`).join("")
    : '<li class="meta">No saved missions yet.</li>';
  $("project-list").querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", async () => openProject(await api("/api/projects/" + encodeURIComponent(b.dataset.open)))));
  $("project-list").querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Delete the mission "${b.dataset.del}" from this laptop?`)) return;
    await api("/api/projects/delete", { name: b.dataset.del }); listProjects(); }));
}
function openProject(p) {
  P = p; loadDone(); renderForms(); drawPlaces(); fitMission(); runCheck(); listProjects(); toast("Opened: " + P.name);
}
$("new-project").addEventListener("click", () => {
  const name = prompt("Name of the new mission:", "Field " + new Date().toISOString().slice(0, 10));
  if (!name) return;
  const np = JSON.parse(JSON.stringify(META.default_project));
  np.aircraft = JSON.parse(JSON.stringify(P.aircraft)); np.camera = P.camera; np.link = P.link; np.recognition = P.recognition;
  np.name = name; openProject(np); saveProject();
});
$("export-project").addEventListener("click", () => download(`${P.name}.json`, JSON.stringify(P, null, 2), "application/json"));
$("import-project").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", async (e) => {
  try { const p = JSON.parse(await e.target.files[0].text()); openProject(Object.assign(JSON.parse(JSON.stringify(META.default_project)), p)); saveProject(); }
  catch (err) { toast("Could not read that file: " + err.message, 5000); }
});
function download(name, text, type) {
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* ------------------------------------------------------------------ mission map */
const map = L.map("map").setView([58.6, 25.0], 7);
const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }).addTo(map);
const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { maxZoom: 19, attribution: "Imagery &copy; Esri" });
L.control.layers({ "Map": osm, "Satellite": sat }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);
const G = { area: L.layerGroup().addTo(map), plan: L.layerGroup().addTo(map), pts: L.layerGroup().addTo(map) };
let tool = null;
const HINTS = { launch: "Click where you will launch (throw) the aircraft. This is home.", area: "Click the corners of the area to survey, one after another. Pick another tool when done.",
  landing: "Click the touchdown point: a large, flat, open field.", heading: "Click a point AHEAD of the touchdown point: the aircraft lands travelling towards it. Land into the wind." };
document.querySelectorAll(".tool").forEach((b) => b.addEventListener("click", () => {
  tool = tool === b.dataset.tool ? null : b.dataset.tool;
  document.querySelectorAll(".tool").forEach((x) => x.classList.toggle("on", x.dataset.tool === tool));
  map.getContainer().style.cursor = tool ? "crosshair" : "";
  $("toolhint").textContent = HINTS[tool] || "Pick a tool above, then click on the map.";
}));
function bearing(a, b) {
  const p1 = a[0] * Math.PI / 180, p2 = b[0] * Math.PI / 180, dl = (b[1] - a[1]) * Math.PI / 180;
  const y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}
map.on("click", (e) => {
  const ll = [+e.latlng.lat.toFixed(7), +e.latlng.lng.toFixed(7)], m = P.mission;
  if (tool === "launch") { m.launch = ll; if (!m.landing) m.landing = ll; }
  else if (tool === "area") m.area = (m.area || []).concat([ll]);
  else if (tool === "landing") m.landing = ll;
  else if (tool === "heading" && m.landing) m.landing_heading_deg = Math.round(bearing(m.landing, ll));
  else return;
  drawPlaces(); changed();
});
$("undo-corner").addEventListener("click", () => { P.mission.area = (P.mission.area || []).slice(0, -1); drawPlaces(); changed(); });
$("clear-area").addEventListener("click", () => { if (confirm("Clear the survey area?")) { P.mission.area = []; drawPlaces(); changed(); } });
$("into-wind").addEventListener("click", () => {
  if (P.wind.from_deg == null) { toast("First answer 'Where is the wind coming FROM?' below the map."); return; }
  P.mission.landing_heading_deg = Math.round(P.wind.from_deg) % 360; changed(); toast(`Landing direction set to ${P.mission.landing_heading_deg}°, into the wind.`);
});
$("goto-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const m = $("goto").value.match(/(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)/);
  if (m) map.setView([+m[1], +m[2]], 15); else toast("Type latitude, longitude, e.g. 58.38, 26.72");
});
function drawPlaces() {
  G.pts.clearLayers(); G.area.clearLayers();
  const m = P.mission;
  if ((m.area || []).length) L.polygon(m.area, { color: "#0b6e7f", weight: 2, fillOpacity: 0.08 }).addTo(G.area);
  (m.area || []).forEach((p) => L.circleMarker(p, { radius: 4, color: "#0b6e7f" }).addTo(G.area));
  if (m.launch) L.marker(m.launch, { title: "Launch" }).bindTooltip("Launch / home").addTo(G.pts);
  if (m.landing) L.circleMarker(m.landing, { radius: 8, color: "#b3261e", weight: 3 }).bindTooltip("Touchdown").addTo(G.pts);
}
function fitMission() {
  const m = P.mission, pts = [m.launch, m.landing, ...(m.area || [])].filter(Boolean);
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.3));
}
function drawPlan() {
  G.plan.clearLayers();
  const r = CHECK && CHECK.plan, m = P.mission;
  if (!r) { $("plan-stats").innerHTML = ""; $("det-box").innerHTML = ""; return; }
  L.circle(m.launch, { radius: r.fence_needed_m, color: "#b3261e", weight: 1, fill: false, dashArray: "6 6" }).bindTooltip("Geofence (set on the aircraft)").addTo(G.plan);
  L.polyline([m.launch, ...r.path], { color: "#e8590c", weight: 3 }).addTo(G.plan);
  L.polyline([r.approach_start, m.landing || m.launch], { color: "#b3261e", weight: 5 }).bindTooltip("Final approach").addTo(G.plan);
  let n = 0;
  r.items.forEach((it, i) => {
    if (i === 0 || !["WAYPOINT", "TAKEOFF", "LAND"].includes(it.name)) return; n++;
    L.marker([it.lat, it.lon], { icon: L.divIcon({ className: "", html: `<div class="wpnum">${n}</div>`, iconSize: [18, 18] }) }).bindTooltip(`${it.name} · ${Math.round(it.alt)} m`).addTo(G.plan);
  });
  const st = [["Flight time", r.flight_min + " min"], ["Battery used", Math.round(r.battery_frac * 100) + " %"], ["Distance", r.distance_km + " km"],
    ["Survey lines", r.lines], ["Line spacing", r.spacing_m + " m"], ["Farthest point", r.max_dist_m + " m"], ["Geofence", r.fence_needed_m + " m"], ["Glide slope", r.glide_deg + "°"]];
  $("plan-stats").innerHTML = st.map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
  const d = r.detection;
  $("det-box").innerHTML = d ? `A ${esc(m.target)} at ${m.alt_m} m is <b>${d.px_at_model} pixels</b> (${esc(d.verdict)}) for the recognition model; about 10 pixels are needed - fly at or below <b>${d.max_alt_for_10px} m</b> for that.` : "";
}

/* ------------------------------------------------------------------ send */
async function refreshPorts() {
  const ports = await api("/api/ports");
  $("port-list").innerHTML = ports.map((p) => `<option value="${esc(p.device)}">${esc(p.description)}</option>`).join("");
  if (!ports.length) toast("No serial ports found. Plug in the flight controller's USB cable (and the battery), then try again.", 5000);
  else if (!$("fc-port").value) { $("fc-port").value = $("fc-port-2").value = ports[0].device; P.link.fc_port = ports[0].device; changed(); }
}
["fc-port", "fc-port-2"].forEach((id) => $(id).addEventListener("change", () => { P.link.fc_port = $(id).value.trim(); $("fc-port").value = $("fc-port-2").value = P.link.fc_port; changed(); }));
$("fc-refresh").addEventListener("click", refreshPorts);
async function fcCall(action, label, extra = {}) {
  setResult("fc-result", null, label + "…");
  const r = await api("/api/fc/" + action, Object.assign({ port: P.link.fc_port, baud: P.link.fc_baud, project: P }, extra));
  if (action === "info" && r.ok) {
    r.message = `Connected. ${r.armed ? "ARMED!" : "Disarmed"}, mode ${r.mode || "?"}, firmware ${r.firmware || "?"}, GPS ${r.gps_fix >= 3 ? "fix" : "no fix"} (${r.satellites ?? "?"} satellites), battery ${r.battery_v ?? "?"} V. ` +
      (r.script_loaded ? "GPS-loss script is running." : "GPS-loss script NOT running yet (step 2, then reboot).") + (r.messages ? "\nMessages: " + r.messages.slice(-4).join(" | ") : "");
  }
  setResult("fc-result", r);
  return r;
}
$("fc-info").addEventListener("click", () => fcCall("info", "Connecting"));
let fcSteps = {};
$("fc-params").addEventListener("click", async () => { const r = await fcCall("params", "Writing parameters (about a minute)"); fcSteps.params = r.ok; fcDone(); });
$("fc-script").addEventListener("click", async () => { const r = await fcCall("script", "Copying the script"); fcSteps.script = r.ok; fcDone(); });
$("fc-mission").addEventListener("click", async () => {
  if (!confirm("Upload this mission to the aircraft? It must be disarmed. It is read back and compared.")) return;
  const r = await fcCall("mission", "Uploading the mission"); fcSteps.mission = r.ok; fcDone(); });
function fcDone() { if (fcSteps.params && fcSteps.script && fcSteps.mission) markDone("fc"); }
document.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", async () => {
  const kind = b.dataset.dl;
  const r = await fetch("/api/files/" + kind, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project: P }) });
  const ct = r.headers.get("Content-Type") || "";
  if (ct.includes("json")) { const j = await r.json(); toast(j.message || "not ready", 6000); return; }
  const cd = r.headers.get("Content-Disposition") || ""; const name = (cd.match(/filename="([^"]+)"/) || [])[1] || kind;
  download(name, await r.text(), "text/plain");
}));
$("pi-address").addEventListener("change", () => { P.link.pi_address = $("pi-address").value.trim(); changed(); });
$("pi-status").addEventListener("click", async () => {
  setResult("pi-result", null, "Connecting to the aircraft computer…");
  const r = await api("/api/pi/status", { address: P.link.pi_address });
  if (r.ok) {
    const a = r.aircraft || {};
    r.message = `Connected to ${r.hostname} (software ${r.software}). ` + (r.program_running ? `Flight program running: ${a.armed ? "ARMED" : "disarmed"}, GPS ${a.gps || "?"}, camera ${a.camera_frame_age_s != null ? "sending pictures" : "NOT sending pictures"}.` : "Flight program NOT running.") +
      ` Settings on the Pi: ${r.settings_present ? (r.settings_info.project ? "'" + r.settings_info.project + "' from " + r.settings_info.received : "present") : "none yet"}. ${r.flights} flight(s) stored, ${r.free_mb} MB free.`;
  }
  setResult("pi-result", r);
});
$("pi-send").addEventListener("click", async () => {
  setResult("pi-result", null, "Sending…");
  const r = await api("/api/pi/send", { project: P, address: P.link.pi_address });
  setResult("pi-result", r); if (r.ok) markDone("pi");
});
$("pi-log").addEventListener("click", async () => { const r = await api("/api/pi/log", { address: P.link.pi_address }); $("pi-log-text").textContent = r.ok ? r.log : r.message; });
async function refreshDrives() {
  const d = await api("/api/drives");
  $("drive-list").innerHTML = d.map((p) => `<option value="${esc(p)}"></option>`).join("");
  if (d.length && !$("usb-path").value) { $("usb-path").value = d[0]; P.link.usb_path = d[0]; changed(); }
  if (!d.length) toast("No USB stick found. Plug in the stick named SILM and try again.", 5000);
}
$("usb-refresh").addEventListener("click", refreshDrives);
$("usb-path").addEventListener("change", () => { P.link.usb_path = $("usb-path").value.trim(); changed(); });
$("usb-write").addEventListener("click", async () => {
  setResult("usb-result", null, "Writing…");
  setResult("usb-result", await api("/api/usb/write", { project: P, path: P.link.usb_path }));
});
$("usb-read").addEventListener("click", async () => {
  const r = await api("/api/usb/read", { path: P.link.usb_path });
  if (!r.ok) return setResult("usb-result", r);
  if (!r.present) return setResult("usb-result", { ok: false, message: "No SILM_TRANSFER folder on this stick yet - write the settings first." });
  const ir = r.import_result;
  const msg = (ir ? (ir.ok ? `The aircraft imported the settings at ${ir.time}: ${ir.message}.` : `The aircraft REJECTED the settings at ${ir.time}: ${ir.message}`) : "The aircraft has not read this stick yet (plug it into the Pi, power on, wait 10 s).")
    + (r.flights.length ? `\nFlights on the stick: ${r.flights.join(", ")} (import them on the Flights tab).` : "");
  setResult("usb-result", { ok: !!(ir && ir.ok), message: msg });
  if (ir && ir.ok) markDone("pi");
});

/* ------------------------------------------------------------------ launch */
const CHECKLIST = [
  "This mission was flown in the Simulator (step 7) and passed.",
  "The flight controller has the parameters, the script and THIS mission (step 5A), and was rebooted after the first time.",
  "The aircraft computer has the camera settings (step 5B or 5C).",
  "The battery is fully charged, in, and the balance point was checked with it.",
  "The flight controller shows GPS OK and no 'PreArm' message (Send tab: Check connection).",
  "I walked the landing strip and the approach: clear, flat, nobody on it.",
  "The wind is within the limit and the landing direction is into the wind.",
  "I am allowed to fly here today (authorisation, area, height), and people nearby know.",
  "Propeller clear, the aircraft in my hand, ready to throw.",
];
function renderLaunch() {
  const w = P.wind, max = META.max_wind_mps;
  const ok = w.speed_mps != null && w.from_deg != null && w.speed_mps <= max;
  $("wind-recap").innerHTML = `<h2>Wind today</h2>` + (w.speed_mps == null || w.from_deg == null
    ? `<p class="result err">Enter today's wind on the Mission tab (speed and direction).</p>`
    : `<p class="result ${ok ? "ok" : "err"}">${w.speed_mps} m/s from ${w.from_deg}° — ${ok ? "within" : "ABOVE"} the ${max} m/s limit. Landing direction: ${P.mission.landing_heading_deg}°.</p>`);
  const ul = $("checklist");
  if (!ul.children.length) {
    ul.innerHTML = CHECKLIST.map((t, i) => `<li><label><input type="checkbox" data-i="${i}"> <span>${esc(t)}</span></label></li>`).join("");
    ul.querySelectorAll("input").forEach((c) => c.addEventListener("change", armState));
  }
  armState();
}
function armState() {
  const all = [...$("checklist").querySelectorAll("input")].every((c) => c.checked);
  const errs = CHECK ? CHECK.errors.length : 1;
  $("arm").disabled = !(all && !errs);
  $("arm-help").textContent = errs ? `Fix the ${errs} problem(s) listed on the right first.` : !all ? "Tick every line of the checklist to enable arming."
    : "After arming: unplug the USB cable, hold it level and nose into the wind, and throw firmly. The motor starts after the throw.";
}
$("arm").addEventListener("click", async () => {
  if (!confirm("ARM the aircraft now?\n\nThe motor will start when the aircraft is thrown (or jolted). Propeller clear, aircraft in your hand.")) return;
  setResult("arm-result", null, "Checking the mission on the aircraft and arming…");
  const r = await api("/api/fc/arm", { port: P.link.fc_port, baud: P.link.fc_baud, project: P, checklist_done: true });
  if (r.ok) r.message = "ARMED in AUTO. Unplug the USB cable now, hold it level, nose into the wind, and throw firmly.";
  setResult("arm-result", r);
});
$("disarm").addEventListener("click", async () => { setResult("arm-result", null, "Disarming…"); setResult("arm-result", await api("/api/fc/disarm", { port: P.link.fc_port, baud: P.link.fc_baud })); });

/* ------------------------------------------------------------------ simulator */
let simTimer = null;
async function simPoll() {
  const s = await api("/api/sim/status");
  $("sim-avail").innerHTML = s.available ? `<p class="result ok">✓ The simulator is installed (${esc(s.ardupilot_dir)}).</p>`
    : `<p class="result err">The simulator can't run from here: ${esc(s.why)}</p>`;
  $("sim-start").disabled = !s.available || s.state === "running";
  if (!s.available) {
    $("sim-cmds-card").hidden = false;
    const c = await api("/api/sim/commands", { project: P }); $("sim-cmds").textContent = c.text || "";
  }
  if (s.state && s.state !== "idle") {
    $("sim-log").textContent = (s.log || []).join("\n"); $("sim-log").scrollTop = 1e9;
    const label = { running: `Running: ${s.step} (${s.elapsed_s} s)`, passed: "PASSED - every check passed", failed: "FAILED - see the checks below", error: "Could not run - see the progress log", stopped: "Stopped" }[s.state];
    setResult("sim-result", { ok: s.state === "passed" || s.state === "running", message: label });
    if (s.state === "running") $("sim-result").className = "result busy";
    if (s.result) {
      const names = { rtl_in_time: "Turned back for the landing in time", headed_home_while_blind: "Headed for the landing strip while blind",
        stayed_inside_fence: "Stayed inside the geofence", estimate_reconverged: "Knew where it was again after GPS came back",
        landed: "Landed and switched itself off", landed_near_touchdown: "Landed close to the planned point" };
      $("sim-checks").innerHTML = `<ul class="checklist">` + Object.entries(s.result.checks || {}).map(([k, v]) => `<li class="${v ? "ok" : "err"}">${v ? "✓" : "✗"} ${esc(names[k] || k)}</li>`).join("") +
        `</ul><p class="help">Touchdown ${s.result.touchdown_error_m} m from the planned point; failsafe after ${s.result.rtl_after_s} s; farthest ${s.result.max_true_dist_m} m from home.</p>`;
      $("sim-track").src = "/api/sim/track.png?t=" + Date.now(); $("sim-track").hidden = false;
      if (s.state === "passed") markDone("sim");
    }
  }
  clearTimeout(simTimer);
  if (s.state === "running") simTimer = setTimeout(simPoll, 2000);
}
$("sim-start").addEventListener("click", async () => {
  if (CHECK && CHECK.errors.length) { toast("Fix the problems on the right first.", 4000); return; }
  $("sim-checks").innerHTML = ""; $("sim-track").hidden = true;
  const r = await api("/api/sim/start", { project: P, fault: $("sim-fault").value, fault_s: Number($("sim-fault-s").value) || 90 });
  setResult("sim-result", r); if (r.ok) simPoll();
});
$("sim-stop").addEventListener("click", async () => { await api("/api/sim/stop", {}); simPoll(); });

/* ------------------------------------------------------------------ flights */
let flMap = null, flLayers = null, FL = null, flOn = new Set(), flShown = [], lbIndex = 0;
const PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#9a6324", "#469990", "#800000"];
function colorFor(p) { if (p.kind === "frame") return "#6c757d"; const i = FL.labels.indexOf(p.label); return PALETTE[i % PALETTE.length]; }
async function loadLibrary() {
  const list = await api("/api/library");
  $("fl-list").innerHTML = list.length ? list.map((f) => `<li class="clickable ${FL && FL.name === f.name ? "active" : ""}" data-fl="${esc(f.name)}"><span><b>${esc(f.when || f.name)}</b><br><span class="meta">${f.pictures} pictures · ${f.detections} found</span></span></li>`).join("")
    : '<li class="meta">No flights on this laptop yet. Bring one in above.</li>';
  $("fl-list").querySelectorAll("[data-fl]").forEach((li) => li.addEventListener("click", () => openFlight(li.dataset.fl)));
}
async function openFlight(name) {
  FL = await api("/api/library/" + encodeURIComponent(name));
  if (FL.error) { toast(FL.error); return; }
  $("fl-empty").hidden = true; $("fl-view").hidden = false;
  $("fl-title").textContent = `Flight ${FL.when || FL.name}`;
  $("fl-geojson").href = `/api/library/${encodeURIComponent(name)}/export.geojson`;
  $("fl-csv").href = `/api/library/${encodeURIComponent(name)}/export.csv`;
  const s = FL.summary;
  $("fl-stats").innerHTML = [["Pictures", s.pictures], ["Objects found", s.detections], ["Ordinary pictures", s.frames], ["With coordinates", s.with_position],
    ["Minutes logged", s.minutes_logged ?? "-"], ["GPS problems", s.gps_problems]].map(([k, v]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
  flOn = new Set([...FL.labels, ...(s.frames ? ["(ordinary)"] : [])]);
  $("fl-chips").innerHTML = FL.labels.map((l, i) => `<button class="chip on" data-l="${esc(l)}"><i style="background:${PALETTE[i % PALETTE.length]}"></i>${esc(l)}</button>`).join("") +
    (s.frames ? `<button class="chip on" data-l="(ordinary)"><i style="background:#6c757d"></i>ordinary pictures</button>` : "");
  $("fl-chips").querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => { c.classList.toggle("on"); c.classList.contains("on") ? flOn.add(c.dataset.l) : flOn.delete(c.dataset.l); renderFlight(); }));
  if (!flMap) {
    flMap = L.map("fl-map");
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }).addTo(flMap);
    flLayers = L.layerGroup().addTo(flMap);
  }
  setTimeout(() => { flMap.invalidateSize(); renderFlight(true); }, 30);
  loadLibrary();
}
function visible(p) { return flOn.has(p.kind === "frame" ? "(ordinary)" : p.label); }
function renderFlight(fit) {
  flLayers.clearLayers();
  const pts = [];
  let seg = [], bad = null;
  const flush = () => { if (seg.length > 1) L.polyline(seg, { color: bad ? "#e03131" : "#495057", weight: bad ? 4 : 2 }).addTo(flLayers); };
  FL.track.forEach((t) => { const b = t.gps && t.gps !== "OK"; if (bad !== null && b !== bad) { flush(); seg = seg.slice(-1); } bad = b; seg.push([t.lat, t.lon]); pts.push([t.lat, t.lon]); });
  flush();
  flShown = FL.pictures.filter(visible);
  flShown.forEach((p, i) => {
    if (p.lat == null) return;
    L.circleMarker([p.lat, p.lon], { radius: p.kind === "frame" ? 5 : 8, color: "#fff", weight: 1.5, fillColor: colorFor(p), fillOpacity: 0.95 })
      .bindTooltip(`${p.label || "ordinary picture"} · ${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}`).on("click", () => openLightbox(i)).addTo(flLayers);
    pts.push([p.lat, p.lon]);
  });
  if (fit) { if (pts.length) flMap.fitBounds(pts, { padding: [30, 30] }); else flMap.setView([58.6, 25.0], 7); }
  $("fl-grid").innerHTML = flShown.map((p, i) => `<div class="pic" data-i="${i}" tabindex="0"><img loading="lazy" src="${imgURL(p)}" alt="${esc(p.label || "picture")}">
    <div><b style="color:${colorFor(p)}">${esc(p.label || "ordinary picture")}</b>${p.score != null ? " " + Math.round(p.score * 100) + " %" : ""}<br>
    <span class="coord">${p.lat != null ? p.lat.toFixed(6) + ", " + p.lon.toFixed(6) : "no position"}</span><br><span class="meta">${esc((p.time || "").replace("T", " ").replace("Z", " UTC"))}</span></div></div>`).join("")
    || '<p class="help">No pictures with the chosen filters.</p>';
  $("fl-grid").querySelectorAll(".pic").forEach((d) => { d.addEventListener("click", () => openLightbox(+d.dataset.i)); d.addEventListener("keydown", (e) => { if (e.key === "Enter") openLightbox(+d.dataset.i); }); });
}
function imgURL(p) { return `/api/library/${encodeURIComponent(FL.name)}/img?p=${encodeURIComponent(p.img)}`; }
function openLightbox(i) {
  lbIndex = (i + flShown.length) % flShown.length; const p = flShown[lbIndex];
  $("lb-img").src = imgURL(p); $("lb-img").alt = p.label || "picture";
  const box = $("lb-box");
  if (p.box) { box.style.display = "block"; box.style.left = p.box[0] * 100 + "%"; box.style.top = p.box[1] * 100 + "%"; box.style.width = (p.box[2] - p.box[0]) * 100 + "%"; box.style.height = (p.box[3] - p.box[1]) * 100 + "%"; }
  else box.style.display = "none";
  const ll = p.lat != null ? `${p.lat.toFixed(7)}, ${p.lon.toFixed(7)}` : "unknown";
  const rows = [["What", p.label ? `${p.label} (${Math.round(p.score * 100)} % sure)` : "ordinary picture"], ["Coordinates", `<span class="coord">${esc(ll)}</span> <button class="btn ghost" id="lb-copy">Copy</button>`],
    ["Position from", p.pos_source], ["Time", (p.time || "").replace("T", " ").replace("Z", " UTC")], ["Aircraft height", p.alt_m != null ? Math.round(p.alt_m) + " m" : "-"],
    ["Aircraft heading", p.heading_deg != null ? Math.round(p.heading_deg) + "°" : "-"], ["File", p.img],
    ["Open in a map", p.lat != null ? `<a href="https://www.openstreetmap.org/?mlat=${p.lat}&mlon=${p.lon}#map=18/${p.lat}/${p.lon}" target="_blank" rel="noopener">OpenStreetMap</a> · <a href="https://www.google.com/maps?q=${p.lat},${p.lon}" target="_blank" rel="noopener">Google Maps</a>` : "-"]];
  $("lb-info").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${k === "Coordinates" || k === "Open in a map" ? v : esc(v)}</dd>`).join("");
  const c = $("lb-copy"); if (c) c.addEventListener("click", () => { navigator.clipboard && navigator.clipboard.writeText(ll); toast("Coordinates copied"); });
  $("lightbox").hidden = false;
}
$("lb-close").addEventListener("click", () => ($("lightbox").hidden = true));
$("lb-prev").addEventListener("click", () => openLightbox(lbIndex - 1));
$("lb-next").addEventListener("click", () => openLightbox(lbIndex + 1));
$("lightbox").addEventListener("click", (e) => { if (e.target.id === "lightbox") $("lightbox").hidden = true; });
document.addEventListener("keydown", (e) => {
  if ($("lightbox").hidden) return;
  if (e.key === "Escape") $("lightbox").hidden = true; else if (e.key === "ArrowLeft") openLightbox(lbIndex - 1); else if (e.key === "ArrowRight") openLightbox(lbIndex + 1);
});
$("fl-from-pi").addEventListener("click", async () => {
  setResult("fl-result", null, "Asking the aircraft for its flights…");
  const r = await api("/api/pi/flights", { address: P.link.pi_address });
  if (!r.ok) return setResult("fl-result", r);
  setResult("fl-result", { ok: true, message: r.flights.length ? "Flights on the aircraft:" : "No flights on the aircraft yet." });
  $("fl-remote").innerHTML = r.flights.filter((f) => !f.empty).map((f) => `<li><span><b>${esc(f.name.replace("mission_", ""))}</b><br><span class="meta">${f.pictures} pictures, ${f.size_mb} MB</span></span><button class="btn" data-dl="${esc(f.name)}">Download</button></li>`).join("");
  $("fl-remote").querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", async () => {
    setResult("fl-result", null, `Downloading ${b.dataset.dl}…`);
    const d = await api("/api/pi/download", { address: P.link.pi_address, name: b.dataset.dl });
    setResult("fl-result", d); if (d.ok) { await loadLibrary(); openFlight(d.name); }
  }));
});
$("fl-from-usb").addEventListener("click", async () => {
  const r = await api("/api/usb/read", { path: P.link.usb_path });
  if (!r.ok || !r.present) return setResult("fl-result", { ok: false, message: r.message || "No SILM stick found at '" + (P.link.usb_path || "?") + "' - set it on the Send tab (C)." });
  $("fl-remote").innerHTML = r.flights.map((n) => `<li><span><b>${esc(n.replace("mission_", ""))}</b></span><button class="btn" data-imp="${esc(n)}">Import</button></li>`).join("") || '<li class="meta">No flights on the stick.</li>';
  $("fl-remote").querySelectorAll("[data-imp]").forEach((b) => b.addEventListener("click", async () => {
    const d = await api("/api/usb/import", { path: P.link.usb_path, name: b.dataset.imp });
    setResult("fl-result", d); if (d.ok) { await loadLibrary(); openFlight(d.name); }
  }));
});
$("fl-import").addEventListener("click", async () => {
  const d = await api("/api/library/import_folder", { folder: $("fl-folder").value.trim() });
  setResult("fl-result", d); if (d.ok) { await loadLibrary(); openFlight(d.names[0]); }
});

/* ------------------------------------------------------------------ start */
(async function init() {
  META = await api("/api/meta");
  $("version").textContent = "v" + META.version;
  let p = null;
  try { p = JSON.parse(localStorage.getItem("silm-project") || "null"); } catch (e) {}
  P = Object.assign(JSON.parse(JSON.stringify(META.default_project)), p || {});
  for (const k of Object.keys(META.default_project)) if (typeof META.default_project[k] === "object" && META.default_project[k] && !Array.isArray(META.default_project[k])) P[k] = Object.assign({}, META.default_project[k], P[k] || {});
  loadDone(); renderForms(); drawPlaces(); fitMission(); listProjects(); runCheck();
  showTab((location.hash || "#start").slice(1));
})();
