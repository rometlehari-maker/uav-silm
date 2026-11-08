"use strict";
/* SILM Ground Station - "Set up aircraft": a guided, self-checking setup.
   One step per screen; steps that can be checked by the program turn green by
   themselves (the flight controller is found, the Pi answers, the camera
   sends pictures ...). Progress is kept on this laptop (~/SILM/setup.json). */

const WZ = { state: null, cur: 0, timer: null, fc: null, fcBusy: false, pi: null, from: false, sent: null };
const PI_ONE_LINE = "sudo apt update && sudo apt install -y git && ([ -d ~/uav-silm ] || git clone https://github.com/rometlehari-maker/uav-silm.git ~/uav-silm) && cd ~/uav-silm && git pull && bash onboard/pi/install.sh && sudo reboot";

const WZ_STEPS = [
  { id: "need", title: "What you need", sub: "Check you have the parts" },
  { id: "aircraft", title: "Your aircraft", sub: "Answer the questions" },
  { id: "mission", title: "A first mission", sub: "Draw it on the map" },
  { id: "fc_connect", title: "Connect the flight controller", sub: "USB cable" },
  { id: "fc_setup", title: "Set up the flight controller", sub: "One button" },
  { id: "bench", title: "Check the controls", sub: "On the bench" },
  { id: "pi_install", title: "Prepare the Raspberry Pi", sub: "Memory card, then install" },
  { id: "pi_connect", title: "Connect to the Pi", sub: "Its own Wi-Fi" },
  { id: "pi_check", title: "Camera and link", sub: "Then send the settings" },
  { id: "sim", title: "Test in the simulator", sub: "Before the first flight" },
];

const NEED = [
  "A fixed-wing plane (not a quadcopter)",
  "An ArduPilot flight controller that can run Lua scripts (H7 or F7, e.g. Matek H743) with ArduPlane",
  "An airspeed sensor, GPS with compass, and a battery monitor on it",
  "A Raspberry Pi 4 (2 GB is enough), a 32 GB microSD card, and a 5 V / 3 A supply from the aircraft",
  "The camera (SIYI A2 mini, or another network or USB camera)",
  "A USB cable from the flight controller to this laptop",
];
const BENCH = [
  "The propeller is OFF (the motor must never be able to spin on the bench).",
  "Battery in, the aircraft level on the table. It starts in AUTO by itself.",
  "Tilt it with the RIGHT wing down: the right aileron goes DOWN and the left one UP (it tries to lift the right wing).",
  "Tilt the nose DOWN: the elevator (or both V-tail halves) goes UP (it tries to lift the nose).",
  "If anything moved the wrong way: reverse that servo (SERVOn_REVERSED in Mission Planner), then come back here.",
];

/* ---------------------------------------------------------------- state */
async function wzLoad() {
  try { WZ.state = await api("/api/setup"); } catch (e) { WZ.state = { steps: WZ_STEPS.map((s) => s.id), done: {}, complete: false }; }
  try { const c = Number(localStorage.getItem("silm-wz-cur")); if (c >= 0 && c < WZ_STEPS.length) WZ.cur = c; } catch (e) {}
  if (!WZ.state.complete && WZ.state.done[WZ_STEPS[WZ.cur].id]) {           // resume at the first open step
    const first = WZ_STEPS.findIndex((s) => !WZ.state.done[s.id]); if (first >= 0) WZ.cur = first;
  }
  wzBadges();
}
function wzIsDone(id) { return !!(WZ.state && WZ.state.done[id]); }
async function wzMark(id, done = true) {
  if (!WZ.state || wzIsDone(id) === done) return;
  const r = await api("/api/setup", { step: id, done });
  if (r.ok) WZ.state = r;
  wzBadges();
  if ($("tab-setup").classList.contains("active")) { wzRenderList(); wzNextLabel(); wzHeadChip(); }
}
function wzHeadChip() {                         // the "✓ Done" next to "Step n of 10"
  const h = document.querySelector("#wz-card .wz-head"), s = WZ_STEPS[WZ.cur];
  if (!h || !s) return;
  const c = h.querySelector(".wz-chip");
  if (wzIsDone(s.id) && !c) h.insertAdjacentHTML("beforeend", '<span class="wz-chip ok">✓ Done</span>');
  if (!wzIsDone(s.id) && c) c.remove();
}
function wzCount() { return WZ_STEPS.filter((s) => wzIsDone(s.id)).length; }
function wzBadges() {
  if (!WZ.state) return;
  const n = wzCount(), all = n === WZ_STEPS.length;
  $("setup-badge").textContent = all ? "✓" : `${n}/${WZ_STEPS.length}`;
  $("setup-badge").classList.toggle("done", all);
  const ban = $("setup-banner");
  ban.hidden = all;
  $("setup-banner-title").textContent = n ? `Continue setting up your aircraft (${n} of ${WZ_STEPS.length} done)` : "First, set up your aircraft";
  $("setup-banner-go").textContent = n ? "Continue the setup" : "Start the setup";
}
$("setup-banner-go").addEventListener("click", () => showTab("setup"));

