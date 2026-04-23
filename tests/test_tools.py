"""Document tool tests: every tool runs on real inputs and its output is
checked. Fixtures are made with ReportLab; outputs are read back with PDFium
(text, images) and pikepdf (structure), never with the code under test.
Run with `python tests/test_tools.py` from backend/."""
from __future__ import annotations

import io
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pikepdf  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402
import pypdfium2.raw as pdfium_c  # noqa: E402

from seamless_pdf.engine import EditError  # noqa: E402
from seamless_pdf.store import Store  # noqa: E402
from seamless_pdf.tools import TOOLS, run_tool  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "reportlab_arial.pdf")
S = Store()


# -- fixtures ---------------------------------------------------------------------------
def make(draw, pages=1, size=(595, 842)) -> bytes:
    """ReportLab PDF; draw(canvas, page_index, height) paints each page."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    for i in range(pages):
        draw(c, i, size[1])
        c.showPage()
    c.save()
    return buf.getvalue()


def pdf_with(pages=3, text="Page", size=(595, 842), extra=None) -> str:
    def draw(c, i, H):
        c.setFont("Helvetica", 24)
        c.drawString(72, H - 100, f"{text} {i + 1}")
        c.setFont("Helvetica", 11)
        c.drawString(72, H - 140, "Contact: jane.doe@example.com, +49 30 1234567")
        if extra and i in extra:
            c.setFont("Helvetica", 14)
            c.drawString(72, H - 300, extra[i])
    return S.add(make(draw, pages, size), f"{text.lower()}.pdf")


def png(color=(200, 40, 40), w=120, h=80) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def file(data: bytes, name: str) -> str:
    return S.add_any(data, name)


def run(tool_name, inputs=None, **opts):
    return run_tool(S, tool_name, inputs or [], opts)


def out_bytes(res, i=0) -> bytes:
    return S.file_bytes(res["outputs"][i]["id"])[1]


def out_pdf(res, i=0) -> pikepdf.Pdf:
    return pikepdf.open(io.BytesIO(out_bytes(res, i)))


def doc(fid):
    return S.get(fid)


def _bytes(src) -> bytes:
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    if isinstance(src, dict):          # a run() result
        return out_bytes(src)
    return src.pdf_bytes()[1]          # live engine document


def text(src, page=0) -> str:
    """Page text as two independent readers extract it: PDFium, then
    pdfminer (each has blind spots: PDFium splits some Chrome-made pages
    into one glyph per line, pdfminer does the same for rotated text).
    `x in text()` means either sees it; `x not in text()` means neither."""
    import pdfplumber
    data = _bytes(src)
    a = pdfium.PdfDocument(data)[page].get_textpage().get_text_range().replace("\r\n", "\n")
    with pdfplumber.open(io.BytesIO(data)) as pl:
        return a + "\n" + (pl.pages[page].extract_text() or "")


def n_images(src, page=0) -> int:
    pg = pdfium.PdfDocument(_bytes(src))[page]
    return len(list(pg.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=4)))


def page_count(src) -> int:
    return len(pdfium.PdfDocument(_bytes(src)))


def width(src, page=0) -> float:
    return pdfium.PdfDocument(_bytes(src))[page].get_size()[0]


def outline(pdf: pikepdf.Pdf) -> list[str]:
    with pdf.open_outline() as ol:
        return [it.title for it in ol.root]


def widgets(pdf: pikepdf.Pdf, page=0) -> dict:
    out = {}
    for a in pdf.pages[page].obj.get("/Annots") or []:
        if str(a.get("/Subtype")) != "/Widget":
            continue
        f = a if "/FT" in a else a.get("/Parent")
        ft, ff = str(f.get("/FT")), int(f.get("/Ff", 0))
        kind = {"/Tx": "Text", "/Btn": "CheckBox"}.get(ft, "ComboBox" if ff & (1 << 17) else "ListBox")
        out[str(f.get("/T"))] = kind
    return out


INVOICE = {
    "invoice_number": "INV-7", "issue_date": "2026-03-03", "due_date": "2026-04-02", "currency": "EUR",
    "seller": {"name": "Acme GmbH", "address": "Hauptstr. 1", "postcode": "10115", "city": "Berlin",
               "country_code": "DE", "vat_id": "DE123456789"},
    "buyer": {"name": "Northwind AG", "address": "Hafen 2", "postcode": "20095", "city": "Hamburg",
              "country_code": "DE"},
    "items": [{"description": "Consulting", "quantity": 10, "unit": "hour", "unit_price": 120, "vat_rate": 19},
              {"description": "Book", "quantity": 2, "unit": "piece", "unit_price": 25.5, "vat_rate": 7}],
    "iban": "DE89370400440532013000",
}


# -- organize ----------------------------------------------------------------------------
def test_merge_assemble_split_extract():
    a, b = pdf_with(2, "Alpha"), pdf_with(3, "Beta")
    r = run("merge_pdfs", [a, b])
    assert page_count(r) == 5 and outline(out_pdf(r))[0] == "alpha", outline(out_pdf(r))
    r = run("assemble_pdf", [a, b], selection="2:3, 1:1, 2:1")
    t = [text(r, p).split("\n")[0].strip() for p in range(3)]
    assert t == ["Beta 3", "Alpha 1", "Beta 1"], t
    r = run("split_pdf", [b], mode="every_n_pages", n=2)
    assert [o["pages"] for o in r["outputs"]] == [2, 1]
    r = run("split_pdf", [b], mode="ranges", ranges="1;2-3")
    assert len(r["outputs"]) == 2
    r = run("extract_pages", [b], pages="2-3")
    assert page_count(r) == 2
    assert run("merge", [a, b])["outputs"]  # alias


def test_in_place_page_ops_and_undo():
    f = pdf_with(4, "Gamma")
    r = run("rearrange_pages", [f], order="4,3,2,1")
    assert r["structure_changed"] and text(doc(f), 0).startswith("Gamma 4")
    run("remove_pages", [f], pages="1")
    assert doc(f).page_count == 3
    run("rotate_pages", [f], pages="1", angle=90)
    assert doc(f).geometry(0)[3] == 90
    run("insert_blank_pages", [f], after=1, count=2)
    assert doc(f).page_count == 5
    S.get(f).undo()
    assert doc(f).page_count == 3
    try:
        run("remove_pages", [f], pages="all")
        raise AssertionError("removing every page must fail")
    except EditError:
        pass


def test_nup_halve_bookmarks_images_text():
    from reportlab.lib.utils import ImageReader
    f = pdf_with(5, "Delta")
    r = run("pages_per_sheet", [f], per_sheet=4)
    assert page_count(r) == 2
    r = run("halve_pages", [f], pages="1")
    assert page_count(r) == 6

    def draw(c, i, H):
        c.setFont("Helvetica", 22)
        c.drawString(72, H - 80, ["Chapter One", "Chapter Two"][i])
        c.setFont("Helvetica", 11)
        c.drawString(72, H - 120, ("Body text " if i == 0 else "More body text ") * 8)
        if i == 0:
            c.drawImage(ImageReader(io.BytesIO(png())), 72, H - 280, 120, 80)
    g = S.add(make(draw, 2), "book.pdf")
    r = run("bookmarks", [g], action="auto_from_headings")
    assert [b["title"] for b in r["result"]["bookmarks"]] == ["Chapter One", "Chapter Two"], r["result"]
    r = run("extract_images", [g], format="png")
    assert r["result"]["images"] == 1 and r["outputs"][0]["name"].endswith(".png")
    r = run("extract_text", [g], format="markdown")
    assert "Chapter One" in r["result"]["text"]


def test_form_fields_survive_split_and_merge():
    """Copied pages keep working form fields: every widget stays reachable
    from the output's /AcroForm (Acrobat ignores orphaned widgets), and
    clashing field names from two merged forms are renamed."""
    import warnings
    r = run("create_pdf_form", title="Signup", fields=[{"label": "Full name", "type": "text"},
                                                       {"label": "Subscribe", "type": "checkbox"}])
    form = r["outputs"][0]["id"]
    run("fill_form_fields", [form], values={"full_name": "Ann"})

    def reachable(pdf):
        names, stack = set(), list(pdf.Root.AcroForm.Fields)
        while stack:
            f = stack.pop()
            names.add(f.objgen)
            stack.extend(f.get("/Kids") or [])
        widgets = [a for pg in pdf.pages for a in (pg.obj.get("/Annots") or [])
                   if str(a.get("/Subtype")) == "/Widget"]
        return widgets and all(w.objgen in names for w in widgets), len(widgets)
    with warnings.catch_warnings():
        warnings.simplefilter("error")          # pikepdf warns about orphaned widgets
        part = out_pdf(run("extract_pages", [form], pages="1"))
        ok, n = reachable(part)
        assert ok and n == 2, n
        merged = out_pdf(run("merge_pdfs", [form, form]))
        ok, n = reachable(merged)
        assert ok and n == 4, n
    names = set(widgets(merged, 0)) | set(widgets(merged, 1))
    assert len(names) == 4, names                 # second copy's fields renamed, not merged


# -- edit -----------------------------------------------------------------------------------
def test_watermark_numbers_overlay_crop_size_meta():
    f = pdf_with(3, "Edit")
    run("add_watermark", [f], text="CONFIDENTIAL", opacity=0.2)
    assert "CONFIDENTIAL" in text(doc(f), 1)
    img = file(png(), "logo.png")
    run("add_watermark", [f], image_file=img, position="tile", pages="1")
    assert n_images(doc(f), 0)
    run("add_page_numbers", [f], format="Page {n} of {total}")
    assert "Page 3 of 3" in text(doc(f), 2)
    letterhead = pdf_with(1, "LETTERHEAD")
    run("overlay_pdf", [f], overlay_file=letterhead, mode="background")
    assert "LETTERHEAD" in text(doc(f), 2)
    w0 = width(doc(f), 0)
    run("crop_pages", [f], mode="margins", left=20, right=20, top=10, bottom=10, pages="1")
    assert width(doc(f), 0) < w0 - 30, width(doc(f), 0)
    run("change_page_size", [f], size="Letter")
    assert round(width(doc(f), 1)) == 612
    assert "Edit 2" in text(doc(f), 1)
    r = run("set_metadata", [f], title="Quarterly", author="Jane")
    assert r["result"]["metadata"]["title"] == "Quarterly"
    run("viewer_preferences", [f], page_layout="TwoColumnLeft", fit_window=True, open_page=2)
    assert str(doc(f).pdf.Root.get("/PageLayout")) == "/TwoColumnLeft"


def test_make_fillable_fill_flatten():
    def draw(c, i, H):
        c.setFont("Helvetica", 12)
        c.drawString(72, H - 100, "Name: ____________________")
        c.drawString(72, H - 140, "City:")
        c.line(120, H - 142, 320, H - 142)
        c.rect(72, H - 182, 12, 12)
        c.drawString(90, H - 180, "I agree")
    f = S.add(make(draw), "form.pdf")
    r = run("make_fillable", [f])
    names = {a["name"]: a["type"] for a in r["result"]["fields_added"]}
    assert "name" in names and "city" in names and "checkbox" in names.values(), names
    cb = [n for n, t in names.items() if t == "checkbox"][0]
    r = run("fill_form_fields", [f], values={"name": "Jane Doe", "city": "Berlin", cb: True})
    assert set(r["result"]["filled"]) == {"name", "city", cb}
    # filled values are painted (appearance streams) in the editor's render
    import numpy as np
    box = doc(f).render_image(0, 2.0, clip=[120, 82, 330, 102])
    assert (np.asarray(box.convert("L")) < 100).sum() > 50, "filled field not rendered"
    run("flatten_pdf", [f])
    assert not widgets(doc(f).pdf) and "Jane Doe" in text(doc(f)) and "Berlin" in text(doc(f))


# -- optimize -----------------------------------------------------------------------------------
def test_compress_web_repair_rasterize():
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    big = io.BytesIO()
    Image.effect_noise((1800, 1800), 60).convert("RGB").save(big, "PNG")
    f = S.add(make(lambda c, i, H: c.drawImage(ImageReader(io.BytesIO(big.getvalue())), 0, 0, 595, 842)),
              "photo.pdf")
    r = run("compress_pdf", [f], level="high")
    assert r["result"]["compressed_bytes"] < r["result"]["original_bytes"] * 0.7, r["result"]
    assert r["outputs"][0]["editable"] is False  # exact bytes kept
    assert n_images(r) == 1
    r = run("web_optimize", [pdf_with(2, "Web")])
    assert pikepdf.open(io.BytesIO(out_bytes(r))).is_linearized
    broken = S.add_any(open(FIX, "rb").read()[:-200] + b"garbage", "broken.pdf", raw=True)
    r = run("repair_pdf", [broken])
    assert page_count(r) == 1
    r = run("rasterize_pdf", [pdf_with(1, "Raster")], dpi=72)
    assert n_images(r) and not text(r).strip()


def test_ocr_tool():
    from reportlab.lib.utils import ImageReader

    def draw(c, i, H):
        c.setFont("Helvetica", 18)
        c.drawString(72, H - 120, "Scanned receipt total 42")
    scan = pdfium.PdfDocument(make(draw))[0].render(scale=200 / 72).to_pil()
    b = io.BytesIO()
    scan.save(b, "PNG")
    f = S.add(make(lambda c, i, H: c.drawImage(ImageReader(io.BytesIO(b.getvalue())), 0, 0, 595, 842)),
              "scan.pdf")
    if TOOLS["ocr_pdf"].availability():
        print("  (skipped: no tesseract)")
        return
    r = run("ocr_pdf", [f])
    assert r["result"]["words"] >= 4 and "receipt" in text(doc(f)).lower()


# -- security -----------------------------------------------------------------------------------
def test_protect_unlock_redact_scrub_password():
    f = pdf_with(1, "Secret")
    r = run("protect_pdf", [f], user_password="open-sesame", allow_print=False)
    locked = r["outputs"][0]
    assert locked["locked"] and not locked["editable"]
    try:
        run("unlock_pdf", [locked["id"]], password="wrong")
        raise AssertionError("wrong password accepted")
    except EditError:
        pass
    r = run("unlock_pdf", [locked["id"]], password="open-sesame")
    assert r["outputs"][0]["editable"] and "Secret 1" in text(r)
    g = pdf_with(1, "Redact")
    r = run("redact_pdf", [g], presets=["email", "phone"], terms=["Redact"])
    t = text(doc(g))
    assert "jane.doe@example.com" not in t and "1234567" not in t and "+49" not in t \
        and "Redact" not in t and "Contact:" in t, t
    # the removed bytes are gone from the file itself, not just covered
    pdf = pikepdf.open(io.BytesIO(_bytes(doc(g))))
    page = pdf.pages[0]
    page.contents_coalesce()
    raw = page.obj.Contents.read_bytes()
    assert b"example" not in raw and b"1234567" not in raw, raw
    d = S.get(g)
    d.pdf.docinfo["/Author"] = "Someone"
    d.pdf.docinfo["/Title"] = "T"
    run("remove_metadata", [g])
    assert not any(str(v) for v in doc(g).pdf.docinfo.values())
    r = run("generate_password", length=16, count=3)
    assert len(r["result"]["passwords"]) == 3 and all(len(p) == 16 for p in r["result"]["passwords"])


def test_sign_visual_and_digital_and_verify():
    f = pdf_with(2, "Contract")
    run("sign_pdf", [f], name="Jane Doe")
    t = text(doc(f), 1)
    assert "Jane Doe" in t and "20" in t, t  # typed signature + date on the last page
    sig_img = file(png((10, 10, 120), 300, 90), "sig.png")
    run("sign_pdf", [f], signature_image=sig_img, page=1, add_date=False)
    assert n_images(doc(f), 0)
    r = run("digital_sign_pdf", [f], signer_name="Jane Doe", reason="Approved", visible=True)
    signed, cert = r["outputs"][0], r["outputs"][1]
    assert not signed["editable"] and cert["name"].endswith(".pem")
    v = run("verify_signatures", [signed["id"]])
    s = v["result"]["signatures"][0]
    assert s["unchanged_since_signing"] and not s["trusted"], s
    v = run("verify_signatures", [signed["id"]], trusted_certificate=cert["id"])
    assert v["result"]["signatures"][0]["trusted"]


# -- view -------------------------------------------------------------------------------------------
def test_compare_search_check_info():
    a = pdf_with(2, "Report")
    b = pdf_with(2, "Report", extra={1: "An added sentence."})
    r = run("compare_pdfs", [a, b])
    assert not r["result"]["identical"] and [c["page"] for c in r["result"]["changed_pages"]] == [2], r["result"]
    assert page_count(r) == 2
    r = run("search_pdfs", [a, b], query="added")
    assert r["result"]["count"] == 1 and r["result"]["hits"][0]["page"] == 2
    r = run("check_pdfa", [a])
    assert r["result"]["claims_pdfa"] is None and not r["result"]["passes_basic_checks"]
    r = run("document_info", [a])
    assert r["result"]["pages"] == 2 and "Helvetica" in r["result"]["fonts"], r["result"]


# -- convert ------------------------------------------------------------------------------------------
def test_convert_to_pdf_formats():
    import docx
    import openpyxl
    import pptx
    dx = docx.Document()
    dx.add_heading("Word heading", 1)
    dx.add_paragraph("Hello from Word.")
    b = io.BytesIO()
    dx.save(b)
    word = file(b.getvalue(), "letter.docx")
    wb = openpyxl.Workbook()
    wb.active.append(["Item", "Price"])
    wb.active.append(["Pen", 2.5])
    b = io.BytesIO()
    wb.save(b)
    xls = file(b.getvalue(), "prices.xlsx")
    pr = pptx.Presentation()
    s = pr.slides.add_slide(pr.slide_layouts[1])
    s.shapes.title.text = "Slide title"
    b = io.BytesIO()
    pr.save(b)
    ppt = file(b.getvalue(), "deck.pptx")
    md = file(b"# Notes\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", "notes.md")
    txt = file(b"plain text file\nsecond line", "readme.txt")
    html = file(b"<h1>Hello HTML</h1><p>para</p>", "page.html")
    from PIL import Image
    import pillow_heif
    pillow_heif.register_heif_opener()
    hb = io.BytesIO()
    Image.new("RGB", (64, 48), (0, 120, 200)).save(hb, "HEIF")
    heic = file(hb.getvalue(), "photo.heic")
    webp = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 120, 0)).save(webp, "WEBP")
    wp = file(webp.getvalue(), "photo.webp")
    svg = file(b"<svg xmlns='http://www.w3.org/2000/svg' width='100' height='50'><rect width='100' height='50' "
               b"fill='red'/><text x='5' y='30'>SVG text</text></svg>", "box.svg")
    r = run("convert_to_pdf", [word, xls, ppt, md, txt, html, heic, wp, svg])
    texts = [text(out_bytes(r, i)) for i in range(len(r["outputs"]))]
    assert "Hello from Word." in texts[0] and "Pen" in texts[1] and "Slide title" in texts[2], texts[:3]
    assert "Notes" in texts[3] and "plain text file" in texts[4] and "Hello HTML" in texts[5], texts[3:6]
    assert all(n_images(out_bytes(r, i)) for i in (6, 7)), "image pages"
    assert "SVG text" in texts[8] and round(width(out_bytes(r, 8))) == 75, (texts[8], width(out_bytes(r, 8)))
    r = run("images_to_pdf", [heic, wp, file(png(), "a.png")], page_size="A4")
    assert page_count(r) == 3 and round(width(r)) == 842  # landscape image
    r = run("word_to_pdf", [word])  # alias
    assert r["outputs"]


def test_epub_and_comic_to_pdf():
    import zipfile
    if not __import__("seamless_pdf.tools.base", fromlist=["find_program"]).find_program("pandoc"):
        print("  (skipped: no pandoc)")
        return
    src = S.add(open(FIX, "rb").read(), "invoice.pdf")
    epub = run("pdf_to_epub", [src])["outputs"][0]["id"]
    r = run("convert_to_pdf", [epub])
    assert "Amount" in text(r), text(r)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("01.png", png((1, 2, 3)))
        z.writestr("02.png", png((4, 5, 6)))
    r = run("convert_to_pdf", [file(b.getvalue(), "comic.cbz")])
    assert page_count(r) == 2 and n_images(r)


def test_webpage_to_pdf():
    if TOOLS["webpage_to_pdf"].availability():
        print("  (skipped: no chrome)")
        return
    try:
        run("webpage_to_pdf", url="file:///etc/hosts")
        raise AssertionError("file:// URLs must be refused")
    except EditError:
        pass
    import http.server
    import threading
    tmp = os.path.join(os.path.dirname(__file__), "fixtures")

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=tmp, **k)

        def log_message(self, *a):
            pass
    page = os.path.join(tmp, "_page.html")
    with open(page, "w") as fh:
        fh.write("<h1>Local page</h1><p>printed by chrome</p>")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = run("webpage_to_pdf", url=f"http://127.0.0.1:{srv.server_address[1]}/_page.html")
        assert "Local page" in text(r)
    finally:
        srv.shutdown()
        os.remove(page)


def test_convert_from_pdf_formats():
    src = S.add(open(FIX, "rb").read(), "invoice.pdf")
    for gone in ("pdf_to_word", "pdf_to_odt", "pdf_to_rtf", "pdf_to_svg", "pdf_to_pdfa"):
        assert gone not in TOOLS, gone
    for name, ext in [("pdf_to_excel", "xlsx"), ("pdf_to_powerpoint", "pptx"),
                      ("pdf_to_html", "html"), ("pdf_to_md", "md"), ("pdf_to_txt", "txt"),
                      ("pdf_to_png", "png"), ("pdf_to_jpg", "jpg"),
                      ("pdf_to_tiff", "tiff"), ("pdf_to_webp", "webp"),
                      ("pdf_to_ods", "ods"), ("pdf_to_odp", "odp"), ("pdf_to_epub", "epub")]:
        if TOOLS[name].availability():
            print(f"  (skipped {name}: {TOOLS[name].availability()})")
            continue
        r = run(name, [src])
        o = r["outputs"][0]
        assert o["name"].endswith("." + ext) and o["size"] > 100, (name, o)
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(out_bytes(run("pdf_to_excel", [src]))))
    cells = " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row if c.value is not None)
    assert "Amount" in cells, cells[:300]
    md = out_bytes(run("pdf_to_md", [src])).decode()
    assert "Amount due" in md, md[:300]
    h = out_bytes(run("pdf_to_html", [src])).decode()
    assert "<section" in h and "Amount due" in h


# -- create ----------------------------------------------------------------------------------------------
def test_create_tools():
    r = run("create_pdf", title="Hello", content="Some **bold** text.\n\n| x | y |\n|---|---|\n| 1 | 2 |")
    t = text(r)
    assert "Hello" in t and "bold" in t
    pdf = out_pdf(r)
    assert str(pdf.docinfo.get("/Title")) == "Hello"
    r = run("create_card", kind="business_card", title="Jane Doe", subtitle="Engineer",
            details="jane@example.com", copies_per_a4=True)
    assert text(r).count("Jane Doe") >= 8, text(r)
    imgs = [file(png((i * 40, 80, 160)), f"p{i}.png") for i in range(5)]
    r = run("photo_collage", imgs, columns=3, title="Trip")
    assert n_images(r) == 5 and "Trip" in text(r)
    r = run("create_pdf_form", title="Signup", fields=[
        {"label": "Full name", "type": "text", "required": True},
        {"label": "Plan", "type": "dropdown", "options": "Free, Pro"},
        {"label": "Comments", "type": "multiline"}, {"label": "Subscribe", "type": "checkbox"}])
    ws = widgets(out_pdf(r))
    assert ws == {"full_name": "Text", "plan": "ComboBox", "comments": "Text", "subscribe": "CheckBox"}, ws
    # the generated form is fillable by our own form tools
    form = r["outputs"][0]["id"]
    run("fill_form_fields", [form], values={"full_name": "Ann", "plan": "Pro", "subscribe": True})
    run("flatten_pdf", [form])
    assert "Ann" in text(doc(form)) and "Pro" in text(doc(form))
    cv = pdf_with(2, "CV")
    r = run("job_application", [cv], applicant_name="Jane Doe", position="Engineer", company="Acme",
            email="jane@example.com", cover_letter="Dear team,\n\nI would like to apply.")
    toc = outline(out_pdf(r))
    assert toc == ["Cover page", "Cover letter", "cv"], toc
    assert "Engineer" in text(r, 0) and "apply" in text(r, 1)
    r = run("generate_qr_code", data="https://example.com", format="svg")
    assert r["outputs"][0]["name"] == "qr-code.svg"
    r = run("generate_qr_code", data="https://example.com", format="pdf")
    assert n_images(r) == 1
    f = pdf_with(1, "QR")
    run("add_qr_code", [f], data="https://example.com")
    assert n_images(doc(f))


# -- invoices --------------------------------------------------------------------------------------------
def test_invoices():
    import facturx
    r = run("create_invoice", **INVOICE)
    assert r["result"]["total"] == "1482.57", r["result"]   # 1200*1.19 + 51*1.07
    assert "Northwind AG" in text(r)
    r = run("create_e_invoice", **INVOICE)
    pdf_id, xml_id = r["outputs"][0]["id"], r["outputs"][1]["id"]
    _, pdf_bytes, _ = S.file_bytes(pdf_id)
    name, xml = facturx.get_xml_from_pdf(pdf_bytes)
    assert name == "factur-x.xml" and b"INV-7" in xml
    v = run("validate_e_invoice", [pdf_id, xml_id])
    assert v["result"]["all_valid"], v["result"]
    r = run("create_e_invoice", syntax="ubl", **INVOICE)
    ubl = r["outputs"][1]["id"]
    v = run("validate_e_invoice", [ubl])
    assert v["result"]["all_valid"] and v["result"]["reports"][0]["flavor"].startswith("ubl"), v["result"]
    r = run("xml_e_invoice_to_pdf", [xml_id])
    assert "Consulting" in text(r)
    plain = run("create_invoice", **INVOICE)["outputs"][0]["id"]
    r = run("pdf_invoice_to_e_invoice", [plain], **INVOICE)
    assert facturx.get_xml_from_pdf(out_bytes(r))[0] == "factur-x.xml"
    bad = file(xml.replace(b"1482.57", b"1500.00"), "tampered.xml")
    v = run("validate_e_invoice", [bad])
    assert not v["result"]["all_valid"], v["result"]


def test_every_tool_is_tested_or_unavailable():
    import inspect
    src = inspect.getsource(sys.modules[__name__])
    untested = [n for n, t in TOOLS.items() if f'"{n}"' not in src and not t.availability()]
    assert not untested, untested


if __name__ == "__main__":
    only = sys.argv[1:]
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn) and (not only or any(o in name for o in only)):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.1f}s)")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
