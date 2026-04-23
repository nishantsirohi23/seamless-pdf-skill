"""Organize: merge, assemble, split, rearrange, remove, extract, rotate,
insert, pages per sheet, halve, bookmarks, extract images, copy text."""
from __future__ import annotations

import io
import json
import re
from collections import Counter

import pikepdf

from ..engine import EditError
from .base import PAGES_OPT, PAGES_REQ, PAPER_ENUM, PDFS, paper, parse_order, parse_pages, tool
from .render import page_form, page_size, place_form

CAT = "Organize"
NAME_OPT = {"type": "string", "description": "Name of the output file (optional)."}


def _outline(pdf: pikepdf.Pdf) -> list[tuple[int, str, int]]:
    """[(level, title, 1-based page)]"""
    out = []
    try:
        with pdf.open_outline() as ol:
            def walk(items, level):
                for it in items:
                    p = None
                    try:
                        dest = it.destination
                        if isinstance(dest, int):
                            p = dest + 1
                        elif dest is not None and len(dest):
                            p = pikepdf.Page(dest[0]).index + 1
                    except Exception:
                        p = None
                    if p:
                        out.append((level, str(it.title), p))
                    walk(it.children, level + 1)
            walk(ol.root, 1)
    except Exception:
        pass
    return out


def _set_outline(pdf: pikepdf.Pdf, toc: list) -> None:
    with pdf.open_outline() as ol:
        ol.root.clear()
        stack = [(0, ol.root)]
        for level, title, page in toc:
            item = pikepdf.OutlineItem(str(title), max(0, int(page) - 1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else ol.root
            (parent if isinstance(parent, list) else parent.children).append(item)
            stack.append((level, item))


@tool("merge_pdfs", "Merge PDF", CAT, "Combine several PDFs into one, in the given order.",
      inputs=PDFS, aliases=["merge", "combine", "join_pdfs"],
      options={"output_name": NAME_OPT,
               "bookmarks": {"type": "boolean", "default": True,
                             "description": "Add a bookmark for each source file."}})
def merge_pdfs(ctx, output_name=None, bookmarks=True):
    if len(ctx.inputs) < 2:
        raise EditError("Merge needs at least two PDFs")
    out = pikepdf.new()
    toc = []
    for i in range(len(ctx.inputs)):
        src = ctx.pdf(i)
        start = len(out.pages)
        out.add_pages_from(src)
        if bookmarks:
            toc.append((1, ctx.inputs[i].stem, start + 1))
            toc += [(lvl + 1, t, p + start) for lvl, t, p in _outline(src)]
    if bookmarks:
        _set_outline(out, toc)
    ctx.add_pdf(out, output_name or "merged.pdf")
    ctx.result = {"pages": len(out.pages), "files": len(ctx.inputs)}


@tool("assemble_pdf", "Assemble PDF", CAT,
      "Build a new PDF from chosen pages of several PDFs, in any order. selection lists "
      "file:pages parts, e.g. '1:1-3, 2:all, 1:5' (file numbers follow the input order).",
      inputs=PDFS, required=["selection"],
      options={"selection": {"type": "string", "description": "e.g. 1:1-3, 2:all, 1:5"},
               "output_name": NAME_OPT})
def assemble_pdf(ctx, selection, output_name=None):
    srcs = [ctx.pdf(i) for i in range(len(ctx.inputs))]
    out = pikepdf.new()
    for part in selection.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*:\s*(.+)", part)
        if not m:
            raise EditError(f"bad selection part {part!r}; use file:pages like 2:1-4")
        fi = int(m.group(1)) - 1
        if not 0 <= fi < len(srcs):
            raise EditError(f"there is no input file {fi + 1}")
        n = len(srcs[fi].pages)
        seq = range(n) if m.group(2).strip() == "all" else parse_order(m.group(2), n)
        for p in seq:
            out.add_pages_from(srcs[fi], [p])
    if not len(out.pages):
        raise EditError("the selection is empty")
    ctx.add_pdf(out, output_name or "assembled.pdf")
    ctx.result = {"pages": len(out.pages)}


def _subset(src: pikepdf.Pdf, pages: list[int]) -> pikepdf.Pdf:
    out = pikepdf.new()
    out.add_pages_from(src, list(pages))
    return out


@tool("split_pdf", "Split PDF", CAT,
      "Split a PDF into several files: every N pages, at given ranges, into single pages, or one "
      "file per top-level bookmark.",
      aliases=["split"],
      options={"mode": {"type": "string", "enum": ["every_n_pages", "ranges", "single_pages", "bookmarks"],
                        "default": "every_n_pages"},
               "n": {"type": "integer", "default": 1, "description": "Pages per file (every_n_pages)."},
               "ranges": {"type": "string", "description": "Groups separated by ';', e.g. 1-3; 4-6; 7-"}})
def split_pdf(ctx, mode="every_n_pages", n=1, ranges=None):
    src = ctx.pdf()
    total = len(src.pages)
    groups: list[tuple[str, list[int]]] = []
    if mode == "single_pages" or (mode == "every_n_pages" and n <= 1):
        groups = [(f"page-{p + 1}", [p]) for p in range(total)]
    elif mode == "every_n_pages":
        groups = [(f"pages-{s + 1}-{min(s + n, total)}", list(range(s, min(s + n, total))))
                  for s in range(0, total, n)]
    elif mode == "ranges":
        if not ranges:
            raise EditError("ranges mode needs ranges, e.g. 1-3; 4-6")
        for g in ranges.split(";"):
            if g.strip():
                groups.append((f"pages-{g.strip().replace(',', '_').replace(' ', '')}", parse_pages(g, total)))
    else:
        top = [(t, p - 1) for lvl, t, p in _outline(src) if lvl == 1]
        if not top:
            raise EditError("this PDF has no bookmarks to split by")
        starts = [p for _, p in top] + [total]
        for (title, p), nxt in zip(top, starts[1:]):
            safe = re.sub(r"[^\w\- ]+", "", title).strip()[:40] or f"part-{p + 1}"
            groups.append((safe, list(range(p, max(p + 1, nxt)))))
    for label, pages in groups:
        ctx.add_pdf(_subset(src, pages), f"{ctx.inputs[0].stem}-{label}.pdf")
    ctx.result = {"files": len(groups)}


@tool("rearrange_pages", "Rearrange PDF pages", CAT,
      "Put the pages in a new order, e.g. '3,1,2,4-10' or 'reverse'. Pages left out are removed; "
      "repeated pages are duplicated.",
      in_place=True, required=["order"], aliases=["reorder_pages", "sort_pages"],
      options={"order": {"type": "string", "description": "e.g. 3,1,2,4-10 or reverse"}})
def rearrange_pages(ctx, doc, order):
    pdf = doc.pdf
    seq = parse_order(order, len(pdf.pages))
    pages = [pdf.pages[p] for p in seq]          # pikepdf copies repeated pages itself
    for _ in range(len(pdf.pages)):
        del pdf.pages[0]
    for pg in pages:
        pdf.pages.append(pg)
    doc.touch_all()
    ctx.structure_changed = True
    ctx.result = {"pages": doc.page_count}


@tool("remove_pages", "Remove PDF pages", CAT, "Delete the given pages.",
      in_place=True, required=["pages"], aliases=["delete_pages"], options={"pages": PAGES_REQ})
def remove_pages(ctx, doc, pages):
    pdf = doc.pdf
    drop = sorted(set(parse_pages(pages, len(pdf.pages))), reverse=True)
    if len(drop) >= len(pdf.pages):
        raise EditError("cannot remove every page")
    for p in drop:
        del pdf.pages[p]
    doc.touch_all()
    ctx.structure_changed = True
    ctx.result = {"removed": len(drop), "pages": doc.page_count}


@tool("extract_pages", "Extract PDF pages", CAT, "Copy the given pages into a new PDF (or one PDF per page).",
      required=["pages"],
      options={"pages": PAGES_REQ, "separate_files": {"type": "boolean", "default": False}})
def extract_pages(ctx, pages, separate_files=False):
    src = ctx.pdf()
    sel = parse_pages(pages, len(src.pages))
    stem = ctx.inputs[0].stem
    if separate_files:
        for p in sel:
            ctx.add_pdf(_subset(src, [p]), f"{stem}-page-{p + 1}.pdf")
    else:
        ctx.add_pdf(_subset(src, sel), f"{stem}-extract.pdf")
    ctx.result = {"pages": len(sel)}


@tool("rotate_pages", "Rotate PDF pages", CAT, "Rotate pages by 90, 180 or 270 degrees clockwise.",
      in_place=True, aliases=["rotate"],
      options={"pages": PAGES_OPT, "angle": {"type": "integer", "enum": [90, 180, 270, -90], "default": 90}})
def rotate_pages(ctx, doc, pages="all", angle=90):
    for p in parse_pages(pages, doc.page_count):
        doc.pdf.pages[p].rotate(angle, relative=True)
        doc._touch(p)
    ctx.structure_changed = True


@tool("insert_blank_pages", "Insert blank pages", CAT, "Insert empty pages after a page (0 = at the start).",
      in_place=True,
      options={"after": {"type": "integer", "default": 0, "description": "Insert after this page (0 = start)."},
               "count": {"type": "integer", "default": 1},
               "size": {"type": "string", "default": "same", "enum": ["same"] + PAPER_ENUM}})
def insert_blank_pages(ctx, doc, after=0, count=1, size="same"):
    pdf = doc.pdf
    n = len(pdf.pages)
    after = max(0, min(n, after))
    ref = pdf.pages[max(0, after - 1)]
    w, h = page_size(ref) if size == "same" else paper(size)
    for i in range(max(1, count)):
        pdf.add_blank_page(page_size=(w, h))
        pdf.pages.insert(after + i, pdf.pages[-1])
        del pdf.pages[-1]
    doc.touch_all()
    ctx.structure_changed = True


NUP = {2: (2, 1, "landscape"), 4: (2, 2, "portrait"), 6: (2, 3, "portrait"),
       8: (4, 2, "landscape"), 9: (3, 3, "portrait"), 16: (4, 4, "portrait")}


@tool("pages_per_sheet", "Pages per sheet", CAT,
      "Put several pages on each sheet (n-up), e.g. 2 or 4 pages per sheet.",
      aliases=["n_up", "nup"],
      options={"per_sheet": {"type": "integer", "enum": [2, 4, 6, 8, 9, 16], "default": 2},
               "paper": {"type": "string", "enum": PAPER_ENUM, "default": "A4"},
               "order": {"type": "string", "enum": ["rows", "columns"], "default": "rows"},
               "margin": {"type": "number", "default": 18},
               "border": {"type": "boolean", "default": False}})
def pages_per_sheet(ctx, per_sheet=2, paper="A4", order="rows", margin=18, border=False):
    from .base import paper as paper_size
    src = ctx.pdf()
    cols, rows, orient = NUP[per_sheet]
    W, H = paper_size(paper, orient)
    cw, ch = (W - 2 * margin) / cols, (H - 2 * margin) / rows
    out = pikepdf.new()
    total = len(src.pages)
    for s in range(0, total, per_sheet):
        sheet = out.add_blank_page(page_size=(W, H))
        for k in range(per_sheet):
            p = s + k
            if p >= total:
                break
            c, r = (k % cols, k // cols) if order == "rows" else (k // rows, k % rows)
            x0, y1 = margin + c * cw + 4, H - margin - r * ch - 4
            rect = [x0, y1 - ch + 8, x0 + cw - 8, y1]
            place_form(out, sheet, page_form(out, src, p), rect)
            if border:
                sheet.contents_add(out.make_stream(
                    f"q 0.6 G 0.5 w {rect[0]:.2f} {rect[1]:.2f} {cw - 8:.2f} {ch - 8:.2f} re S Q\n".encode()))
    ctx.add_pdf(out, f"{ctx.inputs[0].stem}-{per_sheet}up.pdf")
    ctx.result = {"sheets": len(out.pages)}


@tool("halve_pages", "Halve PDF pages", CAT,
      "Cut each page into two halves (left/right or top/bottom), e.g. for scanned book spreads.",
      aliases=["split_pages_in_half"],
      options={"direction": {"type": "string", "enum": ["vertical_cut", "horizontal_cut"],
                             "default": "vertical_cut", "description": "vertical_cut = left and right halves"},
               "pages": PAGES_OPT})
def halve_pages(ctx, direction="vertical_cut", pages="all"):
    src = ctx.pdf()
    sel = set(parse_pages(pages, len(src.pages)))
    out = pikepdf.new()
    for p in range(len(src.pages)):
        if p not in sel:
            out.add_pages_from(src, [p])
            continue
        w, h = page_size(src.pages[p])
        form = page_form(out, src, p)
        halves = ([(w / 2, h, [0, 0, w, h]), (w / 2, h, [-w / 2, 0, w / 2, h])] if direction == "vertical_cut"
                  else [(w, h / 2, [0, -h / 2, w, h / 2]), (w, h / 2, [0, 0, w, h])])
        for pw, ph, rect in halves:
            sheet = out.add_blank_page(page_size=(pw, ph))
            place_form(out, sheet, form, rect, keep_proportion=False)
    ctx.add_pdf(out, f"{ctx.inputs[0].stem}-halved.pdf")
    ctx.result = {"pages": len(out.pages)}


def _auto_headings(eng) -> list:
    sizes: Counter = Counter()
    lines = []
    for p in range(eng.page_count):
        for line in eng._lines(p):
            line = sorted(line, key=lambda s: s.origin[0])
            text = " ".join(s.text for s in line).strip()
            if not text or line[0].ocr:
                continue
            size = round(max(s.size for s in line), 1)
            sizes[size] += len(text)
            lines.append((p, size, text))
    if not sizes:
        return []
    body = sizes.most_common(1)[0][0]
    heads = sorted({s for _, s, _ in lines if s >= body * 1.18}, reverse=True)[:3]
    toc = [[heads.index(s) + 1, t, p + 1] for p, s, t in lines if s in heads and len(t) <= 120]
    prev = 0
    for e in toc:
        e[0] = min(e[0], prev + 1)
        prev = e[0]
    return toc


@tool("bookmarks", "Bookmark PDF", CAT,
      "List, add, replace or clear bookmarks (outline), or create them automatically from headings.",
      in_place=True, aliases=["bookmark_pdf", "outline", "toc"],
      options={"action": {"type": "string", "enum": ["list", "add", "replace", "clear", "auto_from_headings"],
                          "default": "list"},
               "items": {"type": "array", "description": "Bookmarks for add/replace.",
                         "items": {"type": "object", "properties": {
                             "title": {"type": "string"}, "page": {"type": "integer"},
                             "level": {"type": "integer"}}}}})
def bookmarks(ctx, doc, action="list", items=None):
    pdf = doc.pdf
    toc = _outline(pdf)
    if action == "list":
        ctx.result = {"bookmarks": [{"level": l, "title": t, "page": p} for l, t, p in toc]}
        return
    if action == "clear":
        new = []
    elif action == "auto_from_headings":
        new = _auto_headings(doc)
        if not new:
            raise EditError("no headings found (no text larger than the body text)")
    else:
        if not items:
            raise EditError("items are required for add/replace")
        add = [(int(i.get("level", 1) or 1), str(i["title"]), int(i["page"])) for i in items]
        new = sorted(toc + add, key=lambda e: e[2]) if action == "add" else add
    _set_outline(pdf, [tuple(e) for e in new])
    doc._touch(0)
    ctx.result = {"bookmarks": [{"level": l, "title": t, "page": p} for l, t, p in _outline(pdf)]}


@tool("extract_images", "Extract PDF images", CAT, "Save the images embedded in a PDF as separate files (plus a zip).",
      options={"format": {"type": "string", "enum": ["original", "png", "jpg"], "default": "original"},
               "min_size": {"type": "integer", "default": 32, "description": "Skip images smaller than this (px)."},
               "pages": PAGES_OPT})
def extract_images(ctx, format="original", min_size=32, pages="all"):
    src = ctx.pdf()
    seen, files = set(), []
    for p in parse_pages(pages, len(src.pages)):
        for name, raw in src.pages[p].images.items():
            if raw.objgen in seen:
                continue
            seen.add(raw.objgen)
            try:
                img = pikepdf.PdfImage(raw)
                if min(img.width, img.height) < min_size:
                    continue
                if format == "original":
                    buf = io.BytesIO()
                    ext = img.extract_to(stream=buf)
                    data, ext = buf.getvalue(), ext.lstrip(".")
                else:
                    pil = img.as_pil_image()
                    if format == "jpg":
                        pil = pil.convert("RGB")
                    buf = io.BytesIO()
                    pil.save(buf, "JPEG" if format == "jpg" else "PNG", **({"quality": 92} if format == "jpg" else {}))
                    data, ext = buf.getvalue(), format
            except Exception:
                continue
            files.append((f"{ctx.inputs[0].stem}-p{p + 1}-img{len(files) + 1}.{ext}", data))
    if not files:
        raise EditError("no images found")
    ctx.add_bundle(files, f"{ctx.inputs[0].stem}-images.zip")
    ctx.result = {"images": len(files)}


def to_markdown(eng, pages: list[int]) -> str:
    """Markdown from the glyph model: headings by size, bold runs, lists."""
    sizes: Counter = Counter()
    for p in pages:
        for s in eng._segments(p):
            if not s.ocr or True:
                sizes[round(s.size, 1)] += len(s.text)
    body = sizes.most_common(1)[0][0] if sizes else 11
    heads = sorted({sz for sz in sizes if sz >= body * 1.18}, reverse=True)[:3]
    out = []
    for p in pages:
        prev_y = None
        for line in eng._lines(p):
            line = sorted(line, key=lambda s: s.origin[0])
            text = " ".join(s.text for s in line).strip()
            if not text:
                continue
            size = round(max(s.size for s in line), 1)
            y = line[0].origin[1]
            if prev_y is not None and y - prev_y > size * 1.9:
                out.append("")
            prev_y = y
            if size in heads and len(text) <= 120:
                out += ["", "#" * (heads.index(size) + 1) + " " + text, ""]
            elif re.match(r"^[•‣●\-\*]\s+", text):
                out.append("- " + re.sub(r"^[•‣●\-\*]\s+", "", text))
            elif all(s.font.bold for s in line):
                out.append(f"**{text}**")
            else:
                out.append(text)
        out.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


@tool("extract_text", "Copy from PDF", CAT,
      "Extract the text of a PDF (or some pages) as plain text, Markdown, or structured JSON with positions.",
      aliases=["copy_from_pdf", "copy_text"],
      options={"pages": PAGES_OPT, "format": {"type": "string", "enum": ["text", "markdown", "json"], "default": "text"}})
def extract_text(ctx, pages="all", format="text"):
    eng = ctx.inputs[0].engine()
    sel = parse_pages(pages, eng.page_count)
    stem = ctx.inputs[0].stem
    if format == "markdown":
        text = to_markdown(eng, sel)
        ctx.add_file(text.encode(), f"{stem}.md", "text/markdown")
    elif format == "json":
        data = [{"page": p + 1, "segments": [{"text": s.text, "bbox": [round(v, 1) for v in s.bbox],
                                               "font": s.font.name, "size": round(s.size, 1)}
                                              for s in eng._segments(p)]} for p in sel]
        text = json.dumps(data)
        ctx.add_file(text.encode(), f"{stem}-text.json", "application/json")
    else:
        text = "\n\n".join(f"--- page {p + 1} ---\n" + eng.page_text(p) for p in sel)
        ctx.add_file(text.encode(), f"{stem}.txt", "text/plain")
    ctx.result = {"characters": len(text), "text": text[:20000]}
