"""Document core on pikepdf: loading, transactions, undo/redo, rendering
(PDFium via pypdfium2), the page models, ids and assets.

Every mutation runs inside `transaction()`: it snapshots the document bytes
first and restores them on any exception, so a failed edit never leaves a
half-edited page; committed transactions become undo entries.
"""
from __future__ import annotations

import base64
import io
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

import pikepdf

from .geom import Matrix, Rect
from .interp import Interpreter, PageModel, page_geometry, rotation_matrix
from .util import EditError, rect_list
from .writer import FontBook

MAX_UNDO = 50
MAX_UNDO_BYTES = 400 * 1024 * 1024


@dataclass
class _Tx:
    label: str
    group: Optional[str]
    before: bytes
    pages: set = field(default_factory=set)
    result_ids: dict = field(default_factory=dict)


@dataclass
class _Entry:
    label: str
    group: Optional[str]
    state: bytes
    pages: set


@dataclass
class Asset:
    id: str
    name: str
    data: bytes
    width: int
    height: int


def open_pdf(data: bytes) -> pikepdf.Pdf:
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError:
        raise EditError("the PDF is password protected")
    except Exception as e:
        raise EditError(f"not a readable PDF: {e}")
    if len(pdf.pages) == 0:
        raise EditError("the PDF has no pages")
    return pdf


