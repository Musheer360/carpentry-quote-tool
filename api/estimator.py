"""
Parametric furniture estimator for the Carpentry Quote Tool.

Given a "Smart Unit" spec (unit type + dimensions in cm + finish + counts +
toggles), it deterministically generates:
  * the cut parts (carcass, doors, shelves, back, drawer boxes, 18mm grid
    backing strips), enforcing the carpentry rules;
  * a 2D nesting layout per material (sheet count, utilization, placements);
  * an itemized Bill of Materials with quantities, unit prices and amounts;
  * a structured cost + logistics breakdown.

Carpentry rules enforced (from the spec):
  Rule A  - all visible body/carcass/shutter/finished panels are 18mm.
  Rule B  - TV Wall / Wall Paneling get a hidden 18mm RAW MDF backing grid:
            7cm-wide strips, full height, spaced <=50cm centre-to-centre.
  Rule C  - drawer internals are 18mm PLAIN/RAW MDF; internal depth = depth-7cm.
Hardware:
  Wardrobe   - 3 hinges per main door (<=240cm), 2 per loft door.
  TV/Paneling- hinges 0 by default (optional manual override); 1 runner / drawer.
  Push-to-open -> 1 magnet per door + per drawer.
  LED        - running metres along the front+sides of 20/40cm-deep shelves.
Edge binding (laminated chipboard only) - linear metres of exposed edges.

All money is computed here for the live UI; the Excel generator re-expresses the
same quantities as spreadsheet formulas.
"""

from __future__ import annotations

import math

CM_PER_M = 100.0
T_MM = 18           # Rule A thickness (mm)
BACK_MM = 8         # back panel thickness
BACKING_STRIP_W = 7.0      # cm  (Rule B)
BACKING_SPACING = 50.0     # cm centre-to-centre (Rule B max)
DRAWER_CLEARANCE = 7.0     # cm  (Rule C: internal depth = depth - 7)
MAIN_DOOR_MAX_H = 240.0    # cm  (above this is a loft shutter)

# Material keys used by the engine -> human labels (EN/AR).
MATERIALS = {
    "laminated_chipboard": {"en": "Laminated Chipboard 18mm", "ar": "خشب لامينيت (شيبورد) 18مم"},
    "plain_mdf_paint":     {"en": "Plain MDF 18mm (for paint)", "ar": "أم دي أف عادي 18مم (للدهان)"},
    "veneer_polish":       {"en": "MDF + Wood Veneer 18mm (polish)", "ar": "أم دي أف مع قشرة خشب 18مم (تلميع)"},
    "commercial_mdf":      {"en": "Commercial MDF 18mm", "ar": "أم دي أف تجاري 18مم"},
    "raw_mdf":             {"en": "Raw/Plain MDF 18mm (backing/internal)", "ar": "أم دي أف خام 18مم (خلفية/داخلي)"},
    "back_panel":          {"en": "Back panel 8mm", "ar": "لوح خلفية 8مم"},
}

# Embedded defaults; overridden by pricebook["estimator"].
DEFAULTS = {
    "sheet_w_cm": 244.0,
    "sheet_h_cm": 122.0,
    "kerf_mm": 4.0,
    "drawer_height_cm": 20.0,
    "prices": {
        # per full sheet (SAR)
        "laminated_chipboard": 250.0,
        "plain_mdf_paint": 200.0,
        "veneer_polish": 600.0,
        "commercial_mdf": 230.0,
        "raw_mdf": 200.0,
        "back_panel": 120.0,
        # per unit
        "hinge": 5.0,            # each
        "runner_set": 40.0,      # per drawer
        "magnet": 6.0,           # each (push-to-open)
        "led_m": 10.0,           # per running metre
        "edge_m": 4.0,           # per metre PVC edge
        "paint_m2": 150.0,       # per m2
        "polish_m2": 150.0,      # per m2
    },
    "labour": {
        "daily_wage": 600.0,     # per day
        "daily_taxi": 60.0,      # per day
        "transport_fixed": 150.0,
    },
}


