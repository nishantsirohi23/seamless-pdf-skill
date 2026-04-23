"""Content-stream interpreter: the engine's model of what is on a page.

Walks a page's instructions (and nested form XObjects) with a faithful
graphics/text state machine and records, for every glyph drawn, its exact
position, direction, size, font resource, colour and *where in the stream*
it comes from. Editing then works on the stream itself: erasing a glyph
replaces its bytes with an equal-width TJ offset, so nothing else on the
page moves; that is more precise than any rectangle-based redaction.

Coordinates exposed to the rest of the app are "page space": origin at the
top-left of the crop box, y down, unrotated (the PDF's /Rotate is applied
by the viewer via the page's rotation matrix).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pikepdf

from .fonts import FontInfo, load_font
from .geom import IDENTITY, Matrix, Rect

Op = pikepdf.Operator


@dataclass
class Glyph:
    stream: object               # key of the stream it lives in
    instr: int                   # index in that stream's (normalised) instruction list
    elem: int                    # index in the TJ array
    pos: int                     # byte offset inside that string
    nbytes: int
    code: int
    char: str
    font: FontInfo
    font_res: str                # font resource name in the stream's resources
    tfs: float
    tc: float
    tw: float
    th: float
    rise: float
    text_ctm: Matrix             # Tm (at this glyph) x CTM, PDF user space
    origin: tuple                # page space
    dir: tuple                   # unit vector along the baseline (page space)
    up: tuple                    # unit vector towards the ascender (page space)
    size: float                  # font size in page units
    adv: float                   # advance in page units (excluding Tc/Tw/kerning)
    rgb: tuple
    alpha: float
    render: int                  # text render mode (3 = invisible)
    bbox: Rect
    bt: int = 0                  # which BT..ET text object (page-wide counter)
    ctm: Optional[Matrix] = None  # CTM in force for this glyph (constant within BT..ET)


@dataclass
class ImagePlacement:
    stream: object
    instr: int
    name: str
    obj: Optional[pikepdf.Object]
    ctm: Matrix                  # image unit square -> PDF user space
    bbox: Rect                   # page space
    inline: bool = False


@dataclass
class Shape:
    bbox: Rect                   # page space
    kind: str                    # "rect" | "line" | "path"
    stroked: bool
    filled: bool


@dataclass
class StreamInfo:
    key: object
    owner: object                # pikepdf.Page or the form XObject stream
    resources: pikepdf.Object
    instrs: list


@dataclass
class PageModel:
    index: int
    P: Matrix                    # PDF user space -> page space
    width: float
    height: float
    rotation: int
    streams: dict = field(default_factory=dict)
    glyphs: list = field(default_factory=list)
    images: list = field(default_factory=list)
    shapes: list = field(default_factory=list)


def page_geometry(page: pikepdf.Page) -> tuple[Matrix, float, float, int]:
    cb = page.cropbox if "/CropBox" in page.obj else page.mediabox
    x0, y0, x1, y1 = [float(v) for v in cb]
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    rot = int(page.obj.get("/Rotate", 0) or 0) % 360
    return Matrix(1, 0, 0, -1, -x0, y1), x1 - x0, y1 - y0, rot


def rotation_matrix(rot: int, w: float, h: float) -> Matrix:
    """Unrotated page space -> displayed (rotated) page space."""
    if rot == 90:
        return Matrix(0, 1, -1, 0, h, 0)
    if rot == 180:
        return Matrix(-1, 0, 0, -1, w, h)
    if rot == 270:
        return Matrix(0, -1, 1, 0, 0, w)
    return IDENTITY


def _f(x, d=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return d


def _normalise(instrs: list) -> list:
    """Rewrite Tj / ' / " as equivalent TJ sequences (one editable form)."""
    out = []
    for ins in instrs:
        if isinstance(ins, pikepdf.ContentStreamInlineImage):
            out.append(ins)
            continue
        op = str(ins.operator)
        ops = list(ins.operands)
        if op == "Tj" and ops:
            out.append(pikepdf.ContentStreamInstruction([pikepdf.Array([ops[0]])], Op("TJ")))
        elif op == "'" and ops:
            out.append(pikepdf.ContentStreamInstruction([], Op("T*")))
            out.append(pikepdf.ContentStreamInstruction([pikepdf.Array([ops[0]])], Op("TJ")))
        elif op == '"' and len(ops) == 3:
            out.append(pikepdf.ContentStreamInstruction([ops[0]], Op("Tw")))
            out.append(pikepdf.ContentStreamInstruction([ops[1]], Op("Tc")))
            out.append(pikepdf.ContentStreamInstruction([], Op("T*")))
            out.append(pikepdf.ContentStreamInstruction([pikepdf.Array([ops[2]])], Op("TJ")))
        else:
            out.append(ins)
    return out


