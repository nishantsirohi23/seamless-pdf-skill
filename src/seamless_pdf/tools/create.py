"""Create: write a PDF, cards, photo collage, fillable forms, job
application bundle, QR codes."""
from __future__ import annotations

import html as _html
import io

import pikepdf

from ..engine import EditError
from ..engine.geom import Rect
from ..engine.util import parse_color
from .base import ANY, FILE_OPT, FILES, NONE, PAPER_ENUM, paper, tool
from .render import html_to_pdf, markdown_to_html, open_bytes, page_form, page_size, place_form, text_to_html

CAT = "Create"
FONTS = {"sans": "sans-serif", "serif": "serif", "mono": "monospace"}


def _canvas(buf, W, H):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(buf, pagesize=(W, H), pageCompression=1)
    c.setCreator("Seamless PDF Editor")
    return c


def _image_reader(data: bytes):
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    return ImageReader(Image.open(io.BytesIO(data)))


def _set_info(pdf: pikepdf.Pdf, **kv) -> None:
    for k, v in kv.items():
        if v:
            pdf.docinfo[f"/{k[0].upper()}{k[1:]}"] = v


@tool("create_pdf", "Create PDF", CAT,
      "Write a new PDF from Markdown, HTML or plain text (headings, lists, "
      "tables, links), on any paper size.",
      inputs=NONE, required=["content"], aliases=["write_pdf", "new_pdf", "text_to_pdf_new"],
      options={"title": {"type": "string"},
               "content": {"type": "string", "format": "textarea",
                           "description": "The text. Markdown by default."},
               "format": {"type": "string", "enum": ["markdown", "html", "text"], "default": "markdown"},
               "page_size": {"type": "string", "enum": PAPER_ENUM, "default": "A4"},
               "orientation": {"type": "string", "enum": ["portrait", "landscape"], "default": "portrait"},
               "font": {"type": "string", "enum": list(FONTS), "default": "sans"},
               "font_size": {"type": "number", "default": 11},
               "margin_mm": {"type": "number", "default": 20},
               "output_name": {"type": "string"}})
def create_pdf(ctx, content, title=None, format="markdown", page_size="A4", orientation="portrait",
               font="sans", font_size=11, margin_mm=20, output_name=None):
    if format == "html":
        html = content
    elif format == "text":
        html = text_to_html(content, title)
    else:
        html = markdown_to_html((f"# {title}\n\n" if title else "") + content)
    css = f"* {{ font-family: {FONTS[font]}; }} body {{ font-size: {font_size}pt; }}"
    d = open_bytes(html_to_pdf(html, css, page_size, orientation, margin_mm * 72 / 25.4))
    _set_info(d, title=title, creator="Seamless PDF Editor")
    ctx.add_pdf(d, output_name or (title or "document"))
    ctx.result = {"pages": len(d.pages)}


CARD_SIZES = {"business_card": (85, 55), "postcard": (148, 105), "greeting_card": (148, 210),
              "invitation": (127, 178), "place_card": (90, 50), "a5_flyer": (148, 210)}


@tool("create_card", "Create card", CAT,
      "Design a card: business card, postcard, greeting card, invitation or "
      "place card, with title, message and details.",
      inputs=NONE, required=["title"], aliases=["business_card", "greeting_card", "invitation"],
      options={"kind": {"type": "string", "enum": list(CARD_SIZES), "default": "greeting_card"},
               "title": {"type": "string", "description": "Main line (name, headline)."},
               "subtitle": {"type": "string"},
               "message": {"type": "string", "format": "textarea"},
               "details": {"type": "string", "format": "textarea",
                           "description": "Small print: phone, email, address, date, venue..."},
               "accent_color": {"type": "string", "format": "color", "default": "#4f46e5"},
               "style": {"type": "string", "enum": ["modern", "classic", "bold"], "default": "modern"},
               "copies_per_a4": {"type": "boolean", "default": False,
                                 "description": "Tile copies on an A4 sheet for printing."}})