def _cfg(pricebook):
    """Merge embedded DEFAULTS with pricebook['estimator'] overrides."""
    cfg = {
        "sheet_w_cm": DEFAULTS["sheet_w_cm"], "sheet_h_cm": DEFAULTS["sheet_h_cm"],
        "kerf_mm": DEFAULTS["kerf_mm"], "drawer_height_cm": DEFAULTS["drawer_height_cm"],
        "prices": dict(DEFAULTS["prices"]), "labour": dict(DEFAULTS["labour"]),
    }
    e = (pricebook or {}).get("estimator") or {}
    for k in ("sheet_w_cm", "sheet_h_cm", "kerf_mm", "drawer_height_cm"):
        if e.get(k) not in (None, ""):
            try: cfg[k] = float(e[k])
            except (TypeError, ValueError): pass
    for grp in ("prices", "labour"):
        for k, v in (e.get(grp) or {}).items():
            try: cfg[grp][k] = float(v)
            except (TypeError, ValueError): pass
    return cfg


def _num(v, default=0.0):
    try:
        if v in (None, ""): return default
        return float(v)
    except (TypeError, ValueError): return default


# ---------------------------------------------------------------------------
# Part generation
# ---------------------------------------------------------------------------
class Part:
    __slots__ = ("label_en", "label_ar", "w", "h", "qty", "material", "grain", "edges")

    def __init__(self, label_en, label_ar, w, h, qty, material, grain=False, edges=0):
        self.label_en = label_en
        self.label_ar = label_ar
        self.w = round(w, 2)      # cm
        self.h = round(h, 2)      # cm
        self.qty = int(qty)
        self.material = material
        self.grain = grain        # True => do NOT rotate when nesting (veneer/laminate)
        self.edges = edges        # exposed edge metres PER PIECE (for binding)

    def as_dict(self):
        return {"label_en": self.label_en, "label_ar": self.label_ar,
                "w": self.w, "h": self.h, "qty": self.qty,
                "material": self.material, "grain": self.grain}


