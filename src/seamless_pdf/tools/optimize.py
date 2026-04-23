"""Optimize & repair: compress, web optimize, OCR, repair, rasterize.

(PDF to PDF/A is deliberately not offered: without Ghostscript, which is
AGPL, there is no free converter of comparable quality.)"""
from __future__ import annotations

import io
import zlib

import pikepdf

from ..engine import EditError
from .base import PAGES_OPT, parse_pages, tool

CAT = "Optimize"

LEVELS = {"low": (200, 150, 85), "medium": (150, 120, 70), "high": (110, 90, 55),
          "extreme": (80, 72, 40)}  # threshold dpi, target dpi, jpeg quality


def _image_dpi(eng) -> dict:
    """Largest effective resolution each image object is drawn at."""
    dpi: dict = {}
    for p in range(eng.page_count):
        for pl in eng.model(p).images:
            if pl.obj is None or not pl.obj.is_indirect or pl.bbox.width <= 0:
                continue
            w = int(pl.obj.get("/Width", 0) or 0)
            h = int(pl.obj.get("/Height", 0) or 0)
            if not w or not h:
                continue
            d = max(w / (pl.bbox.width / 72), h / (max(pl.bbox.height, 1e-6) / 72))
            dpi[pl.obj.objgen] = max(dpi.get(pl.obj.objgen, 0), d)
    return dpi


@tool("compress_pdf", "Compress PDF", CAT,
      "Make a PDF smaller: downsample and recompress images to the resolution they are shown at, and "
      "compact the file structure.",
      aliases=["compress", "reduce_size", "shrink"],
      options={"level": {"type": "string", "enum": list(LEVELS), "default": "medium"},
               "grayscale": {"type": "boolean", "default": False}})
