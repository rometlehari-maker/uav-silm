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

/* ---------------------------------------------------------------- aircraft software */
async function swPost(how, busy) {
  if (busy) setResult("sw-result", null, busy);
  let r;
  try { r = await api("/api/pi/software", { how, address: tlAddr(), path: P.link.usb_path || "" }); } catch (e) { r = { ok: false, message: String(e) }; }
  return r;
}
async function swInfo(ask) {
  const r = await swPost("info", ask ? "Asking the aircraft…" : null);
  if (!r.ok) { if (ask) setResult("sw-result", r); return r; }
  $("sw-app").textContent = r.app_version;
  if (!ask) return r;
  const pi = $("sw-pi");
  pi.textContent = r.pi_version || "no answer";
  pi.classList.toggle("old", !!r.pi_version && r.pi_version !== r.app_version);
  setResult("sw-result", r.pi_version
    ? { ok: true, message: r.pi_version === r.app_version ? "The aircraft has this app's software." : `The aircraft has ${r.pi_version}; this app carries ${r.app_version}.` }
    : { ok: false, message: r.pi_message || "No answer from the aircraft computer." });
  return r;
}
$("sw-info").addEventListener("click", () => swInfo(true));
$("sw-wifi").addEventListener("click", async () => {
  if (!confirm(`Install software ${$("sw-app").textContent} on the aircraft computer at ${tlAddr()}? It takes about a minute; the aircraft must be on the ground, disarmed.`)) return;
  const b = $("sw-wifi"); b.disabled = true;
  const r = await swPost("wifi", "Sending and checking (about a minute)…");
  b.disabled = false;
  setResult("sw-result", r);
  if (r.ok) { $("sw-pi").textContent = r.to_version; $("sw-pi").classList.remove("old"); }
});
$("sw-usb").addEventListener("click", async () => {
  if (!P.link.usb_path) return setResult("sw-result", { ok: false, message: "Choose the USB stick on the Send tab (C) first." });
  setResult("sw-result", await swPost("usb", "Writing to the stick…"));
});

/* ---------------------------------------------------------------- aircraft Wi-Fi */
const WF_KIND = { aircraft: "the aircraft's own Wi-Fi", home: "home Wi-Fi (joined when in range)", other: "other" };
async function wfPost(body, busy) {
  if (busy) setResult("wf-result", null, busy);
  let r;
  try { r = await api("/api/pi/wifi", Object.assign({ address: tlAddr() }, body)); } catch (e) { r = { ok: false, message: String(e) }; }
  return r;
}
async function wfList(quiet) {
  const r = await wfPost({ action: "list" }, quiet ? null : "Asking the aircraft…");
  const ul = $("wf-nets");
  if (!r.ok) { setResult("wf-result", r); ul.innerHTML = ""; return; }
  if (!quiet) setResult("wf-result", { ok: true, message: `${r.networks.length} Wi-Fi network(s) on the aircraft` });
  ul.innerHTML = r.networks.map((n) => {
    const home = n.kind === "home" ? n.name.slice(5) : null;
    return `<li><span><b>${esc(home || n.name)}</b><br><span class="meta">${esc(WF_KIND[n.kind] || n.kind)}${n.auto ? "" : " · not automatic"}</span></span>`
      + (home ? `<button class="btn ghost" data-wfrm="${esc(home)}">Forget</button>` : "") + "</li>";
  }).join("");
  ul.querySelectorAll("[data-wfrm]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Forget the home Wi-Fi '${b.dataset.wfrm}' on the aircraft?`)) return;
    const res = await wfPost({ action: "home-remove", name: b.dataset.wfrm }, "Forgetting…");
    setResult("wf-result", res); if (res.ok) wfList(true);
  }));
}
const WF_NAME = /^[A-Za-z0-9 ._-]{1,32}$/;
function wfCheck(name, pass) {
  if (!WF_NAME.test(name)) return "The name must be 1 to 32 letters, digits, spaces or . _ -";
  if (pass.length < 8 || pass.length > 63) return "The password must be 8 to 63 characters.";
  return null;
}
$("wf-list").addEventListener("click", () => wfList(false));
$("wf-hs-set").addEventListener("click", async () => {
  const name = $("wf-hs-name").value.trim(), pass = $("wf-hs-pass").value;
  const bad = wfCheck(name, pass); if (bad) return setResult("wf-result", { ok: false, message: bad });
  if (!confirm(`Rename the aircraft's Wi-Fi to '${name}'? From its next start, join '${name}' with the new password. Write the password down.`)) return;
  const r = await wfPost({ action: "hotspot", name, password: pass }, "Changing…");
  setResult("wf-result", r);
  if (r.ok) $("wf-hs-pass").value = "";
});
$("wf-home-add").addEventListener("click", async () => {
  const name = $("wf-home-name").value.trim(), pass = $("wf-home-pass").value;
  const bad = wfCheck(name, pass); if (bad) return setResult("wf-result", { ok: false, message: bad });
  const r = await wfPost({ action: "home-add", name, password: pass }, "Adding…");
  setResult("wf-result", r);
  if (r.ok) { $("wf-home-pass").value = ""; wfList(true); }
});
$("wf-cc-set").addEventListener("click", async () => {
  const cc = $("wf-cc").value.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(cc)) return setResult("wf-result", { ok: false, message: "Two letters, e.g. EE, FI, DE." });
  setResult("wf-result", await wfPost({ action: "country", country: cc }, "Setting…"));
});

