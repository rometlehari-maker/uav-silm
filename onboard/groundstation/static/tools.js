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
