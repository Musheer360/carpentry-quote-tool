/* Carpentry Quote Tool - frontend (English UI). Talks to /api/*. */
"use strict";

const API = "/api";
let PB = null;            // price book {categories, items}
let CUR = null;           // currently open project (full)
let VIEW = "projects";
let PASSWORD = localStorage.getItem("cqt_pw") || "";

const $ = (sel, el = document) => el.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null) continue;
    n.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  }
  return n;
};

function setStatus(msg, isErr = false) {
  const s = $("#status");
  if (!s) return;
  s.textContent = msg || "";
  s.className = "status" + (isErr ? " err" : "");
  if (msg && !isErr) setTimeout(() => { if (s.textContent === msg) s.textContent = ""; }, 3000);
}

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

/* ---------------------------------------------------------------- auth ---- */
async function ensureAuth() {
  const cfg = await fetch(API + "/config").then(r => r.json()).catch(() => ({}));
  if (cfg.password_required && !PASSWORD) promptPassword();
}
function promptPassword() {
  const pw = window.prompt("Enter the access password for this tool:");
  if (pw != null) { PASSWORD = pw; localStorage.setItem("cqt_pw", pw); location.reload(); }
}

/* --------------------------------------------------------------- helpers -- */
function money(n) { return (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " SAR"; }
function itemById(id) { return (PB?.items || []).find(i => i.id === id); }
function catName(id) { const c = (PB?.categories || []).find(c => c.id === id); return c ? c.name_en : id; }

/* compute a live preview of a project item's total (mirrors generator logic) */
function priceOf(id) {
  const ov = CUR?.price_overrides || {};
  if (ov[id] !== undefined && ov[id] !== "" && ov[id] !== null) return Number(ov[id]) || 0;
  const it = itemById(id);
  return it ? Number(it.price) || 0 : 0;
}
function lineTotal(line) {
  let t = 0;
  const q = Number(line.qty) || 0;
  if (line.item_id) t += q * priceOf(line.item_id);
  if (line.kind === "mdf" && (line.finish === "polish" || line.finish === "paint")) {
    const area = (Number(line.length) || 0) * (Number(line.width) || 0) * (Number(line.finish_count) || q || 1);
    t += area * priceOf(line.finish === "polish" ? "polish_wood" : "paint_wood");
  }
  if (line.kind === "laminated" && line.process === "cnc")
    t += (Number(line.process_qty) || q || 1) * priceOf("cnc_cut");
  if (line.kind === "laminated" && line.process === "pvc_edge")
    t += (Number(line.process_qty) || 0) * priceOf("edge_pvc");
  return t;
}
function itemTotal(item) {
  let t = (item.lines || []).reduce((s, l) => s + lineTotal(l), 0);
  const days = Number(item.labour_days) || 0;
  if (days > 0) t += days * priceOf("labour_day") + days * 2 * priceOf("transport_labour");
  const trips = Number(item.material_transport_trips) || 0;
  if (trips > 0) t += trips * priceOf("transport_mat");
  return t;
}

/* ============================================================ PRICE BOOK == */
function renderPriceBook(root) {
  root.appendChild(el("h1", {}, "Price Book"));
  root.appendChild(el("p", { class: "muted" },
    "Base prices in SAR. These persist across sessions and feed every quote. Edit a price and click Save."));

  const panel = el("div", { class: "panel" });
  const toolbar = el("div", { class: "toolbar" },
    el("button", { class: "btn primary", onclick: savePriceBook }, "Save Price Book"),
    el("span", { class: "spacer" }),
    el("button", { class: "btn ghost sm", onclick: () => addPriceItem() }, "+ Add material/service"));
  panel.appendChild(toolbar);

  for (const cat of PB.categories) {
    const items = PB.items.filter(i => i.category === cat.id);
    panel.appendChild(el("div", { class: "cat-head" },
      el("h2", {}, cat.name_en), el("span", { class: "ar arabic" }, cat.name_ar)));
    const tbl = el("table");
    tbl.appendChild(el("tr", {},
      el("th", {}, "Material / Service (EN)"),
      el("th", { class: "arabic" }, "Arabic"),
      el("th", {}, "Unit"),
      el("th", { class: "num" }, "Price (SAR)"),
      el("th", {}, "")));
    for (const it of items) tbl.appendChild(priceRow(it));
    panel.appendChild(tbl);
  }
  root.appendChild(panel);
}

function priceRow(it) {
  return el("tr", {},
    el("td", {}, el("input", { value: it.name_en || "", oninput: e => it.name_en = e.target.value, style: "width:100%" })),
    el("td", {}, el("input", { class: "arabic", value: it.name_ar || "", oninput: e => it.name_ar = e.target.value, style: "width:100%" })),
    el("td", {}, el("input", { value: it.unit || "", oninput: e => it.unit = e.target.value, style: "width:80px" })),
    el("td", { class: "num" }, el("input", { class: "price-input", type: "number", step: "0.01", value: it.price, oninput: e => it.price = e.target.value })),
    el("td", {}, el("button", { class: "btn danger sm", onclick: () => { PB.items = PB.items.filter(x => x !== it); render(); } }, "Remove")));
}

function addPriceItem() {
  const cat = PB.categories[0]?.id || "boards";
  PB.items.push({ id: "custom_" + Math.random().toString(36).slice(2, 8), category: cat, name_en: "New item", name_ar: "", unit: "piece", price: 0 });
  render();
}

async function savePriceBook() {
  try {
    for (const it of PB.items) it.price = Number(it.price) || 0;
    await api("/pricebook", { method: "PUT", body: JSON.stringify(PB) });
    setStatus("Price book saved.");
  } catch (e) { setStatus("Save failed: " + e.message, true); }
}

/* ============================================================== PROJECTS == */
async function renderProjects(root) {
  root.appendChild(el("h1", {}, "Projects"));
  const bar = el("div", { class: "toolbar" },
    el("button", { class: "btn primary", onclick: newProject }, "+ New Client Project"));
  root.appendChild(bar);

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
  const p = await api("/projects", { method: "POST", body: JSON.stringify({ unit_name: "New Project" }) });
  openProject(p.id);
}
async function openProject(id) {
  CUR = await api("/projects/" + id);
  CUR.items = CUR.items || [];
  CUR.price_overrides = CUR.price_overrides || {};
  VIEW = "editor";
  render();
}

/* ============================================================== EDITOR ==== */
function renderEditor(root) {
  const p = CUR;
  root.appendChild(el("div", { class: "toolbar" },
    el("button", { class: "btn ghost", onclick: () => { VIEW = "projects"; CUR = null; render(); } }, "← Back"),
    el("span", { class: "spacer" }),
    el("button", { class: "btn ghost", onclick: () => { if (confirm("Delete this project?")) deleteProject(); } }, "Delete"),
    el("button", { class: "btn primary", onclick: saveProject }, "Save"),
    el("button", { class: "btn accent", onclick: generate }, "⤓ Generate Excel")));

  // metadata
  const meta = el("div", { class: "panel" });
  meta.appendChild(el("h2", {}, "Project Details"));
  const f = (label, key, ar = false, ph = "") => el("div", { class: "field" },
    el("label", {}, label),
    el("input", { class: ar ? "arabic" : "", value: p[key] || "", placeholder: ph, oninput: e => p[key] = e.target.value }));
  meta.appendChild(el("div", { class: "row" },
    f("Company name (EN)", "company_name_en"), f("Company name (AR)", "company_name_ar", true)));
  meta.appendChild(el("div", { class: "row" },
    f("Client name (EN)", "client_name_en"), f("Client name (AR)", "client_name_ar", true)));
  meta.appendChild(el("div", { class: "row" },
    f("Unit / Project name", "unit_name"), f("Location", "location")));
  root.appendChild(meta);

  // price overrides (optional)
  root.appendChild(renderOverrides(p));

  // items
  const head = el("div", { class: "toolbar" },
    el("h2", {}, "Items (one sheet each)"),
    el("span", { class: "spacer" }),
    el("span", { class: "pill" }, "Project total: " + money(p.items.reduce((s, it) => s + itemTotal(it), 0))),
    el("button", { class: "btn ghost sm", onclick: addItem }, "+ Add item"));
  root.appendChild(head);

  if (!p.items.length) root.appendChild(el("div", { class: "empty" }, "No items yet. Add the first piece (TV wall, wardrobe, ...)."));
  p.items.forEach((item, idx) => root.appendChild(renderItem(item, idx)));
}

function renderOverrides(p) {
  const panel = el("div", { class: "panel" });
  const open = !!Object.keys(p.price_overrides || {}).length;
  const body = el("div", { style: open ? "" : "display:none" });
  const toggle = el("button", { class: "btn ghost sm", onclick: () => { body.style.display = body.style.display === "none" ? "" : "none"; } },
    "Toggle");
  panel.appendChild(el("div", { class: "toolbar" },
    el("h2", {}, "Client-specific price overrides"),
    el("span", { class: "spacer" }), toggle));
  panel.appendChild(el("p", { class: "hint" }, "Leave blank to use the base Price Book value. Set a number to override it for THIS client only."));
  const tbl = el("table");
  tbl.appendChild(el("tr", {}, el("th", {}, "Material/Service"), el("th", {}, "Base"), el("th", {}, "Override (SAR)")));
  for (const it of PB.items) {
    tbl.appendChild(el("tr", {},
      el("td", {}, it.name_en + "  ", el("span", { class: "arabic muted" }, it.name_ar || "")),
      el("td", { class: "muted" }, money(it.price)),
      el("td", {}, el("input", {
        class: "price-input", type: "number", step: "0.01",
        value: (p.price_overrides[it.id] ?? ""),
        oninput: e => {
          const v = e.target.value;
          if (v === "") delete p.price_overrides[it.id]; else p.price_overrides[it.id] = Number(v);
          refreshTotals();
        }
      }))));
  }
  body.appendChild(tbl);
  panel.appendChild(body);
  return panel;
}

function addItem() {
  CUR.items.push({ name_en: "New item", name_ar: "", place_ar: "", time: "", note_ar: "",
    image: "", labour_days: 0, material_transport_trips: 0, lines: [] });
  render();
}

function renderItem(item, idx) {
  const block = el("div", { class: "item-block" + (item._open ? " open" : "") });
  const head = el("div", { class: "item-head", onclick: () => { item._open = !item._open; render(); } },
    el("span", { class: "chev" }, "▶"),
    el("span", { class: "name" }, (item.name_en || "Item") + (item.name_ar ? "  " : "")),
    el("span", { class: "arabic muted" }, item.name_ar || ""),
    el("span", { class: "tot" }, money(itemTotal(item))));
  block.appendChild(head);

  const body = el("div", { class: "item-body" });

  // item meta
  const fi = (label, key, ar = false) => el("div", { class: "field" },
    el("label", {}, label), el("input", { class: ar ? "arabic" : "", value: item[key] || "", oninput: e => { item[key] = e.target.value; } }));
  body.appendChild(el("div", { class: "row" }, fi("Item name (EN)", "name_en"), fi("Item name (AR)", "name_ar", true)));
  body.appendChild(el("div", { class: "row" },
    fi("Location / place (AR)", "place_ar", true),
    el("div", { class: "field" }, el("label", {}, "Time"), el("input", { type: "time", value: item.time || "", oninput: e => item.time = e.target.value }))));
  body.appendChild(el("div", { class: "row" }, fi("Note (AR, optional)", "note_ar", true)));

  // image
  const imgField = el("div", { class: "field" });
  imgField.appendChild(el("label", {}, "Item photo"));
  const imgRow = el("div", { class: "row", style: "align-items:center" });
  if (item.image) {
    const src = item.image.startsWith("http") ? item.image : "/" + item.image;
    imgRow.appendChild(el("img", { class: "thumb", src }));
  }
  const fileIn = el("input", { type: "file", accept: "image/*", onchange: e => uploadItemImage(item, e.target.files[0]) });
  imgRow.appendChild(fileIn);
  if (item.image) imgRow.appendChild(el("button", { class: "btn danger sm", onclick: () => { item.image = ""; render(); } }, "Remove photo"));
  imgField.appendChild(imgRow);
  body.appendChild(imgField);

  // labour + transport
  body.appendChild(el("div", { class: "row" },
    el("div", { class: "field" }, el("label", {}, "Labour work days"),
      el("input", { type: "number", step: "0.5", min: "0", value: item.labour_days || 0, oninput: e => { item.labour_days = e.target.value; refreshTotals(); } })),
    el("div", { class: "field" }, el("label", {}, "Material transport trips"),
      el("input", { type: "number", step: "1", min: "0", value: item.material_transport_trips || 0, oninput: e => { item.material_transport_trips = e.target.value; refreshTotals(); } }))));
  body.appendChild(el("p", { class: "hint" }, "Labour transport (limousine) is auto-calculated as work days × 2."));

  // material lines
  body.appendChild(el("h3", {}, "Materials & services"));
  item.lines = item.lines || [];
  item.lines.forEach((line, li) => body.appendChild(renderLine(item, line, li)));
  body.appendChild(el("button", { class: "btn ghost sm", onclick: () => { item.lines.push({ kind: "simple", item_id: "", qty: 1 }); render(); } }, "+ Add material line"));

  block.appendChild(body);
  return block;
}

function materialSelect(line, onchange) {
  const sel = el("select", { onchange: e => { line.item_id = e.target.value; onchange(); } });
  sel.appendChild(el("option", { value: "" }, "— select material —"));
  for (const cat of PB.categories) {
    const grp = el("optgroup", { label: cat.name_en });
    for (const it of PB.items.filter(i => i.category === cat.id))
      grp.appendChild(el("option", { value: it.id, selected: line.item_id === it.id ? "selected" : null }, `${it.name_en} (${it.unit})`));
    sel.appendChild(grp);
  }
  return sel;
}

function renderLine(item, line, li) {
  const wrap = el("div", { class: "line" });
  // kind
  const kindSel = el("select", { onchange: e => { line.kind = e.target.value; render(); } });
  for (const k of [["simple", "Simple"], ["mdf", "MDF/Veneer"], ["laminated", "Laminated"]])
    kindSel.appendChild(el("option", { value: k[0], selected: line.kind === k[0] ? "selected" : null }, k[1]));
  wrap.appendChild(el("div", { class: "field" }, el("label", {}, "Type"), kindSel));

  // material
  wrap.appendChild(el("div", { class: "field" }, el("label", {}, "Material / service"), materialSelect(line, refreshTotals)));

  // qty
  wrap.appendChild(el("div", { class: "field" }, el("label", {}, "Qty"),
    el("input", { type: "number", step: "0.001", value: line.qty ?? 1, oninput: e => { line.qty = e.target.value; refreshTotals(); } })));

  // line total + remove
  wrap.appendChild(el("div", { style: "display:flex;gap:6px;align-items:center" },
    el("span", { class: "linetotal" }, money(lineTotal(line))),
    el("button", { class: "btn danger sm", onclick: () => { item.lines.splice(li, 1); render(); } }, "✕")));

  // sub-options depending on kind
  if (line.kind === "mdf") {
    const opts = el("div", { class: "sub-opts" });
    const nf = (label, key, ph) => el("div", { class: "field" }, el("label", {}, label),
      el("input", { type: "number", step: "0.01", value: line[key] ?? "", placeholder: ph, oninput: e => { line[key] = e.target.value; refreshTotals(); } }));
    opts.appendChild(nf("Length (m)", "length"));
    opts.appendChild(nf("Width (m)", "width"));
    opts.appendChild(nf("Thickness (mm)", "thickness"));
    const fin = el("select", { onchange: e => { line.finish = e.target.value; refreshTotals(); } });
    for (const o of [["none", "No finish"], ["polish", "Polish"], ["paint", "Paint"]])
      fin.appendChild(el("option", { value: o[0], selected: (line.finish || "none") === o[0] ? "selected" : null }, o[1]));
    opts.appendChild(el("div", { class: "field" }, el("label", {}, "Finish"), fin));
    opts.appendChild(nf("Finish faces (count)", "finish_count"));
    opts.appendChild(el("div", { class: "field" }, el("label", {}, " "), el("span", { class: "hint" }, "Area = L × W × faces, priced per m².")));
    wrap.appendChild(opts);
  } else if (line.kind === "laminated") {
    const opts = el("div", { class: "sub-opts" });
    const proc = el("select", { onchange: e => { line.process = e.target.value; refreshTotals(); } });
    for (const o of [["none", "No process"], ["cnc", "CNC cutting"], ["pvc_edge", "PVC edge binding"]])
      proc.appendChild(el("option", { value: o[0], selected: (line.process || "none") === o[0] ? "selected" : null }, o[1]));
    opts.appendChild(el("div", { class: "field" }, el("label", {}, "Process"), proc));
    opts.appendChild(el("div", { class: "field" }, el("label", {}, line.process === "pvc_edge" ? "Edge length (m)" : "Process qty"),
      el("input", { type: "number", step: "0.01", value: line.process_qty ?? "", oninput: e => { line.process_qty = e.target.value; refreshTotals(); } })));
    wrap.appendChild(opts);
  }
  return wrap;
}

/* update totals without full re-render (keeps inputs focused) */
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
  const pill = document.querySelector(".pill");
  if (pill) pill.textContent = "Project total: " + money(CUR.items.reduce((s, it) => s + itemTotal(it), 0));
}