/* things the program can check itself */
function wzAircraftOK() { return !!(CHECK && !CHECK.errors.some((e) => META.questions.some((q) => q.tab === "aircraft" && e.includes(q.q)))); }
function wzMissionOK() { return !!(CHECK && CHECK.plan && !CHECK.errors.some((e) => e.startsWith("Mission"))); }
function wzRefresh() {                       // called after every check of the answers
  if (!WZ.state) return;
  if (wzAircraftOK()) wzMark("aircraft"); else if (wzIsDone("aircraft")) wzMark("aircraft", false);
  if (wzMissionOK()) wzMark("mission");
  if ($("tab-setup").classList.contains("active")) { wzRenderList(); if (["aircraft", "mission"].includes(WZ_STEPS[WZ.cur].id)) wzRenderStep(); }
}

/* ---------------------------------------------------------------- navigation */
function wzOnTab(name) {
  $("back-setup").hidden = !(WZ.from && name !== "setup");
  if (name === "setup") { WZ.from = false; wzRenderList(); wzRenderStep(); } else wzStopPoll();
}
function wzGo(tab) { WZ.from = true; showTab(tab); }
$("back-setup").addEventListener("click", () => showTab("setup"));
function wzSelect(i) {
  WZ.cur = Math.max(0, Math.min(WZ_STEPS.length - 1, i));
  try { localStorage.setItem("silm-wz-cur", String(WZ.cur)); } catch (e) {}
  wzRenderList(); wzRenderStep(); window.scrollTo(0, 0);
}
$("wz-prev").addEventListener("click", () => wzSelect(WZ.cur - 1));
$("wz-next").addEventListener("click", () => {
  if (WZ.cur === WZ_STEPS.length - 1) { WZ.cur = WZ_STEPS.length; wzRenderStep(); return; }   // the summary
  wzSelect(WZ.cur + 1);
});

function wzRenderList() {
  if (!WZ.state) return;
  $("wz-steps").innerHTML = WZ_STEPS.map((s, i) => `<li class="${wzIsDone(s.id) ? "done" : ""} ${i === WZ.cur ? "cur" : ""}">
    <button data-i="${i}"><b>${wzIsDone(s.id) ? "✓" : i + 1}</b><span>${esc(s.title)}<small>${esc(s.sub)}</small></span></button></li>`).join("");
  $("wz-steps").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => wzSelect(Number(b.dataset.i))));
  const n = wzCount();
  $("wz-bar").style.width = `${(100 * n) / WZ_STEPS.length}%`;
  $("wz-count").textContent = `${n} of ${WZ_STEPS.length} done`;
}