def generate_parts(unit, cfg):
    """Return (parts, meta) for a unit. meta carries hardware/led/edge tallies."""
    ut = unit.get("unit_type", "wardrobe")
    H = _num(unit.get("height_cm"))
    W = _num(unit.get("width_cm"))
    D = _num(unit.get("depth_cm"))
    finish = unit.get("finish", "laminated_chipboard")
    if finish not in MATERIALS:
        finish = "laminated_chipboard"
    grain = finish in ("laminated_chipboard", "veneer_polish")  # don't rotate these
    laminated = finish == "laminated_chipboard"

    main_doors = int(_num(unit.get("main_doors")))
    loft_doors = int(_num(unit.get("loft_doors")))
    drawers = int(_num(unit.get("drawers")))
    shelves = int(_num(unit.get("shelves")))
    led_shelves = int(_num(unit.get("led_shelf_count")))
    led_depth = _num(unit.get("led_shelf_depth"), 20.0) or 20.0
    push = bool(unit.get("push_to_open"))
    partitions = int(_num(unit.get("partitions")))

    parts = []
    edge_m = 0.0  # accumulates exposed-edge metres (only billed if laminated)

    def add(label_en, label_ar, w, h, qty, material, grn=False, edge_each=0.0):
        nonlocal edge_m
        if qty <= 0 or w <= 0 or h <= 0:
            return
        parts.append(Part(label_en, label_ar, w, h, qty, material, grn, edge_each))
        edge_m += edge_each * qty

    finished = finish  # finished-panel material key

    if ut == "wardrobe":
        # carcass (Rule A: 18mm finished)
        add("Side panel", "جنب", D, H, 2, finished, grain, (H) / CM_PER_M)            # front edge banded
        add("Top / Bottom", "علوي/سفلي", W, D, 2, finished, grain, (W) / CM_PER_M)
        if partitions > 0:
            add("Vertical partition", "حاجز رأسي", D, H, partitions, finished, grain, (H) / CM_PER_M)
        add("Shelf", "رف", W, D, shelves, finished, grain, (W) / CM_PER_M)            # front edge banded
        # back panel (8mm)
        add("Back panel", "ظهر", W, H, 1, "back_panel", False, 0.0)
        # doors / shutters (Rule A)
        if main_doors > 0:
            dw = W / main_doors
            dh = min(H, MAIN_DOOR_MAX_H)
            add("Main shutter", "درفة رئيسية", dw, dh, main_doors, finished, grain, 2 * (dw + dh) / CM_PER_M)
        if loft_doors > 0:
            dw = W / loft_doors
            dh = max(H - MAIN_DOOR_MAX_H, 0) or 50.0
            add("Loft shutter", "درفة علوية", dw, dh, loft_doors, finished, grain, 2 * (dw + dh) / CM_PER_M)

    elif ut in ("tv_wall", "wall_paneling"):
        # finished face (nester tiles it into sheet-sized panels)
        add("Face panel", "لوح واجهة", W, H, 1, finished, grain, 0.0)
        # Rule B: hidden 18mm RAW MDF backing grid
        strip_count = int(math.floor(W / BACKING_SPACING) + 1)
        add("Backing strip 7cm (grid)", "شريحة خلفية 7سم", BACKING_STRIP_W, H, strip_count, "raw_mdf", False, 0.0)
        # optional shutters (manual override)
        if unit.get("add_hinges_manual") and main_doors > 0:
            dw = W / max(main_doors, 1)
            dh = min(H, MAIN_DOOR_MAX_H)
            add("Shutter", "درفة", dw, dh, main_doors, finished, grain, 2 * (dw + dh) / CM_PER_M)
        # shelves (e.g. open racks)
        add("Shelf", "رف", W, D or 30.0, shelves, finished, grain, (W) / CM_PER_M)

    # Rule C: drawer internal boxes (18mm raw/plain MDF), depth = D - 7
    if drawers > 0:
        dh = cfg["drawer_height_cm"]
        idepth = max(D - DRAWER_CLEARANCE, 10.0)
        dwid = _num(unit.get("drawer_width_cm"), 60.0) or 60.0
        dwid = min(dwid, W) if W > 0 else dwid
        add("Drawer side (internal)", "جنب درج (داخلي)", idepth, dh, 2 * drawers, "raw_mdf")
        add("Drawer front/back (internal)", "أمام/خلف درج", dwid, dh, 2 * drawers, "raw_mdf")
        add("Drawer base (internal)", "قاعدة درج", dwid, idepth, drawers, "raw_mdf")

    # ---- hardware tallies ----
    if ut == "wardrobe":
        hinges = main_doors * 3 + loft_doors * 2
    else:
        hinges = int(_num(unit.get("manual_hinges"))) if unit.get("add_hinges_manual") else 0
    doors_total = main_doors + loft_doors
    magnets = (doors_total + drawers) if push else 0
    runners = drawers

    # ---- LED running metres (front + 2 sides of each LED shelf) ----
    led_m = led_shelves * (W + 2 * led_depth) / CM_PER_M if led_shelves > 0 else 0.0

    # ---- finishing area (one finished face, m2) ----
    finishing = unit.get("finishing", "none")
    finish_area = 0.0
    if finishing in ("paint", "polish"):
        finish_area = (W * H) / (CM_PER_M * CM_PER_M)

    meta = {
        "hinges": hinges, "runners": runners, "magnets": magnets,
        "led_m": round(led_m, 2), "edge_m": round(edge_m, 2) if laminated else 0.0,
        "laminated": laminated, "finishing": finishing, "finish_area": round(finish_area, 3),
    }
    return parts, meta


