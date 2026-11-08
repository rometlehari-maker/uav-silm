"use strict";
/* SILM Ground Station - Tools tab: check, calibrate and look after the aircraft. */

const TL = { camTimer: null, camOn: false, lastCal: null };

function tlAddr() { return P.link.pi_address || "10.42.0.1"; }

/* ---------------------------------------------------------------- check everything */
$("pf-run").addEventListener("click", async () => {
  const b = $("pf-run"); b.disabled = true;
  setResult("pf-result", null, "Checking (about a minute)…");
  let r;
  try { r = await api("/api/pi/preflight", { address: tlAddr(), project: P }); } catch (e) { r = { ok: false, message: String(e) }; }
  b.disabled = false;
  setResult("pf-result", r.ok ? { ok: !r.fails, message: r.message } : r);
  const t = $("pf-table");
  if (!r.ok || !r.results) { t.hidden = true; return; }
  t.tBodies[0].innerHTML = r.results.map((x) => `<tr class="pf-${esc(x.status.toLowerCase())}"><td>${esc(x.part)}</td><td><b>${esc(x.status)}</b></td><td>${esc(x.message)}</td></tr>`).join("");
  t.hidden = false;
});

/* ---------------------------------------------------------------- camera */
function camStop() { clearTimeout(TL.camTimer); TL.camOn = false; $("cam-toggle").textContent = "Show the live picture"; $("cam-view").hidden = true; }
async function camTick() {
  if (!TL.camOn) return;
  try {
    const r = await fetch(`/api/pi/camera.jpg?address=${encodeURIComponent(tlAddr())}&t=${Date.now()}`);
    if (r.ok) {
      const url = URL.createObjectURL(await r.blob());
      const img = $("cam-img"), old = img.src;
      img.src = url; img.hidden = false; $("cam-msg").hidden = true;
      if (old.startsWith("blob:")) setTimeout(() => URL.revokeObjectURL(old), 2000);
    } else {
      const j = await r.json().catch(() => ({}));
      $("cam-msg").hidden = false;
      $("cam-msg").textContent = r.status === 503 ? "Waiting for a picture… (the camera needs power and its Ethernet cable; the Pi only sends pictures on the ground)" : (j.message || "No answer from the aircraft computer.");
    }
  } catch (e) { $("cam-msg").hidden = false; $("cam-msg").textContent = "No answer: " + e; }
  if (TL.camOn) TL.camTimer = setTimeout(camTick, 1000);
}
$("cam-toggle").addEventListener("click", () => {
  if (TL.camOn) return camStop();
  TL.camOn = true; $("cam-toggle").textContent = "Stop the live picture"; $("cam-view").hidden = false; $("cam-img").hidden = true;
  $("cam-msg").hidden = false; $("cam-msg").textContent = "Waiting for a picture…";
  camTick();
});

/* ---------------------------------------------------------------- lens calibration */
function lcBody() { return { address: tlAddr(), cols: Number($("lc-cols").value), rows: Number($("lc-rows").value), square_mm: Number($("lc-sq").value), fisheye: true }; }
function lcCurrent() {
  const c = P.camera && P.camera.calibration;
  $("lc-current").textContent = c ? `This mission uses a measured lens calibration (${c.width} × ${c.height}, ${c.model || "fisheye"} model). It goes to the aircraft with the settings.` : "No lens calibration yet: the aircraft uses the standard values for this lens.";
}
async function lcCall(action, busy) {
  setResult("lc-result", null, busy);
  let r;
  try { r = await api("/api/pi/calibration", Object.assign(lcBody(), { action })); } catch (e) { r = { ok: false, message: String(e) }; }
  return r;
}
$("lc-capture").addEventListener("click", async () => {
  if (!TL.camOn) $("cam-toggle").click();                // the board has to be seen
  const r = await lcCall("capture", "Looking for the checkerboard…");
  setResult("lc-result", { ok: r.ok, message: r.ok ? `${r.message} (${r.shots} so far; 20 to 30 is good)` : r.message + (r.shots != null ? ` (${r.shots} kept so far)` : "") });
});
$("lc-compute").addEventListener("click", async () => {
  const r = await lcCall("compute", "Calculating (up to a minute)…");
  TL.lastCal = r.ok ? r.calibration : null;
  setResult("lc-result", r);
  if (r.ok) {
    const el = $("lc-result");
    el.insertAdjacentHTML("beforeend", r.quality === "poor"
      ? "\nThe error is high: take new pictures (sharp, whole board, all over the picture) and calculate again."
      : '\n<button class="btn primary" id="lc-use">Use this calibration</button>');
    const u = $("lc-use");
    if (u) u.addEventListener("click", () => {
      P.camera.calibration = TL.lastCal; changed(); lcCurrent();
      toast("Lens calibration saved in this mission. Send the settings to the aircraft (Send tab).", 6000);
    });
  }
});
$("lc-reset").addEventListener("click", async () => {
  if (!confirm("Delete the calibration pictures on the aircraft computer and start again?")) return;
  setResult("lc-result", await lcCall("reset", "Deleting…"));
});

