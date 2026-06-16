"""
Excel generator for the Carpentry Quote Tool.

Produces ONE workbook per client. Inside:
  - "الأسعار" (Prices): the ONLY place unit prices are typed. Every item sheet
    references these cells, so updating a price updates the whole quote.
  - one sheet PER ITEM: Arabic, right-to-left, SAR. All money cells are formulas
    (line total = qty * price-reference; subtotals = SUM; finishing area = L*W*count;
    labour transport = days*2; etc.). Nothing computed is hardcoded.
  - "الملخص" (Summary): per-item totals (by reference), grand total, total work
    days and an estimated timeline (weeks / months), plus a category breakdown.

The generator is intentionally defensive about its inputs: unknown price ids,
missing dimensions, empty items, etc. all degrade gracefully.
"""

from __future__ import annotations

import io
import os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage

# ----------------------------------------------------------------------------
# Styling constants
# ----------------------------------------------------------------------------
SAR_FMT = '#,##0.00\\ "ر.س"'      # western digits, Arabic SAR suffix
QTY_FMT = '#,##0.###'

C_HEADER_BG = "1F4E5F"
C_HEADER_FG = "FFFFFF"
C_TOTAL_BG = "D9E8EE"
C_META_BG = "F2F2F2"
C_BREAK_BG = "EAF3E7"