def _cmyk(c, m, y, k) -> tuple:
    return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))


def _color(cs_n: int, vals: list) -> tuple:
    v = [_f(x) for x in vals if not isinstance(x, pikepdf.Name)]
    if cs_n == 1 and len(v) >= 1:
        return (v[0], v[0], v[0])
    if cs_n == 3 and len(v) >= 3:
        return (v[0], v[1], v[2])
    if cs_n == 4 and len(v) >= 4:
        return _cmyk(*v[:4])
    return (0.0, 0.0, 0.0)


class _State:
    __slots__ = ("ctm", "rgb", "alpha", "cs_n", "font", "font_res", "tfs", "tc", "tw", "th",
                 "tl", "rise", "tr")

    def __init__(self):
        self.ctm = IDENTITY
        self.rgb = (0.0, 0.0, 0.0)
        self.alpha = 1.0
        self.cs_n = 1
        self.font: Optional[FontInfo] = None
        self.font_res = ""
        self.tfs = 0.0
        self.tc = 0.0
        self.tw = 0.0
        self.th = 1.0
        self.tl = 0.0
        self.rise = 0.0
        self.tr = 0

    def copy(self) -> "_State":
        s = _State()
        for k in self.__slots__:
            setattr(s, k, getattr(self, k))
        return s


