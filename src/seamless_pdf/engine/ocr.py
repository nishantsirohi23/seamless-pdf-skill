"""Scanned pages: detection, OCR text layer (Tesseract CLI), and editing
scanned words.

OCR words become an invisible text layer (render mode 3), the standard
"searchable PDF" technique, which also makes each scanned word an editable
text object. Editing one samples the paper and ink colours, patches the
word and draws the replacement in Times or Helvetica (chosen per line by
which reproduces the ink better), sized and placed from the word's inked
pixels. Moving a scanned word moves its actual pixels.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from statistics import median
from typing import Optional

from .geom import Matrix, Rect
from .interp import rotation_matrix
from .util import EditError, parse_color
from .writer import append_content, color_op, family_std14

_SCAN_COVERAGE = 0.5
_SCAN_MAX_CHARS = 40
# approximate inked extents (em) of standard faces, by character class
_INK = {"Helvetica": {"cap": 0.718, "asc": 0.718, "x": 0.523, "desc": -0.207},
        "Times": {"cap": 0.662, "asc": 0.683, "x": 0.448, "desc": -0.217}}


def _tesseract() -> Optional[str]:
    for p in (shutil.which("tesseract"), "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract",
              "/usr/bin/tesseract"):
        if p and os.path.exists(p):
            return p
    return None


def _ink_em(text: str, family: str) -> tuple[float, float]:
    m = _INK["Times" if family.startswith("Times") else "Helvetica"]
    top = bottom = 0.0
    for c in text:
        if c.isupper() or c.isdigit() or c in "bdfhklt#$%&@?!/\\|()[]{}":
            top = max(top, m["cap"] if not c.islower() else m["asc"])
        elif c.isalpha():
            top = max(top, m["x"])
        if c in "gjpqyQ,;()[]{}|":
            bottom = min(bottom, m["desc"])
    return top or m["x"], bottom


class OcrOps:
    def is_scanned(self, page_no: int) -> bool:
        key = ("scanned", self._dirty, page_no)
        if key in self._misc_cache:
            return self._misc_cache[key]
        model = self.model(page_no)
        visible = sum(1 for g in model.glyphs if g.render != 3 and g.char.strip())
        result = False
        if visible <= _SCAN_MAX_CHARS:
            area = model.width * model.height
            page_rect = Rect(0, 0, model.width, model.height)
            covered = max([(im.bbox & page_rect).area for im in model.images] or [0])
            result = area > 0 and covered / area >= _SCAN_COVERAGE
        self._misc_cache[key] = result
        return result

    def has_ocr_layer(self, page_no: int) -> bool:
        return any(g.render == 3 for g in self.model(page_no).glyphs)

    # -- OCR -------------------------------------------------------------------------------
    def ocr_page(self, page_no: int, language: str = "eng", dpi: int = 300) -> dict:
        exe = _tesseract()
        if not exe:
            raise EditError("OCR needs Tesseract on the backend machine: macOS `brew install tesseract`, "
                            "Debian/Ubuntu `apt install tesseract-ocr`; extra languages need their traineddata.")
        with self.transaction("OCR page"):
            scale = dpi / 72.0
            img = self.render_image(page_no, scale)
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "page.png")
                img.save(path)
                r = subprocess.run([exe, path, "stdout", "-l", language, "--dpi", str(dpi), "tsv"],
                                   capture_output=True, timeout=300)
            if r.returncode != 0:
                raise EditError(f"OCR failed: {r.stderr.decode(errors='ignore')[-300:]}")
            rows = [ln.split("\t") for ln in r.stdout.decode("utf-8", "replace").splitlines()[1:]]
            words = [w for w in rows if len(w) == 12 and w[0] == "5" and w[11].strip()]
            _, pw, ph, rot = self.geometry(page_no)
            back = rotation_matrix(rot, pw, ph).inverse()
            lines: dict[tuple, list] = {}
            for w in words:
                lines.setdefault((w[2], w[3], w[4]), []).append(w)
            placed = []
            for ws in lines.values():
                boxes = []
                for w in ws:
                    x, y, bw, bh = (int(v) / scale for v in w[6:10])
                    boxes.append((w[11], Rect(x, y, x + bw, y + bh).transform(back)))
                plain = [b.y1 for t, b in boxes if not set(t) & set("gjpqy,;()")]
                base = median(plain) if plain else median(b.y1 for _, b in boxes) - 0.2 * median(
                    b.height for _, b in boxes)
                for t, b in boxes:
                    top, bottom = _ink_em(t, "Helvetica")
                    size = max(4.0, (base - b.y0) / max(top, 0.3))
                    placed.append((t, b.x0, base, size, b.width))
            self._clear_ocr_layer(page_no)
            self._write_ocr_layer(page_no, placed)
            self._touch(page_no)
            return {"page": page_no, "words": len(placed)}

    def _clear_ocr_layer(self, page_no: int) -> None:
        inv = [g for g in self.model(page_no).glyphs if g.render == 3]
        if inv:
            self._erase(page_no, inv)

    def _write_ocr_layer(self, page_no: int, words) -> None:
        """words: (text, x, baseline y, font size, ink width) in page space."""
        from .text import Style
        from reportlab.pdfbase.pdfmetrics import stringWidth
        Pi = self.model(page_no).P.inverse()
        page = self.page(page_no)
        from .writer import font_resource, text_block
        obj, info = self.fontbook.std14("Helvetica")
        res = font_resource(page, obj)
        chunks = []
        for text, x, y, size, width in words:
            text = "".join(c if c in info.uni2code() else "?" for c in text)
            natural = stringWidth(text, "Helvetica", size)
            th = max(0.2, min(5.0, width / natural)) if natural > 0 else 1.0
            ux, uy = Pi.apply(x, y)
            chunks.append(text_block([(text, info, res)], [1, 0, 0, 1, ux, uy], size, (0, 0, 0),
                                     th=th, render=3))
        if chunks:
            append_content(self.pdf, page, b"".join(chunks))
        self._touch(page_no)

    # -- colours / patching ---------------------------------------------------------------------
    def _sample_colors(self, page_no: int, rect: Rect, size: float):
        """(paper rgb, ink rgb, ink bbox page-space or None) from a 200 dpi render."""
        pad = max(1.5, 0.3 * size)
        _, pw, ph, _ = self.geometry(page_no)
        outer = rect.expand(pad) & Rect(0, 0, pw, ph)
        scale = 200 / 72
        img = self.render_image(page_no, scale, outer.list())
        w, h = img.size
        px = img.load()
        sx, sy = w / max(outer.width, 1e-6), h / max(outer.height, 1e-6)
        ix0, iy0 = int((rect.x0 - outer.x0) * sx), int((rect.y0 - outer.y0) * sy)
        ix1, iy1 = int((rect.x1 - outer.x0) * sx), int((rect.y1 - outer.y0) * sy)
        ring = ([], [], [])
        inner = []
        for y in range(h):
            for x in range(w):
                p = px[x, y]
                if ix0 <= x < ix1 and iy0 <= y < iy1:
                    inner.append((p, x, y))
                else:
                    for c in range(3):
                        ring[c].append(p[c])
        if not ring[0]:
            return (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), None
        bg = tuple(median(ch) for ch in ring)
        lum = lambda p: 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]  # noqa: E731
        dark = lum(bg) > 110
        hits = [(p, x, y) for p, x, y in inner if (lum(bg) - lum(p)) * (1 if dark else -1) > 60]
        box = None
        if hits:
            xs, ys = [x for _, x, _ in hits], [y for _, _, y in hits]
            box = Rect(outer.x0 + min(xs) / sx, outer.y0 + min(ys) / sy,
                       outer.x0 + (max(xs) + 1) / sx, outer.y0 + (max(ys) + 1) / sy)
            inks = sorted((p for p, _, _ in hits), key=lum, reverse=not dark)
            core = inks[: max(1, len(inks) // 2)]
            ink = tuple(median(p[c] for p in core) for c in range(3))
        else:
            ink = (0, 0, 0) if dark else (255, 255, 255)
        return tuple(c / 255 for c in bg), tuple(c / 255 for c in ink), box

    @staticmethod
    def _patch_rect(seg) -> Rect:
        return seg.bbox.expand(0.08 * seg.size, 0.12 * seg.size)

    def _fill_rect(self, page_no: int, rect: Rect, rgb) -> None:
        Pi = self.model(page_no).P.inverse()
        r = rect.transform(Pi)
        append_content(self.pdf, self.page(page_no),
                       f"q {color_op(rgb)} {r.x0:.3f} {r.y0:.3f} {r.width:.3f} {r.height:.3f} re f Q\n".encode())

    def _patch_scan(self, seg, bg=None) -> None:
        if bg is None:
            bg, _, _ = self._sample_colors(seg.page, seg.bbox, seg.size)
        _, pw, ph, _ = self.geometry(seg.page)
        self._fill_rect(seg.page, self._patch_rect(seg) & Rect(0, 0, pw, ph), bg)

    def _scan_fit(self, text: str, ink: Rect, face: str):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        top, bottom = _ink_em(text, face)
        size = ink.height / max(top - bottom, 0.2)
        width = stringWidth(text, face, size)
        err = abs(width - ink.width) / max(ink.width, 1e-6)
        return err, size, bottom, width

    def _scan_family(self, seg, segs, bold, italic) -> str:
        line = [o for o in segs if o.ocr and abs(o.origin[1] - seg.origin[1]) < 0.5 * seg.size]
        line = sorted(line, key=lambda o: abs(o.origin[0] - seg.origin[0]))[:12]
        score = {"times": 0.0, "helvetica": 0.0}
        for o in line:
            _, _, box = self._sample_colors(o.page, o.bbox, o.size)
            if box is None:
                continue
            for fam in score:
                score[fam] += self._scan_fit(o.text, box, family_std14(fam, bool(bold), bool(italic)))[0] \
                    * len(o.text)
        return min(score, key=score.get)

    def _edit_scanned(self, seg, segs, new_text: str, font_size, color, font, bold, italic) -> dict:
        from .text import Style, _seg_id
        bg, ink, box = self._sample_colors(seg.page, seg.bbox, seg.size)
        rgb = parse_color(color) or ink
        fam = font or self._scan_family(seg, segs, bold, italic)
        face = family_std14(fam, bool(bold), bool(italic))
        if box is not None and abs(seg.dir[1]) < 1e-3:
            _, size, bottom, width = self._scan_fit(seg.text, box, face)
            th = max(0.85, min(1.15, box.width / width)) if width > 0 else 1.0
            origin = (box.x0, box.y1 + bottom * size)
        else:
            size, th, origin = seg.size, 1.0, seg.origin
        if font_size:
            size = float(font_size)
        self._patch_scan(seg, bg)
        self._erase(seg.page, seg.glyphs)
        if new_text.strip():
            Pi = self.model(seg.page).P.inverse()
            ux, uy = Pi.apply(*origin)
            st = Style(primary=None, primary_obj=None, std14=face, bold=bool(bold), italic=bool(italic),
                       tfs=size, th=th, rgb=rgb, tm=Matrix(1, 0, 0, 1, ux, uy))
            self._paint(seg.page, new_text, st)
        new_id = _seg_id(seg.page, origin) if new_text.strip() else None
        self._renamed(seg.id, new_id)
        return {"id": new_id, "changed": True, "anchor": "left", "font_matched": False,
                "font_used": face, "scanned": True}

    def _move_scanned(self, seg, segs, dx: float, dy: float) -> str:
        from .images import image_xobject, place_image
        from .text import _seg_id
        _, pw, ph, _ = self.geometry(seg.page)
        src = self._patch_rect(seg) & Rect(0, 0, pw, ph)
        img = self.render_image(seg.page, 300 / 72, src.list())
        buf = io.BytesIO()
        img.save(buf, "PNG")
        self._patch_scan(seg)
        self._erase(seg.page, seg.glyphs)
        xobj, _, _ = image_xobject(self.pdf, buf.getvalue())
        place_image(self, seg.page, self.pdf.make_indirect(xobj), src.offset(dx, dy))
        origin = (seg.origin[0] + dx, seg.origin[1] + dy)
        self._write_ocr_layer(seg.page, [(seg.text, origin[0], origin[1], seg.size, seg.length)])
        self._touch(seg.page)
        new_id = _seg_id(seg.page, origin)
        self._renamed(seg.id, new_id)
        return new_id