THIN = Side(style="thin", color="BBBBBB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

F_TITLE = Font(name="Arial", size=15, bold=True, color="1F4E5F")
F_HEAD = Font(name="Arial", size=11, bold=True, color=C_HEADER_FG)
F_LABEL = Font(name="Arial", size=11, bold=True)
F_CELL = Font(name="Arial", size=11)
F_TOTAL = Font(name="Arial", size=12, bold=True)

CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center", wrap_text=True)


def _safe_sheet_title(name: str, used: set) -> str:
    """Excel sheet titles: <=31 chars, no []:*?/\\ , unique."""
    bad = '[]:*?/\\'
    clean = "".join("_" if ch in bad else ch for ch in (name or "Item")).strip() or "Item"
    clean = clean[:31]
    base = clean
    i = 2
    while clean.lower() in used:
        suffix = f" ({i})"
        clean = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(clean.lower())
    return clean


class QuoteGenerator:
    def __init__(self, pricebook: dict):
        self.pb = pricebook
        self.items_by_id = {it["id"]: it for it in pricebook.get("items", [])}
        self.cat_by_id = {c["id"]: c for c in pricebook.get("categories", [])}
        self.week_days = pricebook.get("week_days", 6) or 6
        self.month_days = pricebook.get("month_days", 26) or 26

    # -- price resolution ----------------------------------------------------
    def _price(self, item_id: str, overrides: dict) -> float:
        if overrides and item_id in overrides and overrides[item_id] not in (None, ""):
            try:
                return float(overrides[item_id])
            except (TypeError, ValueError):
                pass
        it = self.items_by_id.get(item_id)
        return float(it["price"]) if it else 0.0

    # -- public --------------------------------------------------------------
    def build(self, project: dict, data_dir: str, out_path: str) -> str:
        wb = Workbook()
        wb.remove(wb.active)

        overrides = project.get("price_overrides", {}) or {}
        items = project.get("items", []) or []

        # 1) Collect every price id actually used so the Prices sheet is complete.
        used_ids: list[str] = []
        seen = set()
        for item in items:
            for pid in self._ids_in_item(item):
                if pid and pid not in seen and pid in self.items_by_id:
                    seen.add(pid)
                    used_ids.append(pid)

        price_ref = self._build_prices_sheet(wb, used_ids, overrides)

        # 2) One sheet per item.
        used_titles: set = set()
        item_sheets = []  # (sheet_title, total_cell, days_cell)
        for item in items:
            title = _safe_sheet_title(item.get("name_ar") or item.get("name_en") or "بند", used_titles)
            ws = wb.create_sheet(title=title)
            total_cell, days_cell = self._build_item_sheet(ws, project, item, price_ref, data_dir)
            item_sheets.append((title, total_cell, days_cell))

        # 3) Summary sheet (created last, then moved to front).
        self._build_summary_sheet(wb, project, item_sheets)
        wb.move_sheet(wb["الملخص"], -(len(wb.sheetnames) - 1))

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        wb.save(out_path)
        return out_path

    # -- item id discovery ---------------------------------------------------
    def _ids_in_item(self, item: dict):
        ids = []
        for ln in item.get("lines", []) or []:
            if ln.get("item_id"):
                ids.append(ln["item_id"])
            finish = ln.get("finish")
            if finish == "polish":
                ids.append("polish_wood")
            elif finish == "paint":
                ids.append("paint_wood")
            proc = ln.get("process")
            if proc == "cnc":
                ids.append("cnc_cut")
            elif proc == "pvc_edge":
                ids.append("edge_pvc")
        if float(item.get("labour_days") or 0) > 0:
            ids.append("labour_day")
            ids.append("transport_labour")
        if float(item.get("material_transport_trips") or 0) > 0:
            ids.append("transport_mat")
        return ids

    # -- Prices sheet --------------------------------------------------------
    def _build_prices_sheet(self, wb, used_ids, overrides) -> dict:
        ws = wb.create_sheet(title="الأسعار")
        ws.sheet_view.rightToLeft = True
        ws.column_dimensions["A"].width = 4
        ws.column_dimensions["B"].width = 22
        ws.column_dimensions["C"].width = 40
        ws.column_dimensions["D"].width = 12
        ws.column_dimensions["E"].width = 16

        ws["B1"] = "قائمة الأسعار (المصدر الوحيد للأسعار)"
        ws["B1"].font = F_TITLE
        ws.merge_cells("B1:E1")

        hdr = ["", "التصنيف", "المادة / الخدمة", "الوحدة", "السعر (ر.س)"]
        for col, text in enumerate(hdr, start=1):
            c = ws.cell(row=2, column=col, value=text)
            c.font = F_HEAD
            c.alignment = CENTER
            c.fill = PatternFill("solid", fgColor=C_HEADER_BG)
            c.border = BORDER

        ref = {}
        r = 3
        for pid in used_ids:
            it = self.items_by_id[pid]
            cat = self.cat_by_id.get(it.get("category"), {})
            ws.cell(row=r, column=2, value=cat.get("name_ar", "")).font = F_CELL
            ws.cell(row=r, column=3, value=it.get("name_ar", "")).font = F_CELL
            ws.cell(row=r, column=4, value=it.get("unit", "")).font = F_CELL
            pc = ws.cell(row=r, column=5, value=self._price(pid, overrides))
            pc.font = F_LABEL
            pc.number_format = SAR_FMT
            for col in range(2, 6):
                ws.cell(row=r, column=col).border = BORDER
                ws.cell(row=r, column=col).alignment = RIGHT
            pc.alignment = CENTER
            # absolute reference to this price cell, used by every item sheet
            ref[pid] = f"'الأسعار'!$E${r}"
            r += 1
        return ref

    # -- one item sheet ------------------------------------------------------
    def _build_item_sheet(self, ws, project, item, price_ref, data_dir):
        ws.sheet_view.rightToLeft = True
        ws.sheet_view.showGridLines = False
        for col, w in {
            "A": 2, "B": 12, "C": 12, "D": 12, "E": 12, "F": 12, "G": 4,
            "H": 10, "I": 10, "J": 16, "K": 14, "L": 10, "M": 34,
        }.items():
            ws.column_dimensions[col].width = w

        # ---- metadata header (right side, J:M) + image (left, B:F) ----
        meta = [
            ("اسم الشركة", project.get("company_name_ar") or project.get("company_name_en") or ""),
            ("اسم العميل", project.get("client_name_ar") or project.get("client_name_en") or ""),
            ("اسم الوحدة / المشروع", project.get("unit_name") or ""),
            ("الموقع / المكان", item.get("place_ar") or item.get("place_en") or project.get("location") or ""),
            ("البند", item.get("name_ar") or item.get("name_en") or ""),
        ]
        row = 1
        for label, value in meta:
            lc = ws.cell(row=row, column=13, value=label)  # M
            lc.font = F_LABEL
            lc.alignment = RIGHT
            lc.fill = PatternFill("solid", fgColor=C_META_BG)
            ws.merge_cells(start_row=row, start_column=10, end_row=row, end_column=12)
            vc = ws.cell(row=row, column=10, value=value)  # J = merge anchor
            vc.font = F_CELL
            vc.alignment = RIGHT
            for col in (10, 13):
                ws.cell(row=row, column=col).border = BORDER
            row += 1
        # date (TODAY formula) + time
        ws.cell(row=row, column=13, value="التاريخ").font = F_LABEL
        ws.cell(row=row, column=13).alignment = RIGHT
        ws.cell(row=row, column=13).fill = PatternFill("solid", fgColor=C_META_BG)
        ws.merge_cells(start_row=row, start_column=10, end_row=row, end_column=12)
        dc = ws.cell(row=row, column=10, value="=TODAY()")
        dc.number_format = "yyyy-mm-dd"
        dc.alignment = RIGHT
        row += 1
        ws.cell(row=row, column=13, value="الوقت").font = F_LABEL
        ws.cell(row=row, column=13).alignment = RIGHT
        ws.cell(row=row, column=13).fill = PatternFill("solid", fgColor=C_META_BG)
        ws.merge_cells(start_row=row, start_column=10, end_row=row, end_column=12)
        ws.cell(row=row, column=10, value=item.get("time") or "")
        meta_bottom = row

        # item image on the left
        self._place_image(ws, item, data_dir, anchor="B2")

        # ---- materials table ----
        start = meta_bottom + 2
        headers = {10: "قيمة الإجمالي", 11: "السعر", 12: "عدد", 13: "مواد العمل و اكسسوارات و غيرہ"}
        for col, text in headers.items():
            c = ws.cell(row=start, column=col, value=text)
            c.font = F_HEAD
            c.alignment = CENTER
            c.fill = PatternFill("solid", fgColor=C_HEADER_BG)
            c.border = BORDER
        # helper column O (15) = category id, hidden, used for the breakdown SUMIFs
        ws.column_dimensions["O"].hidden = True

        first_data = start + 1
        r = first_data
        rows_written = 0
        for line in item.get("lines", []) or []:
            r = self._write_line(ws, r, line, price_ref)
            rows_written += 1
        # labour + transport rows (mirrors original blocks)
        r = self._write_labour_transport(ws, r, item, price_ref)
        last_data = r - 1

        # subtotal
        if last_data < first_data:
            last_data = first_data  # avoid broken SUM on empty item
        total_row = r
        tc = ws.cell(row=total_row, column=10, value=f"=SUM(J{first_data}:J{last_data})")
        tc.font = F_TOTAL
        tc.number_format = SAR_FMT
        tc.alignment = CENTER
        tc.fill = PatternFill("solid", fgColor=C_TOTAL_BG)
        lbl = ws.cell(row=total_row, column=13, value="إجمالي تكلفة البند")
        lbl.font = F_TOTAL
        lbl.alignment = RIGHT
        lbl.fill = PatternFill("solid", fgColor=C_TOTAL_BG)
        for col in (11, 12):
            ws.cell(row=total_row, column=col).fill = PatternFill("solid", fgColor=C_TOTAL_BG)
        for col in range(10, 14):
            ws.cell(row=total_row, column=col).border = BORDER
        total_cell = f"'{ws.title}'!$J${total_row}"

        # optional note
        nr = total_row + 1
        if item.get("note_ar"):
            ws.merge_cells(start_row=nr, start_column=10, end_row=nr, end_column=13)
            nc = ws.cell(row=nr, column=10, value="ملاحظة - " + item["note_ar"])
            nc.font = Font(name="Arial", size=10, italic=True, color="B00000")
            nc.alignment = RIGHT
            nr += 1

        # ---- breakdown by category (SUMIF on hidden O column) ----
        br = nr + 1
        days_cell = self._write_breakdown_and_time(
            ws, br, item, first_data, last_data, total_row
        )
        return total_cell, days_cell

    def _write_line(self, ws, r, line, price_ref):
        """Render one input line into one or more sheet rows. Returns next row."""
        kind = line.get("kind", "simple")
        pid = line.get("item_id")
        qty = self._num(line.get("qty"))

        # main material/board row
        if pid:
            self._material_row(ws, r, pid, qty_value=qty, price_ref=price_ref)
            r += 1

        # MDF / veneer finishing: polish or paint, area = length * width * count
        if kind == "mdf" and line.get("finish") in ("polish", "paint"):
            finish_id = "polish_wood" if line["finish"] == "polish" else "paint_wood"
            length = self._num(line.get("length"))
            width = self._num(line.get("width"))
            count = self._num(line.get("finish_count"), default=qty or 1)
            # dimension inputs go in helper cols P(16)=len, Q(17)=wid, R(18)=count
            ws.cell(row=r, column=16, value=length)
            ws.cell(row=r, column=17, value=width)
            ws.cell(row=r, column=18, value=count)
            qty_formula = f"=P{r}*Q{r}*R{r}"
            self._material_row(ws, r, finish_id, qty_formula=qty_formula, price_ref=price_ref)
            ws.cell(row=r, column=13).value = (
                self.items_by_id.get(finish_id, {}).get("name_ar", "")
                + f"  (الطول×العرض×العدد: P{r}×Q{r}×R{r})"
            )
            r += 1

        # Laminated process: CNC or PVC edge binding
        if kind == "laminated" and line.get("process") in ("cnc", "pvc_edge"):
            if line["process"] == "cnc":
                proc_id = "cnc_cut"
                proc_qty = self._num(line.get("process_qty"), default=qty or 1)
            else:
                proc_id = "edge_pvc"
                proc_qty = self._num(line.get("process_qty"))
            self._material_row(ws, r, proc_id, qty_value=proc_qty, price_ref=price_ref)
            r += 1
        return r

    def _material_row(self, ws, r, pid, price_ref, qty_value=None, qty_formula=None):
        it = self.items_by_id.get(pid, {})
        # Material name (M)
        nc = ws.cell(row=r, column=13, value=it.get("name_ar", pid))
        nc.font = F_CELL
        nc.alignment = RIGHT
        # Qty (L)
        lc = ws.cell(row=r, column=12, value=qty_formula if qty_formula else qty_value)
        lc.number_format = QTY_FMT
        lc.alignment = CENTER
        lc.font = F_CELL
        # Price (K) -> reference to Prices sheet (never hardcoded)
        kref = price_ref.get(pid)
        kc = ws.cell(row=r, column=11, value=(f"={kref}" if kref else 0))
        kc.number_format = SAR_FMT
        kc.alignment = CENTER
        kc.font = F_CELL
        # Total (J) = qty * price
        jc = ws.cell(row=r, column=10, value=f"=L{r}*K{r}")
        jc.number_format = SAR_FMT
        jc.alignment = CENTER
        jc.font = F_CELL
        # hidden category tag (O)
        ws.cell(row=r, column=15, value=it.get("category", ""))
        for col in range(10, 14):
            ws.cell(row=r, column=col).border = BORDER

    def _write_labour_transport(self, ws, r, item, price_ref):
        days = self._num(item.get("labour_days"))
        trips = self._num(item.get("material_transport_trips"))
        if days > 0:
            # carpentry work days
            self._material_row(ws, r, "labour_day", qty_value=days, price_ref=price_ref)
            day_qty_row = r
            r += 1
            # labour transport (limousine) = work days * 2  (original pattern)
            self._material_row(
                ws, r, "transport_labour",
                qty_formula=f"=L{day_qty_row}*2", price_ref=price_ref,
            )
            r += 1
        if trips > 0:
            self._material_row(ws, r, "transport_mat", qty_value=trips, price_ref=price_ref)
            r += 1
        return r

    def _write_breakdown_and_time(self, ws, br, item, first_data, last_data, total_row):
        """Category breakdown (SUMIF) + time estimate. Returns the days cell ref."""
        ws.cell(row=br, column=13, value="تفصيل التكلفة حسب التصنيف").font = F_TOTAL
        ws.cell(row=br, column=13).alignment = RIGHT
        br += 1
        cats_present = []
        # determine categories present in this item
        present = set()
        for rr in range(first_data, last_data + 1):
            v = ws.cell(row=rr, column=15).value
            if v:
                present.add(v)
        for cat in self.pb.get("categories", []):
            if cat["id"] in present:
                cats_present.append(cat)
        for cat in cats_present:
            lc = ws.cell(row=br, column=13, value=cat["name_ar"])
            lc.font = F_CELL
            lc.alignment = RIGHT
            lc.fill = PatternFill("solid", fgColor=C_BREAK_BG)
            vc = ws.cell(
                row=br, column=10,
                value=f'=SUMIF($O${first_data}:$O${last_data},"{cat["id"]}",$J${first_data}:$J${last_data})',
            )
            vc.number_format = SAR_FMT
            vc.alignment = CENTER
            vc.font = F_CELL
            vc.fill = PatternFill("solid", fgColor=C_BREAK_BG)
            for col in range(10, 14):
                ws.cell(row=br, column=col).border = BORDER
            br += 1

        # time estimate
        br += 1
        days = self._num(item.get("labour_days"))
        ws.cell(row=br, column=13, value="الوقت التقديري للتنفيذ").font = F_TOTAL
        ws.cell(row=br, column=13).alignment = RIGHT
        br += 1
        days_cell = f"'{ws.title}'!$L${br}"
        rows = [
            ("عدد أيام العمل", days, QTY_FMT),
            ("بالأسابيع (أسبوع = %d يوم)" % self.week_days, f"=L{br}/{self.week_days}", "#,##0.0"),
            ("بالأشهر (شهر = %d يوم)" % self.month_days, f"=L{br}/{self.month_days}", "#,##0.0"),
        ]
        base_row = br
        for i, (label, val, fmt) in enumerate(rows):
            rr = base_row + i
            lc = ws.cell(row=rr, column=13, value=label)
            lc.font = F_CELL
            lc.alignment = RIGHT
            vcell = ws.cell(row=rr, column=12, value=(val if i == 0 else val.replace(f"L{br}", f"L{base_row}")))
            vcell.number_format = fmt
            vcell.alignment = CENTER
            for col in (12, 13):
                ws.cell(row=rr, column=col).border = BORDER
        return days_cell

    # -- summary -------------------------------------------------------------
    def _build_summary_sheet(self, wb, project, item_sheets):
        ws = wb.create_sheet(title="الملخص")
        ws.sheet_view.rightToLeft = True
        ws.sheet_view.showGridLines = False
        for col, w in {"A": 2, "B": 6, "C": 40, "D": 18, "E": 16}.items():
            ws.column_dimensions[col].width = w

        ws["B1"] = project.get("unit_name") or project.get("client_name_ar") or "ملخص المشروع"
        ws["B1"].font = F_TITLE
        ws.merge_cells("B1:E1")
        ws["B2"] = "العميل: " + (project.get("client_name_ar") or project.get("client_name_en") or "")
        ws["B2"].font = F_LABEL
        ws["B2"].alignment = RIGHT
        ws.merge_cells("B2:E2")
        dc = ws["E3"]
        ws["D3"] = "التاريخ"
        ws["D3"].font = F_LABEL
        ws["D3"].alignment = RIGHT
        dc.value = "=TODAY()"
        dc.number_format = "yyyy-mm-dd"

        hr = 5
        for col, text in {2: "#", 3: "البند", 4: "إجمالي التكلفة", 5: "أيام العمل"}.items():
            c = ws.cell(row=hr, column=col, value=text)
            c.font = F_HEAD
            c.alignment = CENTER
            c.fill = PatternFill("solid", fgColor=C_HEADER_BG)
            c.border = BORDER

        r = hr + 1
        first = r
        for idx, (title, total_cell, days_cell) in enumerate(item_sheets, start=1):
            ws.cell(row=r, column=2, value=idx).alignment = CENTER
            nc = ws.cell(row=r, column=3, value=title)
            nc.alignment = RIGHT
            nc.font = F_CELL
            tc = ws.cell(row=r, column=4, value=f"={total_cell}")
            tc.number_format = SAR_FMT
            tc.alignment = CENTER
            dcell = ws.cell(row=r, column=5, value=f"={days_cell}" if days_cell else 0)
            dcell.number_format = QTY_FMT
            dcell.alignment = CENTER
            for col in range(2, 6):
                ws.cell(row=r, column=col).border = BORDER
            r += 1
        last = r - 1 if r > first else first

        # grand totals
        gc = ws.cell(row=r, column=4, value=f"=SUM(D{first}:D{last})")
        gc.font = F_TOTAL
        gc.number_format = SAR_FMT
        gc.alignment = CENTER
        gc.fill = PatternFill("solid", fgColor=C_TOTAL_BG)
        dgc = ws.cell(row=r, column=5, value=f"=SUM(E{first}:E{last})")
        dgc.font = F_TOTAL
        dgc.number_format = QTY_FMT
        dgc.alignment = CENTER
        dgc.fill = PatternFill("solid", fgColor=C_TOTAL_BG)
        glabel = ws.cell(row=r, column=3, value="الإجمالي الكلي للمشروع")
        glabel.font = F_TOTAL
        glabel.alignment = RIGHT
        glabel.fill = PatternFill("solid", fgColor=C_TOTAL_BG)
        ws.cell(row=r, column=2).fill = PatternFill("solid", fgColor=C_TOTAL_BG)
        for col in range(2, 6):
            ws.cell(row=r, column=col).border = BORDER
        grand_days_row = r

        # timeline derived from total days
        r += 2
        ws.cell(row=r, column=3, value="المدة الزمنية التقديرية للمشروع").font = F_TOTAL
        ws.cell(row=r, column=3).alignment = RIGHT
        r += 1
        timeline = [
            ("إجمالي أيام العمل", f"=E{grand_days_row}", QTY_FMT),
            ("بالأسابيع (أسبوع = %d يوم)" % self.week_days, f"=E{grand_days_row}/{self.week_days}", "#,##0.0"),
            ("بالأشهر (شهر = %d يوم)" % self.month_days, f"=E{grand_days_row}/{self.month_days}", "#,##0.0"),
        ]
        for label, val, fmt in timeline:
            lc = ws.cell(row=r, column=3, value=label)
            lc.alignment = RIGHT
            lc.font = F_CELL
            vc = ws.cell(row=r, column=4, value=val)
            vc.number_format = fmt
            vc.alignment = CENTER
            for col in (3, 4):
                ws.cell(row=r, column=col).border = BORDER
            r += 1

    # -- helpers -------------------------------------------------------------
    def _place_image(self, ws, item, data_dir, anchor):
        rel = item.get("image")
        if not rel:
            return
        raw = None
        try:
            from store import read_image_bytes
            raw = read_image_bytes(rel)
        except Exception:
            raw = None
        if raw is None:
            # fall back to direct local path (keeps generator usable standalone)
            path = rel if os.path.isabs(rel) else os.path.join(data_dir, rel)
            if not os.path.exists(path):
                return
            with open(path, "rb") as f:
                raw = f.read()
        try:
            bio = io.BytesIO(raw)
            img = XLImage(bio)
            # scale to fit roughly within the metadata block
            max_w, max_h = 360, 260
            if img.width and img.height:
                ratio = min(max_w / img.width, max_h / img.height, 1.0)
                img.width = int(img.width * ratio)
                img.height = int(img.height * ratio)
            ws.add_image(img, anchor)
        except Exception:
            pass  # never let a bad image break generation

    @staticmethod
    def _num(value, default=0.0):
        try:
            if value in (None, ""):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default