async function uploadItemImage(item, file) {
  if (!file) return;
  try {
    setStatus("Uploading image...");
    const fd = new FormData();
    fd.append("file", file);
    const headers = {};
    if (PASSWORD) headers["X-App-Password"] = PASSWORD;
    const res = await fetch(API + "/upload", { method: "POST", body: fd, headers });
    if (!res.ok) throw new Error("upload failed");
    const data = await res.json();
    item.image = data.path;
    setStatus("Image uploaded.");
    render();
  } catch (e) { setStatus("Image upload failed: " + e.message, true); }
}

async function saveProject() {
  try {
    stripUI(CUR);
    await api("/projects/" + CUR.id, { method: "PUT", body: JSON.stringify(CUR) });
    setStatus("Project saved.");
  } catch (e) { setStatus("Save failed: " + e.message, true); }
}
function stripUI(p) { (p.items || []).forEach(it => delete it._open); }

async function deleteProject() {
  try {
    await api("/projects/" + CUR.id, { method: "DELETE" });
    VIEW = "projects"; CUR = null; render();
    setStatus("Project deleted.");
  } catch (e) { setStatus("Delete failed: " + e.message, true); }
}

async function generate() {
  try {
    stripUI(CUR);
    await api("/projects/" + CUR.id, { method: "PUT", body: JSON.stringify(CUR) });
    setStatus("Generating Excel...");
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
    setStatus("Excel downloaded.");
  } catch (e) { setStatus("Generate failed: " + e.message, true); }
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
  tab.addEventListener("click", () => { VIEW = tab.dataset.view; CUR = null; render(); }));

(async function init() {
  await ensureAuth();
  render();
})();
