"""Edit (in place, undoable): watermark, page numbers, overlay, crop, page
size, document information, viewer preferences, flatten, make fillable,
fill form fields."""
from __future__ import annotations

import io
import re

import pikepdf

from ..engine import EditError
from ..engine.geom import Rect
from ..engine.util import parse_color
from .base import COLOR_OPT, FILE_OPT, PAGES_OPT, PAPER_ENUM, paper, parse_pages, pdf_bytes, tool
from .render import display_size, draw_text_display, draw_xobject_display, open_bytes, page_form, place_form

CAT = "Edit"
POSITIONS = ["center", "top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right", "tile"]


def _anchor(W, H, box_w, box_h, position, margin):
    x = {"left": margin, "center": (W - box_w) / 2, "right": W - margin - box_w}
    y = {"top": margin, "center": (H - box_h) / 2, "bottom": H - margin - box_h}
    if position == "center":
        return x["center"], y["center"]
    v, h = position.split("-")
    return x[h], y[v]


def _faded_image(ctx, file_id, opacity: float) -> bytes:
    from PIL import Image
    _, data, _ = ctx.store.file_bytes(file_id)
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    if opacity < 0.999:
        im.putalpha(im.getchannel("A").point(lambda v: int(v * opacity)))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _tile_spots(W, H, w, h, gap=1.4):
    spots, row, y = [], 0, 20.0
    sx, sy = max(w * gap, 60), max(h * 3.2, 60)
    while y < H:
        x = (sx / 2 if row % 2 else 0) - w / 2
        while x < W:
            spots.append((x, y))
            x += sx
        y += sy
        row += 1
    return spots


@tool("add_watermark", "Add watermark", CAT,
      "Stamp a text or image watermark on pages, e.g. CONFIDENTIAL diagonally across every page, "
      "behind or over the content.",
      in_place=True, aliases=["watermark", "stamp"],
      options={"text": {"type": "string", "description": "Watermark text (or use image_file)."},
               "image_file": {**FILE_OPT, "description": "Image to use instead of text."},
               "font_size": {"type": "number", "default": 60},
               "color": {**COLOR_OPT, "default": "#cc0000"},
               "opacity": {"type": "number", "default": 0.25},
               "rotation": {"type": "number", "default": 45, "description": "Degrees counter-clockwise."},
               "position": {"type": "string", "enum": POSITIONS, "default": "center"},
               "behind_content": {"type": "boolean", "default": False},
               "image_width": {"type": "number", "default": 200, "description": "Image width in points."},
               "pages": PAGES_OPT})
def add_watermark(ctx, doc, text=None, image_file=None, font_size=60, color="#cc0000", opacity=0.25,
                  rotation=45, position="center", behind_content=False, image_width=200, pages="all"):
    import math
    if not text and not image_file:
        raise EditError("give text or image_file")
    rgb = parse_color(color)
    xobj = None
    if image_file:
        from ..engine.images import image_xobject
        stream, iw, ih = image_xobject(doc.pdf, _faded_image(ctx, image_file, opacity))
        xobj = doc.pdf.make_indirect(stream)
    from reportlab.pdfbase.pdfmetrics import stringWidth
    for p in parse_pages(pages, doc.page_count):
        W, H = display_size(doc, p)
        if xobj is not None:
            w, h = image_width, image_width * ih / iw
            spots = _tile_spots(W, H, w, h) if position == "tile" else [_anchor(W, H, w, h, position, 36)]
            for x, y in spots:
                draw_xobject_display(doc, p, xobj, [x, y, x + w, y + h], behind=behind_content)
        else:
            tw = stringWidth(text, "Helvetica-Bold", font_size)
            spots = _tile_spots(W, H, tw, font_size, 1.6) if position == "tile" else \
                [_anchor(W, H, tw, font_size, position, 36)]
            a = math.radians(rotation)
            for x, y in spots:
                cx, cy = x + tw / 2, y + font_size / 2
                # start so the text's centre lands on (cx, cy) after rotation
                sx = cx - math.cos(a) * tw / 2 - math.sin(a) * font_size * 0.35
                sy = cy + math.sin(a) * tw / 2 + math.cos(a) * font_size * 0.35
                draw_text_display(doc, p, text, sx, sy, font_size, rgb, "Helvetica-Bold", rotation,
                                  opacity, behind_content)