function toolsOnTab(name) {
  if (name !== "tools") { if (TL.camOn) camStop(); return; }
  lcCurrent();
  swInfo(false);
  piInstallTools();
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

/* ---------------- servos: each output's job and direction */
$("sv-read").addEventListener("click", async () => {
  const r = await fcPost("/api/fc/servos", {}, "sv-result", "Reading the outputs…");
  const t = $("sv-table");
  if (!r.ok) { t.hidden = true; return; }
  const used = r.servos.filter((s) => s.used).length;
  setResult("sv-result", { ok: true, message: `${r.servos.length} outputs, ${used} in use` });
  const opts = (f) => Object.entries(r.functions).map(([k, l]) => `<option value="${k}" ${+k === f ? "selected" : ""}>${esc(l)}</option>`).join("")
    + (r.functions[f] === undefined ? `<option value="${f}" selected>function ${f}</option>` : "");
  t.tBodies[0].innerHTML = r.servos.map((s) => `<tr class="${s.used ? "" : "sv-off"}"><td>S${s.n}</td><td><select data-svf="${s.n}" aria-label="Output ${s.n} drives">${opts(s.function)}</select></td><td><div class="yesno" data-sv="${s.n}"><button type="button" data-v="0" class="${s.reversed ? "" : "on"}">Normal</button><button type="button" data-v="1" class="${s.reversed ? "on" : ""}">Reversed</button></div></td></tr>`).join("");
  t.hidden = false;
  t.querySelectorAll("[data-svf]").forEach((sel) => sel.addEventListener("change", async () => {
    const res = await fcPost("/api/fc/servo_function", { n: Number(sel.dataset.svf), function: Number(sel.value) }, "sv-result", "Setting…");
    if (res.ok) sel.closest("tr").classList.toggle("sv-off", sel.value === "0");
  }));
  t.querySelectorAll("[data-sv]").forEach((g) => g.querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
    const res = await fcPost("/api/fc/servo_reverse", { n: Number(g.dataset.sv), reversed: b.dataset.v === "1" }, "sv-result", "Setting…");
    if (res.ok) g.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  })));
});

