"""Annotations on pikepdf, with our own appearance streams (/AP), so every
viewer (PDF.js, PDFium, Acrobat, Preview) draws them the same way."""
from __future__ import annotations

import datetime
import re
import uuid
from typing import Optional

import pikepdf

from .geom import Matrix, Rect
from .util import EditError, as_rect, parse_color, rect_list, rgb_to_hex

_ID = re.compile(r"^a:(\d+):([A-Za-z0-9_-]+)$")


def annot_key(a) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(a.get("/NM", "")))[:48] or f"x{a.objgen[0]}"
MARKUP = {"highlight": "/Highlight", "underline": "/Underline", "strikeout": "/StrikeOut",
          "squiggly": "/Squiggly"}
KINDS = tuple(MARKUP) + ("note", "freetext", "rect", "circle", "line")
DEFAULT_COLOR = {"highlight": (1.0, 0.92, 0.23), "underline": (0.1, 0.45, 0.95),
                 "strikeout": (0.86, 0.15, 0.15), "squiggly": (0.86, 0.15, 0.15),
                 "note": (1.0, 0.82, 0.2), "rect": (0.86, 0.15, 0.15), "circle": (0.86, 0.15, 0.15),
                 "line": (0.86, 0.15, 0.15), "freetext": (0.0, 0.0, 0.0)}
BOXED = {"/Text", "/FreeText", "/Square", "/Circle", "/Stamp", "/FileAttachment"}
NAMES = {"/Highlight": "Highlight", "/Underline": "Underline", "/StrikeOut": "StrikeOut",
         "/Squiggly": "Squiggly", "/Text": "Text", "/FreeText": "FreeText", "/Square": "Square",
         "/Circle": "Circle", "/Line": "Line", "/Ink": "Ink", "/Stamp": "Stamp", "/Polygon": "Polygon",
         "/PolyLine": "PolyLine", "/Caret": "Caret", "/FileAttachment": "FileAttachment"}


def _n(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _rgb(arr) -> Optional[tuple]:
    try:
        vals = [float(x) for x in arr]
    except Exception:
        return None
    if len(vals) == 3:
        return tuple(vals)
    if len(vals) == 1:
        return (vals[0],) * 3
    if len(vals) == 4:
        c, m, y, k = vals
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _pdf_date() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("D:%Y%m%d%H%M%SZ")


def _helv(pdf) -> pikepdf.Object:
    return pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica,
                                                Encoding=pikepdf.Name.WinAnsiEncoding))


def _wrap(text: str, size: float, width: float) -> list[str]:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    out = []
    for para in (text or "").split("\n"):
        cur = ""
        for w in para.split(" "):
            cand = w if not cur else cur + " " + w
            if cur and stringWidth(cand, "Helvetica", size) > width:
                out.append(cur)
                cur = w
            else:
                cur = cand
        out.append(cur)
    return out


