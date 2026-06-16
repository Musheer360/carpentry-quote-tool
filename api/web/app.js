/* Carpentry Quote Tool - frontend (English UI). Talks to /api/*. */
"use strict";

const API = "/api";
let PB = null;            // price book {categories, items, week_days, month_days, defaults}
let CUR = null;           // currently open project (full)
let VIEW = "projects";    // projects | pricebook | editor
let MODE = "edit";        // editor sub-mode: edit | preview
let PASSWORD = localStorage.getItem("cqt_pw") || "";
let DIRTY = false;
let saveTimer = null;
let pbSearch = "";

const $ = (sel, e = document) => e.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    n.appendChild(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
  }
  return n;
};
const stop = (fn) => (e) => { e.stopPropagation(); fn(e); };

/* ----------------------------------------------------------- toasts ------- */
function toast(msg, type = "ok") {
  let host = $("#toasts");
  if (!host) { host = el("div", { id: "toasts" }); document.body.appendChild(host); }
  const t = el("div", { class: "toast " + type }, msg);
  host.appendChild(t);
  setTimeout(() => { t.classList.add("show"); }, 10);
  setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 300); }, 3200);
}

/* ------------------------------------------------------------- api -------- */
async function api(path, opts = {}) {
  opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (PASSWORD) opts.headers["X-App-Password"] = PASSWORD;
  const res = await fetch(API + path, opts);
  if (res.status === 401) { promptPassword(); throw new Error("unauthorized"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

/* ------------------------------------------------------------- auth ------- */
async function ensureAuth() {
  const cfg = await fetch(API + "/config").then(r => r.json()).catch(() => ({}));
  if (cfg.password_required && !PASSWORD) promptPassword();
}
function promptPassword() {
  const pw = window.prompt("Enter the access password for this tool:");
  if (pw != null) { PASSWORD = pw; localStorage.setItem("cqt_pw", pw); location.reload(); }
}

/* ----------------------------------------------------------- helpers ------ */
function money(n) { return (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " SAR"; }
function num(n) { return Number(n) || 0; }
function round3(n) { return +(Number(n) || 0).toFixed(3); }
function itemById(id) { return (PB?.items || []).find(i => i.id === id); }
function catById(id) { return (PB?.categories || []).find(c => c.id === id); }
const weekDays = () => PB?.week_days || 6;
const monthDays = () => PB?.month_days || 26;

function priceOf(id) {
  const ov = CUR?.price_overrides || {};
  if (ov[id] !== undefined && ov[id] !== "" && ov[id] !== null) return num(ov[id]);
  const it = itemById(id);
  return it ? num(it.price) : 0;
}

function lineComponents(line) {
  const out = [];
  const q = num(line.qty);
  if (line.item_id) {
    const it = itemById(line.item_id);
    out.push({ label: it ? it.name_en : line.item_id, name_ar: it?.name_ar || "", qty: q, price: priceOf(line.item_id), amount: q * priceOf(line.item_id), cat: it?.category });
  }
  if (line.kind === "mdf" && (line.finish === "polish" || line.finish === "paint")) {
    const faces = num(line.finish_count) || q || 1;
    const area = num(line.length) * num(line.width) * faces;
    const pid = line.finish === "polish" ? "polish_wood" : "paint_wood";
    out.push({ label: (line.finish === "polish" ? "Polish" : "Paint") + ` (${num(line.length)}×${num(line.width)}×${faces})`, name_ar: itemById(pid)?.name_ar || "", qty: area, price: priceOf(pid), amount: area * priceOf(pid), cat: "paint_polish" });
  }
  if (line.kind === "laminated" && line.process === "cnc") {
    const pq = num(line.process_qty) || q || 1;
    out.push({ label: "CNC cutting", name_ar: itemById("cnc_cut")?.name_ar || "", qty: pq, price: priceOf("cnc_cut"), amount: pq * priceOf("cnc_cut"), cat: "edge_cnc" });
  }
  if (line.kind === "laminated" && line.process === "pvc_edge") {
    const pq = num(line.process_qty);
    out.push({ label: "PVC edge binding", name_ar: itemById("edge_pvc")?.name_ar || "", qty: pq, price: priceOf("edge_pvc"), amount: pq * priceOf("edge_pvc"), cat: "edge_cnc" });
  }
  return out;
}
function lineTotal(line) { return lineComponents(line).reduce((s, c) => s + c.amount, 0); }

function itemComponents(item) {
  const comps = [];
  (item.lines || []).forEach(l => comps.push(...lineComponents(l)));
  const days = num(item.labour_days);
  if (days > 0) {
    comps.push({ label: "Carpentry work days", name_ar: itemById("labour_day")?.name_ar || "", qty: days, price: priceOf("labour_day"), amount: days * priceOf("labour_day"), cat: "labour" });
    comps.push({ label: "Labour transport (days × 2)", name_ar: itemById("transport_labour")?.name_ar || "", qty: days * 2, price: priceOf("transport_labour"), amount: days * 2 * priceOf("transport_labour"), cat: "transport" });
  }
  const trips = num(item.material_transport_trips);
  if (trips > 0) comps.push({ label: "Material transport", name_ar: itemById("transport_mat")?.name_ar || "", qty: trips, price: priceOf("transport_mat"), amount: trips * priceOf("transport_mat"), cat: "transport" });
  return comps;
}
function itemTotal(item) { return itemComponents(item).reduce((s, c) => s + c.amount, 0); }
function projectTotal(p) { return (p.items || []).reduce((s, it) => s + itemTotal(it), 0); }
function projectDays(p) { return (p.items || []).reduce((s, it) => s + num(it.labour_days), 0); }
function categoryTotals(comps) {
  const map = {};
  comps.forEach(c => { if (c.cat) map[c.cat] = (map[c.cat] || 0) + c.amount; });
  return map;
}
function projectComponents(p) { const a = []; (p.items || []).forEach(it => a.push(...itemComponents(it))); return a; }

/* ----------------------------------------------------- dirty / autosave --- */
function touch() {
  DIRTY = true;
  updateDirtyBadge();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(autosave, 1500);
}
function updateDirtyBadge() {
  const b = $("#dirtyBadge");
  if (b) { b.textContent = DIRTY ? "● Unsaved" : "✓ Saved"; b.className = "dirty-badge" + (DIRTY ? " on" : ""); }
}
async function autosave() {
  if (!CUR || !DIRTY) return;
  try {
    const payload = JSON.parse(JSON.stringify(CUR));
    (payload.items || []).forEach(it => delete it._open);
    await api("/projects/" + CUR.id, { method: "PUT", body: JSON.stringify(payload) });
    DIRTY = false; updateDirtyBadge();
    toast("Autosaved", "ok");
  } catch (e) { toast("Autosave failed: " + e.message, "err"); }
}
window.addEventListener("beforeunload", (e) => { if (DIRTY) { e.preventDefault(); e.returnValue = ""; } });

/* ============================================================ PRICE BOOK == */
function renderPriceBook(root) {
  root.appendChild(el("h1", {}, "Price Book & Settings"));

  // Settings card
  const setp = el("div", { class: "panel" });
  setp.appendChild(el("h2", {}, "Settings"));
  PB.defaults = PB.defaults || {};
  const sf = (label, getv, setv, type = "text") => {
    const inp = el("input", { type, value: getv() ?? "", oninput: e => setv(e.target.value) });
    return el("div", { class: "field" }, el("label", {}, label), inp);
  };
  setp.appendChild(el("div", { class: "row" },
    sf("Days per week", () => PB.week_days, v => PB.week_days = num(v) || 6, "number"),
    sf("Days per month", () => PB.month_days, v => PB.month_days = num(v) || 26, "number")));
  setp.appendChild(el("div", { class: "row" },
    sf("Default company (EN)", () => PB.defaults.company_name_en, v => PB.defaults.company_name_en = v),
    sf("Default company (AR)", () => PB.defaults.company_name_ar, v => PB.defaults.company_name_ar = v)));
  root.appendChild(setp);

  // Price book
  root.appendChild(el("p", { class: "muted" }, "Base prices in SAR. These persist across sessions and feed every quote."));
  const searchInput = el("input", { class: "search", placeholder: "🔎 Search materials (English / Arabic / category)…", value: pbSearch, oninput: e => { pbSearch = e.target.value; rerenderPB(); } });
  const tools = el("div", { class: "toolbar" },
    el("button", { class: "btn primary", onclick: savePriceBook }, "Save Price Book & Settings"),
    searchInput,
    el("span", { class: "spacer" }),
    el("button", { class: "btn ghost sm", onclick: () => addPriceItem() }, "+ Add material/service"));
  const panel = el("div", { class: "panel", id: "pbPanel" });
  panel.appendChild(tools);
  const listWrap = el("div", { id: "pbList" });
  panel.appendChild(listWrap);
  root.appendChild(panel);
  buildPBList(listWrap);
}
function rerenderPB() { const w = $("#pbList"); if (w) buildPBList(w); }
function buildPBList(listWrap) {
  listWrap.innerHTML = "";
  const q = pbSearch.toLowerCase().trim();
  let shown = 0;
  for (const cat of PB.categories) {
    const items = PB.items.filter(i => i.category === cat.id && (!q ||
      (i.name_en || "").toLowerCase().includes(q) || (i.name_ar || "").includes(pbSearch) || cat.name_en.toLowerCase().includes(q)));
    if (!items.length) continue;
    shown += items.length;
    listWrap.appendChild(el("div", { class: "cat-head" }, el("h2", {}, cat.name_en), el("span", { class: "ar arabic" }, cat.name_ar)));
    const tbl = el("table");
    tbl.appendChild(el("tr", {}, el("th", {}, "Material / Service (EN)"), el("th", { class: "arabic" }, "Arabic"), el("th", {}, "Unit"), el("th", { class: "num" }, "Price (SAR)"), el("th", {}, "")));
    for (const it of items) tbl.appendChild(priceRow(it));
    listWrap.appendChild(tbl);
  }
  if (!shown) listWrap.appendChild(el("div", { class: "empty" }, "No materials match your search."));
}
function priceRow(it) {
  const enI = el("input", { value: it.name_en || "", oninput: e => it.name_en = e.target.value, style: "width:100%" });
  const arI = el("input", { class: "arabic", value: it.name_ar || "", oninput: e => it.name_ar = e.target.value, style: "width:100%" });
  const unitI = el("input", { value: it.unit || "", oninput: e => it.unit = e.target.value, style: "width:80px" });
  const priceI = el("input", { class: "price-input", type: "number", step: "0.01", value: it.price, oninput: e => it.price = e.target.value });
  const rm = el("button", { class: "btn danger sm", onclick: () => { PB.items = PB.items.filter(x => x !== it); rerenderPB(); } }, "Remove");
  return el("tr", {}, el("td", {}, enI), el("td", {}, arI), el("td", {}, unitI), el("td", { class: "num" }, priceI), el("td", {}, rm));
}
function addPriceItem() {
  const cat = PB.categories[0]?.id || "boards";
  PB.items.push({ id: "custom_" + Math.random().toString(36).slice(2, 8), category: cat, name_en: "New item", name_ar: "", unit: "piece", price: 0 });
  rerenderPB();
}
async function savePriceBook() {
  try {
    for (const it of PB.items) it.price = num(it.price);
    await api("/pricebook", { method: "PUT", body: JSON.stringify(PB) });
    toast("Price book & settings saved.");
  } catch (e) { toast("Save failed: " + e.message, "err"); }
}

/* ============================================================== PROJECTS == */
async function renderProjects(root) {
  root.appendChild(el("h1", {}, "Projects"));
  root.appendChild(el("div", { class: "toolbar" }, el("button", { class: "btn primary", onclick: newProject }, "+ New Client Project")));
  const list = await api("/projects");
  if (!list.length) { root.appendChild(el("div", { class: "empty" }, "No projects yet. Create your first client project.")); return; }
  const grid = el("div", { class: "cardlist" });
  for (const p of list) {
    grid.appendChild(el("div", { class: "card", onclick: () => openProject(p.id) },
      el("div", { class: "ttl" }, p.unit_name || p.client_name_en || "Untitled"),
      el("div", { class: "sub" }, p.client_name_en || ""),
      el("div", { class: "sub arabic" }, p.client_name_ar || ""),
      el("div", { class: "meta" }, `${p.items} item(s) · updated ${p.updated || "-"}`)));
  }
  root.appendChild(grid);
}
async function newProject() {
  const body = { unit_name: "New Project" };
  if (PB?.defaults) { body.company_name_en = PB.defaults.company_name_en || ""; body.company_name_ar = PB.defaults.company_name_ar || ""; }
  const p = await api("/projects", { method: "POST", body: JSON.stringify(body) });
  openProject(p.id);
}
async function openProject(id) {
  CUR = await api("/projects/" + id);
  CUR.items = CUR.items || [];
  CUR.price_overrides = CUR.price_overrides || {};
  VIEW = "editor"; MODE = "edit"; DIRTY = false;
  render();
}

/* ============================================================== EDITOR ==== */
function renderEditor(root) {
  const p = CUR;
  // sticky toolbar with breadcrumb + dirty + actions
  const bar = el("div", { class: "editbar" },
    el("div", { class: "crumbs" },
      el("a", { class: "crumb", onclick: () => { if (guardLeave()) { VIEW = "projects"; CUR = null; render(); } } }, "Projects"),
      el("span", { class: "crumb-sep" }, "›"),
      el("span", { class: "crumb cur" }, p.unit_name || p.client_name_en || "Project")),
    el("span", { id: "dirtyBadge", class: "dirty-badge" }, "✓ Saved"),
    el("span", { class: "spacer" }),
    el("div", { class: "seg" },
      el("button", { class: "seg-btn" + (MODE === "edit" ? " on" : ""), onclick: () => { MODE = "edit"; render(); } }, "✎ Edit"),
      el("button", { class: "seg-btn" + (MODE === "preview" ? " on" : ""), onclick: () => { MODE = "preview"; render(); } }, "👁 Preview")),
    el("button", { class: "btn ghost", onclick: () => { if (confirm("Delete this project?")) deleteProject(); } }, "Delete"),
    el("button", { class: "btn primary", onclick: saveProject }, "Save"),
    el("button", { class: "btn accent", onclick: generate }, "⤓ Generate Excel"));
  root.appendChild(bar);
  updateDirtyBadge();

  root.appendChild(renderDashboard(p));

  if (MODE === "preview") { root.appendChild(renderPreview(p)); return; }

  // validation checklist
  const issues = projectIssues(p);
  root.appendChild(renderChecklist(issues));

  // metadata
  const meta = el("div", { class: "panel" });
  meta.appendChild(el("h2", {}, "Project Details"));
  const f = (label, key, ar = false) => {
    const inp = el("input", { class: ar ? "arabic" : "", value: p[key] || "", oninput: e => { p[key] = e.target.value; touch(); } });
    return el("div", { class: "field" }, el("label", {}, label), inp);
  };
  meta.appendChild(el("div", { class: "row" }, f("Company name (EN)", "company_name_en"), f("Company name (AR)", "company_name_ar", true)));
  meta.appendChild(el("div", { class: "row" }, f("Client name (EN)", "client_name_en"), f("Client name (AR)", "client_name_ar", true)));
  meta.appendChild(el("div", { class: "row" }, f("Unit / Project name", "unit_name"), f("Location", "location")));
  root.appendChild(meta);

  root.appendChild(renderOverrides(p));

  // items toolbar
  root.appendChild(el("div", { class: "toolbar" },
    el("h2", {}, "Items (one sheet each)"),
    el("span", { class: "spacer" }),
    el("button", { class: "btn ghost sm", onclick: () => { p.items.forEach(i => i._open = false); render(); } }, "Collapse all"),
    el("button", { class: "btn ghost sm", onclick: () => { p.items.forEach(i => i._open = true); render(); } }, "Expand all"),
    el("button", { class: "btn ghost sm", onclick: addItem }, "+ Add item")));

  if (!p.items.length) root.appendChild(el("div", { class: "empty" }, "No items yet. Add the first piece (TV wall, wardrobe, ...)."));
  p.items.forEach((item, idx) => root.appendChild(renderItem(item, idx)));
}

function guardLeave() {
  if (!DIRTY) return true;
  return confirm("You have unsaved changes. Leave anyway?");
}

function renderDashboard(p) {
  const total = projectTotal(p), days = projectDays(p);
  const stat = (val, lbl) => el("div", { class: "stat" }, el("div", { class: "stat-val" }, val), el("div", { class: "stat-lbl" }, lbl));
  return el("div", { class: "dashboard" },
    stat(money(total), "Project total"),
    stat(String(p.items.length), "Items / sheets"),
    stat(String(round3(days)), "Work days"),
    stat((days / weekDays()).toFixed(1), `Weeks (${weekDays()}d)`),
    stat((days / monthDays()).toFixed(1), `Months (${monthDays()}d)`));
}

function renderChecklist(issues) {
  const panel = el("div", { class: "panel checklist " + (issues.length ? "has-issues" : "ok") });
  if (!issues.length) {
    panel.appendChild(el("div", { class: "chk-ok" }, "✓ Everything looks good — ready to generate."));
    return panel;
  }
  panel.appendChild(el("h2", {}, `⚠ ${issues.length} thing(s) to review`));
  const ul = el("ul", { class: "chk-list" });
  issues.forEach(i => ul.appendChild(el("li", {}, i)));
  panel.appendChild(ul);
  return panel;
}
function projectIssues(p) {
  const out = [];
  if (!(p.client_name_en || p.client_name_ar)) out.push("Client name is empty.");
  if (!(p.company_name_en || p.company_name_ar)) out.push("Company name is empty.");
  if (!p.items.length) out.push("No items added yet.");
  p.items.forEach((it, i) => {
    const label = it.name_en || it.name_ar || `Item ${i + 1}`;
    const lines = it.lines || [];
    if (!lines.length && !num(it.labour_days) && !num(it.material_transport_trips)) out.push(`“${label}” has no materials or labour.`);
    lines.forEach((l, li) => { if (!l.item_id) out.push(`“${label}” line ${li + 1} has no material selected.`); });
    if (it.kind === undefined) {}
  });
  return out;
}

function renderOverrides(p) {
  const panel = el("div", { class: "panel" });
  const count = Object.keys(p.price_overrides || {}).length;
  const body = el("div", { style: count ? "" : "display:none" });
  panel.appendChild(el("div", { class: "toolbar" },
    el("h2", {}, "Client-specific price overrides" + (count ? ` (${count})` : "")),
    el("span", { class: "spacer" }),
    el("button", { class: "btn ghost sm", onclick: () => { body.style.display = body.style.display === "none" ? "" : "none"; } }, "Show / hide")));
  panel.appendChild(el("p", { class: "hint" }, "Leave blank to use the base Price Book value. Set a number to override it for THIS client only."));
  const tbl = el("table");
  tbl.appendChild(el("tr", {}, el("th", {}, "Material/Service"), el("th", {}, "Base"), el("th", {}, "Override (SAR)")));
  for (const it of PB.items) {
    const ovI = el("input", { class: "price-input", type: "number", step: "0.01", value: (p.price_overrides[it.id] ?? "") });
    ovI.addEventListener("input", e => {
      const v = e.target.value;
      if (v === "") delete p.price_overrides[it.id]; else p.price_overrides[it.id] = num(v);
      refreshTotals(); touch();
    });
    const nameTd = el("td", {}, it.name_en + "  ", el("span", { class: "arabic muted" }, it.name_ar || ""));
    tbl.appendChild(el("tr", {}, nameTd, el("td", { class: "muted" }, money(it.price)), el("td", {}, ovI)));
  }
  body.appendChild(tbl);
  panel.appendChild(body);
  return panel;
}

function addItem() {
  CUR.items.push({ name_en: "New item", name_ar: "", place_ar: "", time: "", note_ar: "", image: "", labour_days: 0, material_transport_trips: 0, lines: [], _open: true });
  touch(); render();
}
function duplicateItem(idx) {
  const copy = JSON.parse(JSON.stringify(CUR.items[idx]));
  copy.name_en = (copy.name_en || "Item") + " (copy)"; copy._open = false;
  CUR.items.splice(idx + 1, 0, copy); touch(); render();
}
function moveItem(idx, dir) {
  const j = idx + dir;
  if (j < 0 || j >= CUR.items.length) return;
  [CUR.items[idx], CUR.items[j]] = [CUR.items[j], CUR.items[idx]];
  touch(); render();
}

function renderItem(item, idx) {
  const block = el("div", { class: "item-block" + (item._open ? " open" : "") });
  const chips = el("span", { class: "chips" },
    el("span", { class: "chip" }, `${(item.lines || []).length} mat.`),
    el("span", { class: "chip" }, `${round3(num(item.labour_days))} d`));
  const head = el("div", { class: "item-head", onclick: () => { item._open = !item._open; render(); } },
    el("span", { class: "chev" }, "▶"),
    el("span", { class: "name" }, (item.name_en || "Item")),
    el("span", { class: "arabic muted" }, item.name_ar || ""),
    chips,
    el("span", { class: "tot" }, money(itemTotal(item))),
    el("span", { class: "item-actions" },
      el("button", { class: "btn ghost xs", title: "Move up", onclick: stop(() => moveItem(idx, -1)) }, "↑"),
      el("button", { class: "btn ghost xs", title: "Move down", onclick: stop(() => moveItem(idx, 1)) }, "↓"),
      el("button", { class: "btn ghost xs", title: "Duplicate", onclick: stop(() => duplicateItem(idx)) }, "⧉"),
      el("button", { class: "btn danger xs", title: "Delete", onclick: stop(() => { if (confirm("Delete this item?")) { CUR.items.splice(idx, 1); touch(); render(); } }) }, "✕")));
  block.appendChild(head);

  const body = el("div", { class: "item-body" });
  const fi = (label, key, ar = false) => {
    const inp = el("input", { class: ar ? "arabic" : "", value: item[key] || "" });
    inp.addEventListener("input", e => { item[key] = e.target.value; touch(); });
    return el("div", { class: "field" }, el("label", {}, label), inp);
  };
  body.appendChild(el("div", { class: "row" }, fi("Item name (EN)", "name_en"), fi("Item name (AR)", "name_ar", true)));
  const timeI = el("input", { type: "time", value: item.time || "" });
  timeI.addEventListener("input", e => { item.time = e.target.value; touch(); });
  body.appendChild(el("div", { class: "row" }, fi("Location / place (AR)", "place_ar", true), el("div", { class: "field" }, el("label", {}, "Time"), timeI)));
  body.appendChild(el("div", { class: "row" }, fi("Note (AR, optional)", "note_ar", true)));

  // image
  const imgField = el("div", { class: "field" });
  imgField.appendChild(el("label", {}, "Item photo"));
  const imgRow = el("div", { class: "row", style: "align-items:center" });
  if (item.image) imgRow.appendChild(el("img", { class: "thumb", src: item.image.startsWith("http") ? item.image : "/" + item.image }));
  imgRow.appendChild(el("input", { type: "file", accept: "image/*", onchange: e => uploadItemImage(item, e.target.files[0]) }));
  if (item.image) imgRow.appendChild(el("button", { class: "btn danger sm", onclick: () => { item.image = ""; touch(); render(); } }, "Remove photo"));
  imgField.appendChild(imgRow);
  body.appendChild(imgField);

  const daysI = el("input", { type: "number", step: "0.5", min: "0", value: item.labour_days || 0 });
  daysI.addEventListener("input", e => { item.labour_days = e.target.value; refreshTotals(); touch(); });
  const tripsI = el("input", { type: "number", step: "1", min: "0", value: item.material_transport_trips || 0 });
  tripsI.addEventListener("input", e => { item.material_transport_trips = e.target.value; refreshTotals(); touch(); });
  body.appendChild(el("div", { class: "row" },
    el("div", { class: "field" }, el("label", {}, "Labour work days"), daysI),
    el("div", { class: "field" }, el("label", {}, "Material transport trips"), tripsI)));
  body.appendChild(el("p", { class: "hint" }, "Labour transport (limousine) is auto-calculated as work days × 2."));

  body.appendChild(el("h3", {}, "Materials & services"));
  item.lines = item.lines || [];
  if (!item.lines.length) body.appendChild(el("p", { class: "hint warn" }, "⚠ No materials yet — add at least one line."));
  item.lines.forEach((line, li) => body.appendChild(renderLine(item, line, li)));
  body.appendChild(el("button", { class: "btn ghost sm", onclick: () => { item.lines.push({ kind: "simple", item_id: "", qty: 1 }); touch(); render(); } }, "+ Add material line"));

  body.appendChild(renderItemBreakdown(item));
  block.appendChild(body);
  return block;
}

function renderItemBreakdown(item) {
  const wrap = el("div", { class: "breakdown" });
  wrap.appendChild(el("h3", {}, "Calculation breakdown"));
  const comps = itemComponents(item);
  if (!comps.length) { wrap.appendChild(el("p", { class: "hint" }, "Add materials to see the breakdown.")); return wrap; }
  const tbl = el("table", { class: "calc-table" });
  tbl.appendChild(el("tr", {}, el("th", {}, "Component"), el("th", { class: "num" }, "Qty"), el("th", { class: "num" }, "Unit price"), el("th", { class: "num" }, "Amount")));
  for (const c of comps) {
    tbl.appendChild(el("tr", {}, el("td", {}, c.label), el("td", { class: "num" }, String(round3(c.qty))), el("td", { class: "num" }, money(c.price)), el("td", { class: "num" }, el("strong", {}, money(c.amount)))));
  }
  tbl.appendChild(el("tr", { class: "calc-total" }, el("td", { colspan: "3" }, "Item total"), el("td", { class: "num" }, money(itemTotal(item)))));
  wrap.appendChild(tbl);
  wrap.appendChild(catBars(categoryTotals(comps), itemTotal(item)));
  return wrap;
}

function catBars(cats, total) {
  total = total || 1;
  const keys = Object.keys(cats).sort((a, b) => cats[b] - cats[a]);
  return el("div", { class: "cat-bars" }, ...keys.map(k => {
    const pct = (cats[k] / total) * 100;
    return el("div", { class: "cat-bar" },
      el("div", { class: "cat-bar-label" }, (catById(k)?.name_en || k), el("span", { class: "muted" }, "  " + money(cats[k]) + " · " + pct.toFixed(1) + "%")),
      el("div", { class: "cat-bar-track" }, el("div", { class: "cat-bar-fill", style: `width:${pct}%` })));
  }));
}

/* ---------------------------------------------------- searchable combo ---- */
function displayName(id) { const it = itemById(id); return it ? `${it.name_en} (${it.unit})` : ""; }
function materialCombo(line, onSelect) {
  const wrap = el("div", { class: "combo" });
  const input = el("input", { class: "combo-input", placeholder: "Search material…", value: displayName(line.item_id) });
  const menu = el("div", { class: "combo-menu" });
  function build(q) {
    menu.innerHTML = "";
    const ql = (q || "").toLowerCase();
    let n = 0;
    for (const cat of PB.categories) {
      const items = PB.items.filter(i => i.category === cat.id && (!ql ||
        (i.name_en || "").toLowerCase().includes(ql) || (i.name_ar || "").includes(q) || cat.name_en.toLowerCase().includes(ql)));
      if (!items.length) continue;
      menu.appendChild(el("div", { class: "combo-group" }, cat.name_en));
      for (const it of items) {
        n++;
        const opt = el("div", { class: "combo-opt" + (line.item_id === it.id ? " sel" : "") },
          el("span", {}, it.name_en), el("span", { class: "arabic muted" }, "  " + (it.name_ar || "")),
          el("span", { class: "combo-meta" }, `${it.unit} · ${money(it.price)}`));
        opt.addEventListener("mousedown", (e) => {
          e.preventDefault();
          line.item_id = it.id; input.value = displayName(it.id);
          menu.classList.remove("open"); onSelect();
        });
        menu.appendChild(opt);
      }
    }
    if (!n) menu.appendChild(el("div", { class: "combo-empty" }, "No match"));
  }
  input.addEventListener("focus", () => { build(""); input.select(); menu.classList.add("open"); });
  input.addEventListener("input", () => { build(input.value); menu.classList.add("open"); });
  input.addEventListener("blur", () => setTimeout(() => menu.classList.remove("open"), 150));
  wrap.appendChild(input); wrap.appendChild(menu);
  return wrap;
}

function renderLine(item, line, li) {
  const wrap = el("div", { class: "line" });
  const kindSel = el("select");
  for (const k of [["simple", "Simple"], ["mdf", "MDF/Veneer"], ["laminated", "Laminated"]])
    kindSel.appendChild(el("option", { value: k[0], selected: line.kind === k[0] }, k[1]));
  kindSel.addEventListener("change", e => { line.kind = e.target.value; touch(); render(); });
  wrap.appendChild(el("div", { class: "field" }, el("label", {}, "Type"), kindSel));

  const matField = el("div", { class: "field grow" }, el("label", {}, "Material / service"), materialCombo(line, () => { touch(); render(); }));
  const unit = itemById(line.item_id)?.unit;
  if (unit) matField.appendChild(el("span", { class: "unit-hint" }, "priced per " + unit + " · " + money(priceOf(line.item_id))));
  wrap.appendChild(matField);

  const qtyI = el("input", { type: "number", step: "0.001", value: line.qty ?? 1 });
  qtyI.addEventListener("input", e => { line.qty = e.target.value; refreshTotals(); touch(); });
  wrap.appendChild(el("div", { class: "field" }, el("label", {}, "Qty"), qtyI));

  wrap.appendChild(el("div", { class: "line-end" },
    el("span", { class: "linetotal" }, money(lineTotal(line))),
    el("button", { class: "btn ghost xs", title: "Duplicate line", onclick: () => { item.lines.splice(li + 1, 0, JSON.parse(JSON.stringify(line))); touch(); render(); } }, "⧉"),
    el("button", { class: "btn danger xs", title: "Remove line", onclick: () => { item.lines.splice(li, 1); touch(); render(); } }, "✕")));

  if (line.kind === "mdf") {
    const opts = el("div", { class: "sub-opts" });
    const nf = (label, key) => {
      const inp = el("input", { type: "number", step: "0.01", value: line[key] ?? "" });
      inp.addEventListener("input", e => { line[key] = e.target.value; refreshTotals(); touch(); });
      return el("div", { class: "field" }, el("label", {}, label), inp);
    };
    opts.appendChild(nf("Length (m)", "length"));
    opts.appendChild(nf("Width (m)", "width"));
    opts.appendChild(nf("Thickness (mm)", "thickness"));
    const fin = el("select");
    for (const o of [["none", "No finish"], ["polish", "Polish"], ["paint", "Paint"]])
      fin.appendChild(el("option", { value: o[0], selected: (line.finish || "none") === o[0] }, o[1]));
    fin.addEventListener("change", e => { line.finish = e.target.value; touch(); render(); });
    opts.appendChild(el("div", { class: "field" }, el("label", {}, "Finish"), fin));
    opts.appendChild(nf("Finish faces (count)", "finish_count"));
    opts.appendChild(el("div", { class: "field" }, el("label", {}, " "), el("span", { class: "hint" }, "Area = L × W × faces, priced per m².")));
    wrap.appendChild(opts);
  } else if (line.kind === "laminated") {
    const opts = el("div", { class: "sub-opts" });
    const proc = el("select");
    for (const o of [["none", "No process"], ["cnc", "CNC cutting"], ["pvc_edge", "PVC edge binding"]])
      proc.appendChild(el("option", { value: o[0], selected: (line.process || "none") === o[0] }, o[1]));
    proc.addEventListener("change", e => { line.process = e.target.value; touch(); render(); });
    opts.appendChild(el("div", { class: "field" }, el("label", {}, "Process"), proc));
    if (line.process && line.process !== "none") {
      const pq = el("input", { type: "number", step: "0.01", value: line.process_qty ?? "" });
      pq.addEventListener("input", e => { line.process_qty = e.target.value; refreshTotals(); touch(); });
      opts.appendChild(el("div", { class: "field" }, el("label", {}, line.process === "pvc_edge" ? "Edge length (m)" : "Process qty"), pq));
    }
    wrap.appendChild(opts);
  }
  return wrap;
}

/* ---------------------------------------------------------- preview ------- */
function renderPreview(p) {
  const wrap = el("div", { class: "preview" });
  // header
  wrap.appendChild(el("div", { class: "pv-head" },
    el("div", { class: "pv-co" }, p.company_name_ar || p.company_name_en || "—"),
    el("div", { class: "pv-sub" }, "عرض سعر أعمال النجارة و الديكور الداخلي"),
    el("div", { class: "pv-meta" },
      el("span", {}, "العميل: " + (p.client_name_ar || p.client_name_en || "—")),
      el("span", {}, "الوحدة: " + (p.unit_name || "—")),
      el("span", {}, "الموقع: " + (p.location || "—")),
      el("span", {}, "التاريخ: " + new Date().toISOString().slice(0, 10)))));

  if (!p.items.length) { wrap.appendChild(el("div", { class: "empty" }, "No items to preview yet.")); return wrap; }

  // each item
  p.items.forEach((item, i) => {
    const comps = itemComponents(item);
    const card = el("div", { class: "pv-item" });
    const head = el("div", { class: "pv-item-head" },
      el("div", {},
        el("div", { class: "pv-item-title" }, `${i + 1}. ` + (item.name_ar || item.name_en || "بند")),
        el("div", { class: "pv-item-place muted" }, item.place_ar || "")),
      el("div", { class: "pv-item-total" }, money(itemTotal(item))));
    card.appendChild(head);
    if (item.image) card.appendChild(el("img", { class: "pv-img", src: item.image.startsWith("http") ? item.image : "/" + item.image }));
    const tbl = el("table", { class: "pv-table arabic" });
    tbl.appendChild(el("tr", {}, el("th", {}, "قيمة الإجمالي"), el("th", {}, "السعر"), el("th", {}, "العدد"), el("th", {}, "البند")));
    comps.forEach(c => tbl.appendChild(el("tr", {},
      el("td", {}, money(c.amount)), el("td", {}, money(c.price)), el("td", {}, String(round3(c.qty))), el("td", {}, c.name_ar || c.label))));
    tbl.appendChild(el("tr", { class: "pv-total-row" }, el("td", {}, money(itemTotal(item))), el("td", { colspan: "3" }, "إجمالي تكلفة البند")));
    card.appendChild(tbl);
    if (item.note_ar) card.appendChild(el("div", { class: "pv-note" }, "ملاحظة - " + item.note_ar));
    wrap.appendChild(card);
  });

  // project summary
  const days = projectDays(p);
  const sum = el("div", { class: "pv-summary" });
  sum.appendChild(el("h3", {}, "ملخص المشروع"));
  const stbl = el("table", { class: "pv-table arabic" });
  stbl.appendChild(el("tr", {}, el("th", {}, "أيام العمل"), el("th", {}, "إجمالي التكلفة"), el("th", {}, "البند"), el("th", {}, "#")));
  p.items.forEach((it, i) => stbl.appendChild(el("tr", {},
    el("td", {}, String(round3(num(it.labour_days)))), el("td", {}, money(itemTotal(it))), el("td", {}, it.name_ar || it.name_en || "بند"), el("td", {}, String(i + 1)))));
  stbl.appendChild(el("tr", { class: "pv-total-row" }, el("td", {}, String(round3(days))), el("td", {}, money(projectTotal(p))), el("td", { colspan: "2" }, "الإجمالي الكلي للمشروع")));
  sum.appendChild(stbl);
  wrap.appendChild(sum);

  // project-wide category breakdown
  const pcat = el("div", { class: "pv-summary" });
  pcat.appendChild(el("h3", {}, "تفصيل التكلفة حسب التصنيف (المشروع كامل)"));
  pcat.appendChild(catBars(categoryTotals(projectComponents(p)), projectTotal(p)));
  wrap.appendChild(pcat);

  // timeline
  const tl = el("div", { class: "pv-summary" });
  tl.appendChild(el("h3", {}, "المدة الزمنية التقديرية"));
  tl.appendChild(el("div", { class: "pv-timeline" },
    el("div", { class: "tl-box" }, el("b", {}, round3(days)), el("span", {}, "أيام")),
    el("div", { class: "tl-box" }, el("b", {}, (days / weekDays()).toFixed(1)), el("span", {}, `أسابيع (${weekDays()} يوم)`)),
    el("div", { class: "tl-box" }, el("b", {}, (days / monthDays()).toFixed(1)), el("span", {}, `أشهر (${monthDays()} يوم)`))));
  wrap.appendChild(tl);
  return wrap;
}

/* ------------------------------------------------ live total refresh ------ */
function refreshTotals() {
  document.querySelectorAll(".item-block").forEach((block, i) => {
    const item = CUR.items[i];
    if (!item) return;
    const tot = block.querySelector(".item-head .tot");
    if (tot) tot.textContent = money(itemTotal(item));
    block.querySelectorAll(".line").forEach((lineEl, li) => {
      const span = lineEl.querySelector(".linetotal");
      if (span && item.lines[li]) span.textContent = money(lineTotal(item.lines[li]));
    });
  });
  const stats = document.querySelectorAll(".dashboard .stat-val");
  if (stats.length >= 5) {
    const days = projectDays(CUR);
    stats[0].textContent = money(projectTotal(CUR));
    stats[1].textContent = String(CUR.items.length);
    stats[2].textContent = String(round3(days));
    stats[3].textContent = (days / weekDays()).toFixed(1);
    stats[4].textContent = (days / monthDays()).toFixed(1);
  }
}

async function uploadItemImage(item, file) {
  if (!file) return;
  try {
    toast("Uploading image…");
    const fd = new FormData();
    fd.append("file", file);
    const headers = {};
    if (PASSWORD) headers["X-App-Password"] = PASSWORD;
    const res = await fetch(API + "/upload", { method: "POST", body: fd, headers });
    if (!res.ok) throw new Error("upload failed");
    const data = await res.json();
    item.image = data.path; touch();
    toast("Image uploaded.");
    render();
  } catch (e) { toast("Image upload failed: " + e.message, "err"); }
}

function stripUI(p) { (p.items || []).forEach(it => delete it._open); }
async function saveProject() {
  try {
    clearTimeout(saveTimer);
    const payload = JSON.parse(JSON.stringify(CUR)); stripUI(payload);
    await api("/projects/" + CUR.id, { method: "PUT", body: JSON.stringify(payload) });
    DIRTY = false; updateDirtyBadge();
    toast("Project saved.");
  } catch (e) { toast("Save failed: " + e.message, "err"); }
}
async function deleteProject() {
  try {
    await api("/projects/" + CUR.id, { method: "DELETE" });
    DIRTY = false; VIEW = "projects"; CUR = null; render();
    toast("Project deleted.");
  } catch (e) { toast("Delete failed: " + e.message, "err"); }
}
async function generate() {
  try {
    clearTimeout(saveTimer);
    const payload = JSON.parse(JSON.stringify(CUR)); stripUI(payload);
    await api("/projects/" + CUR.id, { method: "PUT", body: JSON.stringify(payload) });
    DIRTY = false; updateDirtyBadge();
    toast("Generating Excel…");
    const headers = {};
    if (PASSWORD) headers["X-App-Password"] = PASSWORD;
    const res = await fetch(`${API}/projects/${CUR.id}/generate`, { headers });
    if (!res.ok) throw new Error("generation failed");
    const blob = await res.blob();
    const cd = res.headers.get("content-disposition") || "";
    const m = cd.match(/filename="?([^"]+)"?/);
    const name = m ? m[1] : "quote.xlsx";
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("Excel downloaded.");
  } catch (e) { toast("Generate failed: " + e.message, "err"); }
}

/* ================================================================ ROUTER == */
async function render() {
  const root = $("#app");
  root.innerHTML = "";
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === (VIEW === "editor" ? "projects" : VIEW)));
  try {
    if (!PB) PB = await api("/pricebook");
    if (VIEW === "pricebook") renderPriceBook(root);
    else if (VIEW === "editor" && CUR) renderEditor(root);
    else await renderProjects(root);
  } catch (e) {
    root.appendChild(el("div", { class: "empty" }, "Error: " + e.message));
  }
}

document.querySelectorAll(".tab").forEach(tab =>
  tab.addEventListener("click", () => {
    if (VIEW === "editor" && !guardLeave()) return;
    VIEW = tab.dataset.view; CUR = null; render();
  }));

(async function init() {
  await ensureAuth();
  render();
})();
