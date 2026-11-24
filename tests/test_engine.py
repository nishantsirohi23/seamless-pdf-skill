"""Engine tests (no PyMuPDF). Run with `python tests/test_engine.py` from backend/.

Inputs are the same fixture PDFs the original engine was tested on; every
edit is checked on the text model *and* on rendered pixels (PDFium): an edit
must change nothing outside the edited object.
"""
from __future__ import annotations

import io
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np  # noqa: E402
import pikepdf  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402

from seamless_pdf.engine import EditError, PdfDoc  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name: str) -> PdfDoc:
    with open(os.path.join(FIX, name), "rb") as fh:
        return PdfDoc(fh.read(), name)


def make(lines, pages=1, size=(595, 842)) -> PdfDoc:
    """Build a PDF with ReportLab. lines: (page, x, y_top_down, text, size, font, rgb)."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=size)
    for p in range(pages):
        for ln in lines:
            pg, x, y, text, sz = ln[:5]
            font = ln[5] if len(ln) > 5 else "Helvetica"
            rgb = ln[6] if len(ln) > 6 else (0, 0, 0)
            if pg == p:
                c.setFillColorRGB(*rgb)
                c.setFont(font, sz)
                c.drawString(x, size[1] - y, text)
        c.showPage()
    c.save()
    return PdfDoc(buf.getvalue(), "made.pdf")


def render(pdf: PdfDoc, page=0, scale=2.0) -> np.ndarray:
    doc = pdfium.PdfDocument(pdf.pdf_bytes()[1])
    return np.asarray(doc[page].render(scale=scale).to_pil().convert("L"), dtype=np.int16)


def changed_box(a, b, scale=2.0, tol=40):
    mask = np.abs(a - b) > tol
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    from seamless_pdf.engine.geom import Rect
    return Rect(xs.min() / scale, ys.min() / scale, (xs.max() + 1) / scale, (ys.max() + 1) / scale)


def texts(pdf, page=0):
    return [o["text"] for o in pdf.text_objects(page)]


def texts_by_y(pdf, page=0):
    return [o["text"] for o in sorted(pdf.text_objects(page), key=lambda o: (o["origin"][1], o["origin"][0]))]


def seg(pdf, text, page=0):
    for o in pdf.text_objects(page):
        if o["text"] == text:
            return o
    raise AssertionError(f"{text!r} not in {texts(pdf, page)}")


def glyph_xs(pdf, text, page=0):
    for s in pdf._segments(page):
        if s.text == text:
            return [round(g.origin[0], 2) for g in s.glyphs]
    raise AssertionError(text)


def run(pdf, method, *args, **kw):
    with pdf.transaction(method):
        return getattr(pdf, method)(*args, **kw)


# -- text --------------------------------------------------------------------------------
def test_tight_leading_neighbours_untouched():
    pdf = fixture("tight_leading.pdf")
    before = render(pdf)
    run(pdf, "edit_text", seg(pdf, "Second line yjpq")["id"], "Second line EDITED")
    assert texts_by_y(pdf) == ["Alpha bravo gypsy jQuery", "Second line EDITED", "Third line Hgjy"], texts(pdf)
    box = changed_box(before, render(pdf))
    assert box.y0 >= 111 - 11 * 1.1 and box.y1 <= 111 + 11 * 0.35, box
    assert box.x0 >= 72 + 50, box


def test_table_cells_edit_independently():
    pdf = fixture("table_row.pdf")
    assert texts(pdf) == ["Widget", "12", "$4.00"], texts(pdf)
    before = render(pdf)
    run(pdf, "edit_text", seg(pdf, "12")["id"], "15")
    assert texts_by_y(pdf) == ["Widget", "15", "$4.00"]
    box = changed_box(before, render(pdf))
    w = seg(pdf, "15")
    assert box.x0 >= w["bbox"][0] - 1 and box.x1 <= w["bbox"][2] + 1, (box, w["bbox"])


def test_right_aligned_column_keeps_right_edge():
    pdf = fixture("right_aligned.pdf")
    right = seg(pdf, "$4.00")["bbox"][2]
    res = run(pdf, "edit_text", seg(pdf, "$4.00")["id"], "$1,234.00")
    assert res["anchor"] == "right", res
    assert abs(seg(pdf, "$1,234.00")["bbox"][2] - right) < 0.3


def test_touching_runs_on_one_baseline():
    pdf = fixture("touching_runs.pdf")
    before = render(pdf)
    run(pdf, "edit_text", seg(pdf, "$100.00")["id"], "$250.00")
    assert texts_by_y(pdf) == ["Total:", "$250.00"], texts(pdf)
    box = changed_box(before, render(pdf))
    assert box.x0 >= seg(pdf, "Total:")["bbox"][2] - 0.5, box


def test_simple_truetype_subset_reused_raw():
    pdf = fixture("reportlab_arial.pdf")
    before = render(pdf)
    old = seg(pdf, "Amount due: $4,120.50 by March 3")
    xs_old = glyph_xs(pdf, old["text"])
    res = run(pdf, "edit_text", old["id"], "Amount due: $4,120.50 by March 9")
    assert res["font_matched"] and res["font_used"] == "ArialMT", res
    xs_new = glyph_xs(pdf, "Amount due: $4,120.50 by March 9")
    assert max(abs(a - b) for a, b in zip(xs_old, xs_new)) < 0.01, (xs_old, xs_new)
    box = changed_box(before, render(pdf))
    assert box.x0 > xs_old[-1] - 1, box
    fonts = {str(v.get("/BaseFont")) for v in pdf.pdf.pages[0].Resources.Font.values()}
    assert not any("Vera" in f for f in fonts), fonts           # no fallback font needed


def test_fallback_characters_stay_one_object_and_reedit():
    """A subset lacks '7' and '9': those characters come from a stand-in
    font, but the edited line is still one editable object, and editing it
    again reuses the document's own font for everything it can draw."""
    pdf = fixture("reportlab_arial.pdf")
    old = seg(pdf, "Amount due: $4,120.50 by March 3")
    run(pdf, "edit_text", old["id"], "Amount due: $9,875.00 by April 30")
    line = [t for t in texts(pdf) if t.startswith("Amount")]
    assert line == ["Amount due: $9,875.00 by April 30"], texts(pdf)
    o = seg(pdf, line[0])
    assert o["font"].startswith("ArialMT") or "Arial" in str(o.get("font")), o
    res = run(pdf, "edit_text", o["id"], "Amount due: $4,120.50 by March 3")
    assert res["font_used"] == "ArialMT", res
    assert [t for t in texts(pdf) if t.startswith("Amount")] == ["Amount due: $4,120.50 by March 3"]
    # a genuine font switch inside one text object (bold label, regular value) still splits
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    to = c.beginText(72, 742)
    to.setFont("Helvetica-Bold", 12)
    to.textOut("Total:")
    to.setFont("Helvetica", 12)
    to.textOut("$100")
    c.drawText(to)
    c.save()
    assert texts(PdfDoc(buf.getvalue())) == ["Total:", "$100"], texts(PdfDoc(buf.getvalue()))