function toolsOnTab(name) {
  if (name !== "tools") { if (TL.camOn) camStop(); return; }
  lcCurrent();
}

/* ================================================================ flight controller */
const FCJ = { timer: null };
function fcPort() { return P.link.fc_port || ""; }
function fcBody(extra) { return Object.assign({ port: fcPort(), baud: P.link.fc_baud }, extra || {}); }
$("tl-port").addEventListener("change", () => { P.link.fc_port = $("tl-port").value.trim(); $("fc-port").value = $("fc-port-2").value = P.link.fc_port; changed(); });
$("tl-ports").addEventListener("click", async () => { await refreshPorts(); $("tl-port").value = fcPort(); });
async function fcPost(path, extra, busyId, busy) {
  if (busyId) setResult(busyId, null, busy);
  let r;
  try { r = await api(path, fcBody(extra)); } catch (e) { r = { ok: false, message: String(e) }; }
  if (busyId) setResult(busyId, r);
  return r;
}

/* ---------------- long jobs: calibrations, log download, firmware */
const JOB_TITLES = { accel: "Accelerometer calibration", level: "Level", compass: "Compass calibration", airspeed: "Airspeed sensor", log: "Downloading a log", firmware: "Updating ArduPlane" };
async function fcJobStart(kind, extra) {
  const r = await api("/api/fc/job/start", fcBody(Object.assign({ kind }, extra || {})));
  if (!r.ok) { toast(r.message, 5000); return; }
  $("fc-job-card").hidden = false; $("fcj-title").textContent = JOB_TITLES[kind] || kind;
  $("fcj-result").textContent = ""; $("fcj-result").className = "result";
  $("fc-job-card").scrollIntoView({ behavior: "smooth", block: "center" });
  fcJobPoll();
}
async function fcJobPoll() {
  clearTimeout(FCJ.timer);
  let s;
  try { s = await api("/api/fc/job"); } catch (e) { return; }
  if (!s || s.state === "idle") return;
  $("fc-job-card").hidden = false;
  $("fcj-title").textContent = JOB_TITLES[s.kind] || s.kind;
  $("fcj-prompt").textContent = s.prompt || "";
  $("fcj-prompt").hidden = !s.prompt;
  $("fcj-bar").style.width = (s.progress || 0) + "%";
  $("fcj-step").textContent = s.step || "";
  $("fcj-done").hidden = s.state !== "waiting";
  $("fcj-cancel").hidden = !["running", "waiting"].includes(s.state);
  $("fcj-log").textContent = (s.log || []).join("\n");
  if (["running", "waiting"].includes(s.state)) { FCJ.timer = setTimeout(fcJobPoll, 700); return; }
  setResult("fcj-result", { ok: s.state === "done", message: s.result || s.state });
  if (s.kind === "log" && s.state === "done") lgList(true);
  if (s.kind === "firmware") fwFiles();
}
$("fcj-done").addEventListener("click", async () => { $("fcj-done").hidden = true; await api("/api/fc/job/answer", {}); fcJobPoll(); });
$("fcj-cancel").addEventListener("click", async () => { await api("/api/fc/job/cancel", {}); fcJobPoll(); });
document.querySelectorAll("[data-fcjob]").forEach((b) => b.addEventListener("click", () => fcJobStart(b.dataset.fcjob)));