/* ---------------------------------------------------------------- the steps */
function chip(ok, text, warn) { return `<span class="wz-chip ${ok ? "ok" : warn ? "warn" : "no"}">${ok ? "✓" : warn ? "!" : "✗"} ${esc(text)}</span>`; }
function copyBox(text, id) { return `<div class="wz-copy"><code id="${id}">${esc(text)}</code><button class="btn ghost" data-copy="${id}">Copy</button></div>`; }
function bindCopy(el) {
  el.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const t = $(b.dataset.copy).textContent;
    try { await navigator.clipboard.writeText(t); b.textContent = "Copied ✓"; }
    catch (e) { const r = document.createRange(); r.selectNodeContents($(b.dataset.copy)); getSelection().removeAllRanges(); getSelection().addRange(r); b.textContent = "Selected - press Ctrl+C"; }
    setTimeout(() => (b.textContent = "Copy"), 2500);
  }));
}
function ticks(list, key) {
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem("silm-wz-" + key) || "[]"); } catch (e) {}
  return `<ul class="wz-ticks" data-key="${key}">` + list.map((t, i) => `<li><label><input type="checkbox" data-i="${i}" ${saved.includes(i) || wzIsDone(key) ? "checked" : ""}> <span>${esc(t)}</span></label></li>`).join("") + "</ul>";
}
function bindTicks(el, key, total) {
  const boxes = [...el.querySelectorAll(`.wz-ticks[data-key="${key}"] input`)];
  const upd = () => {
    const on = boxes.filter((b) => b.checked).map((b) => Number(b.dataset.i));
    try { localStorage.setItem("silm-wz-" + key, JSON.stringify(on)); } catch (e) {}
    wzMark(key, on.length === total);
    wzNextLabel();
  };
  boxes.forEach((b) => b.addEventListener("change", upd));
}
function wzNextLabel() {
  const s = WZ_STEPS[WZ.cur];
  $("wz-prev").disabled = WZ.cur === 0;
  $("wz-next").textContent = WZ.cur >= WZ_STEPS.length - 1 ? "Finish ▶" : s && wzIsDone(s.id) ? "Next ▶" : "Skip for now ▶";
  $("wz-next").classList.toggle("primary", !!(s && wzIsDone(s.id)));
}

function wzRenderStep() {
  wzStopPoll();
  $("wz-next").hidden = false;
  const el = $("wz-card");
  if (WZ.cur >= WZ_STEPS.length) return wzSummary(el);
  const s = WZ_STEPS[WZ.cur];
  const head = `<div class="wz-head"><span class="wz-num">Step ${WZ.cur + 1} of ${WZ_STEPS.length}</span>${wzIsDone(s.id) ? '<span class="wz-chip ok">✓ Done</span>' : ""}</div><h2 class="wz-title">${esc(s.title)}</h2>`;
  el.innerHTML = head + (WZ_RENDER[s.id] || (() => ""))();
  bindCopy(el);
  (WZ_BIND[s.id] || (() => {}))(el);
  wzNextLabel();
}

