"""Writing to pages: content streams, font resources, fallback fonts.

Fallback chain for a character the original font cannot draw:
  1. the standard-14 face closest to the original (Helvetica/Times/Courier,
     bold/italic) - not embedded, every viewer has it, exact AFM metrics
  2. DejaVu Sans (shipped in app/assets/fonts, Bitstream Vera licence plus
     public-domain changes), embedded as a Type0/Identity-H font subset with
     fontTools, for anything outside WinAnsi (currency signs, arrows, Greek,
     Cyrillic, maths symbols, ...). The embedded font is grown in place when new glyphs are
     needed (glyph ids are retained, so earlier text stays valid).
"""
from __future__ import annotations

import io
import os
from typing import Optional

import pikepdf

from .fonts import FontInfo, load_font

Op = pikepdf.Operator
WRAP_KEY = pikepdf.Name("/SPEWrapped")


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def std14_name(bold: bool, italic: bool, serif: bool, mono: bool) -> str:
    if mono:
        base, b, i, bi = "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"
    elif serif:
        base, b, i, bi = "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"
    else:
        base, b, i, bi = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"
    return bi if bold and italic else b if bold else i if italic else base


def family_std14(family: Optional[str], bold: bool, italic: bool) -> str:
    fam = (family or "helvetica").lower()
    return std14_name(bold, italic, serif=fam.startswith("times") or fam == "serif",
                      mono=fam.startswith("cour") or fam == "mono")


# Marks fonts the editor adds as stand-ins for characters a document's own
# font cannot draw; text runs that switch to one stay one editable object.
FALLBACK_KEY = "/SPEFallback"

FONT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")
UNICODE_FALLBACK = {(0, 0): "DejaVuSans.ttf", (1, 0): "DejaVuSans-Bold.ttf",
                    (0, 1): "DejaVuSans-Oblique.ttf", (1, 1): "DejaVuSans-BoldOblique.ttf"}