/* ---------------- servos */
$("sv-read").addEventListener("click", async () => {
  const r = await fcPost("/api/fc/servos", {}, "sv-result", "Reading the outputs…");
  const t = $("sv-table");
  if (!r.ok) { t.hidden = true; return; }
  setResult("sv-result", { ok: true, message: `${r.servos.length} outputs in use` });
  t.tBodies[0].innerHTML = r.servos.map((s) => `<tr><td>${s.n}</td><td>${esc(s.name)}</td><td><div class="yesno" data-sv="${s.n}"><button type="button" data-v="0" class="${s.reversed ? "" : "on"}">Normal</button><button type="button" data-v="1" class="${s.reversed ? "on" : ""}">Reversed</button></div></td></tr>`).join("");
  t.hidden = false;
  t.querySelectorAll("[data-sv]").forEach((g) => g.querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
    const res = await fcPost("/api/fc/servo_reverse", { n: Number(g.dataset.sv), reversed: b.dataset.v === "1" }, "sv-result", "Setting…");
    if (res.ok) g.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  })));
});

/* ---------------- battery */
$("bt-read").addEventListener("click", () => fcPost("/api/fc/battery", {}, "bt-result", "Measuring…"));
$("bt-cal").addEventListener("click", () => {
  const v = Number($("bt-meas").value);
  if (!v) return toast("Enter the voltage your multimeter shows.", 4000);
  fcPost("/api/fc/battery_cal", { measured_v: v }, "bt-result", "Calibrating…");
});

/* ---------------- logs */
async function lgList(quiet) {
  const r = await fcPost("/api/fc/logs", {}, quiet ? null : "lg-result", "Asking for the list…");
  const t = $("lg-table");
  if (!r.ok) { if (quiet) setResult("lg-result", r); t.hidden = true; return; }
  if (!quiet) setResult("lg-result", { ok: true, message: r.logs.length ? `${r.logs.length} logs on the flight controller` : "no logs on the flight controller yet" });
  t.tBodies[0].innerHTML = r.logs.map((l) => `<tr><td>${l.id}</td><td>${esc(l.time || "-")}</td><td>${(l.size / 1e6).toFixed(1)} MB</td><td><button class="btn ghost" data-log="${l.id}" data-size="${l.size}">Download</button></td></tr>`).join("");
  t.hidden = !r.logs.length;
  t.querySelectorAll("[data-log]").forEach((b) => b.addEventListener("click", () => fcJobStart("log", { log_id: Number(b.dataset.log), size: Number(b.dataset.size) })));
}
$("lg-list").addEventListener("click", () => lgList(false));

/* ---------------- firmware */
async function fwFiles() {
  let r;
  try { r = await api("/api/fc/firmware"); } catch (e) { return; }
  const sel = $("fw-board");
  if (!sel.options.length) sel.innerHTML = Object.entries(r.boards).map(([k, l]) => `<option value="${esc(k)}">${esc(l)}</option>`).join("");
  $("fw-files").innerHTML = r.files.map((f) => `<li><span><b>${esc(f.file)}</b><br><span class="meta">${f.size_mb} MB · saved ${esc(f.saved)}</span></span><button class="btn primary" data-fw="${esc(f.file)}">Install on the flight controller</button></li>`).join("");
  $("fw-files").querySelectorAll("[data-fw]").forEach((b) => b.addEventListener("click", () => {
    if (!confirm(`Install ${b.dataset.fw} on the flight controller (${fcPort() || "no port chosen"})? Don't unplug it until it says it's done.`)) return;
    fcJobStart("firmware", { file: b.dataset.fw });
  }));
}
$("fw-download").addEventListener("click", async () => {
  setResult("fw-result", null, "Downloading from firmware.ardupilot.org (internet needed)…");
  let r;
  try { r = await api("/api/fc/firmware/download", { board: $("fw-board").value, release: "stable" }); } catch (e) { r = { ok: false, message: String(e) }; }
  setResult("fw-result", r); if (r.ok) fwFiles();
});
$("fw-file").addEventListener("change", async (e) => {
  const f = e.target.files[0]; if (!f) return;
  const buf = new Uint8Array(await f.arrayBuffer());
  let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  let r;
  try { r = await api("/api/fc/firmware/upload", { name: f.name, data_b64: btoa(bin) }); } catch (err) { r = { ok: false, message: String(err) }; }
  setResult("fw-result", r); if (r.ok) fwFiles();
  e.target.value = "";
});

const _toolsOnTabPi = toolsOnTab;
toolsOnTab = function (name) {                // eslint-disable-line no-func-assign
  _toolsOnTabPi(name);
  if (name !== "tools") return;
  $("tl-port").value = fcPort();
  fwFiles(); fcJobPoll();
};