def create_card(ctx, title, kind="greeting_card", subtitle=None, message=None, details=None,
                accent_color="#4f46e5", style="modern", copies_per_a4=False):
    w_mm, h_mm = CARD_SIZES[kind]
    serif = style == "classic"
    small = kind in ("business_card", "place_card")
    e = lambda s: _html.escape(s or "").replace("\n", "<br/>")  # noqa: E731
    css = f"""
      * {{ font-family: {'serif' if serif else 'sans-serif'}; }}
      body {{ text-align: center; color: #1f2430; }}
      .bar {{ background: {accent_color}; height: {3 if small else 6}pt; margin-bottom: {6 if small else 18}pt; }}
      h1 {{ font-size: {14 if small else 30}pt; margin: 0; color: {accent_color if style == 'bold' else '#1f2430'}; }}
      h2 {{ font-size: {8 if small else 14}pt; font-weight: normal; color: #59606b; margin: 2pt 0 8pt; }}
      .msg {{ font-size: {8 if small else 13}pt; margin: {4 if small else 14}pt 0; line-height: 1.5; }}
      .det {{ font-size: {6.5 if small else 9.5}pt; color: #59606b; margin-top: {4 if small else 18}pt; }}
    """
    html = (f"<html><body><div class='bar'></div><h1>{e(title)}</h1>"
            + (f"<h2>{e(subtitle)}</h2>" if subtitle else "")
            + (f"<div class='msg'>{e(message)}</div>" if message else "")
            + (f"<div class='det'>{e(details)}</div>" if details else "") + "</body></html>")
    mm = 72 / 25.4
    card = open_bytes(html_to_pdf(html, css, f"{w_mm}x{h_mm}mm", "landscape" if w_mm > h_mm else "portrait",
                                  margin=(5 if small else 12) * mm))
    while len(card.pages) > 1:  # a card is one page
        del card.pages[-1]
    if copies_per_a4:
        sheet = pikepdf.new()
        W, H = paper("A4")
        cw, ch = page_size(card.pages[0])
        page = sheet.add_blank_page(page_size=(W, H))
        form = page_form(sheet, card, 0)
        cols, rows = max(1, int((W - 36) // cw)), max(1, int((H - 36) // ch))
        x0, y0 = (W - cols * cw) / 2, (H - rows * ch) / 2
        lines = []
        for r in range(rows):
            for c in range(cols):
                x, y = x0 + c * cw, y0 + r * ch
                place_form(sheet, page, form, [x, y, x + cw, y + ch], keep_proportion=False)
                lines.append(f"{x:.2f} {y:.2f} {cw:.2f} {ch:.2f} re S")
        page.contents_add(sheet.make_stream(("q 0.85 G 0.3 w [2] 0 d " + " ".join(lines) + " Q\n").encode()))
        card = sheet
    ctx.add_pdf(card, f"{kind}-{title[:30]}")
    ctx.result = {"kind": kind, "size_mm": [w_mm, h_mm], "pages": len(card.pages)}


@tool("photo_collage", "Create photo collage", CAT,
      "Arrange photos in a grid on one or more pages, with spacing, background "
      "colour and an optional title.",
      inputs=FILES, accept="image/*,.heic,.heif", aliases=["collage", "photo_grid"],
      options={"columns": {"type": "integer", "default": 3},
               "page_size": {"type": "string", "enum": PAPER_ENUM, "default": "A4"},
               "orientation": {"type": "string", "enum": ["portrait", "landscape"], "default": "landscape"},
               "spacing_mm": {"type": "number", "default": 4},
               "fill": {"type": "string", "enum": ["fit", "crop"], "default": "crop"},
               "background": {"type": "string", "format": "color", "default": "#ffffff"},
               "title": {"type": "string"}})
def photo_collage(ctx, columns=3, page_size="A4", orientation="landscape", spacing_mm=4, fill="crop",
                  background="#ffffff", title=None):
    from PIL import Image, ImageOps
    from .convert import _image_frames
    imgs = []
    for inp in ctx.inputs:
        imgs.extend(_image_frames(inp.data, inp.ext)[:1])
    if not imgs:
        raise EditError("no images")
    W, H = paper(page_size, orientation)
    mm = 72 / 25.4
    gap, margin = spacing_mm * mm, 12 * mm
    cols = max(1, columns)
    head = 30 if title else 0
    rows_per_page = max(1, min(-(-len(imgs) // cols), 4 if orientation == "landscape" else 5))
    cw = (W - 2 * margin - (cols - 1) * gap) / cols
    ch = (H - 2 * margin - head - (rows_per_page - 1) * gap) / rows_per_page
    per_page = cols * rows_per_page
    buf = io.BytesIO()
    c = _canvas(buf, W, H)
    pages = 0
    for start in range(0, len(imgs), per_page):
        if start:
            c.showPage()
        pages += 1
        c.setFillColorRGB(*parse_color(background))
        c.rect(0, 0, W, H, stroke=0, fill=1)
        if title and start == 0:
            c.setFillColorRGB(0.1, 0.1, 0.1)
            c.setFont("Helvetica-Bold", 18)
            c.drawString(margin, H - margin - 16, title)
        for k, (data, iw, ih) in enumerate(imgs[start:start + per_page]):
            r, col = divmod(k, cols)
            x = margin + col * (cw + gap)
            top = margin + head + r * (ch + gap)
            y = H - top - ch
            if fill == "crop":
                im = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), (int(cw * 3), int(ch * 3)))
                b = io.BytesIO()
                im.save(b, "JPEG", quality=88)
                c.drawImage(_image_reader(b.getvalue()), x, y, cw, ch)
            else:
                c.drawImage(_image_reader(data), x, y, cw, ch, preserveAspectRatio=True, anchor="c", mask="auto")
    c.save()
    ctx.add_pdf(buf.getvalue(), title or "collage")
    ctx.result = {"photos": len(imgs), "pages": pages}


FIELD_KINDS = ["text", "multiline", "checkbox", "dropdown", "date", "email", "number", "signature_line"]


@tool("create_pdf_form", "Create PDF form", CAT,
      "Generate a fillable PDF form from a list of fields (label + type), with "
      "a title and intro text.",
      inputs=NONE, required=["fields"], aliases=["new_form", "fillable_form"],
      options={"title": {"type": "string", "default": "Form"},
               "intro": {"type": "string", "format": "textarea"},
               "fields": {"type": "array", "description": "Fields in order.",
                          "items": {"type": "object", "properties": {
                              "label": {"type": "string"},
                              "type": {"type": "string", "enum": FIELD_KINDS},
                              "options": {"type": "string", "description": "Comma-separated, for dropdowns."},
                              "required": {"type": "boolean"}}}},
               "page_size": {"type": "string", "enum": PAPER_ENUM, "default": "A4"}})
def create_pdf_form(ctx, fields, title="Form", intro=None, page_size="A4"):
    from reportlab.lib.colors import Color
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    W, H = paper(page_size)
    m = 56
    buf = io.BytesIO()
    c = _canvas(buf, W, H)
    c.setTitle(title)
    border, bg = Color(0.6, 0.62, 0.66), Color(0.97, 0.97, 0.99)
    pages = 1
    y = m  # distance from the top
    c.setFont("Helvetica-Bold", 20)
    c.drawString(m, H - (y + 18), title)
    y += 34
    if intro:
        para = Paragraph(_html.escape(intro).replace("\n", "<br/>"),
                         ParagraphStyle("intro", fontName="Helvetica", fontSize=10, leading=13,
                                        textColor=Color(0.3, 0.3, 0.3)))
        _, ph = para.wrap(W - 2 * m, H)
        para.drawOn(c, m, H - y - ph)
        y += ph + 12
    names = set()
    for f in fields:
        label = str(f.get("label") or "Field")
        kind = f.get("type") or "text"
        if kind not in FIELD_KINDS:
            raise EditError(f"field type {kind!r} must be one of {FIELD_KINDS}")
        height = 60 if kind == "multiline" else 20
        if y + height + 30 > H - m:
            c.showPage()
            pages += 1
            y = m
        name = "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_")[:40] or "field"
        base, i = name, 2
        while name in names:
            name, i = f"{base}_{i}", i + 1
        names.add(name)
        req = bool(f.get("required"))
        star = " *" if req else ""
        c.setFillColorRGB(0.1, 0.1, 0.1)
        if kind == "checkbox":
            c.acroForm.checkbox(name=name, tooltip=label, x=m, y=H - (y + 14), size=14, buttonStyle="check",
                                borderColor=border, fillColor=bg, borderWidth=0.8,
                                fieldFlags="required" if req else "")
            c.setFont("Helvetica", 10.5)
            c.drawString(m + 22, H - (y + 11), label + star)
            y += 28
            continue
        c.setFont("Helvetica-Bold" if req else "Helvetica", 10)
        c.drawString(m, H - (y + 10), label + star)
        y += 16
        if kind == "signature_line":
            c.setStrokeColorRGB(0.3, 0.3, 0.3)
            c.setLineWidth(0.8)
            c.line(m, H - (y + height), m + 240, H - (y + height))
            y += height + 16
            continue
        common = dict(name=name, tooltip=label, x=m, y=H - (y + height), width=W - 2 * m, height=height,
                      borderColor=border, fillColor=bg, borderWidth=0.8, textColor=Color(0, 0, 0),
                      fontName="Helvetica", fontSize=10)
        flags = ["required"] if req else []
        if kind == "dropdown":
            opts = [o.strip() for o in str(f.get("options") or "").split(",") if o.strip()] or ["Option 1"]
            c.acroForm.choice(value=opts[0], options=opts, fieldFlags=" ".join(flags + ["combo"]), **common)
        else:
            if kind == "multiline":
                flags.append("multiline")
            c.acroForm.textfield(value="", maxlen=None if kind == "multiline" else 200,
                                 fieldFlags=" ".join(flags), **common)
        y += height + 14
    c.save()
    ctx.add_pdf(buf.getvalue(), title)
    ctx.result = {"fields": len(fields), "pages": pages}


@tool("job_application", "Create PDF job application", CAT,
      "Assemble a job application: a cover page (name, position, contact, "
      "photo), the cover letter, then your CV, certificates and other files "
      "(PDFs, Word, images) merged in order, with bookmarks.",
      inputs=ANY, required=["applicant_name", "position"], aliases=["application_pdf", "bewerbung"],
      options={"applicant_name": {"type": "string"}, "position": {"type": "string"},
               "company": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"},
               "address": {"type": "string"},
               "photo": {**FILE_OPT, "description": "Applicant photo (optional)."},
               "cover_letter": {"type": "string", "format": "textarea", "description": "Markdown."},
               "include_cover_page": {"type": "boolean", "default": True}})
def job_application(ctx, applicant_name, position, company=None, email=None, phone=None, address=None,
                    photo=None, cover_letter=None, include_cover_page=True):
    from .convert import _one_to_pdf
    from .organize import _set_outline
    out = pikepdf.new()
    toc = []
    if include_cover_page:
        W, H = paper("A4")
        buf = io.BytesIO()
        c = _canvas(buf, W, H)
        c.setFillColorRGB(0.31, 0.27, 0.9)
        c.rect(0, H - 8, W, 8, stroke=0, fill=1)
        y = 250
        if photo:
            _, pdata, _ = ctx.store.file_bytes(photo)
            c.drawImage(_image_reader(pdata), W / 2 - 60, H - 240, 120, 150, preserveAspectRatio=True,
                        anchor="c", mask="auto")
            y = 280

        def center(text, size, font="Helvetica", color=(0.12, 0.14, 0.19)):
            nonlocal y
            c.setFont(font, size)
            c.setFillColorRGB(*color)
            c.drawCentredString(W / 2, H - y, text)
            y += size * 1.8
        center("Application", 13, color=(0.4, 0.42, 0.47))
        center(f"for the position of {position}", 20, "Helvetica-Bold")
        if company:
            center(f"at {company}", 14)
        y += 30
        center(applicant_name, 18, "Helvetica-Bold")
        for line in (address, phone, email):
            if line:
                center(line, 11, color=(0.35, 0.37, 0.42))
        c.save()
        out.pages.extend(open_bytes(buf.getvalue()).pages)
        toc.append((1, "Cover page", 1))
    if cover_letter:
        start = len(out.pages) + 1
        out.add_pages_from(open_bytes(html_to_pdf(markdown_to_html(cover_letter))))
        toc.append((1, "Cover letter", start))
    for inp in ctx.inputs:
        start = len(out.pages) + 1
        out.add_pages_from(open_bytes(_one_to_pdf(inp)))
        toc.append((1, inp.stem, start))
    if not len(out.pages):
        raise EditError("nothing to assemble: add a cover page, cover letter or files")
    _set_outline(out, toc)
    _set_info(out, title=f"Application - {applicant_name} - {position}", author=applicant_name)
    ctx.add_pdf(out, f"Application-{applicant_name.replace(' ', '_')}")
    ctx.result = {"pages": len(out.pages), "sections": [t[1] for t in toc]}


def _qr(data: str, error: str, scale: int, dark: str, light: str, kind: str) -> bytes:
    import segno
    qr = segno.make(data, error=error.lower())
    buf = io.BytesIO()
    qr.save(buf, kind=kind, scale=scale, dark=dark, light=light, border=2)
    return buf.getvalue()


@tool("generate_qr_code", "Generate QR code", CAT,
      "Make a QR code for a link, text, Wi-Fi login, contact card, etc. as "
      "PNG, SVG or PDF.",
      inputs=NONE, required=["data"], aliases=["qr_code", "qr"],
      options={"data": {"type": "string", "description": "URL or text to encode."},
               "format": {"type": "string", "enum": ["png", "svg", "pdf"], "default": "png"},
               "error_correction": {"type": "string", "enum": ["L", "M", "Q", "H"], "default": "M"},
               "scale": {"type": "integer", "default": 10},
               "color": {"type": "string", "format": "color", "default": "#000000"},
               "background": {"type": "string", "format": "color", "default": "#ffffff"}})
def generate_qr_code(ctx, data, format="png", error_correction="M", scale=10, color="#000000",
                     background="#ffffff"):
    if format == "pdf":
        png = _qr(data, error_correction, 20, color, background, "png")
        buf = io.BytesIO()
        c = _canvas(buf, 200, 200)
        c.drawImage(_image_reader(png), 10, 10, 180, 180)
        c.save()
        ctx.add_pdf(buf.getvalue(), "qr-code")
    else:
        ctx.add_file(_qr(data, error_correction, scale, color, background, format), f"qr-code.{format}")
    ctx.result = {"encoded": data}


@tool("add_qr_code", "Add QR code to PDF", "Edit",
      "Stamp a QR code onto a page of the open PDF at a position or rect.",
      in_place=True, required=["data"],
      options={"data": {"type": "string"},
               "page": {"type": "integer", "default": 1},
               "position": {"type": "string", "enum": ["top-left", "top-right", "bottom-left", "bottom-right"],
                            "default": "bottom-right"},
               "size": {"type": "number", "default": 72, "description": "Side length in points."},
               "rect": {"type": "array", "items": {"type": "number"}, "description": "Overrides position."}})
def add_qr_code(ctx, doc, data, page=1, position="bottom-right", size=72, rect=None):
    from ..engine.images import image_xobject
    from ..engine.interp import rotation_matrix
    from .render import display_size, draw_xobject_display
    p = max(0, min(doc.page_count, page) - 1)
    _, w, h, rot = doc.geometry(p)
    R = rotation_matrix(rot, w, h)
    if rect:
        box = Rect.of(rect)
        disp = box.transform(R)
    else:
        W, H = display_size(doc, p)
        m = 28
        x = m if "left" in position else W - m - size
        y = m if "top" in position else H - m - size
        disp = Rect(x, y, x + size, y + size)
        box = disp.transform(R.inverse())
    stream, _, _ = image_xobject(doc.pdf, _qr(data, "M", 12, "#000000", "#ffffff", "png"))
    draw_xobject_display(doc, p, doc.pdf.make_indirect(stream), disp.list())
    ctx.result = {"rect": [round(v, 1) for v in box.list()]}
