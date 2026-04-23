"""Shared helpers: HTML -> PDF (headless Chrome, LibreOffice fallback),
LibreOffice conversions, Chrome page printing, page placement."""
from __future__ import annotations

import html as _html
import io
import os
import subprocess
import tempfile

import pikepdf

from ..engine import EditError
from .base import find_program, paper

BASE_CSS = """
* { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-size: 11pt; line-height: 1.45; color: #1b1f24; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1 { font-size: 22pt; margin: 0 0 10pt; } h2 { font-size: 16pt; margin: 14pt 0 6pt; }
h3 { font-size: 13pt; margin: 12pt 0 4pt; } p { margin: 0 0 8pt; }
table { border-collapse: collapse; width: 100%; } td, th { border: 0.5pt solid #c8ccd2; padding: 4pt 6pt; }
th { background: #f1f3f5; text-align: left; } code, pre { font-family: Menlo, Consolas, monospace; font-size: 9.5pt; }
pre { background: #f6f8fa; padding: 6pt; white-space: pre-wrap; } blockquote { color: #555; margin-left: 12pt; }
img { max-width: 100%; }
"""


def html_to_pdf(html: str, css: str = "", size: str = "A4", orientation: str = "portrait",
                margin: float = 54, files: dict | None = None) -> bytes:
    """Lay out HTML as PDF pages. Chrome renders it (full CSS, exact @page
    size); LibreOffice is the fallback. `files` are written next to the HTML
    so relative URLs (images) resolve."""
    w, h = paper(size, orientation)
    page_css = f"@page {{ size: {w:.2f}pt {h:.2f}pt; margin: {margin:.2f}pt; }}"
    body = html if "<html" in html.lower() else f"<html><body>{html}</body></html>"
    style = f"<style>{page_css}{BASE_CSS}{css or ''}</style>"
    if "<head>" in body:
        body = body.replace("<head>", "<head><meta charset='utf-8'>" + style, 1)
    else:
        body = body.replace("<html>", "<html><head><meta charset='utf-8'>" + style + "</head>", 1)
    with tempfile.TemporaryDirectory(prefix="html2pdf-") as tmp:
        path = os.path.join(tmp, "doc.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        for name, data in (files or {}).items():
            with open(os.path.join(tmp, name), "wb") as fh:
                fh.write(data)
        if find_program("chrome"):
            return chrome_print("file://" + path)
        if find_program("soffice"):
            with open(path, "rb") as fh:
                return soffice_convert(fh.read(), "doc.html", "pdf:writer_web_pdf_Export")
    raise EditError("rendering HTML needs Chrome/Chromium or LibreOffice on the server")


def text_to_html(text: str, title: str | None = None) -> str:
    body = "".join(f"<p>{_html.escape(par).replace(chr(10), '<br/>')}</p>"
                   for par in text.replace("\r\n", "\n").split("\n\n"))
    head = f"<h1>{_html.escape(title)}</h1>" if title else ""
    return f"<html><body>{head}{body}</body></html>"


def markdown_to_html(md: str) -> str:
    import markdown
    return "<html><body>" + markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"]) \
        + "</body></html>"


def soffice_convert(data: bytes, name: str, target: str, timeout: int = 240) -> bytes:
    """Convert a file with headless LibreOffice (isolated profile)."""
    exe = find_program("soffice")
    if not exe:
        raise EditError("LibreOffice is needed for this conversion (brew install --cask libreoffice)")
    with tempfile.TemporaryDirectory(prefix="soffice-") as tmp:
        src = os.path.join(tmp, os.path.basename(name) or "input")
        with open(src, "wb") as fh:
            fh.write(data)
        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir)
        profile = "file://" + os.path.join(tmp, "profile")
        cmd = [exe, f"-env:UserInstallation={profile}", "--headless", "--norestore",
               "--convert-to", target, "--outdir", out_dir, src]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise EditError("LibreOffice timed out")
        produced = os.listdir(out_dir)
        if not produced:
            raise EditError(f"LibreOffice could not convert {name}: "
                            f"{(r.stderr or r.stdout).decode(errors='ignore')[-300:]}")
        with open(os.path.join(out_dir, produced[0]), "rb") as fh:
            return fh.read()