@tool("add_page_numbers", "Add page numbers", CAT,
      "Number the pages, e.g. 'Page {n} of {total}' at the bottom centre. {n} is the page number, {total} the count.",
      in_place=True, aliases=["page_numbers", "number_pages"],
      options={"format": {"type": "string", "default": "{n}", "description": "e.g. {n}, Page {n} of {total}, - {n} -"},
               "position": {"type": "string", "enum": POSITIONS[1:7], "default": "bottom-center"},
               "start_at": {"type": "integer", "default": 1},
               "font_size": {"type": "number", "default": 10},
               "color": COLOR_OPT,
               "margin": {"type": "number", "default": 28},
               "pages": PAGES_OPT})
def add_page_numbers(ctx, doc, format="{n}", position="bottom-center", start_at=1, font_size=10,
                     color="#000000", margin=28, pages="all"):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    sel = parse_pages(pages, doc.page_count)
    total = len(sel) + start_at - 1
    rgb = parse_color(color)
    for i, p in enumerate(sel):
        label = format.replace("{n}", str(start_at + i)).replace("{total}", str(total))
        W, H = display_size(doc, p)
        w = stringWidth(label, "Helvetica", font_size)
        x, y = _anchor(W, H, w, font_size, position, margin)
        draw_text_display(doc, p, label, x, y + font_size * 0.8, font_size, rgb)


@tool("overlay_pdf", "PDF Overlay", CAT,
      "Lay the pages of another PDF (letterhead, stamp, form) over or under this one's pages.",
      in_place=True, required=["overlay_file"], aliases=["overlay", "letterhead"],
      options={"overlay_file": {**FILE_OPT, "description": "PDF to lay over/under."},
               "mode": {"type": "string", "enum": ["foreground", "background"], "default": "foreground"},
               "page_mapping": {"type": "string", "enum": ["first_page_on_all", "page_by_page", "cycle"],
                                "default": "first_page_on_all"},
               "pages": PAGES_OPT})
def overlay_pdf(ctx, doc, overlay_file, mode="foreground", page_mapping="first_page_on_all", pages="all"):
    _, data, _ = ctx.store.file_bytes(overlay_file)
    ov = open_bytes(data)
    forms = {}
    for i, p in enumerate(parse_pages(pages, doc.page_count)):
        if page_mapping == "first_page_on_all":
            op = 0
        elif page_mapping == "page_by_page":
            if i >= len(ov.pages):
                break
            op = i
        else:
            op = i % len(ov.pages)
        if op not in forms:
            forms[op] = page_form(doc.pdf, ov, op)
        W, H = display_size(doc, p)
        draw_xobject_display(doc, p, forms[op], [0, 0, W, H], keep_proportion=False,
                             behind=(mode == "background"))


@tool("crop_pages", "Crop PDF", CAT, "Crop pages by margins (in mm) or automatically trim white space around the content.",
      in_place=True, aliases=["crop", "trim"],
      options={"mode": {"type": "string", "enum": ["margins", "auto_trim"], "default": "margins"},
               "top": {"type": "number", "default": 10}, "right": {"type": "number", "default": 10},
               "bottom": {"type": "number", "default": 10}, "left": {"type": "number", "default": 10},
               "padding": {"type": "number", "default": 5, "description": "mm kept around content (auto_trim)."},
               "pages": PAGES_OPT})
def crop_pages(ctx, doc, mode="margins", top=10, right=10, bottom=10, left=10, padding=5, pages="all"):
    mm = 72 / 25.4
    for p in parse_pages(pages, doc.page_count):
        P, w, h, _ = doc.geometry(p)
        page_rect = Rect(0, 0, w, h)
        if mode == "auto_trim":
            m = doc.model(p)
            c = Rect.empty()
            for g in m.glyphs:
                if g.render != 3 and g.char.strip():
                    c = c | g.bbox
            for im in m.images:
                c = c | im.bbox
            for s in m.shapes:
                c = c | s.bbox
            if c.is_empty:
                continue
            new = c.expand(padding * mm) & page_rect
        else:
            new = Rect(left * mm, top * mm, w - right * mm, h - bottom * mm)
        if new.width < 20 or new.height < 20:
            raise EditError(f"crop would leave page {p + 1} too small")
        r = new.transform(P.inverse())
        doc.pdf.pages[p].cropbox = pikepdf.Array([r.x0, r.y0, r.x1, r.y1])
        doc._touch(p)
    ctx.structure_changed = True


@tool("change_page_size", "Change PDF page size", CAT,
      "Resize pages to a paper size (A4, Letter, ...), scaling the content to fit. Annotations and form "
      "fields are flattened into the page.",
      in_place=True, aliases=["resize_pages", "page_size"],
      options={"size": {"type": "string", "default": "A4", "description": "A4, Letter, Legal, A3, A5 ... or e.g. 210x297mm"},
               "orientation": {"type": "string", "enum": ["auto", "portrait", "landscape"], "default": "auto"},
               "fit": {"type": "string", "enum": ["fit", "stretch"], "default": "fit"},
               "pages": PAGES_OPT})
