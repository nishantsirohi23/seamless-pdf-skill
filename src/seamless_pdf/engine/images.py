"""Images: list, insert, replace, move/resize, delete.

Placements come from the content-stream interpreter, which knows the exact
`Do` instruction and the CTM that paints each one. Moving or resizing
wraps that one instruction as `q X cm /Im Do Q` with X = T x M x T^-1
(T: the image's CTM, M: the change in PDF space), so the pixels, masks and
compression of the image are never touched. Deleting removes the
instruction.
"""
from __future__ import annotations

import io
import re
import zlib

import pikepdf

from .geom import Matrix, Rect
from .util import EditError, as_rect, rect_list
from .writer import append_content, write_stream

_ID = re.compile(r"^i:(\d+):(\d+)$")
Op = pikepdf.Operator


def image_xobject(pdf: pikepdf.Pdf, data: bytes) -> tuple[pikepdf.Stream, int, int]:
    """Encode image bytes as a PDF image XObject (JPEG kept as-is)."""
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(data))
        fmt = im.format
        im = ImageOps.exif_transpose(im)
    except Exception as e:
        raise EditError(f"cannot read image: {e}")
    w, h = im.size
    if fmt == "JPEG" and im.mode in ("RGB", "L", "CMYK"):
        cs = {"RGB": pikepdf.Name.DeviceRGB, "L": pikepdf.Name.DeviceGray,
              "CMYK": pikepdf.Name.DeviceCMYK}[im.mode]
        try:
            orientation = Image.open(io.BytesIO(data)).getexif().get(0x0112, 1)
        except Exception:
            orientation = 1
        if orientation in (None, 1):
            raw = data                       # JPEG passes through untouched
        else:
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=95)  # re-encode only to apply rotation
            raw = buf.getvalue()
        stream = pikepdf.Stream(pdf, raw)
        stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        stream.Width, stream.Height, stream.BitsPerComponent = w, h, 8
        stream.ColorSpace, stream.Filter = cs, pikepdf.Name.DCTDecode
        if im.mode == "CMYK":
            stream.Decode = pikepdf.Array([1, 0, 1, 0, 1, 0, 1, 0])
        return stream, w, h
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    rgba = im.convert("RGBA") if has_alpha else im.convert("RGB")
    rgb = rgba.convert("RGB")
    stream = pikepdf.Stream(pdf, zlib.compress(rgb.tobytes(), 6))
    stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    stream.Width, stream.Height, stream.BitsPerComponent = w, h, 8
    stream.ColorSpace, stream.Filter = pikepdf.Name.DeviceRGB, pikepdf.Name.FlateDecode
    if has_alpha:
        alpha = rgba.getchannel("A").tobytes()
        mask = pikepdf.Stream(pdf, zlib.compress(alpha, 6))
        mask.Type, mask.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        mask.Width, mask.Height, mask.BitsPerComponent = w, h, 8
        mask.ColorSpace, mask.Filter = pikepdf.Name.DeviceGray, pikepdf.Name.FlateDecode
        stream.SMask = mask
    return stream, w, h


def _fit(rect: Rect, w: int, h: int) -> Rect:
    s = min(rect.width / w, rect.height / h)
    nw, nh = w * s, h * s
    return Rect(rect.x0 + (rect.width - nw) / 2, rect.y0 + (rect.height - nh) / 2,
                rect.x0 + (rect.width + nw) / 2, rect.y0 + (rect.height + nh) / 2)


def place_image(doc, page_no: int, xobj: pikepdf.Object, rect: Rect) -> None:
    """Paint an image XObject into `rect` (page space)."""
    page = doc.page(page_no)
    name = page.add_resource(xobj, pikepdf.Name.XObject, prefix="SI")
    P = doc.model(page_no).P.inverse()
    x0, y0 = P.apply(rect.x0, rect.y1)   # bottom-left in PDF space
    m = [rect.width, 0, 0, rect.height, x0, y0]
    append_content(doc.pdf, page, f"q {' '.join(f'{v:.4f}' for v in m)} cm {name} Do Q\n".encode())