const WZ_RENDER = {
  need: () => `<p class="wz-lead">Tick what you have. Everything is needed before the first flight, but you can start with the laptop part now.</p>${ticks(NEED, "need")}
    <p class="help">Quadcopters, DJI, PX4 or iNav flight controllers, and planes without an airspeed sensor will not work with this software.</p>`,
  aircraft: () => {
    const ok = wzAircraftOK();
    const n = CHECK ? CHECK.errors.filter((e) => META.questions.some((q) => q.tab === "aircraft" && e.includes(q.q))).length : 0;
    return `<p class="wz-lead">Weight, wingspan, battery, speeds, how you launch it and where it balances. These become the flight controller's settings. Every question explains what to enter.</p>
      <div class="wz-status">${ok ? chip(true, "All aircraft questions answered") : chip(false, `${n} question${n === 1 ? "" : "s"} still to answer`)}</div>
      <button class="btn ${ok ? "" : "primary"} big" data-go="aircraft">${ok ? "Review the answers" : "Answer the questions"}</button>`;
  },
  mission: () => {
    const ok = wzMissionOK();
    return `<p class="wz-lead">The flight controller needs a mission before it can be set up, and the simulator flies it. Draw any area you might survey: you can change it any time.</p>
      <ol class="wz-howto"><li>Search or zoom to your field.</li><li><b>Set launch point</b>, click where you will throw it.</li><li><b>Draw survey area</b>, click its corners, then the tool again.</li><li><b>Set landing point</b>, then <b>Land into the wind</b>.</li></ol>
      <div class="wz-status">${ok ? chip(true, "Mission is valid") : chip(false, "No valid mission yet")}</div>
      <button class="btn ${ok ? "" : "primary"} big" data-go="mission">${ok ? "Open the map" : "Draw it on the map"}</button>`;
  },
  fc_connect: () => `<p class="wz-lead">Once, in Mission Planner: <b>Setup → Install Firmware → ArduPlane</b>, then calibrate the accelerometer, compass and airspeed sensor there. Close Mission Planner afterwards (it would hold the USB port).</p>
    <ol class="wz-howto"><li>Plug the battery into the aircraft.</li><li>Connect the flight controller to this laptop with the USB cable.</li><li>Wait: it is found by itself.</li></ol>
    <div class="wz-live" id="wz-fc-live"><span class="wz-spin"></span> Looking for the flight controller…</div>
    <div class="inline"><select id="wz-port" aria-label="USB port"></select><button class="btn ghost" id="wz-fc-retry">Check again</button></div>`,
  fc_setup: () => `<p class="wz-lead">One button sends everything the flight controller needs: its settings for your aircraft (no radio control, failsafes, speeds, the geofence, the port to the Pi), the GPS-loss script and the mission. Everything is read back to make sure it arrived.</p>
    ${wzMissionOK() ? "" : `<div class="wz-status">${chip(false, "Draw a mission first (step 3)")}</div>`}
    <ul class="wz-run" id="wz-run">
      <li data-k="params"><i></i>Aircraft settings (about a minute)</li>
      <li data-k="script"><i></i>GPS-loss script</li>
      <li data-k="reboot"><i></i>Restart: <b>unplug the battery and USB, wait 10 seconds, plug both back in</b></li>
      <li data-k="mission"><i></i>Mission</li>
    </ul>
    <div class="result" id="wz-fc-result"></div>
    <button class="btn primary big" id="wz-fc-go" ${wzMissionOK() ? "" : "disabled"}>Set up the flight controller</button>`,
  bench: () => `<p class="wz-lead">With no pilot to correct it, a control surface that moves the wrong way would crash the first flight. Check it now, on the table.</p>${ticks(BENCH, "bench")}`,
  pi_install: () => `<p class="wz-lead">The Pi needs a prepared memory card, then this app installs everything on it. About 20 minutes, mostly waiting.</p>
    <ol class="wz-howto">
      <li>Install <b>Raspberry Pi Imager</b> on this laptop (raspberrypi.com/software). Choose <b>Raspberry Pi 4</b> and <b>Raspberry Pi OS Lite (64-bit)</b>.</li>
      <li>When it asks about settings, choose <b>Edit settings</b>: hostname <code>silm</code>, a username and password, <b>your home Wi-Fi</b> with its country, and under Services <b>enable SSH</b> (password). Write the card.</li>
      <li>Put the card in the Pi and power it. Wait two minutes. This laptop must be on the same home Wi-Fi.</li>
      <li>Fill in and press <b>Install</b>:</li>
    </ol>
    ${piInstallForm()}
    <details class="wz-byhand"><summary>Or install by hand in a terminal</summary>
      <p>Connect to the Pi (Windows: <b>PowerShell</b>):${copyBox("ssh YOUR-USERNAME@silm.local", "wz-ssh")}
      then paste this line. It asks for a password for the aircraft's own Wi-Fi, installs everything and restarts the Pi:${copyBox(PI_ONE_LINE, "wz-oneline")}</p>
    </details>
    <label class="wz-confirm"><input type="checkbox" id="wz-pi-installed" ${wzIsDone("pi_install") ? "checked" : ""}> The installer finished and the Pi restarted</label>`,
  pi_connect: () => `<p class="wz-lead">After the restart, the Pi makes its own Wi-Fi network named <b>SILM-silm</b> whenever your home Wi-Fi is out of reach (at the field, or with the aircraft away from home).</p>
    <ol class="wz-howto"><li>Join this laptop to the Wi-Fi <b>SILM-…</b> with the password you chose. (No internet while joined: that is normal.)</li><li>Wait: the Pi is found by itself.</li></ol>
    <div class="inline"><span class="unit">Pi address</span><input id="wz-pi-addr" value="${esc(P.link.pi_address || "10.42.0.1")}" aria-label="Pi address"></div>
    <div class="wz-live" id="wz-pi-live"><span class="wz-spin"></span> Looking for the aircraft computer…</div>`,
  pi_check: () => `<p class="wz-lead">With the aircraft powered (camera and flight controller on), the Pi should see pictures from the camera and data from the flight controller.</p>
    <div class="wz-live" id="wz-pi-live"><span class="wz-spin"></span> Asking the aircraft computer…</div>
    <div class="wz-hints" id="wz-pi-hints"></div>
    <button class="btn primary big" id="wz-pi-send">Send the settings to the aircraft computer</button>
    <div class="result" id="wz-pi-result"></div>`,
  sim: () => META.sim && META.sim.available
    ? `<p class="wz-lead">Fly your mission in ArduPilot's simulator with the GPS jammed halfway. It must turn back, land on the strip and switch itself off: all seven checks green.</p>
       <div class="wz-status">${wzIsDone("sim") ? chip(true, "Passed") : chip(false, "Not passed yet")}</div>
       <button class="btn primary big" data-go="sim">Open the simulator</button>`
    : `<p class="wz-lead">The simulator flies your exact mission with the GPS jammed halfway, before you risk the real aircraft. It runs on Linux: on Windows, inside <b>Ubuntu (WSL2)</b>.</p>
       <ol class="wz-howto"><li>Set it up once: follow <b>SITL_HOWTO.md</b> in the software folder (part A, about 30 minutes, mostly waiting).</li><li>Start the Ground Station inside Ubuntu and open the <b>Simulator</b> tab, or run the commands it shows.</li><li>All seven checks must say PASS.</li></ol>
       <label class="wz-confirm"><input type="checkbox" id="wz-sim-ok" ${wzIsDone("sim") ? "checked" : ""}> I flew this mission in the simulator and every check passed</label>`,
};