# ---------------------------------------------------------------------------
# 2D nesting (shelf / First-Fit-Decreasing-Height) per material
# ---------------------------------------------------------------------------
def nest_material(parts, sheet_w, sheet_h, kerf):
    """Pack a material's parts onto sheets. Returns dict with sheets + placements."""
    instances = []
    for p in parts:
        for _ in range(p.qty):
            w, h = p.w, p.h
            if p.grain:
                # grain runs along the longer side -> lay it along sheet_w (x)
                if h > w:
                    w, h = h, w
            else:
                # rotate to reduce splits when the short sheet side blocks a fit
                if w > sheet_w and w <= sheet_h:
                    w, h = h, w
            # split anything larger than a sheet into sheet-bounded tiles
            cols = max(1, math.ceil(w / sheet_w - 1e-9))
            rows = max(1, math.ceil(h / sheet_h - 1e-9))
            for ci in range(cols):
                pw = sheet_w if ci < cols - 1 else w - sheet_w * (cols - 1)
                for ri in range(rows):
                    ph = sheet_h if ri < rows - 1 else h - sheet_h * (rows - 1)
                    instances.append({"w": round(pw, 2), "h": round(ph, 2),
                                      "grain": p.grain, "label": p.label_en})
    # sort by height desc, then width desc
    instances.sort(key=lambda x: (x["h"], x["w"]), reverse=True)

    sheets = []  # each: {"shelves":[{y,h,x}], "placements":[...], "used_area"}

    def new_sheet():
        sheets.append({"shelf_y": 0.0, "shelf_h": 0.0, "shelf_x": 0.0,
                       "placements": [], "used_area": 0.0})
        return sheets[-1]

    def place(it):
        pw, ph = it["w"] + kerf, it["h"] + kerf
        for s in sheets:
            # try current shelf
            if s["shelf_h"] > 0 and ph <= s["shelf_h"] + 1e-6 and s["shelf_x"] + pw <= sheet_w + 1e-6:
                s["placements"].append({"x": s["shelf_x"], "y": s["shelf_y"], "w": it["w"], "h": it["h"], "label": it["label"]})
                s["shelf_x"] += pw
                s["used_area"] += it["w"] * it["h"]
                return True
            # try opening a new shelf in this sheet
            new_y = s["shelf_y"] + s["shelf_h"] if s["shelf_h"] > 0 else 0.0
            if new_y + ph <= sheet_h + 1e-6 and pw <= sheet_w + 1e-6:
                s["shelf_y"] = new_y
                s["shelf_h"] = ph
                s["shelf_x"] = pw
                s["placements"].append({"x": 0.0, "y": new_y, "w": it["w"], "h": it["h"], "label": it["label"]})
                s["used_area"] += it["w"] * it["h"]
                return True
        return False

    for it in instances:
        if not place(it):
            s = new_sheet()
            s["shelf_y"] = 0.0
            s["shelf_h"] = it["h"] + kerf
            s["shelf_x"] = it["w"] + kerf
            s["placements"].append({"x": 0.0, "y": 0.0, "w": it["w"], "h": it["h"], "label": it["label"]})
            s["used_area"] += it["w"] * it["h"]

    sheet_area = sheet_w * sheet_h
    layouts = [{"placements": s["placements"],
                "utilization": round(s["used_area"] / sheet_area, 4) if sheet_area else 0}
               for s in sheets]
    total_used = sum(s["used_area"] for s in sheets)
    util = round(total_used / (sheet_area * len(sheets)), 4) if sheets else 0.0
    return {"sheet_count": len(sheets), "utilization": util,
            "sheet_w": sheet_w, "sheet_h": sheet_h, "layouts": layouts}