def compress_pdf(ctx, level="medium", grayscale=False):
    from PIL import Image
    before = len(ctx.inputs[0].data)
    eng = ctx.inputs[0].engine()
    dpi = _image_dpi(eng)
    pdf = eng.pdf
    thr, target, q = LEVELS[level]
    changed = 0
    for objgen, d in dpi.items():
        obj = pdf.get_object(objgen)
        if obj is None or "/SMask" in obj or "/Mask" in obj or int(obj.get("/BitsPerComponent", 8)) < 8:
            continue
        if str(obj.get("/Filter", "")) in ("/JBIG2Decode", "/CCITTFaxDecode"):
            continue
        try:
            im = pikepdf.PdfImage(obj).as_pil_image()
        except Exception:
            continue
        if d > thr:
            k = target / d
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
        elif not grayscale and str(obj.get("/Filter", "")) == "/DCTDecode":
            continue  # already a JPEG at a sensible resolution
        im = im.convert("L" if grayscale or im.mode == "L" else "RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True)
        if len(buf.getvalue()) >= len(obj.read_raw_bytes()) and d <= thr:
            continue
        obj.write(buf.getvalue(), filter=pikepdf.Name.DCTDecode)
        obj.Width, obj.Height, obj.BitsPerComponent = im.width, im.height, 8
        obj.ColorSpace = pikepdf.Name.DeviceGray if im.mode == "L" else pikepdf.Name.DeviceRGB
        for k in ("/DecodeParms", "/Decode"):
            if k in obj:
                del obj[k]
        changed += 1
    out = io.BytesIO()
    pdf.remove_unreferenced_resources()
    pdf.save(out, compress_streams=True, recompress_flate=True,
             object_stream_mode=pikepdf.ObjectStreamMode.generate)
    data = out.getvalue()
    if len(data) >= before:
        data, note = ctx.inputs[0].data, "already well compressed; kept the original"
    else:
        note = f"reduced by {100 - len(data) * 100 // before}%"
    ctx.add_pdf(data, f"{ctx.inputs[0].stem}-compressed.pdf", raw=True)
    ctx.result = {"original_bytes": before, "compressed_bytes": len(data), "images_recompressed": changed,
                  "note": note}


@tool("web_optimize", "Web optimize PDF", CAT,
      "Linearize the PDF (fast web view) so browsers can show page 1 before the whole file has downloaded.",
      aliases=["linearize", "fast_web_view"])
def web_optimize(ctx):
    with pikepdf.open(io.BytesIO(ctx.inputs[0].data)) as pdf:
        buf = io.BytesIO()
        pdf.save(buf, linearize=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    ctx.add_pdf(buf.getvalue(), f"{ctx.inputs[0].stem}-web.pdf", raw=True)
    ctx.result = {"linearized": True, "bytes": len(buf.getvalue())}


@tool("repair_pdf", "Repair PDF", CAT,
      "Rebuild a damaged PDF (broken cross-reference table, truncated file, bad objects) into a clean, valid file.",
      aliases=["fix_pdf", "repair"])
def repair_pdf(ctx):
    try:
        with pikepdf.open(io.BytesIO(ctx.inputs[0].data)) as pdf:
            buf = io.BytesIO()
            pdf.save(buf, fix_metadata_version=True)
            pages = len(pdf.pages)
    except pikepdf.PasswordError:
        raise EditError("the PDF is password protected; unlock it first")
    except Exception as e:
        raise EditError(f"the file is too damaged to repair: {e}")
    ctx.add_pdf(buf.getvalue(), f"{ctx.inputs[0].stem}-repaired.pdf")
    ctx.result = {"pages": pages, "notes": ["rebuilt with qpdf"]}


@tool("rasterize_pdf", "Rasterize PDF", CAT,
      "Turn every page into an image (flattens everything; text is no longer selectable unless you run OCR).",
      aliases=["flatten_to_images"],
      options={"dpi": {"type": "integer", "default": 150},
               "format": {"type": "string", "enum": ["jpg", "png"], "default": "jpg"},
               "quality": {"type": "integer", "default": 80},
               "grayscale": {"type": "boolean", "default": False},
               "pages": PAGES_OPT})
def rasterize_pdf(ctx, dpi=150, format="jpg", quality=80, grayscale=False, pages="all"):
    from .render import display_size
    eng = ctx.inputs[0].engine()
    sel = set(parse_pages(pages, eng.page_count))
    src = ctx.pdf()
    out = pikepdf.new()
    for p in range(eng.page_count):
        if p not in sel:
            out.add_pages_from(src, [p])
            continue
        img = eng.render_image(p, dpi / 72)
        img = img.convert("L") if grayscale else img
        w, h = display_size(eng, p)
        page = out.add_blank_page(page_size=(w, h))
        if format == "jpg":
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality)
            xo = pikepdf.Stream(out, buf.getvalue())
            xo.Filter = pikepdf.Name.DCTDecode
        else:
            xo = pikepdf.Stream(out, zlib.compress(img.tobytes(), 6))
            xo.Filter = pikepdf.Name.FlateDecode
        xo.Type, xo.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        xo.Width, xo.Height, xo.BitsPerComponent = img.width, img.height, 8
        xo.ColorSpace = pikepdf.Name.DeviceGray if img.mode == "L" else pikepdf.Name.DeviceRGB
        name = page.add_resource(out.make_indirect(xo), pikepdf.Name.XObject, prefix="R")
        page.contents_add(out.make_stream(f"q {w:.3f} 0 0 {h:.3f} 0 0 cm {name} Do Q".encode()))
    ctx.add_pdf(out, f"{ctx.inputs[0].stem}-rasterized.pdf")
    ctx.result = {"pages": len(sel), "dpi": dpi}


@tool("ocr_pdf", "PDF OCR", CAT,
      "Recognise text on scanned pages so it becomes searchable, selectable and editable (adds an invisible text layer).",
      in_place=True, requires=["tesseract"], aliases=["ocr", "make_searchable"],
      options={"language": {"type": "string", "default": "eng", "description": "Tesseract language(s), e.g. eng, deu, eng+fra"},
               "pages": {"type": "string", "default": "scanned",
                         "description": "'scanned' (pages without text), 'all', or e.g. 1-3"},
               "dpi": {"type": "integer", "default": 300}})
def ocr_pdf(ctx, doc, language="eng", pages="scanned", dpi=300):
    n = doc.page_count
    sel = [p for p in range(n) if doc.is_scanned(p)] if pages == "scanned" else parse_pages(pages, n)
    if not sel:
        raise EditError("no scanned pages found; use pages='all' to OCR anyway")
    words = 0
    for p in sel:
        words += doc.ocr_page(p, language, dpi)["words"]
    ctx.result = {"pages": [p + 1 for p in sel], "words": words}