const WZ_BIND = {
  need: (el) => bindTicks(el, "need", NEED.length),
  bench: (el) => bindTicks(el, "bench", BENCH.length),
  aircraft: (el) => el.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => wzGo(b.dataset.go))),
  mission: (el) => el.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => wzGo(b.dataset.go))),
  sim: (el) => {
    el.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => wzGo(b.dataset.go)));
    const c = $("wz-sim-ok"); if (c) c.addEventListener("change", () => { wzMark("sim", c.checked); wzRenderStep(); });
  },
  pi_install: (el) => {
    $("wz-pi-installed").addEventListener("change", (e) => { wzMark("pi_install", e.target.checked); wzNextLabel(); });
    piInstallBind(el);
  },
  fc_connect: () => {
    $("wz-fc-retry").addEventListener("click", () => { WZ.fc = null; wzFcPoll(true); });
    $("wz-port").addEventListener("change", () => { P.link.fc_port = $("wz-port").value; $("fc-port").value = $("fc-port-2").value = P.link.fc_port; changed(); WZ.fc = null; wzFcPoll(true); });
    wzFcPoll(true); WZ.timer = setInterval(() => wzFcPoll(false), 3000);
  },
  fc_setup: () => { $("wz-fc-go").addEventListener("click", wzFcSetup); if (wzIsDone("fc_setup")) wzRunMark(["params", "script", "reboot", "mission"], "ok"); },
  pi_connect: () => {
    $("wz-pi-addr").addEventListener("change", () => { P.link.pi_address = $("wz-pi-addr").value.trim(); $("pi-address").value = P.link.pi_address; changed(); wzPiPoll(); });
    wzPiPoll(); WZ.timer = setInterval(wzPiPoll, 3000);
  },
  pi_check: () => { $("wz-pi-send").addEventListener("click", wzPiSend); wzPiPoll(); WZ.timer = setInterval(wzPiPoll, 3000); },
};
function wzStopPoll() { clearInterval(WZ.timer); WZ.timer = null; }