# ---------------------------------------------------------------------------
# Top-level estimate
# ---------------------------------------------------------------------------
def estimate(unit, pricebook):
    cfg = _cfg(pricebook)
    prices = cfg["prices"]
    sw, sh, kerf = cfg["sheet_w_cm"], cfg["sheet_h_cm"], cfg["kerf_mm"] / 10.0  # kerf cm

    parts, meta = generate_parts(unit, cfg)

    # group parts by material and nest each
    by_mat = {}
    for p in parts:
        by_mat.setdefault(p.material, []).append(p)
    nesting = {}
    for mat, plist in by_mat.items():
        nesting[mat] = nest_material(plist, sw, sh, kerf)

    # ---- BOM lines ----
    bom = []

    def line(label_en, label_ar, qty, unit_label, price, cat):
        amt = round(qty * price, 2)
        bom.append({"label_en": label_en, "label_ar": label_ar, "qty": round(qty, 3),
                    "unit": unit_label, "price": round(price, 2), "amount": amt, "cat": cat})
        return amt

    materials_cost = 0.0
    # sheets per material
    for mat, nz in nesting.items():
        n = nz["sheet_count"]
        if n <= 0:
            continue
        price = prices.get(mat, 0.0)
        materials_cost += line(MATERIALS[mat]["en"] + " (sheets)", MATERIALS[mat]["ar"] + " (ألواح)",
                               n, "sheet", price, "boards")

    # edge binding (laminated only)
    if meta["edge_m"] > 0:
        materials_cost += line("PVC edge binding", "تلبيس حروف PVC", meta["edge_m"], "m",
                               prices["edge_m"], "edge_cnc")

    # hardware
    hardware_cost = 0.0
    if meta["hinges"] > 0:
        hardware_cost += line("Hinges (kabza)", "مفصلات (كبسة)", meta["hinges"], "pc", prices["hinge"], "hardware")
    if meta["runners"] > 0:
        hardware_cost += line("Drawer runner sets", "سحابات أدراج (طقم)", meta["runners"], "set", prices["runner_set"], "hardware")
    if meta["magnets"] > 0:
        hardware_cost += line("Push-to-open magnets", "مغناطيس بوش أوبن", meta["magnets"], "pc", prices["magnet"], "hardware")
    if meta["led_m"] > 0:
        hardware_cost += line("Hidden LED strip", "شريط إنارة مخفي", meta["led_m"], "m", prices["led_m"], "lighting")

    # finishing
    finishing_cost = 0.0
    if meta["finishing"] == "paint" and meta["finish_area"] > 0:
        finishing_cost += line("Paint", "دهان", meta["finish_area"], "m²", prices["paint_m2"], "paint_polish")
    elif meta["finishing"] == "polish" and meta["finish_area"] > 0:
        finishing_cost += line("Polish", "تلميع", meta["finish_area"], "m²", prices["polish_m2"], "paint_polish")

    # logistics
    days = _num(unit.get("labour_days"))
    daily_wage = _num(unit.get("daily_wage"), cfg["labour"]["daily_wage"])
    daily_taxi = _num(unit.get("daily_taxi"), cfg["labour"]["daily_taxi"])
    transport_fixed = _num(unit.get("transport_fixed"), cfg["labour"]["transport_fixed"])
    logistics_cost = 0.0
    if days > 0:
        logistics_cost += line("Labour (work days)", "عمالة (أيام عمل)", days, "day", daily_wage, "labour")
        logistics_cost += line("Daily transport/taxi", "مواصلات يومية", days, "day", daily_taxi, "transport")
    if transport_fixed > 0:
        logistics_cost += line("Material transport (fixed)", "نقل مواد (ثابت)", 1, "trip", transport_fixed, "transport")

    grand = round(materials_cost + hardware_cost + finishing_cost + logistics_cost, 2)

    return {
        "unit_type": unit.get("unit_type"),
        "parts": [p.as_dict() for p in parts],
        "nesting": {m: {"sheet_count": n["sheet_count"], "utilization": n["utilization"],
                        "sheet_w": n["sheet_w"], "sheet_h": n["sheet_h"], "layouts": n["layouts"],
                        "material_en": MATERIALS[m]["en"], "material_ar": MATERIALS[m]["ar"]}
                    for m, n in nesting.items()},
        "bom": bom,
        "meta": meta,
        "costs": {
            "materials": round(materials_cost, 2),
            "hardware": round(hardware_cost, 2),
            "finishing": round(finishing_cost, 2),
            "logistics": round(logistics_cost, 2),
            "grand": grand,
        },
        "config": {"sheet_w_cm": sw, "sheet_h_cm": sh, "kerf_mm": cfg["kerf_mm"]},
    }