class Interpreter:
    def __init__(self, pdf: pikepdf.Pdf, font_cache: dict):
        self.pdf = pdf
        self.font_cache = font_cache
        self.bt = 0

    def font(self, obj) -> Optional[FontInfo]:
        if obj is None:
            return None
        key = obj.objgen if obj.is_indirect else ("direct", id(obj))
        f = self.font_cache.get(key)
        if f is None:
            try:
                f = load_font(obj)
            except Exception:
                return None
            self.font_cache[key] = f
        return f

    def page(self, index: int) -> PageModel:
        page = self.pdf.pages[index]
        P, w, h, rot = page_geometry(page)
        model = PageModel(index=index, P=P, width=w, height=h, rotation=rot)
        res = page.obj.get("/Resources") or pikepdf.Dictionary()
        try:
            instrs = _normalise(pikepdf.parse_content_stream(page))
        except Exception:
            instrs = []
        model.streams["page"] = StreamInfo("page", page, res, instrs)
        self._run(model, "page", instrs, res, _State(), depth=0, seen=set())
        return model

    def _run(self, model: PageModel, skey, instrs, res, gs: _State, depth: int, seen: set):
        stack: list[_State] = []
        tm = tlm = IDENTITY
        path: list[tuple[float, float]] = []
        path_is_rect = True
        P = model.P
        fonts = res.get("/Font") if res is not None else None
        xobjs = res.get("/XObject") if res is not None else None
        extg = res.get("/ExtGState") if res is not None else None
        for idx, ins in enumerate(instrs):
            if isinstance(ins, pikepdf.ContentStreamInlineImage):
                bbox = Rect(0, 0, 1, 1).transform(gs.ctm * P)
                model.images.append(ImagePlacement(skey, idx, "inline", None, gs.ctm, bbox, inline=True))
                continue
            op = str(ins.operator)
            a = ins.operands
            try:
                if op == "q":
                    stack.append(gs.copy())
                elif op == "Q":
                    if stack:
                        gs = stack.pop()
                elif op == "cm" and len(a) == 6:
                    gs.ctm = Matrix.of(a) * gs.ctm
                elif op == "BT":
                    tm = tlm = IDENTITY
                    self.bt += 1
                elif op == "Tf" and len(a) == 2:
                    gs.font_res = str(a[0])
                    gs.font = self.font(fonts.get(a[0]) if fonts is not None else None)
                    gs.tfs = _f(a[1])
                elif op == "Tc":
                    gs.tc = _f(a[0])
                elif op == "Tw":
                    gs.tw = _f(a[0])
                elif op == "Tz":
                    gs.th = _f(a[0], 100) / 100.0
                elif op == "TL":
                    gs.tl = _f(a[0])
                elif op == "Ts":
                    gs.rise = _f(a[0])
                elif op == "Tr":
                    gs.tr = int(_f(a[0]))
                elif op == "Td" and len(a) == 2:
                    tlm = Matrix.translate(_f(a[0]), _f(a[1])) * tlm
                    tm = tlm
                elif op == "TD" and len(a) == 2:
                    gs.tl = -_f(a[1])
                    tlm = Matrix.translate(_f(a[0]), _f(a[1])) * tlm
                    tm = tlm
                elif op == "Tm" and len(a) == 6:
                    tm = tlm = Matrix.of(a)
                elif op == "T*":
                    tlm = Matrix.translate(0, -gs.tl) * tlm
                    tm = tlm
                elif op == "TJ" and a:
                    tm = self._show(model, skey, idx, a[0], gs, tm)
                elif op in ("g", "rg", "k"):
                    gs.cs_n = {"g": 1, "rg": 3, "k": 4}[op]
                    gs.rgb = _color(gs.cs_n, list(a))
                elif op == "cs" and a:
                    gs.cs_n = self._cs_n(res, a[0])
                    gs.rgb = (0.0, 0.0, 0.0)
                elif op in ("sc", "scn"):
                    gs.rgb = _color(gs.cs_n, list(a))
                elif op == "gs" and a and extg is not None:
                    d = extg.get(a[0])
                    if d is not None and "/ca" in d:
                        gs.alpha = _f(d.ca, 1.0)
                elif op == "Do" and a and xobjs is not None:
                    xo = xobjs.get(a[0])
                    if xo is None:
                        continue
                    sub = str(xo.get("/Subtype", ""))
                    if sub == "/Image":
                        bbox = Rect(0, 0, 1, 1).transform(gs.ctm * P)
                        model.images.append(ImagePlacement(skey, idx, str(a[0]), xo, gs.ctm, bbox))
                    elif sub == "/Form" and depth < 12 and xo.objgen not in seen:
                        self._form(model, xo, gs, depth, seen)
                elif op in ("m", "l"):
                    path.append(gs.ctm.apply(_f(a[0]), _f(a[1])))
                    if op == "l":
                        path_is_rect = False
                elif op in ("c", "v", "y"):
                    pts = [_f(x) for x in a]
                    for i in range(0, len(pts) - 1, 2):
                        path.append(gs.ctm.apply(pts[i], pts[i + 1]))
                    path_is_rect = False
                elif op == "re" and len(a) == 4:
                    x, y, w, h = [_f(v) for v in a]
                    path.extend(gs.ctm.apply(px, py) for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)))
                elif op in ("S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"):
                    if path and op != "n":
                        bb = Rect.bound(P.apply(*p) for p in path)
                        kind = "rect" if path_is_rect else ("line" if min(bb.width, bb.height) < 1.5 else "path")
                        model.shapes.append(Shape(bb, kind, stroked=op in ("S", "s", "B", "B*", "b", "b*"),
                                                  filled=op not in ("S", "s")))
                    path = []
                    path_is_rect = True
            except Exception:
                continue  # a malformed operator never breaks the whole page

    def _cs_n(self, res, name) -> int:
        n = str(name)
        if n in ("/DeviceGray", "/G", "/CalGray"):
            return 1
        if n in ("/DeviceRGB", "/RGB", "/CalRGB"):
            return 3
        if n in ("/DeviceCMYK", "/CMYK"):
            return 4
        cs = (res.get("/ColorSpace") or {}).get(name) if res is not None else None
        try:
            if isinstance(cs, pikepdf.Array) and str(cs[0]) == "/ICCBased":
                return int(cs[1].get("/N", 3))
            if isinstance(cs, pikepdf.Array) and str(cs[0]) in ("/CalRGB", "/Lab"):
                return 3
        except Exception:
            pass
        return 0

    def _form(self, model: PageModel, xo, gs: _State, depth: int, seen: set):
        m = Matrix.of(xo.get("/Matrix", [1, 0, 0, 1, 0, 0]))
        inner = gs.copy()
        inner.ctm = m * gs.ctm
        key = ("form", xo.objgen)
        if key not in model.streams:
            try:
                instrs = _normalise(pikepdf.parse_content_stream(xo))
            except Exception:
                return
            res = xo.get("/Resources") or model.streams["page"].resources
            model.streams[key] = StreamInfo(key, xo, res, instrs)
        si = model.streams[key]
        self._run(model, key, si.instrs, si.resources, inner, depth + 1, seen | {xo.objgen})

    def _show(self, model: PageModel, skey, idx: int, arr, gs: _State, tm: Matrix) -> Matrix:
        f = gs.font
        if f is None:
            return tm
        P = model.P
        tfs = gs.tfs if gs.tfs else 1.0
        for ei, el in enumerate(arr):
            if isinstance(el, pikepdf.String):
                data = bytes(el)
                pos = 0
                for code, nb in f.decode(data):
                    w = f.width(code)
                    trm = Matrix(tfs * gs.th, 0, 0, tfs, 0, gs.rise) * tm * gs.ctm
                    L = trm * P
                    ox, oy = L.apply(0, 0)
                    xv = L.apply_vec(1, 0)
                    uv = L.apply_vec(0, 1)
                    xl = (xv[0] ** 2 + xv[1] ** 2) ** 0.5 or 1.0
                    ul = (uv[0] ** 2 + uv[1] ** 2) ** 0.5 or 1.0
                    d = (xv[0] / xl, xv[1] / xl)
                    u = (uv[0] / ul, uv[1] / ul)
                    adv = w * xl
                    asc, desc = f.ascent * ul, f.descent * ul
                    bbox = Rect.bound([(ox + u[0] * asc, oy + u[1] * asc),
                                       (ox + u[0] * desc, oy + u[1] * desc),
                                       (ox + d[0] * adv + u[0] * asc, oy + d[1] * adv + u[1] * asc),
                                       (ox + d[0] * adv + u[0] * desc, oy + d[1] * adv + u[1] * desc)])
                    ch = f.char(code)
                    f.used_codes.add(code)
                    model.glyphs.append(Glyph(
                        stream=skey, instr=idx, elem=ei, pos=pos, nbytes=nb, code=code, char=ch,
                        font=f, font_res=gs.font_res, tfs=gs.tfs, tc=gs.tc, tw=gs.tw, th=gs.th,
                        rise=gs.rise, text_ctm=tm * gs.ctm, origin=(ox, oy), dir=d, up=u,
                        size=ul, adv=adv, rgb=gs.rgb, alpha=gs.alpha, render=gs.tr, bbox=bbox, bt=self.bt,
                        ctm=gs.ctm))
                    space = gs.tw if (code == 32 and nb == 1) else 0.0
                    tx = (w * tfs + gs.tc + space) * gs.th
                    tm = Matrix.translate(tx, 0) * tm
                    pos += nb
            else:
                tx = -_f(el) / 1000.0 * tfs * gs.th
                tm = Matrix.translate(tx, 0) * tm
        return tm