def _pdf_str(s: str) -> str:
    b = (s or "").encode("cp1252", "replace")
    return "(" + b.decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def build_appearance(pdf: pikepdf.Pdf, a: pikepdf.Dictionary) -> None:
    """(Re)generate the normal appearance of an annotation from its keys."""
    sub = str(a.get("/Subtype"))
    r = Rect.of([float(v) for v in a.Rect])
    col = _rgb(a.get("/C", [])) or (0, 0, 0)
    fill = _rgb(a.get("/IC", [])) if "/IC" in a else None
    bw = float(a.BS.get("/W", 1)) if "/BS" in a else 1.0
    ops: list[str] = []
    res = pikepdf.Dictionary()
    rgb = f"{_n(col[0])} {_n(col[1])} {_n(col[2])}"
    if sub in ("/Highlight", "/Underline", "/StrikeOut", "/Squiggly"):
        q = [float(v) for v in a.get("/QuadPoints", [])]
        if sub == "/Highlight":
            res.ExtGState = pikepdf.Dictionary(GH=pikepdf.Dictionary(Type=pikepdf.Name.ExtGState,
                                                                     BM=pikepdf.Name.Multiply,
                                                                     ca=float(a.get("/CA", 1))))
            ops.append(f"/GH gs {rgb} rg")
        else:
            ops.append(f"{rgb} RG")
        for i in range(0, len(q) - 7, 8):
            x1, y1, x2, y2, x3, y3, x4, y4 = q[i:i + 8]       # UL UR LL LR
            h = abs(y1 - y3) or 10
            if sub == "/Highlight":
                ops.append(f"{_n(x3)} {_n(y3)} m {_n(x4)} {_n(y4)} l {_n(x2)} {_n(y2)} l {_n(x1)} {_n(y1)} l f")
            elif sub == "/Underline":
                y = y3 + h * 0.08
                ops.append(f"{_n(h * 0.07)} w {_n(x3)} {_n(y)} m {_n(x4)} {_n(y)} l S")
            elif sub == "/StrikeOut":
                y = y3 + h * 0.4
                ops.append(f"{_n(h * 0.07)} w {_n(x3)} {_n(y)} m {_n(x4)} {_n(y)} l S")
            else:
                step, y = h * 0.12, y3 + h * 0.06
                pts = []
                x, up = x3, True
                while x < x4:
                    pts.append(f"{_n(x)} {_n(y + (step if up else 0))}")
                    x += step
                    up = not up
                ops.append(f"{_n(h * 0.05)} w " + f"{pts[0]} m " + " ".join(p + " l" for p in pts[1:]) + " S")
    elif sub == "/Text":
        ops += [f"{rgb} rg 0.35 G 0.6 w",
                f"{_n(r.x0 + .5)} {_n(r.y0 + .5)} {_n(r.width - 1)} {_n(r.height - 1)} re B",
                "0.25 G 0.8 w"]
        for k in (0.3, 0.5, 0.7):
            y = r.y0 + r.height * k
            ops.append(f"{_n(r.x0 + 4)} {_n(y)} m {_n(r.x1 - 4)} {_n(y)} l S")
    elif sub == "/FreeText":
        da = str(a.get("/DA", "/Helv 11 Tf 0 g"))
        m = re.search(r"([\d.]+)\s+Tf", da)
        size = float(m.group(1)) if m else 11.0
        res.Font = pikepdf.Dictionary(Helv=_helv(pdf))
        if fill:
            ops.append(f"{_n(fill[0])} {_n(fill[1])} {_n(fill[2])} rg {_n(r.x0)} {_n(r.y0)} "
                       f"{_n(r.width)} {_n(r.height)} re f")
        tcol = _rgb([float(x) for x in re.findall(r"([\d.]+)", da.split("Tf")[-1])]) or (0, 0, 0)
        ops.append(f"BT /Helv {_n(size)} Tf {_n(tcol[0])} {_n(tcol[1])} {_n(tcol[2])} rg")
        y = r.y1 - size * 1.1 - 2
        for line in _wrap(str(a.get("/Contents", "")), size, r.width - 4):
            if y < r.y0:
                break
            ops.append(f"1 0 0 1 {_n(r.x0 + 2)} {_n(y)} Tm {_pdf_str(line)} Tj")
            y -= size * 1.2
        ops.append("ET")
    elif sub in ("/Square", "/Circle"):
        h = bw / 2
        x0, y0, x1, y1 = r.x0 + h, r.y0 + h, r.x1 - h, r.y1 - h
        ops.append(f"{_n(bw)} w {rgb} RG")
        if fill:
            ops.append(f"{_n(fill[0])} {_n(fill[1])} {_n(fill[2])} rg")
        paint = "B" if fill else "S"
        if sub == "/Square":
            ops.append(f"{_n(x0)} {_n(y0)} {_n(x1 - x0)} {_n(y1 - y0)} re {paint}")
        else:
            k = 0.5523
            cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
            ops.append(f"{_n(cx + rx)} {_n(cy)} m "
                       f"{_n(cx + rx)} {_n(cy + k * ry)} {_n(cx + k * rx)} {_n(cy + ry)} {_n(cx)} {_n(cy + ry)} c "
                       f"{_n(cx - k * rx)} {_n(cy + ry)} {_n(cx - rx)} {_n(cy + k * ry)} {_n(cx - rx)} {_n(cy)} c "
                       f"{_n(cx - rx)} {_n(cy - k * ry)} {_n(cx - k * rx)} {_n(cy - ry)} {_n(cx)} {_n(cy - ry)} c "
                       f"{_n(cx + k * rx)} {_n(cy - ry)} {_n(cx + rx)} {_n(cy - k * ry)} {_n(cx + rx)} {_n(cy)} c {paint}")
    elif sub == "/Line":
        x1, y1, x2, y2 = [float(v) for v in a.L]
        ops.append(f"{_n(bw)} w {rgb} RG {_n(x1)} {_n(y1)} m {_n(x2)} {_n(y2)} l S")
    else:
        return
    if "/CA" in a and sub != "/Highlight":
        res.ExtGState = pikepdf.Dictionary(GA=pikepdf.Dictionary(Type=pikepdf.Name.ExtGState,
                                                                 CA=float(a.CA), ca=float(a.CA)))
        ops.insert(0, "/GA gs")
    ap = pikepdf.Stream(pdf, ("q\n" + "\n".join(ops) + "\nQ\n").encode())
    ap.Type, ap.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
    ap.BBox = pikepdf.Array([r.x0, r.y0, r.x1, r.y1])
    ap.Resources = res
    a.AP = pikepdf.Dictionary(N=pdf.make_indirect(ap))