class FontBook:
    """Per-document cache of fallback fonts (shared font objects)."""

    def __init__(self, pdf: pikepdf.Pdf):
        self.pdf = pdf
        self.std: dict[str, tuple[pikepdf.Object, FontInfo]] = {}
        self._embedded: dict[str, dict] = {}

    def std14(self, name: str) -> tuple[pikepdf.Object, FontInfo]:
        if name not in self.std:
            d = pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                   BaseFont=pikepdf.Name("/" + name))
            d[FALLBACK_KEY] = True
            if name not in ("Symbol", "ZapfDingbats"):
                d.Encoding = pikepdf.Name.WinAnsiEncoding
            obj = self.pdf.make_indirect(d)
            self.std[name] = (obj, load_font(obj))
        return self.std[name]

    # -- embedded TrueType fonts (Unicode fallback, signature script) ------------------
    def unicode_fallback(self, bold: bool, italic: bool, chars: str) -> tuple[pikepdf.Object, FontInfo]:
        fname = UNICODE_FALLBACK[(int(bold), int(italic))]
        return self.embedded(os.path.join(FONT_DIR, fname), chars, fallback=True)

    def embedded(self, path: str, chars: str, fallback: bool = False) -> tuple[pikepdf.Object, FontInfo]:
        """A Type0/Identity-H subset of the TTF at `path` that covers `chars`,
        grown in place as new characters are needed."""
        from fontTools.ttLib import TTFont
        slot = self._embedded.get(path)
        if slot is None:
            tt = TTFont(path)
            slot = {"cmap": tt.getBestCmap(), "upem": tt["head"].unitsPerEm, "glyphs": set(),
                    "obj": None, "tt": tt, "path": path, "fallback": fallback}
            self._embedded[path] = slot
        need = {c for c in chars if ord(c) in slot["cmap"]}
        if slot["obj"] is None or not need <= slot["glyphs"]:
            slot["glyphs"] |= need
            self._build_embedded(slot)
        info = load_font(slot["obj"])
        info.subset = False
        return slot["obj"], info

    def _build_embedded(self, slot: dict) -> None:
        from fontTools import subset
        from fontTools.ttLib import TTFont
        tt: TTFont = slot["tt"]
        cmap, upem = slot["cmap"], slot["upem"]
        order = tt.getGlyphOrder()
        gid_of = {name: i for i, name in enumerate(order)}
        uni_gid = {c: gid_of[cmap[ord(c)]] for c in slot["glyphs"] if ord(c) in cmap}
        opts = subset.Options()
        opts.retain_gids = True
        opts.notdef_outline = True
        opts.name_IDs = ["*"]
        opts.drop_tables += ["DSIG", "FFTM"]
        sub = subset.Subsetter(opts)
        font = TTFont(slot["path"])
        sub.populate(unicodes=[ord(c) for c in slot["glyphs"]] or [32])
        sub.subset(font)
        buf = io.BytesIO()
        font.save(buf)
        hmtx = tt["hmtx"]
        widths = []
        for c, gid in sorted(uni_gid.items(), key=lambda kv: kv[1]):
            widths += [gid, pikepdf.Array([round(hmtx[order[gid]][0] * 1000 / upem)])]
        tounicode = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
                     "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                     "/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
                     "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
                     f"{len(uni_gid)} beginbfchar\n"
                     + "".join(f"<{gid:04X}> <{ord(c):04X}>\n" for c, gid in uni_gid.items())
                     + "endbfchar endcmap CMapName currentdict /CMap defineresource pop end end\n")
        head, hhea = tt["head"], tt["hhea"]
        k = 1000 / upem
        ps_name = (tt["name"].getDebugName(6) or "EmbeddedFont").replace(" ", "")
        base = pikepdf.Name("/" + ps_name)
        descriptor = pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor, FontName=base, Flags=32,
            FontBBox=[round(head.xMin * k), round(head.yMin * k), round(head.xMax * k), round(head.yMax * k)],
            ItalicAngle=0, Ascent=round(hhea.ascent * k), Descent=round(hhea.descent * k),
            CapHeight=round(hhea.ascent * k * 0.9), StemV=80,
            FontFile2=self.pdf.make_stream(buf.getvalue()))
        cid = pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2, BaseFont=base,
            CIDSystemInfo=pikepdf.Dictionary(Registry=pikepdf.String("Adobe"),
                                             Ordering=pikepdf.String("Identity"), Supplement=0),
            FontDescriptor=self.pdf.make_indirect(descriptor), CIDToGIDMap=pikepdf.Name.Identity,
            DW=1000, W=pikepdf.Array(widths))
        if slot["obj"] is None:
            slot["obj"] = self.pdf.make_indirect(pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0, BaseFont=base,
                Encoding=pikepdf.Name("/Identity-H")))
            if slot.get("fallback"):
                slot["obj"][FALLBACK_KEY] = True
        top = slot["obj"]
        top.DescendantFonts = pikepdf.Array([self.pdf.make_indirect(cid)])
        top.ToUnicode = self.pdf.make_stream(tounicode.encode())


# -- page content --------------------------------------------------------------------
def ensure_wrapped(pdf: pikepdf.Pdf, page: pikepdf.Page) -> None:
    """Wrap the existing content in q/Q once, so appended content starts
    from a clean graphics state whatever the original left behind."""
    if page.obj.get(WRAP_KEY):
        return
    page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
    page.contents_add(pdf.make_stream(b"\nQ\n"), prepend=False)
    page.obj[WRAP_KEY] = True


def append_content(pdf: pikepdf.Pdf, page: pikepdf.Page, data: bytes) -> None:
    ensure_wrapped(pdf, page)
    page.contents_add(pdf.make_stream(data), prepend=False)


