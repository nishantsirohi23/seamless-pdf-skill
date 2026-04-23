"""View & check: compare PDFs, search in PDFs, check PDF/A, document info."""
from __future__ import annotations

import difflib
import io
import re

import numpy as np
import pikepdf

from ..engine import EditError, PdfDoc
from .base import PDFS, tool

CAT = "View & check"


def _gray(doc: PdfDoc, p: int, scale: float, size=None):
    a = np.asarray(doc.render_image(p, scale).convert("L"))
    if size and a.shape != size:
        out = np.full(size, 255, np.uint8)
        h, w = min(size[0], a.shape[0]), min(size[1], a.shape[1])
        out[:h, :w] = a[:h, :w]
        a = out
    return a


def _image_page(out: pikepdf.Pdf, rgb: np.ndarray, w: float, h: float) -> None:
    import zlib
    page = out.add_blank_page(page_size=(w, h))
    xo = pikepdf.Stream(out, zlib.compress(rgb.tobytes(), 6))
    xo.Filter = pikepdf.Name.FlateDecode
    xo.Type, xo.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    xo.Width, xo.Height, xo.BitsPerComponent = rgb.shape[1], rgb.shape[0], 8
    xo.ColorSpace = pikepdf.Name.DeviceRGB
    name = page.add_resource(out.make_indirect(xo), pikepdf.Name.XObject, prefix="R")
    page.contents_add(out.make_stream(f"q {w:.3f} 0 0 {h:.3f} 0 0 cm {name} Do Q".encode()))


def _message_page(out: pikepdf.Pdf, text: str) -> None:
    page = out.add_blank_page(page_size=(595, 842))
    font = out.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica,
                                                Encoding=pikepdf.Name.WinAnsiEncoding))
    name = page.add_resource(font, pikepdf.Name.Font, prefix="F")
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    page.contents_add(out.make_stream(f"BT 0.8 0.1 0.1 rg {name} 14 Tf 72 742 Td ({safe}) Tj ET".encode()))


@tool("compare_pdfs", "Compare PDFs", CAT,
      "Compare two PDFs: a text diff per page plus a visual diff PDF with "
      "changes highlighted in red.",
      inputs=PDFS, aliases=["diff_pdfs", "compare"],
      options={"visual": {"type": "boolean", "default": True},
               "sensitivity": {"type": "integer", "default": 40,
                               "description": "Grey-level difference that counts as a change (0-255)."}})
def compare_pdfs(ctx, visual=True, sensitivity=40):
    from .render import display_size
    if len(ctx.inputs) != 2:
        raise EditError("Compare needs exactly two PDFs")
    a, b = ctx.inputs[0].engine(), ctx.inputs[1].engine()
    na, nb = a.page_count, b.page_count
    pages = max(na, nb)
    changes, diff_doc = [], pikepdf.new()
    for p in range(pages):
        ta = a.page_text(p).splitlines() if p < na else []
        tb = b.page_text(p).splitlines() if p < nb else []
        lines = [l for l in difflib.unified_diff(ta, tb, lineterm="", n=0)
                 if l and l[0] in "+-" and not l.startswith(("+++", "---"))]
        pixel_share = 0.0
        if visual and p < na and p < nb:
            ga = _gray(a, p, 1.0)
            gb = _gray(b, p, 1.0, ga.shape)
            mask = np.abs(ga.astype(np.int16) - gb.astype(np.int16)) > sensitivity
            pixel_share = float(mask.mean())
            rgb = np.stack([ga, ga, ga], axis=-1).astype(np.float32)
            rgb = 255 - (255 - rgb) * 0.35          # fade the unchanged page
            rgb[mask] = (220, 30, 30)
            w, h = display_size(a, p)
            _image_page(diff_doc, rgb.astype(np.uint8), w, h)
        elif visual:
            _message_page(diff_doc, f"Page {p + 1} exists only in "
                                    f"{'the first' if p < na else 'the second'} file")
        if lines or pixel_share > 0.0005 or p >= min(na, nb):
            changes.append({"page": p + 1, "text_changes": lines[:40],
                            "changed_area_percent": round(pixel_share * 100, 2)})
    if visual:
        ctx.add_pdf(diff_doc, f"compare-{ctx.inputs[0].stem}-vs-{ctx.inputs[1].stem}.pdf")
    ctx.result = {"identical": not changes, "pages_compared": pages, "changed_pages": changes}