def chrome_print(url: str, timeout: int = 120) -> bytes:
    exe = find_program("chrome")
    if not exe:
        raise EditError("Chrome or Chromium is needed to print web pages")
    with tempfile.TemporaryDirectory(prefix="chrome-") as tmp:
        out = os.path.join(tmp, "page.pdf")
        cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               f"--user-data-dir={os.path.join(tmp, 'profile')}", "--no-pdf-header-footer",
               "--run-all-compositor-stages-before-draw", "--virtual-time-budget=8000",
               f"--print-to-pdf={out}", url]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise EditError("Chrome timed out loading the page")
        if not os.path.exists(out) or os.path.getsize(out) == 0:
            raise EditError(f"Chrome could not print {url}: {r.stderr.decode(errors='ignore')[-300:]}")
        with open(out, "rb") as fh:
            return fh.read()


def open_bytes(data: bytes) -> pikepdf.Pdf:
    return pikepdf.open(io.BytesIO(data))


def page_size(page: pikepdf.Page) -> tuple[float, float]:
    """Displayed width/height of a page (after /Rotate)."""
    box = page.cropbox if "/CropBox" in page.obj else page.mediabox
    w, h = abs(float(box[2]) - float(box[0])), abs(float(box[3]) - float(box[1]))
    rot = int(page.obj.get("/Rotate", 0) or 0) % 360
    return (h, w) if rot in (90, 270) else (w, h)


def page_form(target: pikepdf.Pdf, src: pikepdf.Pdf, index: int) -> pikepdf.Object:
    """Page `index` of `src` as a form XObject owned by `target` (rotation applied)."""
    form = src.pages[index].as_form_xobject(handle_transformations=True)
    return form if src is target else target.copy_foreign(form)


def place_form(pdf: pikepdf.Pdf, sheet: pikepdf.Page, form: pikepdf.Object, rect,
               keep_proportion: bool = True, underlay: bool = False) -> None:
    """Draw a form XObject into rect [x0, y0, x1, y1] (PDF coordinates),
    fitted (aspect kept, centred) or stretched, over or under the content."""
    from ..engine.geom import Matrix, Rect
    from ..engine.writer import append_content, ensure_wrapped
    x0, y0, x1, y1 = [float(v) for v in rect]
    m = Matrix.of(form.get("/Matrix", [1, 0, 0, 1, 0, 0]))
    bb = Rect.of([float(v) for v in form.BBox]).transform(m)
    sx, sy = (x1 - x0) / bb.width, (y1 - y0) / bb.height
    if keep_proportion:
        s = min(sx, sy)
        x0 += ((x1 - x0) - bb.width * s) / 2
        y0 += ((y1 - y0) - bb.height * s) / 2
        sx = sy = s
    name = sheet.add_resource(form, pikepdf.Name.XObject, prefix="Fm")
    cm = [sx, 0, 0, sy, x0 - bb.x0 * sx, y0 - bb.y0 * sy]
    data = f"q {' '.join(f'{v:.5f}' for v in cm)} cm {name} Do Q\n".encode()
    if underlay:
        ensure_wrapped(pdf, sheet)
        sheet.contents_add(pdf.make_stream(data), prepend=True)
    else:
        append_content(pdf, sheet, data)


