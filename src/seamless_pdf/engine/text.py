"""Seamless text editing on the glyph model (no PyMuPDF).

  1. Read: every glyph's exact origin, direction, size, font resource, text
     state (Tc/Tw/Tz/Ts) and colour come from the content-stream interpreter.
  2. Erase: the glyph's bytes are cut out of its TJ string and replaced by a
     TJ offset equal to its advance, so every other glyph on the page stays
     exactly where it was; neighbours can never be clipped.
  3. Redraw: new text uses the *same font resource* (Tf), the same text
     matrix, size, Tc/Tw/Tz and colour, so the run is reproduced exactly;
     characters the (subset) font lacks fall back per character to the
     closest standard-14 face, then to embedded DejaVu Sans.

A "segment" is a run of same-styled glyphs on one baseline with no wide gap
inside it; table cells that share a style become separate segments.
Invisible text (an OCR layer over a scan) is segmented per word.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import pikepdf

from .fonts import FontInfo
from .geom import Matrix, Rect
from .interp import Glyph, PageModel
from .util import EditError, as_rect, parse_color, rect_list, rgb_to_hex
from .writer import alpha_resource, append_content, family_std14, font_resource, insert_after_text_object, \
    std14_name, stream_resource, \
    text_block, write_stream

_ID = re.compile(r"^t:(\d+):(-?[\d.]+):(-?[\d.]+)(?:~(\d+))?$")
Op = pikepdf.Operator


@dataclass
class Seg:
    page: int
    id: str
    glyphs: list
    text: str
    origin: tuple
    dir: tuple
    up: tuple
    size: float
    font: FontInfo
    rgb: tuple
    alpha: float
    render: int
    bbox: Rect
    length: float

    @property
    def ocr(self) -> bool:
        return self.render == 3

    @property
    def first(self) -> Glyph:
        return self.glyphs[0]

    def proj(self, x: float, y: float) -> float:
        return (x - self.origin[0]) * self.dir[0] + (y - self.origin[1]) * self.dir[1]


@dataclass
class Style:
    """How to draw text exactly like an existing run."""
    primary: Optional[FontInfo]
    primary_obj: Optional[pikepdf.Object]
    std14: str                    # fallback standard-14 face
    bold: bool
    italic: bool
    tfs: float
    tc: float = 0.0
    tw: float = 0.0
    th: float = 1.0
    rise: float = 0.0
    rgb: tuple = (0.0, 0.0, 0.0)
    alpha: float = 1.0
    render: int = 0
    tm: Matrix = field(default_factory=Matrix)   # text matrix x CTM, PDF user space


def _seg_id(page_no: int, origin) -> str:
    return f"t:{page_no}:{origin[0]:.1f}:{origin[1]:.1f}"


def _same(a, b, tol=1e-3) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class TextOps:
    # -- segments ------------------------------------------------------------------
    def _segments(self, page_no: int) -> list[Seg]:
        model: PageModel = self.model(page_no)
        groups: list[list[Glyph]] = []
        cur: list[Glyph] = []
        space_run = 0.0

        def flush():
            nonlocal cur
            g = cur
            while g and not g[0].char.strip():
                g = g[1:]
            while g and not g[-1].char.strip():
                g = g[:-1]
            if g:
                groups.append(g)
            cur = []

        for g in model.glyphs:
            is_space = not g.char.strip()
            if cur:
                p = cur[-1]
                exp = (p.origin[0] + p.dir[0] * p.adv, p.origin[1] + p.dir[1] * p.adv)
                dx, dy = g.origin[0] - exp[0], g.origin[1] - exp[1]
                along = dx * p.dir[0] + dy * p.dir[1]
                perp = dx * p.up[0] + dy * p.up[1]
                size = max(p.size, 0.1)
                same_font = g.font is p.font or (
                    (g.font.fallback or p.font.fallback) and g.bt == p.bt and abs(along) < 0.15 * size)
                same_style = (g.stream == p.stream and same_font and abs(g.size - p.size) < 0.02 * size
                              and _same(g.rgb, p.rgb, 0.01) and (g.render == 3) == (p.render == 3)
                              and _same(g.dir, p.dir))
                word_gap = g.render == 3 and along > 0.12 * size   # OCR words have no space glyphs
                if (not same_style or word_gap or abs(perp) > 0.2 * size or along > 0.6 * size
                        or along < -0.35 * size):
                    flush()
                    space_run = 0.0
            if is_space:
                space_run += g.adv
                if g.render == 3 or space_run > 0.7 * max(g.size, 0.1):
                    flush()
                    continue
            else:
                space_run = 0.0
            cur.append(g)
        flush()

        segs: list[Seg] = []
        seen: dict[str, int] = {}
        for gl in groups:
            f = gl[0]
            bbox = gl[0].bbox
            for g in gl[1:]:
                bbox = bbox | g.bbox
            end = (gl[-1].origin[0] + gl[-1].dir[0] * gl[-1].adv, gl[-1].origin[1] + gl[-1].dir[1] * gl[-1].adv)
            length = (end[0] - f.origin[0]) * f.dir[0] + (end[1] - f.origin[1]) * f.dir[1]
            base = _seg_id(page_no, f.origin)
            k = seen.get(base, 0)
            seen[base] = k + 1
            segs.append(Seg(page=page_no, id=base if k == 0 else f"{base}~{k}", glyphs=gl,
                            text="".join(g.char or "�" for g in gl), origin=f.origin, dir=f.dir,
                            up=f.up, size=f.size,
                            font=next((g.font for g in gl if not g.font.fallback), f.font), rgb=f.rgb, alpha=f.alpha,
                            render=f.render, bbox=bbox, length=max(length, 0.1)))
        return segs

    def _resolve_seg(self, sid: str) -> tuple[Seg, list[Seg]]:
        m = _ID.match(sid or "")
        if not m:
            raise EditError(f"{sid!r} is not a text id")
        page_no = int(m.group(1))
        segs = self._segments(page_no)
        for s in segs:
            if s.id == sid:
                return s, segs
        raise EditError(f"text {sid!r} is no longer on page {page_no}; "
                        "list the page objects again to get fresh ids")

    def _public(self, s: Seg) -> dict:
        f = s.font
        return {"id": s.id, "kind": "text", "page": s.page, "text": s.text,
                "bbox": rect_list(s.bbox), "origin": [round(s.origin[0], 2), round(s.origin[1], 2)],
                "font": f.name, "size": round(s.size, 2), "color": rgb_to_hex(s.rgb),
                "bold": f.bold, "italic": f.italic, "ocr": s.ocr, "rotated": abs(s.dir[1]) > 1e-3,
                "quad": self._quad(s)}

    @staticmethod
    def _quad(s: Seg) -> list:
        """The line's outline along its baseline in page space: ascender-left,
        ascender-right, descender-right, descender-left. For rotated text this
        is much tighter than the axis-aligned bbox."""
        ox, oy = s.origin
        (dx, dy), (ux, uy) = s.dir, s.up
        asc = max(s.font.ascent, 0.5) * s.size
        desc = min(s.font.descent, -0.1) * s.size
        L = s.length
        pts = [(ox + ux * asc, oy + uy * asc), (ox + dx * L + ux * asc, oy + dy * L + uy * asc),
               (ox + dx * L + ux * desc, oy + dy * L + uy * desc), (ox + ux * desc, oy + uy * desc)]
        return [[round(x, 2), round(y, 2)] for x, y in pts]

    def text_objects(self, page_no: int) -> list[dict]:
        return [self._public(s) for s in self._segments(page_no)]

    def _lines(self, page_no: int) -> list[list[Seg]]:
        segs = self._segments(page_no)
        lines: list[list[Seg]] = []
        for s in sorted(segs, key=lambda s: (round(s.origin[1] / max(s.size * 0.5, 1)), s.origin[0])):
            if lines and abs(lines[-1][0].origin[1] - s.origin[1]) < 0.4 * max(s.size, 1) \
                    and abs(s.dir[1]) < 1e-3:
                lines[-1].append(s)
            else:
                lines.append([s])
        return lines

    def page_text(self, page_no: int) -> str:
        return "\n".join(" ".join(s.text for s in sorted(ln, key=lambda s: s.origin[0]))
                         for ln in self._lines(page_no))

    def find_text(self, query: str, match_case: bool = False, limit: int = 60) -> dict:
        with self.lock:
            if not query:
                raise EditError("empty query")
            q = query if match_case else query.lower()
            norm = (lambda t: t) if match_case else (lambda t: t.lower())
            matches, phrases = [], []
            for p in range(self.page_count):
                for line in self._lines(p):
                    line = sorted(line, key=lambda s: s.origin[0])
                    for s in line:
                        if q in norm(s.text):
                            matches.append(self._public(s))
                    joined, spans, pos = "", [], 0
                    for s in line:
                        spans.append((pos, pos + len(s.text), s))
                        joined += s.text + " "
                        pos = len(joined)
                    for m in re.finditer(re.escape(q), norm(joined)):
                        inside = [s for a, b, s in spans if a < m.end() and b > m.start()]
                        if len(inside) > 1:
                            phrases.append({"page": p, "ids": [s.id for s in inside],
                                            "texts": [s.text for s in inside]})
                if len(matches) >= limit:
                    break
            return {"match_count": len(matches), "matches": matches[:limit],
                    "multi_segment_matches": phrases[:20]}

    # -- style / fonts ------------------------------------------------------------------
    def _style_for(self, seg: Optional[Seg], family: Optional[str] = None,
                   bold: Optional[bool] = None, italic: Optional[bool] = None) -> Style:
        f = seg.font if seg else None
        b = f.bold if f else False
        i = f.italic if f else False
        restyled = f is not None and ((bold is not None and bold != b) or (italic is not None and italic != i))
        if bold is not None:
            b = bool(bold)
        if italic is not None:
            i = bool(italic)
        std = family_std14(family, b, i) if family else std14_name(b, i, bool(f and f.serif),
                                                                    bool(f and f.mono))
        if seg is None:
            return Style(primary=None, primary_obj=None, std14=std, bold=b, italic=i, tfs=11.0)
        g = seg.first
        st = Style(primary=None, primary_obj=None, std14=std, bold=b, italic=i, tfs=g.tfs or 1.0,
                   tc=g.tc, tw=g.tw, th=g.th, rise=g.rise, rgb=g.rgb, alpha=g.alpha,
                   render=g.render, tm=g.text_ctm)
        if not family and not restyled and f.subtype != "Type3":
            self.learn_glyph_usage()
            st.primary, st.primary_obj = f, f.obj
        return st

    def _runs(self, text: str, st: Style) -> list[tuple[str, FontInfo, pikepdf.Object]]:
        runs: list[tuple[str, FontInfo, pikepdf.Object]] = []
        std_obj, std_info = self.fontbook.std14(st.std14)
        for c in text:
            if st.primary is not None and st.primary.can_draw(c):
                info, obj = st.primary, st.primary_obj
            elif c in std_info.uni2code():
                info, obj = std_info, std_obj
            else:
                obj, info = self.fontbook.unicode_fallback(st.bold, st.italic, text)
                if c not in info.uni2code():
                    c = "?"
                    info, obj = std_info, std_obj
            if runs and runs[-1][1] is info:
                runs[-1] = (runs[-1][0] + c, info, obj)
            else:
                runs.append((c, info, obj))
        return runs

    def _measure(self, text: str, st: Style) -> float:
        """Advance of `text` in text-space units (what Tm translations use)."""
        total = 0.0
        for run, info, _ in self._runs(text, st):
            u2c = info.uni2code()
            for c in run:
                code = u2c.get(c, 0)
                space = st.tw if (c == " " and info.code_len == 1) else 0.0
                total += (info.width(code) * st.tfs + st.tc + space) * st.th
        return total

    def _units(self, st: Style, page_no: int) -> float:
        """Page units per text-space unit along the baseline."""
        P = self.model(page_no).P
        v = (st.tm * P).apply_vec(1, 0)
        return (v[0] ** 2 + v[1] ** 2) ** 0.5 or 1.0

    def _fonts_used(self, text: str, st: Style) -> str:
        """Fonts that actually draw `text`, original first, e.g. 'ArialMT + Helvetica'."""
        names: list[str] = []
        for t, info, _ in self._runs(text, st):
            if t.strip() and info.name not in names:
                names.append(info.name)
        if st.primary is not None and st.primary.name in names:
            names.remove(st.primary.name)
            names.insert(0, st.primary.name)
        return " + ".join(names) or (st.primary.name if st.primary else st.std14)

    def _matched(self, text: str, st: Style) -> bool:
        return all(info is st.primary or (st.primary is not None and info.std14 and
                                          info.std14 == st.primary.std14)
                   for t, info, _ in self._runs(text, st) if t.strip())

    # -- low-level erase / draw ------------------------------------------------------
    def _erase(self, page_no: int, glyphs: list[Glyph]) -> None:
        """Cut glyphs out of their TJ strings, leaving equal-width offsets."""
        model = self.model(page_no)
        by_instr: dict[tuple, list[Glyph]] = defaultdict(list)
        for g in glyphs:
            by_instr[(g.stream, g.instr)].append(g)
        touched = set()
        for (skey, idx), gl in by_instr.items():
            si = model.streams[skey]
            ins = si.instrs[idx]
            arr = list(ins.operands[0])
            by_elem: dict[int, list[Glyph]] = defaultdict(list)
            for g in gl:
                by_elem[g.elem].append(g)
            new: list = []
            for ei, el in enumerate(arr):
                if ei not in by_elem:
                    new.append(el)
                    continue
                data = bytes(el)
                cursor = 0
                for g in sorted(by_elem[ei], key=lambda g: g.pos):
                    if g.pos > cursor:
                        new.append(pikepdf.String(data[cursor:g.pos]))
                    tfs = g.tfs or 1.0
                    space = g.tw if (g.code == 32 and g.nbytes == 1) else 0.0
                    disp = g.font.width(g.code) * tfs + g.tc + space
                    new.append(round(-disp * 1000.0 / tfs, 4))
                    cursor = g.pos + g.nbytes
                if cursor < len(data):
                    new.append(pikepdf.String(data[cursor:]))
            merged: list = []
            for el in new:
                if not isinstance(el, pikepdf.String) and merged and not isinstance(merged[-1], pikepdf.String):
                    merged[-1] = round(float(merged[-1]) + float(el), 4)
                else:
                    merged.append(el)
            si.instrs[idx] = pikepdf.ContentStreamInstruction([pikepdf.Array(merged)], Op("TJ"))
            touched.add(skey)
        for skey in touched:
            write_stream(self.pdf, model.streams[skey])
        self._touch(page_no)

    def _paint(self, page_no: int, text: str, st: Style, tm: Optional[Matrix] = None,
               at: Optional[Glyph] = None) -> None:
        """Draw `text` from text matrix `tm` (PDF user space). With `at` (a glyph
        of the text being replaced) the new text goes into the same content
        stream right after that glyph's text object, so it keeps its place in
        the painting order: a watermark stays behind the table drawn over it,
        and a label stays under a box drawn after it. Otherwise it is drawn on
        top of the page."""
        if not text:
            return
        tm = tm or st.tm
        if at is not None and at.ctm is not None and abs(at.ctm.det) > 1e-12:
            si = self.model(page_no).streams.get(at.stream)
            if si is not None and at.instr < len(si.instrs):
                runs = [(run, info, stream_resource(self.pdf, si, obj, "/Font", "SF"))
                        for run, info, obj in self._runs(text, st)]
                gs = None
                if st.alpha < 0.999 and st.render != 3:
                    gobj = self.pdf.make_indirect(pikepdf.Dictionary(
                        Type=pikepdf.Name.ExtGState, ca=round(st.alpha, 3), CA=round(st.alpha, 3)))
                    gs = stream_resource(self.pdf, si, gobj, "/ExtGState", "SA")
                local = tm * at.ctm.inverse()
                data = text_block(runs, local.list(), st.tfs, st.rgb, st.tc, st.tw, st.th, st.rise,
                                  st.render, gs)
                if insert_after_text_object(self.pdf, si, at.instr, data):
                    self._touch(page_no)
                    return
        page = self.page(page_no)
        runs = []
        for run, info, obj in self._runs(text, st):
            runs.append((run, info, font_resource(page, obj)))
        gs = alpha_resource(self.pdf, page, st.alpha) if st.alpha < 0.999 and st.render != 3 else None
        data = text_block(runs, list(tm), st.tfs, st.rgb, st.tc, st.tw, st.th, st.rise,
                          st.render, gs)
        append_content(self.pdf, page, data)
        self._touch(page_no)

    @staticmethod
    def _anchor(seg: Seg, segs: list[Seg]) -> str:
        if abs(seg.dir[1]) > 1e-3:
            return "left"
        x0, x1 = seg.bbox.x0, seg.bbox.x1
        cx = (x0 + x1) / 2
        left = right = center = 0
        for o in segs:
            if o is seg or o.ocr != seg.ocr:
                continue
            left += abs(o.bbox.x0 - x0) < 1.0
            right += abs(o.bbox.x1 - x1) < 1.0
            center += abs((o.bbox.x0 + o.bbox.x1) / 2 - cx) < 1.0
        if right > left and right >= center:
            return "right"
        if center > left and center > right:
            return "center"
        return "left"

    # -- operations ----------------------------------------------------------------------
    def edit_text(self, sid: str, text: Optional[str] = None, font_size: Optional[float] = None,
                  color=None, anchor: str = "auto", font: Optional[str] = None,
                  bold: Optional[bool] = None, italic: Optional[bool] = None) -> dict:
        with self.transaction("Edit text"):
            seg, segs = self._resolve_seg(sid)
            new_text = seg.text if text is None else str(text).replace("\n", " ")
            if (new_text == seg.text and not font_size and color is None and font is None
                    and bold is None and italic is None):
                return {"id": sid, "changed": False}
            if seg.ocr:
                return self._edit_scanned(seg, segs, new_text, font_size, color, font, bold, italic)
            st = self._style_for(seg, font, bold, italic)
            rgb = parse_color(color)
            if rgb is not None:
                st.rgb = rgb
            if font_size:
                st.tfs = st.tfs * float(font_size) / max(seg.size, 0.01)
            how = self._anchor(seg, segs) if anchor in (None, "", "auto") else anchor
            if how not in ("left", "right", "center"):
                raise EditError("anchor must be auto, left, right or center")
            units = self._units(st, seg.page)
            old_len = seg.length / units
            new_len = self._measure(new_text, st) - st.tc * st.th
            shift = {"left": 0.0, "right": old_len - new_len, "center": (old_len - new_len) / 2}[how]
            tm = Matrix.translate(shift, 0) * st.tm
            self._erase(seg.page, seg.glyphs)
            if new_text.strip():
                self._paint(seg.page, new_text, st, tm, at=seg.glyphs[-1])
            origin = (seg.origin[0] + seg.dir[0] * shift * units, seg.origin[1] + seg.dir[1] * shift * units)
            new_id = _seg_id(seg.page, origin) if new_text.strip() else None
            self._renamed(sid, new_id)
            return {"id": new_id, "changed": True, "anchor": how,
                    "font_matched": self._matched(new_text, st), "font_used": self._fonts_used(new_text, st)}

    def delete_text(self, sid: str) -> dict:
        with self.transaction("Delete text"):
            seg, _ = self._resolve_seg(sid)
            if seg.ocr:
                self._patch_scan(seg)
            self._erase(seg.page, seg.glyphs)
            return {"deleted": sid}

    def move_text(self, sid: str, dx: float, dy: float) -> str:
        with self.transaction("Move text"):
            seg, segs = self._resolve_seg(sid)
            if seg.ocr:
                return self._move_scanned(seg, segs, dx, dy)
            st = self._style_for(seg)
            P = self.model(seg.page).P
            ux, uy = P.inverse().apply_vec(dx, dy)
            tm = st.tm * Matrix.translate(ux, uy)
            self._erase(seg.page, seg.glyphs)
            self._paint(seg.page, seg.text, st, tm, at=seg.glyphs[-1])
            new_id = _seg_id(seg.page, (seg.origin[0] + dx, seg.origin[1] + dy))
            self._renamed(sid, new_id)
            return new_id

    def replace_all(self, find: str, replace: str, match_case: bool = False,
                    pages: Optional[list[int]] = None) -> dict:
        if not find:
            raise EditError("empty search text")
        pat = re.compile(re.escape(find), 0 if match_case else re.IGNORECASE)
        with self.transaction("Replace all"):
            ids = []
            for p in (pages if pages else range(self.page_count)):
                targets = [s.id for s in self._segments(p) if pat.search(s.text)]
                for sid in targets:
                    seg, _ = self._resolve_seg(sid)
                    res = self.edit_text(sid, pat.sub(lambda _m: replace, seg.text))
                    ids.append(res["id"])
            return {"replaced_segments": len(ids), "ids": ids}

    def insert_text(self, page_no: int, x: float, y: float, text: str,
                    font_size: Optional[float] = None, color=None, font: Optional[str] = None,
                    bold: Optional[bool] = None, italic: Optional[bool] = None,
                    width: Optional[float] = None, align: str = "left",
                    match_style_of: Optional[str] = None, line_height: Optional[float] = None) -> dict:
        if not text:
            raise EditError("empty text")
        with self.transaction("Insert text"):
            self.page(page_no)
            x, y = float(x), float(y)
            style: Optional[Seg] = None
            if match_style_of:
                style, _ = self._resolve_seg(match_style_of)
            elif font is None or font_size is None or color is None:
                visible = [s for s in self._segments(page_no) if not s.ocr and abs(s.dir[1]) < 1e-3]
                above = [s for s in visible if s.origin[1] <= y + 1 and y - s.origin[1] < 4 * s.size
                         and s.bbox.x0 - 2 * s.size <= x <= s.bbox.x1 + 2 * s.size]
                if above:
                    style = min(above, key=lambda s: (y - s.origin[1], abs(s.bbox.x0 - x)))
                elif visible:
                    style = min(visible, key=lambda s: s.bbox.distance_to(x, y))
            st = self._style_for(style, font, bold, italic)
            size = float(font_size) if font_size else (style.size if style else 11.0)
            st.tfs, st.tw, st.rise, st.render = size, 0.0, 0.0, 0
            if style is None or font_size:
                st.th = 1.0 if style is None else st.th
            rgb = parse_color(color)
            if rgb is not None:
                st.rgb = rgb
            elif style is None:
                st.rgb = (0.0, 0.0, 0.0)
            if st.primary is not None:
                missing = sum(1 for c in text if not c.isspace() and not st.primary.can_draw(c))
                if missing > len(text) // 4:
                    st.primary = st.primary_obj = None
            P = self.model(page_no).P
            Pi = P.inverse()
            lh = float(line_height) if line_height else size * 1.2
            lines: list[str] = []
            for para in str(text).split("\n"):
                lines.extend(self._wrap(para, st, float(width)) if width else [para])
            rect = None
            for i, line in enumerate(lines):
                w = self._measure(line, st)
                off = 0.0
                if width and align == "right":
                    off = float(width) - w
                elif width and align == "center":
                    off = (float(width) - w) / 2
                ox, oy = x + off, y + i * lh
                if line.strip():
                    ux, uy = Pi.apply(ox, oy)
                    self._paint(page_no, line, st, Matrix(1, 0, 0, 1, ux, uy))
                r = Rect(ox, oy - size, ox + w, oy + size * 0.3)
                rect = r if rect is None else rect | r
            matched = all(self._matched(ln, st) for ln in lines) and st.primary is not None
            return {"rect": rect_list(rect), "lines": len(lines), "size": size, "font_matched": matched,
                    "font_used": self._fonts_used("\n".join(lines), st)}

    def _wrap(self, para: str, st: Style, width: float) -> list[str]:
        words, lines, cur = para.split(" "), [], ""
        for w in words:
            cand = w if not cur else cur + " " + w
            if cur and self._measure(cand, st) > width:
                lines.append(cur)
                cur = w
            else:
                cur = cand
        lines.append(cur)
        return lines

    def text_in_rect(self, page_no: int, rect) -> list[str]:
        r = as_rect(rect)
        return [s.id for s in self._segments(page_no) if s.bbox.intersects(r)]