def write_stream(pdf: pikepdf.Pdf, si) -> None:
    """Write a (modified) instruction list back to its page or form XObject."""
    data = pikepdf.unparse_content_stream(si.instrs)
    if si.key == "page":
        si.owner.obj.Contents = pdf.make_stream(data)
    else:
        si.owner.write(data)


def font_resource(page: pikepdf.Page, font_obj: pikepdf.Object) -> str:
    """Name under which this page can use font_obj (adding it if needed)."""
    res = page.obj.get("/Resources")
    fonts = res.get("/Font") if res is not None else None
    if fonts is not None:
        for k, v in fonts.items():
            if v.is_indirect and font_obj.is_indirect and v.objgen == font_obj.objgen:
                return k
    return str(page.add_resource(font_obj, pikepdf.Name.Font, prefix="SF"))


def stream_resource(pdf: pikepdf.Pdf, si, obj: pikepdf.Object, kind: str, prefix: str) -> str:
    """Name under which the content stream `si` (page or form XObject) can
    use `obj` as a /Font or /ExtGState resource, adding it if needed."""
    if si.key == "page":
        if kind == "/Font":
            return font_resource(si.owner, obj)
        return str(si.owner.add_resource(obj, pikepdf.Name(kind), prefix=prefix))
    owner = si.owner
    if "/Resources" not in owner:
        owner.Resources = pikepdf.Dictionary()
    res = owner.Resources
    if kind not in res:
        res[kind] = pikepdf.Dictionary()
    group = res[kind]
    for k, v in group.items():
        if v.is_indirect and obj.is_indirect and v.objgen == obj.objgen:
            return k
    i = 1
    while f"/{prefix}{i}" in group:
        i += 1
    group[f"/{prefix}{i}"] = obj
    return f"/{prefix}{i}"


def insert_after_text_object(pdf: pikepdf.Pdf, si, instr: int, data: bytes) -> bool:
    """Insert content right after the ET that closes the text object holding
    instruction `instr`, so it paints exactly where the original did in the
    stacking order. False if the text object is never closed."""
    for j in range(instr, len(si.instrs)):
        if str(si.instrs[j].operator) == "ET":
            new = list(pikepdf.parse_content_stream(pdf.make_stream(data)))
            si.instrs[j + 1:j + 1] = new
            write_stream(pdf, si)
            return True
    return False


def alpha_resource(pdf: pikepdf.Pdf, page: pikepdf.Page, alpha: float) -> str:
    gs = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, ca=round(alpha, 3),
                                              CA=round(alpha, 3)))
    return str(page.add_resource(gs, pikepdf.Name.ExtGState, prefix="SA"))


def color_op(rgb, stroke: bool = False) -> str:
    r, g, b = rgb
    if abs(r - g) < 1e-3 and abs(g - b) < 1e-3:
        return f"{_num(r)} {'G' if stroke else 'g'}"
    return f"{_num(r)} {_num(g)} {_num(b)} {'RG' if stroke else 'rg'}"


def text_block(runs: list[tuple[str, FontInfo, str]], tm, tfs: float, rgb, tc=0.0, tw=0.0, th=1.0,
               rise=0.0, render=0, gs_name: Optional[str] = None) -> bytes:
    """q BT ... ET Q drawing `runs` [(text, font info, resource name)] in order
    from text matrix `tm` (PDF user space). The text position advances
    naturally between runs, exactly as for one string."""
    parts = ["q"]
    if gs_name:
        parts.append(f"{gs_name} gs")
    parts += ["BT", color_op(rgb), f"{render} Tr", f"{_num(tc)} Tc", f"{_num(tw)} Tw",
              f"{_num(th * 100)} Tz", f"{_num(rise)} Ts", " ".join(_num(v) for v in tm) + " Tm"]
    for text, info, res in runs:
        if not text:
            continue
        parts.append(f"{res} {_num(tfs)} Tf <{info.encode(text).hex()}> Tj")
    parts += ["ET", "Q", ""]
    return "\n".join(parts).encode()