class AnnotOps:
    def _annots(self, page_no: int):
        page = self.page(page_no)
        for a in page.obj.get("/Annots", []) or []:
            try:
                sub = str(a.get("/Subtype"))
            except Exception:
                continue
            if sub in ("/Popup", "/Link", "/Widget"):
                continue
            yield a

    def annot_objects(self, page_no: int) -> list[dict]:
        P = self.model(page_no).P
        out = []
        for a in self._annots(page_no):
            sub = str(a.Subtype)
            col = _rgb(a.get("/C", [])) if "/C" in a else None
            bbox = Rect.of([float(v) for v in a.Rect]).transform(P)
            out.append({"id": f"a:{page_no}:{annot_key(a)}", "kind": "annotation", "page": page_no,
                        "subtype": NAMES.get(sub, sub.lstrip("/")), "bbox": rect_list(bbox),
                        "text": str(a.get("/Contents", "")), "color": rgb_to_hex(col) if col else None,
                        "movable": sub in BOXED})
        return out

    def _resolve_annot(self, aid: str):
        m = _ID.match(aid or "")
        if not m:
            raise EditError(f"{aid!r} is not an annotation id")
        page_no, key = int(m.group(1)), m.group(2)
        for a in self._annots(page_no):
            if annot_key(a) == key:
                return page_no, a
        raise EditError(f"annotation {aid!r} is no longer on page {page_no}")

    def _add(self, page_no: int, d: pikepdf.Dictionary) -> pikepdf.Object:
        page = self.page(page_no)
        d.Type = pikepdf.Name.Annot
        d.F = 4
        d.M = _pdf_date()
        d.NM = pikepdf.String(uuid.uuid4().hex[:16])
        d.P = page.obj
        a = self.pdf.make_indirect(d)
        build_appearance(self.pdf, a)
        if "/Annots" not in page.obj:
            page.obj.Annots = pikepdf.Array()
        page.obj.Annots.append(a)
        return a

    def _word_boxes(self, page_no: int) -> list[Rect]:
        boxes = []
        for s in self._segments(page_no):
            cur = None
            for g in s.glyphs:
                if not g.char.strip():
                    if cur is not None:
                        boxes.append(cur)
                    cur = None
                    continue
                cur = g.bbox if cur is None else cur | g.bbox
            if cur is not None:
                boxes.append(cur)
        return boxes

    def _phrase_boxes(self, page_no: int, needle: str) -> list[Rect]:
        """Page-space boxes (one per line) covering every match of needle."""
        out = []
        q = needle.lower()
        for line in self._lines(page_no):
            line = sorted(line, key=lambda s: s.origin[0])
            chars, boxes = [], []
            for s in line:
                if chars:
                    chars.append(" ")
                    boxes.append(None)
                for g in s.glyphs:
                    chars.append((g.char or "?").lower())
                    boxes.append(g.bbox)
            text = "".join(chars)
            start = text.find(q)
            while start >= 0:
                sel = [b for b in boxes[start:start + len(q)] if b is not None]
                if sel:
                    r = sel[0]
                    for b in sel[1:]:
                        r = r | b
                    out.append(r)
                start = text.find(q, start + 1)
        return out

    def _quads(self, page_no: int, target_text, ids, rect) -> tuple[list[float], Rect]:
        if target_text:
            boxes = self._phrase_boxes(page_no, target_text)
            if not boxes:
                raise EditError(f"{target_text!r} not found on page {page_no}")
        elif ids:
            boxes = []
            for sid in ids:
                seg, _ = self._resolve_seg(sid)
                if seg.page != page_no:
                    raise EditError(f"{sid} is not on page {page_no}")
                boxes.append(seg.bbox)
        elif rect is not None:
            r = as_rect(rect)
            boxes = [b for b in self._word_boxes(page_no) if b.intersects(r)]
            if not boxes:
                raise EditError("no text inside that rectangle")
        else:
            raise EditError("text markup needs target_text, ids or rect")
        Pi = self.model(page_no).P.inverse()
        quads: list[float] = []
        union = None
        for b in boxes:
            pr = b.transform(Pi)
            quads += [pr.x0, pr.y1, pr.x1, pr.y1, pr.x0, pr.y0, pr.x1, pr.y0]
            union = pr if union is None else union | pr
        return quads, union

    def add_annotation(self, page_no: int, kind: str, rect=None, point=None, text: Optional[str] = None,
                       color=None, target_text: Optional[str] = None, ids: Optional[list] = None,
                       font_size: float = 11, opacity: Optional[float] = None, fill=None,
                       line_width: Optional[float] = None) -> dict:
        kind = (kind or "").lower()
        if kind not in KINDS:
            raise EditError(f"unknown annotation kind {kind!r}; use one of {', '.join(KINDS)}")
        with self.transaction(f"Add {kind}"):
            self.page(page_no)
            Pi = self.model(page_no).P.inverse()
            rgb = parse_color(color) or DEFAULT_COLOR[kind]
            d = pikepdf.Dictionary(C=list(rgb))
            if opacity is not None:
                d.CA = float(opacity)
            if text:
                d.Contents = pikepdf.String(text)
            if kind in MARKUP:
                quads, union = self._quads(page_no, target_text, ids, rect)
                d.Subtype = pikepdf.Name(MARKUP[kind])
                d.QuadPoints = quads
                d.Rect = [union.x0 - 1, union.y0 - 1, union.x1 + 1, union.y1 + 1]
            elif kind == "note":
                pt = point or (as_rect(rect).list()[:2] if rect is not None else None)
                if pt is None:
                    raise EditError("a note needs point [x, y] or rect")
                ux, uy = Pi.apply(float(pt[0]), float(pt[1]))
                d.Subtype, d.Name, d.Open = pikepdf.Name.Text, pikepdf.Name.Note, False
                d.Rect = [ux, uy - 20, ux + 20, uy]
            elif kind == "freetext":
                r = as_rect(rect).transform(Pi)
                d.Subtype = pikepdf.Name.FreeText
                d.Rect = r.list()
                d.Contents = pikepdf.String(text or "")
                d.DA = pikepdf.String(f"/Helv {_n(float(font_size or 11))} Tf "
                                      f"{_n(rgb[0])} {_n(rgb[1])} {_n(rgb[2])} rg")
                f = parse_color(fill)
                if f:
                    d.IC = list(f)
                d.C = list(f) if f else []
            elif kind == "line":
                if not rect:
                    raise EditError("a line needs rect [x0, y0, x1, y1] as its two end points")
                x1, y1 = Pi.apply(float(rect[0]), float(rect[1]))
                x2, y2 = Pi.apply(float(rect[2]), float(rect[3]))
                w = float(line_width or 1.5)
                d.Subtype, d.L = pikepdf.Name.Line, [x1, y1, x2, y2]
                d.BS = pikepdf.Dictionary(W=w)
                d.Rect = [min(x1, x2) - w, min(y1, y2) - w, max(x1, x2) + w, max(y1, y2) + w]
            else:
                r = as_rect(rect).transform(Pi)
                d.Subtype = pikepdf.Name.Square if kind == "rect" else pikepdf.Name.Circle
                d.Rect = r.list()
                d.BS = pikepdf.Dictionary(W=float(line_width or 1.5))
                f = parse_color(fill)
                if f:
                    d.IC = list(f)
            a = self._add(page_no, d)
            self._touch(page_no)
            P = self.model(page_no).P
            return {"id": f"a:{page_no}:{annot_key(a)}",
                    "rect": rect_list(Rect.of([float(v) for v in a.Rect]).transform(P))}

    def update_annotation(self, aid: str, text: Optional[str] = None, color=None,
                          opacity: Optional[float] = None) -> dict:
        with self.transaction("Edit annotation"):
            page_no, a = self._resolve_annot(aid)
            if text is not None:
                a.Contents = pikepdf.String(text)
            rgb = parse_color(color)
            if rgb is not None:
                if str(a.Subtype) == "/FreeText":
                    da = str(a.get("/DA", "/Helv 11 Tf"))
                    head = da.split("Tf")[0] + "Tf"
                    a.DA = pikepdf.String(f"{head} {_n(rgb[0])} {_n(rgb[1])} {_n(rgb[2])} rg")
                else:
                    a.C = list(rgb)
            if opacity is not None:
                a.CA = float(opacity)
            a.M = _pdf_date()
            build_appearance(self.pdf, a)
            self._touch(page_no)
            return {"id": aid}

    def delete_annotation(self, aid: str) -> dict:
        with self.transaction("Delete annotation"):
            page_no, a = self._resolve_annot(aid)
            page = self.page(page_no)
            popup = a.get("/Popup")
            keep = [x for x in page.obj.Annots
                    if not (x.objgen == a.objgen or (popup is not None and x.objgen == popup.objgen))]
            page.obj.Annots = pikepdf.Array(keep)
            self._touch(page_no)
            return {"deleted": aid}

    def set_annotation_rect(self, aid: str, rect) -> str:
        with self.transaction("Resize annotation"):
            page_no, a = self._resolve_annot(aid)
            if str(a.Subtype) not in BOXED:
                raise EditError(f"{NAMES.get(str(a.Subtype), 'This')} annotations are tied to their "
                                "text and cannot be moved; delete and re-create instead")
            r = as_rect(rect).transform(self.model(page_no).P.inverse())
            a.Rect = r.list()
            build_appearance(self.pdf, a)
            self._touch(page_no)
            return aid

    def move_annotation(self, aid: str, dx: float, dy: float) -> str:
        page_no, a = self._resolve_annot(aid)
        r = Rect.of([float(v) for v in a.Rect]).transform(self.model(page_no).P)
        return self.set_annotation_rect(aid, [r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy])