class ImageOps:
    def _placements(self, page_no: int) -> list[dict]:
        # ids follow paint order: stable across re-saves (object numbers are not)
        model = self.model(page_no)
        out = []
        for k, pl in enumerate(model.images):
            w = int(pl.obj.get("/Width", 0)) if pl.obj is not None else 0
            h = int(pl.obj.get("/Height", 0)) if pl.obj is not None else 0
            out.append({"id": f"i:{page_no}:{k}", "k": k, "pl": pl, "bbox": pl.bbox, "width": w, "height": h})
        return out

    def image_objects(self, page_no: int) -> list[dict]:
        out = []
        for p in self._placements(page_no):
            if p["bbox"].is_empty:
                continue
            out.append({"id": p["id"], "kind": "image", "page": page_no, "bbox": rect_list(p["bbox"]),
                        "pixel_size": [p["width"], p["height"]],
                        "movable": not p["pl"].inline})
        return out

    def _resolve_image(self, iid: str) -> tuple[int, dict]:
        m = _ID.match(iid or "")
        if not m:
            raise EditError(f"{iid!r} is not an image id")
        page_no = int(m.group(1))
        for p in self._placements(page_no):
            if p["id"] == iid:
                if p["pl"].inline:
                    raise EditError("this is an inline image, which cannot be edited")
                return page_no, p
        raise EditError(f"image {iid!r} is no longer on page {page_no}")

    def _rewrite(self, page_no: int, pl, new_instrs: list) -> None:
        si = self.model(page_no).streams[pl.stream]
        si.instrs[pl.instr:pl.instr + 1] = new_instrs
        write_stream(self.pdf, si)
        self._touch(page_no)

    def transform_image(self, iid: str, translate=None, rect=None) -> str:
        with self.transaction("Move image"):
            page_no, p = self._resolve_image(iid)
            P = self.model(page_no).P
            old = p["bbox"]
            if rect is not None:
                new = as_rect(rect)
                sx, sy = new.width / old.width, new.height / old.height
                M_page = Matrix(sx, 0, 0, sy, new.x0 - old.x0 * sx, new.y0 - old.y0 * sy)
            else:
                M_page = Matrix.translate(float(translate[0]), float(translate[1]))
            M_pdf = P * M_page * P.inverse()
            T = p["pl"].ctm
            if abs(T.det) < 1e-12:
                raise EditError("image has a degenerate transform")
            X = T * M_pdf * T.inverse()
            ins = self.model(page_no).streams[p["pl"].stream].instrs[p["pl"].instr]
            self._rewrite(page_no, p["pl"], [
                pikepdf.ContentStreamInstruction([], Op("q")),
                pikepdf.ContentStreamInstruction([round(v, 6) for v in X], Op("cm")),
                ins,
                pikepdf.ContentStreamInstruction([], Op("Q"))])
            return iid

    def delete_image(self, iid: str) -> dict:
        with self.transaction("Delete image"):
            page_no, p = self._resolve_image(iid)
            self._rewrite(page_no, p["pl"], [])
            return {"deleted": iid}

    def insert_image(self, page_no: int, asset_id: str, rect, keep_proportion: bool = True) -> dict:
        with self.transaction("Insert image"):
            self.page(page_no)
            a = self.asset(asset_id)
            r = as_rect(rect)
            before = {p["id"] for p in self._placements(page_no)}
            xobj, w, h = image_xobject(self.pdf, a.data)
            xobj = self.pdf.make_indirect(xobj)
            place_image(self, page_no, xobj, _fit(r, w, h) if keep_proportion else r)
            self._touch(page_no)
            new = [p["id"] for p in self._placements(page_no) if p["id"] not in before]
            return {"id": new[0] if new else None, "rect": rect_list(r)}

    def replace_image(self, iid: str, asset_id: str) -> dict:
        with self.transaction("Replace image"):
            page_no, p = self._resolve_image(iid)
            a = self.asset(asset_id)
            fresh, _, _ = image_xobject(self.pdf, a.data)
            target = p["pl"].obj
            for k in list(target.keys()):
                if k not in ("/Type", "/Subtype", "/Length"):
                    del target[k]
            for k, v in fresh.items():
                if k != "/Length":
                    target[k] = v
            target.write(fresh.read_raw_bytes(), filter=fresh.get("/Filter"))
            for pg in range(self.page_count):
                if any(pl.obj is not None and pl.obj.is_indirect and pl.obj.objgen == target.objgen
                       for pl in self.model(pg).images):
                    self._touch(pg)
            self._touch(page_no)
            return {"id": iid}
