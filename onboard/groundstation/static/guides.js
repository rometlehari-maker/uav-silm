"use strict";
/* SILM Ground Station - Guides: how to do what the app can't (from GUIDES.md).
   Any element with data-guide="<id>" opens that guide. */

const GD = { data: null, cur: null };

async function gdLoad() {
  if (GD.data) return GD.data;
  GD.data = await api("/api/guides");
  $("gd-intro").innerHTML = GD.data.intro;                 // made from GUIDES.md, escaped by the server
  $("gd-list").innerHTML = GD.data.guides.map((g) => `<button class="gd-item" data-gd="${esc(g.id)}">${esc(g.title)}</button>`).join("");
  $("gd-list").querySelectorAll("[data-gd]").forEach((b) => b.addEventListener("click", () => gdShow(b.dataset.gd, true)));
  return GD.data;
}
async function gdShow(id, scroll) {
  const d = await gdLoad();
  const g = d.guides.find((x) => x.id === id) || d.guides[0];
  GD.cur = g.id;
  $("gd-body").innerHTML = `<h2>${esc(g.title)}</h2>` + g.html;
  $("gd-list").querySelectorAll("[data-gd]").forEach((b) => b.classList.toggle("on", b.dataset.gd === g.id));
  if (scroll) $("gd-body").scrollIntoView({ behavior: "smooth", block: "start" });
  try { history.replaceState(null, "", "#guides/" + g.id); } catch (e) {}
}
function openGuide(id) { showTab("guides"); gdShow(id, false); }
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-go-tab]");
  if (t) { e.preventDefault(); showTab(t.dataset.goTab); return; }
  const a = e.target.closest("[data-guide]");
  if (!a) return;
  e.preventDefault();
  openGuide(a.dataset.guide);
});
const _gdOnTab = typeof toolsOnTab === "function" ? toolsOnTab : null;
toolsOnTab = function (name) {                // eslint-disable-line no-func-assign
  if (_gdOnTab) _gdOnTab(name);
  if (name === "guides" && !GD.cur) gdShow((location.hash.match(/^#guides\/([a-z0-9-]+)/) || [])[1] || "parts", false);
};
if (/^#guides/.test(location.hash)) setTimeout(() => openGuide((location.hash.match(/^#guides\/([a-z0-9-]+)/) || [])[1] || "parts"), 0);
