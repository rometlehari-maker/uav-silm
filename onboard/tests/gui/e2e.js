// End-to-end test of the Ground Station in a real browser (Chromium via
// Playwright), driven like a user: every tab, the map, the forms, sending to a
// real ground agent over HTTP, the USB stick, the launch checklist, a flight's
// pictures and drawing a training box. Run by tests/test_gui_browser.py.
const { chromium } = require("playwright");
const assert = (c, m) => { if (!c) throw new Error("FAILED: " + m); };

let PAGE = null;
(async () => {
  const GS = process.env.GS_URL, PI = process.env.PI_ADDR, STICK = process.env.STICK, IMG = process.env.TRAIN_IMG;
  const shots = process.env.SHOTS;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, acceptDownloads: true });
  PAGE = page;
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/tile|arcgis|ERR_/.test(m.text())) errors.push(m.text()); });
  page.on("dialog", (d) => d.accept(d.type() === "prompt" ? "Field B" : undefined));
  const step = async (name) => { console.log("step:", name); if (shots) await page.screenshot({ path: `${shots}/${name}.png` }); };
  const tab = async (t) => { await page.click(`.tab[data-tab="${t}"]`); await page.waitForTimeout(300); };
  const problems = async () => page.$$eval("#problem-list li", (l) => l.map((x) => x.className + ": " + x.textContent));
  const resultOf = async (id, re, timeout = 15000) => {
    await page.waitForFunction(([i, r]) => { const el = document.getElementById(i); return el && new RegExp(r).test(el.textContent) && !el.classList.contains("busy"); }, [id, re], { timeout });
    return page.textContent("#" + id);
  };

  await page.goto(GS + "/#start");
  await page.waitForSelector("#form-aircraft .field", { state: "attached" });
  await step("01-start");

  // ---------------- aircraft: the CG question
  await tab("aircraft");
  assert((await problems()).some((p) => p.includes("center of gravity")), "missing CG is listed as a problem");
  const qtext = await page.textContent('.field[data-qid="aircraft.cg_mm"] .q');
  assert(qtext.startsWith("What is the center of gravity of your aircraft?"), "question shown above the CG input");
  await page.fill("#f-aircraft-cg_mm", "20");
  await page.waitForTimeout(700);
  assert((await page.textContent("#cg-box")).includes("far from the usual"), "bad CG explained");
  assert(await page.$('.field[data-qid="aircraft.cg_mm"].invalid'), "bad CG field highlighted");
  await page.fill("#f-aircraft-cg_mm", "64");
  await page.waitForTimeout(700);
  assert((await page.textContent("#cg-box")).includes("good"), "good CG confirmed");
  await page.selectOption("#f-aircraft-launch", "bungee");
  await step("02-aircraft");

  // ---------------- mission: draw on the map
  await tab("mission");
  await page.fill("#goto", "58.3830, 26.7270");
  await page.click("#goto-form button");
  await page.waitForTimeout(400);
  await page.click('.tool[data-tool="launch"]');
  // measure the map AFTER the tool click (a click may scroll the page)
  const box = await page.$eval("#map", (m) => { const r = m.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const at = (fx, fy) => ({ x: box.x + box.w * fx, y: box.y + box.h * fy });
  let p = at(0.25, 0.75); await page.mouse.click(p.x, p.y);
  await page.click('.tool[data-tool="area"]');
  for (const [fx, fy] of [[0.45, 0.3], [0.8, 0.3], [0.8, 0.6], [0.45, 0.6]]) { p = at(fx, fy); await page.mouse.click(p.x, p.y); }
  await page.click('.tool[data-tool="area"]');     // finish
  await page.fill("#f-wind-speed_mps", "5");
  await page.fill("#f-wind-from_deg", "250");
  await page.click("#into-wind");
  await page.waitForFunction(() => document.querySelectorAll("#plan-stats .stat").length >= 8, null, { timeout: 15000 });
  await page.waitForTimeout(800);
  const pill = await page.textContent("#check-pill");
  assert(!/to fix/.test(pill), "no blocking problems after answering everything: " + JSON.stringify(await problems()));
  assert((await page.textContent("#plan-stats")).includes("Flight time"), "plan statistics shown");
  await step("03-mission");

  // too much wind is refused
  await page.fill("#f-wind-speed_mps", "12");
  await page.waitForTimeout(800);
  assert((await problems()).some((x) => x.includes("above the 8 m/s limit")), "strong wind refused");
  await page.fill("#f-wind-speed_mps", "5");
  await page.waitForTimeout(800);

  // ---------------- camera
  await tab("camera");
  await page.click('#f-recognition-targets button[data-v="truck"]');      // switch truck off
  await page.click('#f-recognition-targets button[data-v="bus"]');        // and bus on
  await page.click('#f-recognition-save_all_frames button[data-v="true"]');
  await step("04-camera");

  // ---------------- send: Wi-Fi to the real agent, USB stick
  await tab("send");
  await page.fill("#pi-address", PI);
  await page.dispatchEvent("#pi-address", "change");
  await page.click("#pi-status");
  assert((await resultOf("pi-result", "Connected|✗")).includes("Connected to"), "Pi connection check");
  await page.click("#pi-send");
  const sent = await resultOf("pi-result", "installed|✗");
  assert(sent.includes("✓"), "settings sent over Wi-Fi: " + sent);
  await page.fill("#usb-path", STICK);
  await page.dispatchEvent("#usb-path", "change");
  await page.click("#usb-write");
  assert((await resultOf("usb-result", "written|✗")).includes("✓"), "settings written to the stick");
  await page.click("#usb-read");
  assert((await resultOf("usb-result", "has not read|imported|REJECTED")).includes("has not read this stick yet"), "stick not yet in the Pi");
  await page.click("summary");     // open the 'Mission Planner instead' section
  const [dl] = await Promise.all([page.waitForEvent("download"), page.click('[data-dl="params"]')]);
  assert(dl.suggestedFilename().endsWith(".param"), "parameter file download");
  await step("05-send");

  // ---------------- launch: checklist gates the ARM button
  await tab("launch");
  assert(await page.isDisabled("#arm"), "ARM disabled before the checklist");
  const boxes = await page.$$("#checklist input");
  assert(boxes.length >= 8, "checklist present");
  for (const b of boxes) await b.check();
  assert(!(await page.isDisabled("#arm")), "ARM enabled after the checklist");
  assert((await page.textContent("#wind-recap")).includes("within"), "wind recap within the limit");
  await step("06-launch");

  // ---------------- flights & pictures
  await tab("flights");
  await page.waitForSelector("#fl-list li[data-fl]");
  await page.click("#fl-list li[data-fl]");
  await page.waitForSelector("#fl-grid .pic");
  const pics = await page.$$("#fl-grid .pic");
  assert(pics.length === 7, "all 7 pictures shown, got " + pics.length);
  assert((await page.textContent("#fl-grid")).includes("58.38"), "coordinates under the pictures");
  await page.click('#fl-chips .chip[data-l="(ordinary)"]');
  assert((await page.$$("#fl-grid .pic")).length === 3, "filter hides ordinary pictures");
  await page.click('#fl-chips .chip[data-l="(ordinary)"]');
  await page.click("#fl-grid .pic");
  await page.waitForSelector("#lightbox:not([hidden])");
  const info = await page.textContent("#lb-info");
  assert(/Coordinates/.test(info) && /58\.38/.test(info), "lightbox shows coordinates");
  assert(await page.isVisible("#lb-box"), "object box drawn on the picture");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Escape");
  assert(await page.isHidden("#lightbox"), "lightbox closes");
  await step("08-flights");

  // ---------------- teach new objects: upload, draw a box
  await tab("train");
  await page.setInputFiles("#tr-files", IMG);
  await resultOf("tr-upload-result", "added");
  await page.waitForSelector("#tr-editor:not([hidden])");
  await page.click('#tr-classes .chip:has-text("tractor")');
  await page.waitForTimeout(400);
  const ib = await page.$eval("#tr-img", (m) => { const r = m.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  await page.mouse.move(ib.x + ib.w * 0.2, ib.y + ib.h * 0.2);
  await page.mouse.down();
  await page.mouse.move(ib.x + ib.w * 0.6, ib.y + ib.h * 0.7, { steps: 5 });
  await page.mouse.up();
  await resultOf("tr-box-result", "Saved");
  assert((await page.textContent("#tr-box-result")).includes("1 box"), "box saved");
  assert((await page.textContent("#tr-stats")).includes("tractor"), "statistics count the box");
  await step("09-train");

  // ---------------- saved missions
  await tab("start");
  await page.click("#save");
  await page.waitForTimeout(500);
  assert((await page.textContent("#project-list")).includes("New mission"), "mission saved and listed");
  await page.click("#new-project");                       // prompt answered "Field B"
  await page.waitForTimeout(800);
  assert((await page.inputValue("#project-name")) === "Field B", "new mission created");
  assert((await page.inputValue("#f-aircraft-cg_mm")) === "64", "aircraft answers carried over");

  assert(errors.length === 0, "browser errors: " + errors.join(" | "));
  console.log("E2E OK");
  await browser.close();
})().catch(async (e) => {
  console.error(e.message);
  try {
    if (PAGE) {
      console.error("problems shown: " + JSON.stringify(await PAGE.$$eval("#problem-list li", (l) => l.map((x) => x.textContent))));
      if (process.env.SHOTS) await PAGE.screenshot({ path: process.env.SHOTS + "/FAILURE.png" });
    }
  } catch (x) { /* ignore */ }
  process.exit(1);
});