@tool("search_pdfs", "Search in PDFs", CAT,
      "Find text (or a regular expression) across one or more PDFs, with "
      "page numbers and context.",
      inputs=PDFS, required=["query"], aliases=["search", "find_in_pdfs"],
      options={"query": {"type": "string"},
               "regex": {"type": "boolean", "default": False},
               "case_sensitive": {"type": "boolean", "default": False}})
def search_pdfs(ctx, query, regex=False, case_sensitive=False):
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pat = re.compile(query if regex else re.escape(query), flags)
    except re.error as e:
        raise EditError(f"invalid regex: {e}")
    hits = []
    for inp in ctx.inputs:
        d = inp.engine()
        for p in range(d.page_count):
            text = d.page_text(p)
            for m in pat.finditer(text):
                s, e = max(0, m.start() - 60), min(len(text), m.end() + 60)
                hits.append({"file": inp.name, "file_id": inp.id, "page": p + 1, "match": m.group(0),
                             "context": " ".join(text[s:e].split())})
                if len(hits) >= 500:
                    break
    ctx.result = {"count": len(hits), "hits": hits}


def _xmp(pdf: pikepdf.Pdf) -> str:
    md = pdf.Root.get("/Metadata")
    if md is None:
        return ""
    try:
        return md.read_bytes().decode("utf-8", "ignore")
    except Exception:
        return ""


def _pdfa_claim(pdf: pikepdf.Pdf) -> str | None:
    xmp = _xmp(pdf)
    part = re.search(r"pdfaid:part[>=\"']+\s*(\d)", xmp)
    conf = re.search(r"pdfaid:conformance[>=\"']+\s*([A-Za-z])", xmp)
    return f"{part.group(1)}{conf.group(1).lower() if conf else ''}" if part else None