/* ---------------------------------------------------------------- flight controller */
async function wzFcPoll(force) {
  if (WZ.fcBusy) return;
  const live = $("wz-fc-live"); if (!live) return wzStopPoll();
  let ports = [];
  try { ports = await api("/api/ports"); } catch (e) {}
  ports = ports.filter((p) => p.usb !== false);           // flight controllers are USB devices
  const sel = $("wz-port");
  if (!sel) return;
  const cur = P.link.fc_port;
  sel.innerHTML = ports.length ? ports.map((p) => `<option value="${esc(p.device)}" ${p.device === cur ? "selected" : ""}>${esc(p.device)} - ${esc(p.description)}</option>`).join("") : '<option value="">no USB ports found</option>';
  if (!ports.length) { live.innerHTML = '<span class="wz-spin"></span> No flight controller on USB yet. Battery in? USB cable plugged in? Mission Planner closed?'; WZ.fc = null; return; }
  if (!ports.some((p) => p.device === cur)) {             // pick the likely one
    const likely = ports.find((p) => /ardu|matek|stm|px4|cube|holybro|flight|acm|usbmodem/i.test(p.description + p.device)) || ports[0];
    P.link.fc_port = likely.device; $("fc-port").value = $("fc-port-2").value = likely.device; sel.value = likely.device; changed(); WZ.fc = null;
  }
  if (WZ.fc && WZ.fc.ok && !force) return;                // found: don't keep re-connecting
  WZ.fcBusy = true;
  live.innerHTML = `<span class="wz-spin"></span> Talking to ${esc(P.link.fc_port)}…`;
  let r;
  try { r = await api("/api/fc/info", { port: P.link.fc_port, baud: P.link.fc_baud }); } catch (e) { r = { ok: false, message: String(e) }; }
  WZ.fcBusy = false;
  if (!$("wz-fc-live")) return;
  WZ.fc = r;
  if (r.ok && r.firmware !== undefined) {
    $("wz-fc-live").innerHTML = chip(true, `Flight controller found on ${P.link.fc_port}`) + chip(true, `firmware ${r.firmware || "?"}`) +
      chip(r.battery_v != null, r.battery_v != null ? `battery ${r.battery_v} V` : "no battery voltage (battery monitor?)", true) +
      chip(r.gps_fix >= 3, r.gps_fix >= 3 ? `GPS fix, ${r.satellites} satellites` : "no GPS fix yet (fine indoors)", true) +
      (r.armed ? chip(false, "ARMED - disarm it") : "");
    wzMark("fc_connect");
  } else {
    $("wz-fc-live").innerHTML = chip(false, `No answer on ${P.link.fc_port}`) + `<div class="help">${esc(r.message || "")} Wrong port? Pick another above. Just plugged in? It needs about 10 seconds to start.</div>`;
  }
  wzNextLabel();
}
function wzRunMark(keys, cls) { keys.forEach((k) => { const li = document.querySelector(`#wz-run li[data-k="${k}"]`); if (li) li.className = cls; }); }
async function wzFcSetup() {
  const btn = $("wz-fc-go"); btn.disabled = true;
  const res = (m, ok) => { const r = $("wz-fc-result"); if (r) { r.className = "result " + (ok ? "ok" : "err"); r.textContent = (ok ? "✓ " : "✗ ") + m; } };
  const call = (a) => api("/api/fc/" + a, { port: P.link.fc_port, baud: P.link.fc_baud, project: P });
  try {
    wzRunMark(["params", "script", "reboot", "mission"], "");
    wzRunMark(["params"], "busy");
    let r = await call("params");
    if (!r.ok) { wzRunMark(["params"], "bad"); return res(r.message || "the settings could not be written", false); }
    wzRunMark(["params"], "ok"); wzRunMark(["script"], "busy");
    r = await call("script");
    if (!r.ok) { wzRunMark(["script"], "bad"); return res(r.message || "the script could not be copied", false); }
    wzRunMark(["script"], "ok"); wzRunMark(["reboot"], "busy wait");
    res("Now restart the flight controller: unplug the battery and the USB cable, wait 10 seconds, plug both back in. This continues by itself.", true);
    const t0 = Date.now(); let seenDown = false, info = null;
    while (Date.now() - t0 < 240000) {                      // up to 4 minutes for the restart
      await new Promise((ok) => setTimeout(ok, 3000));
      if (!$("wz-run")) return;                            // left the step
      try { info = await api("/api/fc/info", { port: P.link.fc_port, baud: P.link.fc_baud }); } catch (e) { info = null; }
      if (!info || !info.ok) { seenDown = true; continue; }
      if (info.script_loaded && (seenDown || Date.now() - t0 > 20000)) break;
    }
    if (!info || !info.ok || !info.script_loaded) { wzRunMark(["reboot"], "bad"); return res("The flight controller did not come back with the GPS-loss script running. Is scripting enabled on this board (H7/F7)? Try the button again after the restart.", false); }
    wzRunMark(["reboot"], "ok"); wzRunMark(["mission"], "busy");
    r = await call("mission");
    if (!r.ok) { wzRunMark(["mission"], "bad"); return res(r.message || "the mission could not be sent", false); }
    wzRunMark(["mission"], "ok");
    res("The flight controller is set up: settings, GPS-loss script and mission are on it and were read back.", true);
    markDone("fc"); wzMark("fc_setup");
  } catch (e) { res(String(e), false); }
  finally { const b = $("wz-fc-go"); if (b) b.disabled = false; wzNextLabel(); }
}