# -- drawing in *displayed* page coordinates (upright on rotated pages) -----------------
def display_ctm(doc, page_no: int, src, dst, keep_proportion: bool = True):
    """CTM that maps rect `src` (PDF units, y up: an image's unit square or
    a form's bbox) onto rect `dst` given in displayed page coordinates
    (top-left origin, after /Rotate), so the result appears upright."""
    from ..engine.geom import Matrix, Rect
    from ..engine.interp import rotation_matrix
    P, w, h, rot = doc.geometry(page_no)
    dst = Rect.of(list(dst))
    sx, sy = dst.width / src.width, dst.height / src.height
    x0, y0 = dst.x0, dst.y0
    if keep_proportion:
        s = min(sx, sy)
        x0 += (dst.width - src.width * s) / 2
        y0 += (dst.height - src.height * s) / 2
        sx = sy = s
    to_display = Matrix(sx, 0, 0, -sy, x0 - src.x0 * sx, y0 + src.y1 * sy)
    return to_display * rotation_matrix(rot, w, h).inverse() * P.inverse()


def draw_xobject_display(doc, page_no: int, xobj, dst, keep_proportion: bool = True,
                         behind: bool = False, alpha: float = 1.0) -> None:
    """Paint an image or form XObject into a displayed-page rect."""
    from ..engine.geom import Matrix, Rect
    from ..engine.writer import alpha_resource, append_content, ensure_wrapped
    page = doc.page(page_no)
    if str(xobj.get("/Subtype")) == "/Image":
        src = Rect(0, 0, 1, 1)
    else:
        m = Matrix.of(xobj.get("/Matrix", [1, 0, 0, 1, 0, 0]))
        src = Rect.of([float(v) for v in xobj.BBox]).transform(m)
    cm = display_ctm(doc, page_no, src, dst, keep_proportion)
    name = page.add_resource(xobj, pikepdf.Name.XObject, prefix="X")
    gs = f"{alpha_resource(doc.pdf, page, alpha)} gs " if alpha < 0.999 else ""
    data = f"q {gs}{' '.join(f'{v:.5f}' for v in cm)} cm {name} Do Q\n".encode()
    if behind:
        ensure_wrapped(doc.pdf, page)
        page.contents_add(doc.pdf.make_stream(data), prepend=True)
    else:
        append_content(doc.pdf, page, data)
    doc._touch(page_no)


def draw_text_display(doc, page_no: int, text: str, x: float, y: float, size: float, rgb,
                      font: str = "Helvetica", angle: float = 0.0, alpha: float = 1.0,
                      behind: bool = False, render: int = 0) -> float:
    """Draw text with its baseline starting at displayed point (x, y),
    rotated `angle` degrees counter-clockwise as seen. Returns its width."""
    import math
    from ..engine.geom import Matrix
    from ..engine.interp import rotation_matrix
    from ..engine.writer import alpha_resource, append_content, ensure_wrapped, font_resource, text_block
    P, w, h, rot = doc.geometry(page_no)
    obj, info = doc.fontbook.std14(font)
    text = "".join(c if c in info.uni2code() else "?" for c in text)
    width = sum(info.width(info.uni2code()[c]) for c in text) * size
    a = math.radians(angle)
    to_user = rotation_matrix(rot, w, h).inverse() * P.inverse()
    ox, oy = to_user.apply(x, y)
    dx, dy = to_user.apply_vec(math.cos(a), -math.sin(a))
    n = math.hypot(dx, dy) or 1
    dx, dy = dx / n, dy / n
    tm = [dx, dy, -dy, dx, ox, oy]
    page = doc.page(page_no)
    res = font_resource(page, obj)
    gs = alpha_resource(doc.pdf, page, alpha) if alpha < 0.999 else None
    data = text_block([(text, info, res)], tm, size, rgb, render=render, gs_name=gs)
    if behind:
        ensure_wrapped(doc.pdf, page)
        page.contents_add(doc.pdf.make_stream(data), prepend=True)
    else:
        append_content(doc.pdf, page, data)
    doc._touch(page_no)
    return width


def display_size(doc, page_no: int) -> tuple[float, float]:
    _, w, h, rot = doc.geometry(page_no)
    return (h, w) if rot in (90, 270) else (w, h)