def _watermark_pdf(in_form: bool) -> PdfDoc:
    """A grey diagonal watermark drawn first, then an opaque box and dark text
    over it (like a certificate's table). Optionally the watermark lives in a
    form XObject placed with its own transform."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))

    def watermark():
        c.saveState()
        c.translate(297.5, 421)
        c.rotate(45)
        c.setFillColorRGB(0.8, 0.8, 0.8)
        c.setFont("Helvetica-Bold", 52)
        c.drawCentredString(0, 0, "Goods and Services Tax")
        c.restoreState()
    if in_form:
        c.beginForm("wm")
        watermark()
        c.endForm()
        c.saveState()
        c.translate(10, -20)
        c.doForm("wm")
        c.restoreState()
    else:
        watermark()
    c.setFillColorRGB(1, 1, 1)
    c.rect(150, 380, 300, 80, stroke=1, fill=1)        # opaque table cell over the watermark
    c.setFillColorRGB(0, 0, 0.6)
    c.setFont("Helvetica", 11)
    c.drawString(160, 420, "ACME TRADING LLP")
    c.save()
    return PdfDoc(buf.getvalue(), "wm.pdf")


def test_edited_text_keeps_its_place_in_the_stacking_order():
    """Editing or moving text that sits behind other content (a watermark
    under a table) must not bring it to the front."""
    from seamless_pdf.engine.geom import Rect
    for in_form in (False, True):
        pdf = _watermark_pdf(in_form)
        cell = Rect(151, 842 - 459, 449, 842 - 381)      # inside the opaque cell, page space
        sl = (slice(int(cell.y0 * 2), int(cell.y1 * 2)), slice(int(cell.x0 * 2), int(cell.x1 * 2)))
        before = render(pdf)
        wm = seg(pdf, "Goods and Services Tax")
        run(pdf, "edit_text", wm["id"], "Goods and Services Tax!")
        assert "Goods and Services Tax!" in texts(pdf), texts(pdf)
        after = render(pdf)
        assert (np.abs(after[sl] - before[sl]) > 40).sum() == 0, ("watermark painted over the cell", in_form)
        assert changed_box(before, after) is not None              # the edit itself is visible
        run(pdf, "move_text", seg(pdf, "Goods and Services Tax!")["id"], 0, 4)
        moved = render(pdf)
        assert (np.abs(moved[sl] - before[sl]) > 40).sum() == 0, ("moved watermark over the cell", in_form)
        # the cell text above it is still editable and still on top
        run(pdf, "edit_text", seg(pdf, "ACME TRADING LLP")["id"], "ACME TRADERS LLP")
        assert "ACME TRADERS LLP" in texts(pdf)


def test_horizontal_scale_and_char_spacing_recovered():
    pdf = fixture("reportlab_arial.pdf")
    xs_old = glyph_xs(pdf, "Condensed spaced line")
    run(pdf, "edit_text", seg(pdf, "Condensed spaced line")["id"], "Condensed spaced lino")
    xs_new = glyph_xs(pdf, "Condensed spaced lino")
    assert max(abs(a - b) for a, b in zip(xs_old, xs_new)) < 0.05, (xs_old, xs_new)


def test_glyph_missing_from_subset_falls_back_per_char():
    pdf = fixture("reportlab_arial.pdf")
    s = seg(pdf, "Customer: Jordan Lee, account 99812")
    res = run(pdf, "edit_text", s["id"], "Customer: Jordan Lee, account 99812 §Q")
    assert not res["font_matched"] and "ArialMT" in res["font_used"], res
    assert "Customer: Jordan Lee, account 99812" in pdf.document_text()
    assert "§" in pdf.document_text() and "Q" in pdf.document_text()


def test_unicode_outside_winansi_uses_embedded_fallback():
    """Rupee sign and arrows are outside WinAnsi and Bitstream Vera; the
    DejaVu Sans fallback draws them instead of substituting '?'."""
    pdf = make([(0, 72, 100, "Price", 12)])
    new = "Price \u20b9 1,200 \u2192 \u2191 \u03a9 \u2264 \u221a"
    res = run(pdf, "edit_text", seg(pdf, "Price")["id"], new)
    t = pdf.document_text()
    assert new in t and "?" not in t, t
    assert res["font_used"] == "Helvetica + DejaVuSans", res
    from fontTools.ttLib import TTFont
    fb = [f for f in pdf.pdf.pages[0].Resources.Font.values() if str(f.get("/BaseFont")).endswith("DejaVuSans")]
    assert len(fb) == 1 and fb[0].get("/SPEFallback"), fb
    ff = TTFont(io.BytesIO(fb[0].DescendantFonts[0].FontDescriptor.FontFile2.read_bytes()))
    for c in "\u20b9\u2192\u2191":                                # real outlines, not .notdef
        assert ff["glyf"][ff.getBestCmap()[ord(c)]].numberOfContours > 0, c
    bold = make([(0, 72, 100, "Total", 12, "Helvetica-Bold")])          # style variant kept
    res = run(bold, "edit_text", seg(bold, "Total")["id"], "Total \u20b9 5")
    assert "Total \u20b9 5" in bold.document_text() and res["font_used"] == "Helvetica-Bold + DejaVuSans-Bold", res


def test_embedded_type0_subset_font_reused():
    pdf = fixture("georgia_subset.pdf")
    s = seg(pdf, "Invoice total due")
    assert "Georgia" in s["font"], s
    res = run(pdf, "edit_text", s["id"], "Invoice to do")
    assert res["font_matched"], res
    s2 = seg(pdf, "Invoice to do")
    assert abs(s2["size"] - 14) < 0.01
    res = run(pdf, "edit_text", s2["id"], "Invoice total Zxx")
    assert not res["font_matched"]
    assert any("Georgia" in o["font"] for o in pdf.text_objects(0))


def test_rotated_text_stays_rotated():
    pdf = fixture("rotated_text.pdf")
    s = seg(pdf, "Rotated label")
    assert s["rotated"]
    run(pdf, "edit_text", s["id"], "Rotated title")
    s2 = seg(pdf, "Rotated title")
    assert s2["rotated"] and abs(s2["origin"][0] - s["origin"][0]) < 0.2 \
        and abs(s2["origin"][1] - s["origin"][1]) < 0.2, (s, s2)


def test_rotated_page_edit_in_place():
    pdf = fixture("rotated_page.pdf")
    s = seg(pdf, "On a rotated page")
    run(pdf, "edit_text", s["id"], "On a turned page")
    s2 = seg(pdf, "On a turned page")
    assert abs(s2["origin"][0] - s["origin"][0]) < 0.2 and abs(s2["origin"][1] - s["origin"][1]) < 0.2
    assert pdf.pages()[0]["rotation"] == 90 and pdf.pages()[0]["width"] == 842


def test_move_text_and_delete_text():
    pdf = fixture("move_delete.pdf")
    m = seg(pdf, "Move me")
    res = run(pdf, "move_objects", [m["id"]], 50, 20)
    new = seg(pdf, "Move me")
    assert res["new_ids"][m["id"]] == new["id"]
    assert abs(new["origin"][0] - 122) < 0.1 and abs(new["origin"][1] - 120) < 0.1
    run(pdf, "delete_objects", [seg(pdf, "Delete me")["id"]])
    assert texts(pdf) == ["Move me"]


def test_restyle_size_and_color():
    pdf = fixture("style.pdf")
    run(pdf, "edit_text", seg(pdf, "Style me")["id"], None, font_size=18, color="#cc0000")
    s = seg(pdf, "Style me")
    assert abs(s["size"] - 18) < 0.01 and s["color"] == "#cc0000", s


def test_replace_all_and_find():
    pdf = fixture("replace_all.pdf")
    assert pdf.find_text("2023")["match_count"] == 4
    res = run(pdf, "replace_all", "2023", "2024")
    assert res["replaced_segments"] == 4
    assert texts_by_y(pdf, 1) == ["Fiscal year 2024 summary", "Totals for 2024"]


def _pdfium_text(pdf: PdfDoc, page: int) -> str:
    return pdfium.PdfDocument(pdf.pdf_bytes()[1])[page].get_textpage().get_text_range()


def test_shared_form_xobject_edits_only_its_page():
    # a footer form XObject drawn by three pages that also share one /Resources dict
    src = pikepdf.new()
    helv = src.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica,
                                                Encoding=pikepdf.Name.WinAnsiEncoding))
    footer = src.make_stream(b"BT /F1 10 Tf 72 40 Td (Acme Ltd confidential) Tj ET")
    footer.Type, footer.Subtype, footer.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 595, 100]
    footer.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helv))
    res = src.make_indirect(pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helv),
                                               XObject=pikepdf.Dictionary(Footer=footer)))
    for i in range(3):
        src.pages.append(pikepdf.Page(pikepdf.Dictionary(
            Type=pikepdf.Name.Page, MediaBox=[0, 0, 595, 842], Resources=res,
            Contents=src.make_stream(f"/Footer Do BT /F1 14 Tf 72 760 Td (Body {i + 1}) Tj ET".encode()))))
    buf = io.BytesIO()
    src.save(buf)
    pdf = PdfDoc(buf.getvalue(), "shared.pdf")
    run(pdf, "edit_text", seg(pdf, "Acme Ltd confidential", 0)["id"], "Globex Ltd confidential")
    assert "Globex Ltd confidential" in _pdfium_text(pdf, 0)
    for p in (1, 2):
        t = _pdfium_text(pdf, p)
        assert "Acme Ltd confidential" in t and "Globex" not in t, (p, t)
    res = run(pdf, "replace_all", "Ltd", "Limited")
    assert res["replaced_segments"] == 3, res
    assert "Globex Limited confidential" in _pdfium_text(pdf, 0)
    for p in (1, 2):
        t = _pdfium_text(pdf, p)
        assert "Acme Limited confidential" in t and "Limited Limited" not in t, (p, t)
        assert f"Body {p + 1}" in t


def _raw_page(content: bytes, resources=None, rotate=0, annots=None) -> PdfDoc:
    """One-page PDF written by hand (Helvetica as /F1), for cases ReportLab does not produce."""
    src = pikepdf.new()
    helv = src.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica,
                                                Encoding=pikepdf.Name.WinAnsiEncoding))
    res = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helv))
    if resources:
        resources(src, res)
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792],
                              Contents=src.make_stream(content), Resources=res)
    if rotate:
        page.Rotate = rotate
    if annots:
        page.Annots = pikepdf.Array(annots)
    src.pages.append(pikepdf.Page(page))
    buf = io.BytesIO()
    src.save(buf)
    return PdfDoc(buf.getvalue(), "raw.pdf")


def _ops(pdf: PdfDoc, page=0) -> list[str]:
    return [str(i.operator) for i in pikepdf.parse_content_stream(pdf.pdf.pages[page])
            if not isinstance(i, pikepdf.ContentStreamInlineImage)]


def test_spot_and_cmyk_colours_survive_an_edit():
    def spot(src, res):
        fn = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 1, 0], N=1)
        res.ColorSpace = pikepdf.Dictionary(CS0=pikepdf.Array(
            [pikepdf.Name.Separation, pikepdf.Name("/Red"), pikepdf.Name.DeviceCMYK, fn]))
    pdf = _raw_page(b"BT /F1 24 Tf /CS0 cs 1 scn 72 700 Td (Spot 100) Tj ET "
                    b"BT /F1 24 Tf 0.1 0.2 0.3 0.4 k 72 600 Td (Cmyk 100) Tj ET", spot)
    assert seg(pdf, "Spot 100")["color"] != "#000000"       # an approximation, not black
    run(pdf, "edit_text", seg(pdf, "Spot 100")["id"], "Spot 200")
    run(pdf, "edit_text", seg(pdf, "Cmyk 100")["id"], "Cmyk 200")
    ops = _ops(pdf)
    assert ops.count("scn") == 2 and ops.count("k") == 2, ops   # redrawn in the original colour spaces
    img = pdfium.PdfDocument(pdf.pdf_bytes()[1])[0].render(scale=2).to_pil().convert("RGB")
    ink = [img.getpixel((x, y)) for x in range(144, 420) for y in range(140, 200)]
    ink = [p for p in ink if sum(p) < 600]
    assert any(r > 200 and g < 60 for r, g, b in ink), sorted(set(ink))[:5]            # solid red
    assert not any(max(p) - min(p) < 40 for p in ink), [p for p in ink if max(p) - min(p) < 40][:5]  # no grey/black


def test_appended_content_survives_an_unclosed_q():
    # the page scales by 0.5 and opens a q it never closes
    pdf = _raw_page(b"0.5 0 0 0.5 0 0 cm q BT /F1 24 Tf 144 1400 Td (Hello) Tj ET")
    run(pdf, "insert_text", 0, 72, 200, "Inserted")
    tp = pdfium.PdfDocument(pdf.pdf_bytes()[1])[0].get_textpage()
    first = next(i for i in range(tp.count_chars()) if tp.get_text_range(i, 1) == "I")
    left, bottom, _, _ = tp.get_charbox(first)
    assert abs(left - 72) < 2 and abs(bottom - (792 - 200)) < 3, (left, bottom)


def test_vertical_text_erase_and_edit():
    from seamless_pdf.engine.writer import FONT_DIR, FontBook
    src = pikepdf.new()
    font, info = FontBook(src).embedded(os.path.join(FONT_DIR, "DejaVuSans.ttf"), "ABEFXYQ")
    font.Encoding = pikepdf.Name("/Identity-V")
    src.pages.append(pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792], Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F0=font)),
        Contents=src.make_stream(f"BT /F0 20 Tf 300 700 Td <{info.encode('ABXYEF').hex()}> Tj ET".encode()))))
    buf = io.BytesIO()
    src.save(buf)
    pdf = PdfDoc(buf.getvalue(), "vertical.pdf")

    def boxes():
        tp = pdfium.PdfDocument(pdf.pdf_bytes()[1])[0].get_textpage()
        return {tp.get_text_range(i, 1): tuple(round(v) for v in tp.get_charbox(i)) for i in range(tp.count_chars())}
    before = boxes()
    s = pdf.text_objects(0)[0]
    x0, y0, x1, y1 = s["bbox"]
    assert s["text"] == "ABXYEF" and y1 - y0 > 4 * (x1 - x0), s      # a column, not a line
    run(pdf, "_erase", 0, [g for g in pdf._segments(0)[0].glyphs if g.char in "XY"])
    after = boxes()
    assert "X" not in after and all(after[c] == before[c] for c in "ABEF"), (before, after)
    ef = seg(pdf, "EF")
    try:
        run(pdf, "edit_text", ef["id"], "EQ")      # Q is in the font file but this subset never drew it
        raise AssertionError("a stand-in glyph was allowed into vertical text")
    except EditError:
        pass
    run(pdf, "edit_text", ef["id"], "FE")
    final = boxes()
    assert final["F"] == before["E"] and final["E"] == before["F"], (before, final)


def test_edit_returns_the_real_id_when_text_starts_with_a_space():
    pdf = make([(0, 72, 100, "Dr. John Smith", 12)])
    res = run(pdf, "replace_all", "Dr.", "")
    assert res["ids"] == [o["id"] for o in pdf.text_objects(0)], (res, pdf.text_objects(0))


def test_insert_text_reads_upright_on_a_rotated_page():
    import pypdfium2.raw as pdfium_c
    pdf = _raw_page(b"BT /F1 14 Tf 0 1 -1 0 300 72 Tm (Upright existing line) Tj ET", rotate=90)
    res = run(pdf, "insert_text", 0, 100, 100, "Inserted")
    assert res["size"] == 14 and res["font_matched"], res               # took the nearby style
    tp = pdfium.PdfDocument(pdf.pdf_bytes()[1])[0].get_textpage()
    angle = {tp.get_text_range(i, 1): round(pdfium_c.FPDFText_GetCharAngle(tp.raw, i), 3)
             for i in range(tp.count_chars())}
    assert angle["I"] == angle["U"], angle


def test_deleting_one_direct_annotation_keeps_the_others():
    def square(x, text):
        return pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Square, Rect=[x, 600, x + 50, 650],
                                  Contents=pikepdf.String(text))
    pdf = _raw_page(b"", annots=[square(72, "one"), square(200, "two"), square(330, "three")])  # direct objects
    run(pdf, "delete_objects", [pdf.annot_objects(0)[1]["id"]])
    saved = pikepdf.open(io.BytesIO(pdf.to_bytes()))
    assert [str(a.Contents) for a in saved.pages[0].obj.Annots] == ["one", "three"]


def test_owner_password_encryption_survives_edit_and_undo():
    plain = pikepdf.open(io.BytesIO(make([(0, 72, 100, "Amount 100", 12)]).to_bytes()))
    buf = io.BytesIO()
    plain.save(buf, encryption=pikepdf.Encryption(owner="owner-only", user="", R=6,
                                                  allow=pikepdf.Permissions(modify_other=False)))
    pdf = PdfDoc(buf.getvalue(), "locked.pdf")
    run(pdf, "edit_text", seg(pdf, "Amount 100")["id"], "Amount 200")
    out = pikepdf.open(io.BytesIO(pdf.to_bytes()))
    assert out.is_encrypted and not out.allow.modify_other and "Amount 200" in _pdfium_text(pdf, 0)
    pdf.undo()
    again = pikepdf.open(io.BytesIO(pdf.to_bytes()))
    assert again.is_encrypted and "Amount 100" in _pdfium_text(pdf, 0)


def test_lines_run_top_to_bottom_whatever_the_size():
    pdf = make([(0, 72, 100, "Heading", 20), (0, 72, 255.7, "GST 18%", 9),
                (0, 72, 279.7, "Total due", 10.5), (0, 72, 400, "Big footer", 30)])
    assert pdf.page_text(0).splitlines() == ["Heading", "GST 18%", "Total due", "Big footer"], pdf.page_text(0)


def test_insert_text_matches_nearby_style():
    pdf = fixture("insert_style.pdf")
    res = run(pdf, "insert_text", 0, 72, 130, "Added line\nSecond added")
    assert res["lines"] == 2
    a = seg(pdf, "Added line")
    assert abs(a["size"] - 13) < 0.01 and a["color"] == "#333399", a
    res = run(pdf, "insert_text", 0, 72, 200, "wrap " * 30, font_size=10, font="times", width=200,
              color="#000000")
    assert res["lines"] > 1


# -- images -------------------------------------------------------------------------------
def _raw_images(pdf):
    return [bytes(pl.obj.read_raw_bytes()) for pl in pdf.model(0).images]


def test_image_move_resize_delete():
    pdf = fixture("image.pdf")
    img = pdf.image_objects(0)[0]
    raw = _raw_images(pdf)
    run(pdf, "move_objects", [img["id"]], -100, 50)
    assert pdf.image_objects(0)[0]["bbox"] == [200, 350, 300, 400], pdf.image_objects(0)
    run(pdf, "set_object_rect", img["id"], [50, 60, 250, 160])
    assert pdf.image_objects(0)[0]["bbox"] == [50, 60, 250, 160]
    assert _raw_images(pdf) == raw
    assert texts(pdf) == ["Caption"]
    run(pdf, "delete_objects", [img["id"]])
    assert pdf.image_objects(0) == []


def test_image_move_on_rotated_page():
    pdf = fixture("image_rotated_page.pdf")
    img = pdf.image_objects(0)[0]
    b = img["bbox"]
    run(pdf, "move_objects", [img["id"]], 10, 20)
    assert pdf.image_objects(0)[0]["bbox"] == [b[0] + 10, b[1] + 20, b[2] + 10, b[3] + 20]


def _png(rgb=(20, 120, 220), size=(30, 30), alpha=False) -> bytes:
    from PIL import Image
    im = Image.new("RGBA" if alpha else "RGB", size, rgb + ((128,) if alpha else ()))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _three_images() -> PdfDoc:
    pdf = make([(0, 72, 60, "Images", 12)])
    for x, rgb in ((50, (255, 0, 0)), (200, (0, 255, 0)), (350, (0, 0, 255))):
        a = pdf.add_asset(_png(rgb, (20, 20)), "c.png")
        run(pdf, "insert_image", 0, a.id, [x, 100, x + 100, 200])
    return pdf


def _pixel(pdf: PdfDoc, x: float, y: float) -> tuple:
    img = pdfium.PdfDocument(pdf.pdf_bytes()[1])[0].render(scale=1.0).to_pil().convert("RGB")
    return img.getpixel((int(x), int(y)))


def test_delete_several_images_at_once():
    pdf = _three_images()
    assert [o["id"] for o in pdf.image_objects(0)] == ["i:0:0", "i:0:1", "i:0:2"]
    from seamless_pdf.ops import run_op
    res = run_op(pdf, "delete_objects", {"ids": ["i:0:0", "i:0:1"]})
    assert len(pdf.image_objects(0)) == 1
    assert _pixel(pdf, 100, 150) == (255, 255, 255) and _pixel(pdf, 250, 150) == (255, 255, 255)
    assert _pixel(pdf, 400, 150) == (0, 0, 255)
    assert res["new_ids"] == {"i:0:2": "i:0:0", "i:0:0": None, "i:0:1": None}, res["new_ids"]


def test_script_follows_renamed_ids():
    import tempfile
    from seamless_pdf import Workspace
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "imgs.pdf")
    with open(path, "wb") as fh:
        fh.write(_three_images().to_bytes())
    ws = Workspace()
    fid = ws.add(path)
    # ids in a script name objects as they were before the first step
    ws.script(fid, [{"op": "delete_objects", "args": {"ids": ["i:0:0"]}},
                    {"op": "delete_objects", "args": {"ids": ["i:0:1"]}}])
    pdf = ws.doc(fid)
    assert len(pdf.image_objects(0)) == 1 and _pixel(pdf, 400, 150) == (0, 0, 255)
    try:
        ws.script(fid, [{"op": "delete_objects", "args": {"ids": ["i:0:0"]}},
                        {"op": "delete_objects", "args": {"ids": ["i:0:0"]}}])
        raise AssertionError("a deleted id was reused")
    except EditError as e:
        assert "deleted by an earlier step" in str(e), e
    # an id that comes back to its first value (move away and back) must not loop
    sid = ws.op(fid, "find_text", {"query": "Images"})["result"]["matches"][0]["id"]
    ws.script(fid, [{"op": "move_objects", "args": {"ids": [sid], "dx": 10, "dy": 0}},
                    {"op": "move_objects", "args": {"ids": [sid], "dx": -10, "dy": 0}},
                    {"op": "edit_text", "args": {"id": sid, "text": "Pictures"}}])
    assert texts(ws.doc(fid)) == ["Pictures"], texts(ws.doc(fid))


def test_insert_and_replace_image_asset():
    pdf = fixture("image.pdf")
    asset = pdf.add_asset(_png(), "blue.png")
    res = run(pdf, "insert_image", 0, asset.id, [100, 500, 160, 560])
    assert res["id"] and len(pdf.image_objects(0)) == 2
    arr = render(pdf, scale=1)
    assert abs(int(arr[530, 130]) - int(np.mean([20, 120, 220]) * 1)) < 60   # blue square drawn
    a2 = pdf.add_asset(_png((250, 0, 0), alpha=True), "red.png")
    run(pdf, "replace_image", pdf.image_objects(0)[0]["id"], a2.id)
    assert render(pdf, scale=1).shape[0] > 0


# -- annotations / forms ------------------------------------------------------------------------
def test_annotations():
    pdf = make([(0, 72, 100, "Highlight this phrase please", 12)])
    h = run(pdf, "add_annotation", 0, "highlight", target_text="this phrase")
    n = run(pdf, "add_annotation", 0, "note", point=[300, 300], text="Check this")
    r = run(pdf, "add_annotation", 0, "rect", rect=[100, 400, 200, 450], color="#0000ff")
    ft = run(pdf, "add_annotation", 0, "freetext", rect=[100, 500, 300, 530], text="Approved")
    kinds = sorted(a["subtype"] for a in pdf.annot_objects(0))
    assert kinds == ["FreeText", "Highlight", "Square", "Text"], kinds
    hb = [a for a in pdf.annot_objects(0) if a["id"] == h["id"]][0]["bbox"]
    assert 100 < hb[0] < 150 and 85 < hb[1] < 92, hb
    rb = [a for a in pdf.annot_objects(0) if a["id"] == r["id"]][0]["bbox"]
    run(pdf, "move_objects", [r["id"]], 10, 10)
    rb2 = [a for a in pdf.annot_objects(0) if a["id"] == r["id"]][0]["bbox"]
    assert [round(v - w, 1) for v, w in zip(rb2, rb)] == [10, 10, 10, 10], (rb, rb2)
    try:
        run(pdf, "move_objects", [h["id"]], 10, 10)
        raise AssertionError("moving a highlight should fail")
    except EditError:
        pass
    run(pdf, "update_annotation", n["id"], text="Updated note", color="#00aa00")
    assert [a for a in pdf.annot_objects(0) if a["id"] == n["id"]][0]["text"] == "Updated note"
    before = render(pdf)
    run(pdf, "delete_objects", [ft["id"], h["id"]])
    assert len(pdf.annot_objects(0)) == 2
    assert changed_box(before, render(pdf)) is not None      # appearances really rendered
    assert texts(pdf) == ["Highlight this phrase please"]


def test_forms():
    pdf = make([(0, 72, 60, "Form", 14)])
    t = run(pdf, "add_form_field", 0, [72, 100, 272, 120], "text", "full_name")
    c = run(pdf, "add_form_field", 0, [72, 140, 86, 154], "checkbox", "agree")
    k = run(pdf, "add_form_field", 0, [72, 170, 200, 190], "combobox", "plan", options=["Basic", "Pro"])
    run(pdf, "set_field_value", t["id"], "Priya Natarajan")
    run(pdf, "set_field_value", c["id"], True)
    run(pdf, "set_field_value", k["id"], "Pro")
    vals = {f["name"]: f["value"] for f in pdf.list_form_fields()["fields"]}
    assert vals == {"full_name": "Priya Natarajan", "agree": True, "plan": "Pro"}, vals
    try:
        run(pdf, "set_field_value", k["id"], "Enterprise")
        raise AssertionError("invalid option accepted")
    except EditError:
        pass
    run(pdf, "move_objects", [t["id"]], 0, 100)
    assert [f for f in pdf.field_objects(0) if f["name"] == "full_name"][0]["bbox"][1] == 200
    run(pdf, "flatten_forms")
    assert pdf.list_form_fields()["count"] == 0
    assert "Priya Natarajan" in pdf.document_text(), pdf.document_text()


# -- history ------------------------------------------------------------------------------------------
def test_undo_redo_and_groups():
    pdf = fixture("style.pdf")
    with pdf.transaction("edit", group="g1"):
        pdf.edit_text(seg(pdf, "Style me")["id"], "Version two")
    with pdf.transaction("edit", group="g1"):
        pdf.edit_text(seg(pdf, "Version two")["id"], "Version three")
    run(pdf, "insert_text", 0, 72, 200, "Separate")
    pdf.undo()
    assert texts(pdf) == ["Version three"]
    pdf.undo()
    assert texts(pdf) == ["Style me"]
    pdf.redo()
    assert texts(pdf) == ["Version three"]
    pdf.redo()
    assert texts_by_y(pdf) == ["Version three", "Separate"]
    try:
        pdf.redo()
        raise AssertionError("redo past the end")
    except EditError:
        pass


def test_failed_transaction_rolls_back():
    pdf = fixture("style.pdf")
    v = pdf.version
    sid = seg(pdf, "Style me")["id"]
    try:
        with pdf.transaction("multi"):
            pdf.edit_text(sid, "Changed")
            pdf.edit_text("t:0:1.0:1.0", "boom")
        raise AssertionError("expected failure")
    except EditError:
        pass
    assert texts(pdf) == ["Style me"] and pdf.version == v and not pdf.history_state()["can_undo"]


# -- scans ----------------------------------------------------------------------------------------------
def test_scanned_page_ocr_edit_and_move():
    pdf = fixture("scan.pdf")
    assert pdf.is_scanned(0) and not pdf.has_ocr_layer(0)
    from seamless_pdf.engine.ocr import _tesseract
    if not _tesseract():
        print("  (skipped: no tesseract)")
        return
    res = run(pdf, "ocr_page", 0)
    words = texts(pdf)
    assert "4471" in words and res["words"] == 9, words
    before = render(pdf)
    target = seg(pdf, "4471")
    res = run(pdf, "edit_text", target["id"], "4472")
    assert res["scanned"]
    assert not seg(pdf, "4472")["ocr"]
    box = changed_box(before, render(pdf))
    tb = target["bbox"]
    assert box.x0 >= tb[0] - 3 and box.x1 <= tb[2] + 3 and box.y0 >= tb[1] - 4 and box.y1 <= tb[3] + 4, (box, tb)
    due = seg(pdf, "due")
    run(pdf, "move_objects", [due["id"]], 0, 100)
    moved = seg(pdf, "due")
    assert moved["ocr"] and abs(moved["origin"][1] - due["origin"][1] - 100) < 0.2
    assert len(pdf.image_objects(0)) == 2


def test_page_objects_summary_render_bytes():
    pdf = fixture("image.pdf")
    po = pdf.page_objects(0)
    assert {o["kind"] for o in po["objects"]} == {"text", "image"} and not po["scanned"]
    s = pdf.summary()
    assert s["page_count"] == 1 and s["version"] == 0
    v, data = pdf.pdf_bytes()
    assert data.startswith(b"%PDF") and v == 0
    assert pdf.render_page(0, 1.0).startswith(b"\x89PNG")
    assert pdf.to_bytes().startswith(b"%PDF")
    assert pikepdf.open(io.BytesIO(pdf.to_bytes())).pages


def test_no_pymupdf_anywhere():
    import importlib.util
    assert importlib.util.find_spec("fitz") is None and importlib.util.find_spec("pymupdf") is None
    root = os.path.join(os.path.dirname(__file__), "..", "src", "seamless_pdf")
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(".py"):
                src = open(os.path.join(dp, f)).read()
                assert "import fitz" not in src and "import pymupdf" not in src, f


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.2f}s)")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