/* ---------------- airspeed sensor */
$("as-read").addEventListener("click", async () => {
  const r = await fcPost("/api/fc/airspeed", {}, "as-result", "Asking the flight controller…");
  if (r.types && !$("as-type").options.length) $("as-type").innerHTML = Object.entries(r.types).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("");
  if (r.type != null) $("as-type").value = String(r.type);
  if (r.bus != null) $("as-bus").value = String(r.bus);
});
$("as-set").addEventListener("click", () => {
  if (!$("as-type").options.length) return setResult("as-result", { ok: false, message: "Press Check first." });
  fcPost("/api/fc/airspeed_set", { type: Number($("as-type").value), bus: Number($("as-bus").value) }, "as-result", "Setting…");
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

/* ================================================================ install the Pi over SSH
   The same form in the setup wizard and on this tab: piInstallForm() + piInstallBind(el). */
const PIN = { timer: null };
function piInstallForm() {
  const pi = P.link.pi_install || {};
  return `<div class="pin-form">
    <div class="pin-grid">
      <label>Pi's name or address<input class="pin-host" value="${esc(pi.host || "silm.local")}" aria-label="Pi name or address"></label>
      <label>Its username<input class="pin-user" value="${esc(pi.user || "")}" autocomplete="username" aria-label="Pi username"></label>
      <label>Its password<input class="pin-pass" type="password" autocomplete="current-password" aria-label="Pi password"></label>
      <label>Aircraft Wi-Fi name <span class="unit">(empty: SILM-silm)</span><input class="pin-ssid" maxlength="32" value="${esc(pi.ssid || "")}" aria-label="Aircraft Wi-Fi name"></label>
      <label>Aircraft Wi-Fi password <span class="unit">(new, 8+ characters)</span><input class="pin-hspass" type="password" maxlength="63" autocomplete="new-password" aria-label="Aircraft Wi-Fi password"></label>
      <label>Wi-Fi country<input class="pin-cc" maxlength="2" value="${esc(pi.country || "EE")}" aria-label="Wi-Fi country"></label>
    </div>
    <label class="pin-check"><input type="checkbox" class="pin-camnet" ${pi.camera_net === false ? "" : "checked"}> The camera (SIYI) uses the Pi's Ethernet port</label>
    <div class="row-btns"><button class="btn primary pin-start">Install</button><button class="btn ghost pin-cancel" hidden>Stop</button></div>
    <div class="pin-job" hidden><div class="wz-bar"><i class="pin-bar"></i></div><div class="help pin-step"></div>
      <details><summary>Details</summary><pre class="log pin-log"></pre></details></div>
    <div class="result pin-result" role="status"></div>
    <p class="help">The passwords go only to the Pi and are not saved. The Pi needs internet once (your home Wi-Fi). The install takes about 15 minutes; keep the Pi powered.</p>
  </div>`;
}
function piInstallBind(el) {
  el.querySelectorAll(".pin-form").forEach((f) => {
    const v = (c) => f.querySelector(c).value.trim();
    f.querySelector(".pin-start").addEventListener("click", async () => {
      const body = { host: v(".pin-host"), user: v(".pin-user"), password: f.querySelector(".pin-pass").value,
        hotspot_password: f.querySelector(".pin-hspass").value, ssid: v(".pin-ssid"), country: v(".pin-cc").toUpperCase(),
        camera_net: f.querySelector(".pin-camnet").checked };
      P.link.pi_install = { host: body.host, user: body.user, ssid: body.ssid, country: body.country, camera_net: body.camera_net }; changed();
      let r;
      try { r = await api("/api/pi/install/start", body); } catch (e) { r = { ok: false, message: String(e) }; }
      if (!r.ok) return setResult(f.querySelector(".pin-result"), r);
      f.querySelector(".pin-pass").value = "";
      piInstallPoll();
    });
    f.querySelector(".pin-cancel").addEventListener("click", async () => {
      if (confirm("Stop the install? You can start it again later; it continues where it can.")) { await api("/api/pi/install/cancel", {}); piInstallPoll(); }
    });
  });
  piInstallPoll();
}
async function piInstallPoll() {
  clearTimeout(PIN.timer);
  let s;
  try { s = await api("/api/pi/install"); } catch (e) { return; }
  const running = s.state === "running";
  document.querySelectorAll(".pin-form").forEach((f) => {
    if (s.state === "idle") return;
    f.querySelector(".pin-job").hidden = false;
    f.querySelector(".pin-bar").style.width = (s.progress || 0) + "%";
    f.querySelector(".pin-step").textContent = s.step || "";
    const log = f.querySelector(".pin-log"); log.textContent = (s.log || []).join("\n"); log.scrollTop = log.scrollHeight;
    f.querySelector(".pin-start").disabled = running;
    f.querySelector(".pin-cancel").hidden = !running;
    const res = f.querySelector(".pin-result");
    if (running) res.textContent = "";
    else setResult(res, { ok: s.state === "done", message: s.result || s.state });
  });
  if (running) { PIN.timer = setTimeout(piInstallPoll, 1000); return; }
  if (s.state === "done" && typeof wzMark === "function" && !wzIsDone("pi_install")) {
    await wzMark("pi_install", true);
    const c = $("wz-pi-installed"); if (c) c.checked = true;
    if (typeof wzNextLabel === "function") wzNextLabel();
  }
}
function piInstallTools() {                     // the Tools tab's copy, made when the tab opens
  const card = $("pin-tools");
  if (card.querySelector(".pin-form")) return;
  card.insertAdjacentHTML("beforeend", piInstallForm());
  piInstallBind(card);
}