/* ---------------------------------------------------------------- Raspberry Pi */
async function wzPiPoll() {
  const live = $("wz-pi-live"); if (!live) return wzStopPoll();
  let r;
  try { r = await api("/api/pi/status", { address: P.link.pi_address }); } catch (e) { r = { ok: false, message: String(e) }; }
  if (!$("wz-pi-live")) return;
  WZ.pi = r;
  const id = WZ_STEPS[WZ.cur].id;
  if (!r.ok) {
    $("wz-pi-live").innerHTML = `<span class="wz-spin"></span> No answer from ${esc(P.link.pi_address)} yet. Is this laptop on the SILM-… Wi-Fi? Has the Pi been on for a minute?`;
    return;
  }
  const a = r.aircraft || {};
  const cam = a.camera_frame_age_s != null && a.camera_frame_age_s < 5, link = !!a.telemetry_ok;
  if (id === "pi_connect") {
    live.innerHTML = chip(true, `Connected to ${r.hostname} (software ${r.software})`) +
      chip(r.program_running, r.program_running ? "flight program running" : "flight program not running yet (it starts by itself; wait a minute)", true);
    wzMark("pi_connect");
  } else {
    live.innerHTML = chip(true, `Connected to ${r.hostname}`) + chip(cam, cam ? "camera sends pictures" : "no pictures from the camera") +
      chip(link, link ? "data from the flight controller" : "no data from the flight controller") +
      chip(!!a.have_fix, a.have_fix ? "GPS fix" : "no GPS fix yet (fine indoors)", true);
    const hints = [];
    if (!cam) hints.push("<b>Camera:</b> does it have 12 V? Is its Ethernet cable in the Pi? The Pi set up the camera network during the install (it needs the restart). Still nothing: the camera address on the Camera tab.");
    if (!link) hints.push("<b>Flight controller:</b> the Pi's TX goes to the flight controller's RX and RX to TX, plus ground. On the Camera tab, answer which port it is connected to, then repeat step 5 (it sets that port).");
    $("wz-pi-hints").innerHTML = hints.map((h) => `<p class="help">${h}</p>`).join("");
    if (cam && link && WZ.sent) wzMark("pi_check");
  }
  wzNextLabel();
}
async function wzPiSend() {
  const out = $("wz-pi-result"); out.className = "result busy"; out.textContent = "Sending…";
  const r = await api("/api/pi/send", { project: P, address: P.link.pi_address });
  if (!$("wz-pi-result")) return;
  out.className = "result " + (r.ok ? "ok" : "err"); out.textContent = (r.ok ? "✓ " : "✗ ") + (r.message || "");
  if (r.ok) {
    WZ.sent = true; markDone("pi");
    const a = (WZ.pi && WZ.pi.aircraft) || {};
    if (a.camera_frame_age_s != null && a.camera_frame_age_s < 5 && a.telemetry_ok) wzMark("pi_check");
    else out.textContent += " This step turns green when the camera and the flight controller both show ✓.";
  }
}

/* ---------------------------------------------------------------- the end */
function wzSummary(el) {
  const n = wzCount(), all = n === WZ_STEPS.length;
  const open = WZ_STEPS.map((s, i) => [s, i]).filter(([s]) => !wzIsDone(s.id));
  el.innerHTML = all
    ? `<div class="wz-finish"><div class="wz-big">✓</div><h2 class="wz-title">Your aircraft is set up</h2>
        <p class="wz-lead">From now on, for every flight: plan the mission, send it, and use the Launch checklist at the field.</p>
        <div class="row-btns"><button class="btn primary big" data-go="mission">Plan a mission</button><button class="btn big" data-go="launch">Flight day: Launch</button></div>
        <button class="btn ghost" id="wz-reset">Run the setup again</button></div>`
    : `<h2 class="wz-title">Almost there: ${n} of ${WZ_STEPS.length} steps done</h2><p class="wz-lead">Still open:</p>
       <ul class="wz-open">${open.map(([s, i]) => `<li><button class="btn ghost" data-i="${i}">${i + 1}. ${esc(s.title)}</button></li>`).join("")}</ul>`;
  el.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.go)));
  el.querySelectorAll("[data-i]").forEach((b) => b.addEventListener("click", () => wzSelect(Number(b.dataset.i))));
  const rs = $("wz-reset");
  if (rs) rs.addEventListener("click", async () => {
    if (!confirm("Start the setup from the beginning? Nothing on the aircraft is changed.")) return;
    const r = await api("/api/setup", { reset: true }); if (r.ok) WZ.state = r;
    ["need", "bench"].forEach((k) => { try { localStorage.removeItem("silm-wz-" + k); } catch (e) {} });
    wzBadges(); wzSelect(0);
  });
  $("wz-prev").disabled = false; $("wz-next").hidden = true;
  wzRenderList();
}