def change_page_size(ctx, doc, size="A4", orientation="auto", fit="fit", pages="all"):
    from ..engine import PdfDoc
    tmp = PdfDoc(doc.pdf_bytes()[1])
    with tmp.transaction("bake"):
        tmp.bake()
        tmp.touch_all()
    src = open_bytes(tmp.pdf_bytes()[1])
    sel = set(parse_pages(pages, len(src.pages)))
    out = pikepdf.new()
    for p in range(len(src.pages)):
        if p not in sel:
            out.add_pages_from(src, [p])
            continue
        W, H = display_size(tmp, p)
        orient = orientation if orientation != "auto" else ("landscape" if W > H else "portrait")
        w, h = paper(size, orient)
        sheet = out.add_blank_page(page_size=(w, h))
        place_form(out, sheet, page_form(out, src, p), [0, 0, w, h], keep_proportion=(fit == "fit"))
    doc.replace_document(pdf_bytes(out))
    ctx.structure_changed = True


INFO_KEYS = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords",
             "creator": "/Creator", "producer": "/Producer"}
XMP_KEYS = {"title": "dc:title", "author": "dc:creator", "subject": "dc:description",
            "keywords": "pdf:Keywords", "creator": "xmp:CreatorTool", "producer": "pdf:Producer"}


def _metadata(pdf) -> dict:
    return {k: str(pdf.docinfo[v]) for k, v in INFO_KEYS.items() if v in pdf.docinfo and str(pdf.docinfo[v])}


@tool("set_metadata", "Change PDF document information", CAT,
      "Read or set title, author, subject, keywords, creator and producer.",
      in_place=True, aliases=["document_information", "metadata"],
      options={"title": {"type": "string"}, "author": {"type": "string"}, "subject": {"type": "string"},
               "keywords": {"type": "string"}, "creator": {"type": "string"}, "producer": {"type": "string"}})
def set_metadata(ctx, doc, **fields):
    changed = {k: v for k, v in fields.items() if v is not None}
    if changed:
        pdf = doc.pdf
        for k, v in changed.items():
            pdf.docinfo[INFO_KEYS[k]] = pikepdf.String(v)
        try:
            with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
                for k, v in changed.items():
                    meta[XMP_KEYS[k]] = [v] if k == "author" else v
        except Exception:
            pass
        doc._touch(0)
    ctx.result = {"metadata": _metadata(doc.pdf)}


@tool("viewer_preferences", "Set PDF viewer preferences", CAT,
      "Control how readers open the PDF: page layout, side panel, full screen, fit window, show title, start page.",
      in_place=True,
      options={"page_layout": {"type": "string", "enum": ["SinglePage", "OneColumn", "TwoColumnLeft",
                                                          "TwoColumnRight", "TwoPageLeft", "TwoPageRight"]},
               "page_mode": {"type": "string", "enum": ["UseNone", "UseOutlines", "UseThumbs", "FullScreen"]},
               "fit_window": {"type": "boolean"}, "center_window": {"type": "boolean"},
               "hide_toolbar": {"type": "boolean"}, "hide_menubar": {"type": "boolean"},
               "display_doc_title": {"type": "boolean"},
               "open_page": {"type": "integer", "description": "1-based page to open at."}})
def viewer_preferences(ctx, doc, page_layout=None, page_mode=None, fit_window=None, center_window=None,
                       hide_toolbar=None, hide_menubar=None, display_doc_title=None, open_page=None):
    root = doc.pdf.Root
    if page_layout:
        root.PageLayout = pikepdf.Name("/" + page_layout)
    if page_mode:
        root.PageMode = pikepdf.Name("/" + page_mode)
    prefs = {"/FitWindow": fit_window, "/CenterWindow": center_window, "/HideToolbar": hide_toolbar,
             "/HideMenubar": hide_menubar, "/DisplayDocTitle": display_doc_title}
    if any(v is not None for v in prefs.values()):
        vp = root.get("/ViewerPreferences") or pikepdf.Dictionary()
        for k, v in prefs.items():
            if v is not None:
                vp[k] = bool(v)
        root.ViewerPreferences = vp
    if open_page:
        pg = doc.pdf.pages[max(0, min(doc.page_count, open_page) - 1)]
        root.OpenAction = pikepdf.Array([pg.obj, pikepdf.Name.Fit])
    doc._touch(0)
    ctx.result = {"page_layout": str(root.get("/PageLayout", "")), "page_mode": str(root.get("/PageMode", ""))}