def pdf_to_bytes(pdf: pikepdf.Pdf, final: bool = False) -> bytes:
    buf = io.BytesIO()
    if final:
        pdf.save(buf, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    else:
        pdf.save(buf, compress_streams=False, normalize_content=False)
    return buf.getvalue()


class DocCore:
    def __init__(self, data: bytes, name: str = "document.pdf"):
        self.name = name
        self.pdf = open_pdf(data)
        self.lock = threading.RLock()
        self.version = 0
        self._dirty = 0                      # bumps on every in-transaction change
        self._tx: Optional[_Tx] = None
        self._undo: list[_Entry] = []
        self._redo: list[_Entry] = []
        self._reset_caches()
        self._ensure_names()
        self.assets: dict[str, Asset] = {}

    def _ensure_names(self) -> None:
        """Give every annotation/widget a unique /NM: object numbers change
        whenever qpdf re-saves the file (undo, rollback), names do not, so
        ids built on them stay valid."""
        import pikepdf
        for page in self.pdf.pages:
            for a in page.obj.get("/Annots", []) or []:
                try:
                    if isinstance(a, pikepdf.Dictionary) and "/NM" not in a:
                        a.NM = pikepdf.String(uuid.uuid4().hex[:16])
                except Exception:
                    continue

    def _reset_caches(self) -> None:
        self._font_cache: dict = {}
        self._models: dict[int, tuple[int, PageModel]] = {}
        self._bytes_cache: tuple[tuple, bytes] | None = None
        self._render_doc: tuple[tuple, object] | None = None
        self._usage_learned_at: Optional[int] = None
        self.fontbook = FontBook(self.pdf)
        self._misc_cache: dict = {}

    # -- transactions --------------------------------------------------------------
    @contextmanager
    def transaction(self, label: str, group: Optional[str] = None):
        with self.lock:
            if self._tx is not None:
                yield self._tx
                return
            tx = _Tx(label=label, group=group, before=pdf_to_bytes(self.pdf))
            self._tx = tx
            try:
                yield tx
            except BaseException:
                self._tx = None
                self._restore(tx.before)
                raise
            self._tx = None
            if tx.pages:
                self._push_undo(_Entry(label, group, tx.before, set(tx.pages)))
                self._redo.clear()
                self._bump()

    def _touch(self, *pages: int) -> None:
        if self._tx is None:
            raise RuntimeError("mutation outside a transaction")
        self._tx.pages.update(int(p) for p in pages)
        self._changed()

    def _changed(self) -> None:
        self._dirty += 1
        self._bytes_cache = None
        self._render_doc = None

    def _renamed(self, old_id: str, new_id: Optional[str]) -> None:
        if self._tx is not None:
            self._tx.result_ids[old_id] = new_id

    def _bump(self) -> None:
        self.version += 1
        self._changed()

    def _push_undo(self, entry: _Entry) -> None:
        self._undo.append(entry)
        while len(self._undo) > MAX_UNDO or (
                len(self._undo) > 1 and sum(len(e.state) for e in self._undo) > MAX_UNDO_BYTES):
            self._undo.pop(0)

    def _restore(self, state: bytes) -> None:
        old = self.pdf
        self.pdf = open_pdf(state)
        self._reset_caches()
        self._ensure_names()
        try:
            old.close()
        except Exception:
            pass

    def replace_document(self, new) -> None:
        """Swap in a rebuilt document inside a transaction (undoable)."""
        if self._tx is None:
            raise RuntimeError("replace_document outside a transaction")
        data = new if isinstance(new, (bytes, bytearray)) else pdf_to_bytes(new)
        self._restore(bytes(data))
        self.touch_all()

    def touch_all(self) -> None:
        self._touch(*range(len(self.pdf.pages)))

    def _pop_group(self, stack):
        top = stack.pop()
        state, pages = top.state, set(top.pages)
        while top.group and stack and stack[-1].group == top.group:
            e = stack.pop()
            state = e.state
            pages |= e.pages
        return top, state, pages

    def undo(self) -> dict:
        with self.lock:
            if not self._undo:
                raise EditError("nothing to undo")
            top, state, pages = self._pop_group(self._undo)
            self._redo.append(_Entry(top.label, top.group, pdf_to_bytes(self.pdf), pages))
            self._restore(state)
            self._bump()
            return {"undone": top.label, "pages_changed": sorted(pages)}

    def redo(self) -> dict:
        with self.lock:
            if not self._redo:
                raise EditError("nothing to redo")
            top = self._redo.pop()
            self._push_undo(_Entry(top.label, top.group, pdf_to_bytes(self.pdf), set(top.pages)))
            self._restore(top.state)
            self._bump()
            return {"redone": top.label, "pages_changed": sorted(top.pages)}

    def history_state(self) -> dict:
        return {"version": self.version, "page_count": len(self.pdf.pages),
                "can_undo": bool(self._undo), "can_redo": bool(self._redo),
                "undo_label": self._undo[-1].label if self._undo else None,
                "redo_label": self._redo[-1].label if self._redo else None}

    # -- pages / models ----------------------------------------------------------------
    @property
    def page_count(self) -> int:
        return len(self.pdf.pages)

    def page(self, page_no) -> pikepdf.Page:
        try:
            page_no = int(page_no)
        except (TypeError, ValueError):
            raise EditError(f"invalid page {page_no!r}")
        if not 0 <= page_no < len(self.pdf.pages):
            raise EditError(f"page {page_no} does not exist "
                            f"(document has {len(self.pdf.pages)} pages, 0-based)")
        return self.pdf.pages[page_no]

    def model(self, page_no: int) -> PageModel:
        self.page(page_no)
        hit = self._models.get(page_no)
        if hit and hit[0] == self._dirty:
            return hit[1]
        m = Interpreter(self.pdf, self._font_cache).page(page_no)
        self._models[page_no] = (self._dirty, m)
        return m

    def learn_glyph_usage(self) -> None:
        """Subset fonts can only draw glyphs the document already uses;
        interpret every page once so `used_codes` covers the whole file."""
        if self._usage_learned_at == self._dirty:
            return
        for p in range(len(self.pdf.pages)):
            self.model(p)
        self._usage_learned_at = self._dirty

    def geometry(self, page_no: int) -> tuple[Matrix, float, float, int]:
        return page_geometry(self.page(page_no))

    def pages(self) -> list[dict]:
        out = []
        for i in range(len(self.pdf.pages)):
            _, w, h, rot = self.geometry(i)
            dw, dh = (h, w) if rot in (90, 270) else (w, h)
            out.append({"index": i, "width": float(dw), "height": float(dh), "rotation": rot,
                        "matrix": rotation_matrix(rot, w, h).list()})
        return out

    # -- output ------------------------------------------------------------------------
    def pdf_bytes(self) -> tuple[int, bytes]:
        with self.lock:
            key = (self.version, self._dirty)
            if self._bytes_cache is None or self._bytes_cache[0] != key:
                self._bytes_cache = (key, pdf_to_bytes(self.pdf))
            return self.version, self._bytes_cache[1]

    def to_bytes(self) -> bytes:
        with self.lock:
            return pdf_to_bytes(self.pdf, final=True)

    def _pdfium(self):
        import pypdfium2 as pdfium
        key = (self.version, self._dirty)
        if self._render_doc is None or self._render_doc[0] != key:
            rd = pdfium.PdfDocument(self.pdf_bytes()[1])
            rd.init_forms()  # without this PDFium skips form widget appearances
            self._render_doc = (key, rd)
        return self._render_doc[1]

    def render_image(self, page_no: int, scale: float = 2.0, clip=None):
        """PIL image of a page as displayed; `clip` in page (object) coords."""
        with self.lock:
            self.page(page_no)
            pg = self._pdfium()[page_no]
            img = pg.render(scale=scale, may_draw_forms=True).to_pil().convert("RGB")
            if clip:
                _, w, h, rot = self.geometry(page_no)
                r = Rect.of(clip).transform(rotation_matrix(rot, w, h))
                img = img.crop((int(r.x0 * scale), int(r.y0 * scale),
                                int(r.x1 * scale) + 1, int(r.y1 * scale) + 1))
            return img

    def render_page(self, page_no: int, scale: float = 2.0, clip=None) -> bytes:
        buf = io.BytesIO()
        self.render_image(page_no, scale, clip).save(buf, "PNG")
        return buf.getvalue()

    def page_png_b64(self, page_no: int, clip=None, max_px: int = 1500) -> str:
        _, w, h = self.geometry(page_no)[:3]
        r = Rect.of(clip) if clip else Rect(0, 0, w, h)
        scale = max(0.5, min(3.0, max_px / max(r.width, r.height, 1)))
        return base64.standard_b64encode(self.render_page(page_no, scale, clip)).decode()

    def document_text(self, pages: Optional[list[int]] = None) -> str:
        with self.lock:
            parts = []
            for p in pages if pages else range(len(self.pdf.pages)):
                parts.append(f"--- page {p} ---")
                parts.append(self.page_text(p))
            return "\n".join(parts)

    # -- assets ------------------------------------------------------------------------------
    def add_asset(self, data: bytes, name: str) -> Asset:
        from PIL import Image
        try:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
        except Exception as exc:
            raise EditError(f"not a supported image: {exc}")
        aid = "img_" + uuid.uuid4().hex[:8]
        a = Asset(aid, name, data, w, h)
        with self.lock:
            self.assets[aid] = a
        return a

    def asset(self, asset_id: str) -> Asset:
        a = self.assets.get(asset_id)
        if a is None:
            raise EditError(f"unknown asset {asset_id!r}; upload an image first")
        return a

    def list_assets(self) -> list[dict]:
        return [{"id": a.id, "name": a.name, "width": a.width, "height": a.height}
                for a in self.assets.values()]

    # -- objects -------------------------------------------------------------------------------
    def page_objects(self, page_no: int) -> dict:
        with self.lock:
            _, w, h, rot = self.geometry(page_no)
            objs = (self.text_objects(page_no) + self.image_objects(page_no)
                    + self.annot_objects(page_no) + self.field_objects(page_no))
            return {"page": page_no, "width": round(w, 2), "height": round(h, 2), "rotation": rot,
                    "scanned": self.is_scanned(page_no), "objects": objs}

    @staticmethod
    def kind_of(obj_id: str) -> str:
        prefix = (obj_id or "").split(":", 1)[0]
        kinds = {"t": "text", "i": "image", "a": "annotation", "w": "field"}
        if prefix not in kinds:
            raise EditError(f"unknown object id {obj_id!r}")
        return kinds[prefix]

    def delete_objects(self, ids: list[str]) -> dict:
        with self.transaction("Delete"):
            # images first: deleting text never moves an image, but removing an
            # image instruction shifts instruction indices inside its stream
            for oid in sorted(ids, key=lambda i: self.kind_of(i) != "image"):
                kind = self.kind_of(oid)
                if kind == "text":
                    self.delete_text(oid)
                elif kind == "image":
                    self.delete_image(oid)
                elif kind == "annotation":
                    self.delete_annotation(oid)
                else:
                    self.delete_field(oid)
            return {"deleted": len(ids)}

    def move_objects(self, ids: list[str], dx: float, dy: float) -> dict:
        dx, dy = float(dx), float(dy)
        with self.transaction("Move"):
            new_ids = {}
            for oid in ids:
                kind = self.kind_of(oid)
                if kind == "text":
                    new_ids[oid] = self.move_text(oid, dx, dy)
                elif kind == "image":
                    new_ids[oid] = self.transform_image(oid, translate=(dx, dy))
                elif kind == "annotation":
                    new_ids[oid] = self.move_annotation(oid, dx, dy)
                else:
                    new_ids[oid] = self.move_field(oid, dx, dy)
            return {"moved": len(ids), "new_ids": new_ids}

    def set_object_rect(self, obj_id: str, rect) -> dict:
        kind = self.kind_of(obj_id)
        with self.transaction("Resize"):
            if kind == "image":
                new_id = self.transform_image(obj_id, rect=rect)
            elif kind == "annotation":
                new_id = self.set_annotation_rect(obj_id, rect)
            elif kind == "field":
                new_id = self.set_field_rect(obj_id, rect)
            else:
                raise EditError("text cannot be resized by rect; use edit_text with font_size, "
                                "or move_objects")
            return {"id": new_id}

    def summary(self) -> dict:
        with self.lock:
            scanned = [i for i in range(self.page_count) if self.is_scanned(i)]
            _, w, h, _ = self.geometry(0)
            return {"name": self.name, "page_count": self.page_count,
                    "page_size_pts": [round(w, 2), round(h, 2)],
                    "rotated_pages": [i for i in range(self.page_count) if self.geometry(i)[3]],
                    "scanned_pages": scanned,
                    "has_form": "/AcroForm" in self.pdf.Root,
                    "assets": self.list_assets(), **self.history_state()}