def _fonts(pdf: pikepdf.Pdf) -> dict:
    """{base font name: {type, embedded}} across pages and nested forms."""
    out, seen = {}, set()

    def walk(res):
        if not isinstance(res, pikepdf.Dictionary):
            return
        for _, f in (res.get("/Font") or {}).items():
            try:
                sub = str(f.get("/Subtype", "")).lstrip("/")
                desc = f
                if sub == "Type0" and f.get("/DescendantFonts"):
                    desc = f.DescendantFonts[0]
                fd = desc.get("/FontDescriptor")
                emb = sub == "Type3" or (fd is not None and any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3")))
                name = str(f.get("/BaseFont", f.get("/Name", "(unnamed)"))).lstrip("/")
                out[name] = {"type": sub, "embedded": bool(emb)}
                if sub == "Type3":
                    walk(f.get("/Resources"))
            except Exception:
                continue
        for _, x in (res.get("/XObject") or {}).items():
            try:
                if str(x.get("/Subtype")) == "/Form" and x.objgen not in seen:
                    seen.add(x.objgen)
                    walk(x.get("/Resources"))
            except Exception:
                continue

    for page in pdf.pages:
        walk(page.obj.get("/Resources"))
        for a in page.obj.get("/Annots") or []:
            ap = a.get("/AP")
            n = ap.get("/N") if isinstance(ap, pikepdf.Dictionary) else None
            if isinstance(n, pikepdf.Stream):
                walk(n.get("/Resources"))
    return out


@tool("check_pdfa", "Check PDF/A", CAT,
      "Check whether a PDF claims PDF/A and run the key conformance checks "
      "(embedded fonts, no encryption, output intent, no JavaScript). This is "
      "a quick check, not a full veraPDF validation.",
      aliases=["validate_pdfa"])
def check_pdfa(ctx):
    inp = ctx.inputs[0]
    try:
        pdf = pikepdf.open(io.BytesIO(inp.data))
        encrypted = pdf.is_encrypted
    except pikepdf.PasswordError:
        raise EditError("the PDF is password protected; unlock it first")
    unembedded = sorted(n for n, f in _fonts(pdf).items() if not f["embedded"])
    names = pdf.Root.get("/Names")
    js = (names is not None and "/JavaScript" in names) or (
        isinstance(pdf.Root.get("/OpenAction"), pikepdf.Dictionary)
        and str(pdf.Root.OpenAction.get("/S")) == "/JavaScript")
    claim = _pdfa_claim(pdf)
    checks = [
        {"check": "Declares PDF/A in XMP metadata", "ok": claim is not None,
         "detail": f"PDF/A-{claim}" if claim else "no pdfaid in XMP"},
        {"check": "All fonts embedded", "ok": not unembedded, "detail": ", ".join(unembedded[:10])},
        {"check": "Not encrypted", "ok": not encrypted, "detail": ""},
        {"check": "Has an output intent (colour profile)", "ok": "/OutputIntents" in pdf.Root, "detail": ""},
        {"check": "No JavaScript", "ok": not js, "detail": ""},
    ]
    ctx.result = {"claims_pdfa": claim, "passes_basic_checks": all(c["ok"] for c in checks),
                  "checks": checks,
                  "note": "Basic checks only; use veraPDF for a full conformance validation."}


def _count_outline(pdf: pikepdf.Pdf) -> int:
    n = 0
    try:
        with pdf.open_outline() as ol:
            stack = list(ol.root)
            while stack:
                it = stack.pop()
                n += 1
                stack.extend(it.children)
    except Exception:
        pass
    return n


@tool("document_info", "Document info", CAT,
      "Everything about a PDF: pages and sizes, metadata, fonts (embedded or "
      "not), images, forms, annotations, signatures, encryption, attachments, "
      "bookmarks, PDF/A claim.",
      aliases=["pdf_info", "inspect_pdf", "properties"])
def document_info(ctx):
    from .render import page_size
    inp = ctx.inputs[0]
    info = {"file": inp.name, "bytes": len(inp.data)}
    try:
        pdf = pikepdf.open(io.BytesIO(inp.data))
    except pikepdf.PasswordError:
        info.update({"encrypted": True, "needs_password": True})
        ctx.result = info
        return
    info.update({"encrypted": pdf.is_encrypted, "needs_password": False})
    eng = PdfDoc(inp.data, inp.name)
    sizes = {}
    images, annots, fields, scanned = 0, 0, 0, 0
    for p, page in enumerate(pdf.pages):
        w, h = page_size(page)
        key = f"{round(w)}x{round(h)}pt"
        sizes[key] = sizes.get(key, 0) + 1
        imgs = len(eng.model(p).images)
        images += imgs
        for a in page.obj.get("/Annots") or []:
            if str(a.get("/Subtype")) == "/Widget":
                fields += 1
            elif str(a.get("/Subtype")) not in ("/Link", "/Popup"):
                annots += 1
        if imgs and not eng.page_text(p).strip():
            scanned += 1
    meta = {str(k).lstrip("/"): str(v) for k, v in pdf.docinfo.items() if str(v)}
    attachments = list(pdf.attachments.keys())
    acro = pdf.Root.get("/AcroForm")
    signed = bool(acro is not None and int(acro.get("/SigFlags", 0) or 0) > 0)
    info.update({"pages": len(pdf.pages), "page_sizes": sizes, "pdf_version": f"PDF {pdf.pdf_version}",
                 "metadata": meta, "fonts": _fonts(pdf), "images": images, "annotations": annots,
                 "form_fields": fields, "signed": signed, "attachments": attachments,
                 "bookmarks": _count_outline(pdf), "scanned_pages": scanned,
                 "pdfa_claim": _pdfa_claim(pdf)})
    ctx.result = info