@tool("flatten_pdf", "Flatten PDF", CAT,
      "Burn annotations and/or form fields into the page so they can no longer be edited.",
      in_place=True, aliases=["flatten"],
      options={"annotations": {"type": "boolean", "default": True},
               "form_fields": {"type": "boolean", "default": True}})
def flatten_pdf(ctx, doc, annotations=True, form_fields=True):
    pages = doc.bake(annots=annotations, widgets=form_fields)
    doc.touch_all()
    ctx.result = {"flattened_pages": [p + 1 for p in pages]}


def _slug(text: str, used: set) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "field"
    base, i = s, 2
    while s in used:
        s = f"{base}_{i}"
        i += 1
    used.add(s)
    return s


@tool("make_fillable", "Make PDF fillable", CAT,
      "Detect blanks in a form (underscore lines, drawn lines, boxes) and turn them into fillable text "
      "fields and checkboxes, named after their labels.",
      in_place=True, aliases=["make_pdf_fillable", "detect_form_fields"],
      options={"pages": PAGES_OPT, "font_size": {"type": "number", "default": 0, "description": "0 = auto"}})
def make_fillable(ctx, doc, pages="all", font_size=0):
    used = {f["name"] for f in doc.list_form_fields()["fields"]}
    added = []
    for p in parse_pages(pages, doc.page_count):
        segs = [s for s in doc._segments(p) if not s.ocr]
        spots = []
        for s in segs:
            for m in re.finditer(r"_{4,}", s.text):
                gl = s.glyphs[m.start():m.end()]
                r = gl[0].bbox
                for g in gl[1:]:
                    r = r | g.bbox
                spots.append((Rect(r.x0, r.y1 - 15, r.x1, r.y1 + 1), "text", s.text[:m.start()]))
            for i, ch in enumerate(s.text):
                if ch in "☐□▢❏":
                    spots.append((s.glyphs[i].bbox, "checkbox", ""))
        word_boxes = [s.bbox for s in segs]
        for sh in doc.model(p).shapes:
            r = sh.bbox
            if r.width > 50 and r.height < 1.5:
                above = Rect(r.x0, r.y0 - 15, r.x1, r.y0 - 1)
                if not any(b.intersects(above) for b in word_boxes):
                    spots.append((Rect(r.x0, r.y0 - 15, r.x1, r.y0 + 1), "text", ""))
            elif 7 <= r.width <= 22 and 7 <= r.height <= 22 and abs(r.width - r.height) < 2 and sh.kind == "rect":
                spots.append((r, "checkbox", ""))
        existing = [Rect.of(f["bbox"]) for f in doc.field_objects(p)]
        for rect, kind, prefix in spots:
            if any(rect.intersects(e) for e in existing):
                continue
            label_segs = [s for s in segs if s.bbox.y1 > rect.y0 - 4 and s.bbox.y0 < rect.y1 + 2
                          and s.bbox.x1 <= rect.x0 + 2 and not re.fullmatch(r"_+", s.text)]
            prefix = re.sub(r"_+", " ", prefix).strip()
            label = prefix.split("  ")[-1] if prefix else \
                " ".join(s.text for s in sorted(label_segs, key=lambda s: s.bbox.x0)[-3:])
            label = re.sub(r"_+", "", label).strip().rstrip(":").strip()
            name = _slug(label or f"{kind}_{p + 1}", used)
            doc.add_form_field(p, rect.list(), "checkbox" if kind == "checkbox" else "text", name,
                               font_size=font_size)
            existing.append(rect)
            added.append({"page": p + 1, "name": name, "type": kind})
    if not added:
        raise EditError("no blanks found (underscores, lines or boxes)")
    ctx.result = {"fields_added": added}


@tool("fill_form_fields", "Fill out PDF", CAT,
      "Fill several form fields at once by field name. Checkboxes take true/false.",
      in_place=True, required=["values"], aliases=["fill_out_pdf", "fill_form"],
      options={"values": {"type": "object", "description": "{field name: value}"}})
def fill_form_fields(ctx, doc, values):
    if not isinstance(values, dict):
        raise EditError("values must be an object mapping field names to values")
    by_name: dict[str, list] = {}
    for f in doc.list_form_fields()["fields"]:
        by_name.setdefault(f["name"], []).append(f["id"])
    filled = []
    for name, val in values.items():
        for fid in by_name.get(name, []):
            doc.set_field_value(fid, val)
            filled.append(name)
    ctx.result = {"filled": sorted(set(filled)), "unknown_fields": [k for k in values if k not in by_name],
                  "available_fields": sorted(by_name)}
