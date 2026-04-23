"""PDF font model on pikepdf: decode character codes, Unicode mapping,
advance widths and vertical metrics for simple, Type0 and Type3 fonts.

This is what lets the engine both *read* exact glyph positions and *write*
new text with the very same font resource (no re-embedding), the core of
seamless editing.
"""
from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field
from typing import Optional

import pikepdf

STD14 = {
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic", "Symbol", "ZapfDingbats",
}
# common aliases producers use for the standard 14
STD_ALIASES = {
    "Arial": "Helvetica", "ArialMT": "Helvetica", "Arial,Bold": "Helvetica-Bold",
    "Arial-BoldMT": "Helvetica-Bold", "Arial,Italic": "Helvetica-Oblique",
    "Arial-ItalicMT": "Helvetica-Oblique", "Arial,BoldItalic": "Helvetica-BoldOblique",
    "TimesNewRoman": "Times-Roman", "TimesNewRomanPSMT": "Times-Roman",
    "TimesNewRoman,Bold": "Times-Bold", "TimesNewRomanPS-BoldMT": "Times-Bold",
    "TimesNewRoman,Italic": "Times-Italic", "TimesNewRomanPS-ItalicMT": "Times-Italic",
    "CourierNew": "Courier", "CourierNewPSMT": "Courier", "Helv": "Helvetica",
}

_AGL = {
    "space": " ", "exclam": "!", "quotedbl": '"', "numbersign": "#", "dollar": "$",
    "percent": "%", "ampersand": "&", "quotesingle": "'", "quoteright": "’",
    "parenleft": "(", "parenright": ")", "asterisk": "*", "plus": "+", "comma": ",",
    "hyphen": "-", "minus": "−", "period": ".", "slash": "/", "zero": "0", "one": "1",
    "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "colon": ":", "semicolon": ";", "less": "<", "equal": "=",
    "greater": ">", "question": "?", "at": "@", "bracketleft": "[", "backslash": "\\",
    "bracketright": "]", "asciicircum": "^", "underscore": "_", "grave": "`",
    "quoteleft": "‘", "braceleft": "{", "bar": "|", "braceright": "}",
    "asciitilde": "~", "endash": "–", "emdash": "—", "bullet": "•",
    "quotedblleft": "“", "quotedblright": "”", "ellipsis": "…",
    "fi": "ﬁ", "fl": "ﬂ", "ff": "ﬀ", "ffi": "ﬃ", "ffl": "ﬄ",
    "sterling": "£", "Euro": "€", "yen": "¥", "cent": "¢",
    "section": "§", "paragraph": "¶", "degree": "°", "copyright": "©",
    "registered": "®", "trademark": "™", "dagger": "†", "daggerdbl": "‡",
    "periodcentered": "·", "multiply": "×", "divide": "÷", "plusminus": "±",
    "eacute": "é", "egrave": "è", "aacute": "á", "agrave": "à",
    "udieresis": "ü", "odieresis": "ö", "adieresis": "ä", "germandbls": "ß",
    "ccedilla": "ç", "ntilde": "ñ", "quotesinglbase": "‚", "quotedblbase": "„",
}


def glyph_to_uni(name: str) -> Optional[str]:
    if len(name) == 1:
        return name
    if name in _AGL:
        return _AGL[name]
    m = re.fullmatch(r"uni([0-9A-Fa-f]{4})", name) or re.fullmatch(r"u([0-9A-Fa-f]{4,6})", name)
    if m:
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return None
    base = name.split(".")[0].split("_")[0]
    return glyph_to_uni(base) if base != name else None


def clean_name(name: str) -> str:
    name = name.lstrip("/").replace("#20", " ")
    if len(name) > 7 and name[6] == "+" and name[:6].isalpha() and name[:6].isupper():
        return name[7:]
    return name


def std14_for(name: str) -> Optional[str]:
    n = clean_name(name)
    if n in STD14:
        return n
    return STD_ALIASES.get(n) or STD_ALIASES.get(n.replace(" ", ""))


def _num(o, default=0.0) -> float:
    try:
        return float(o)
    except Exception:
        return default


def _parse_tounicode(data: bytes) -> tuple[dict[int, str], list[tuple[int, int, int]]]:
    """code -> text, and codespace ranges (nbytes, lo, hi)."""
    c2u: dict[int, str] = {}
    spaces: list[tuple[int, int, int]] = []

    def uni(h: bytes) -> str:
        h = h.strip()
        if len(h) % 4:
            h = h.rjust((len(h) + 3) // 4 * 4, b"0")
        try:
            return codecs.decode(h, "hex").decode("utf-16-be", errors="ignore")
        except Exception:
            return ""

    for m in re.finditer(rb"begincodespacerange(.*?)endcodespacerange", data, re.S):
        for lo, hi in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", m.group(1)):
            spaces.append((len(lo) // 2, int(lo, 16), int(hi, 16)))
    for m in re.finditer(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", m.group(1)):
            c2u[int(src, 16)] = uni(dst)
    for m in re.finditer(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, rest in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", m.group(1)):
            a, b = int(lo, 16), int(hi, 16)
            if b - a > 0xFFFF:
                continue
            if rest.startswith(b"["):
                for i, d in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", rest)):
                    c2u[a + i] = uni(d)
            else:
                start = uni(rest[1:-1])
                if start:
                    for i in range(b - a + 1):
                        c2u[a + i] = start[:-1] + chr(ord(start[-1]) + i)
    return c2u, spaces


def _std_encoding_table(name: str) -> dict[int, str]:
    codec = {"MacRomanEncoding": "mac_roman"}.get(name, "cp1252")
    t = {}
    for code in range(32, 256):
        try:
            t[code] = bytes([code]).decode(codec)
        except UnicodeDecodeError:
            pass
    return t


@dataclass
class FontInfo:
    key: tuple                       # object identity (objgen) or ("direct", id)
    obj: pikepdf.Dictionary
    name: str
    subtype: str
    code_len: int = 1
    codespace: list = field(default_factory=list)
    code2uni: dict = field(default_factory=dict)
    widths: dict = field(default_factory=dict)   # code -> width in text space units (em = 1)
    default_w: float = 0.5
    ascent: float = 0.9
    descent: float = -0.25
    std14: Optional[str] = None
    embedded: bool = False
    subset: bool = False
    bold: bool = False
    italic: bool = False
    serif: bool = False
    mono: bool = False
    used_codes: set = field(default_factory=set)  # codes the document draws with this font
    fallback: bool = False                        # added by the editor as a per-character stand-in

    # -- decoding ---------------------------------------------------------------
    def decode(self, data: bytes) -> list[tuple[int, int]]:
        """bytes -> [(code, nbytes)]"""
        out = []
        i = 0
        if self.code_len == 1 and not self.codespace:
            return [(b, 1) for b in data]
        while i < len(data):
            n = None
            for nb, lo, hi in self.codespace:
                if i + nb <= len(data):
                    c = int.from_bytes(data[i:i + nb], "big")
                    if lo <= c <= hi:
                        n = nb
                        break
            n = n or self.code_len
            out.append((int.from_bytes(data[i:i + n], "big"), n))
            i += n
        return out

    def char(self, code: int) -> str:
        return self.code2uni.get(code, "")

    def width(self, code: int) -> float:
        return self.widths.get(code, self.default_w)

    # -- encoding new text with this font ------------------------------------------
    def uni2code(self) -> dict[str, int]:
        cache = self.__dict__.get("_u2c")
        if cache is None:
            cache = {}
            for code, u in sorted(self.code2uni.items()):
                if len(u) == 1 and u not in cache:
                    cache[u] = code
            self.__dict__["_u2c"] = cache
        return cache

    def can_draw(self, ch: str) -> bool:
        code = self.uni2code().get(ch)
        if code is None:
            return False
        if self.subset or (self.embedded and self.subtype == "Type0"):
            return code in self.used_codes or ch == " " and code in self.widths
        return True

    def encode(self, text: str) -> bytes:
        u2c = self.uni2code()
        return b"".join(u2c[c].to_bytes(self.code_len, "big") for c in text)


def _widths_simple(font, info: FontInfo) -> None:
    fc = font.get("/FirstChar")
    ws = font.get("/Widths")
    if fc is not None and ws is not None:
        first = int(fc)
        scale = 1 / 1000.0
        if info.subtype == "Type3":
            fm = font.get("/FontMatrix")
            scale = _num(fm[0], 0.001) if fm is not None else 0.001
        for i, w in enumerate(ws):
            info.widths[first + i] = _num(w) * scale
        desc = font.get("/FontDescriptor")
        info.default_w = _num(desc.get("/MissingWidth", 0)) / 1000 if desc is not None else 0.0
        return
    if info.std14:
        from reportlab.pdfbase import pdfmetrics
        for code, u in info.code2uni.items():
            try:
                info.widths[code] = pdfmetrics.stringWidth(u, info.std14, 1000) / 1000.0
            except Exception:
                pass
        info.default_w = 0.5


def _widths_cid(desc_font, info: FontInfo) -> None:
    info.default_w = _num(desc_font.get("/DW", 1000)) / 1000.0
    w = desc_font.get("/W")
    if w is None:
        return
    items = list(w)
    i = 0
    while i < len(items):
        c = int(items[i])
        if i + 1 < len(items) and isinstance(items[i + 1], pikepdf.Array):
            for k, v in enumerate(items[i + 1]):
                info.widths[c + k] = _num(v) / 1000.0
            i += 2
        elif i + 2 < len(items):
            c2, v = int(items[i + 1]), _num(items[i + 2]) / 1000.0
            if c2 - c <= 0xFFFF:
                for cc in range(c, c2 + 1):
                    info.widths[cc] = v
            i += 3
        else:
            break


def load_font(font: pikepdf.Dictionary) -> FontInfo:
    subtype = str(font.get("/Subtype", "/Type1")).lstrip("/")
    base = str(font.get("/BaseFont", "/Unknown")) if "/BaseFont" in font else \
        str(font.get("/Name", "/Type3Font"))
    name = clean_name(base)
    key = font.objgen if font.is_indirect else ("direct", id(font))
    info = FontInfo(key=key, obj=font, name=name, subtype=subtype)
    info.fallback = bool(font.get("/SPEFallback", False))
    info.subset = clean_name(base) != base.lstrip("/").replace("#20", " ")
    desc = font.get("/FontDescriptor")
    desc_font = None
    if subtype == "Type0":
        dfs = font.get("/DescendantFonts")
        desc_font = dfs[0] if dfs is not None and len(dfs) else None
        if desc_font is not None:
            desc = desc_font.get("/FontDescriptor")
        info.code_len = 2
        enc = font.get("/Encoding")
        if isinstance(enc, pikepdf.Stream):
            _, spaces = _parse_tounicode(enc.read_bytes())
            info.codespace = sorted(spaces, key=lambda s: s[0])
    if desc is not None:
        info.embedded = any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3"))
        asc, dsc = _num(desc.get("/Ascent", 0)), _num(desc.get("/Descent", 0))
        if asc > 0:
            info.ascent = asc / 1000.0
        if dsc < 0:
            info.descent = dsc / 1000.0
        flags = int(_num(desc.get("/Flags", 0)))
        info.mono = bool(flags & 1)
        info.serif = bool(flags & 2)
        info.italic = bool(flags & 64) or _num(desc.get("/ItalicAngle", 0)) != 0
        info.bold = bool(flags & (1 << 18)) or _num(desc.get("/FontWeight", 400)) >= 600
    low = name.lower()
    info.bold = info.bold or any(k in low for k in ("bold", "black", "heavy", "semibold"))
    info.italic = info.italic or "italic" in low or "oblique" in low
    info.mono = info.mono or any(k in low for k in ("courier", "mono", "consol"))
    if "sans" in low:
        info.serif = False
    elif any(k in low for k in ("times", "georgia", "garamond", "serif", "cambria", "roman")):
        info.serif = True
    if subtype != "Type0" and not info.embedded:
        info.std14 = std14_for(base)
        if info.std14:
            from reportlab.pdfbase import pdfmetrics
            face = pdfmetrics.getFont(info.std14).face
            info.ascent, info.descent = face.ascent / 1000.0, face.descent / 1000.0
    # Unicode mapping
    tu = font.get("/ToUnicode")
    if isinstance(tu, pikepdf.Stream):
        try:
            c2u, spaces = _parse_tounicode(tu.read_bytes())
            info.code2uni = c2u
            if subtype == "Type0" and spaces and not info.codespace:
                info.codespace = sorted(spaces, key=lambda s: s[0])
                info.code_len = max(s[0] for s in spaces)
        except Exception:
            pass
    if not info.code2uni and subtype != "Type0":
        enc = font.get("/Encoding")
        base_enc, diffs = "WinAnsiEncoding", None
        if isinstance(enc, pikepdf.Name):
            base_enc = str(enc).lstrip("/")
        elif isinstance(enc, pikepdf.Dictionary):
            if "/BaseEncoding" in enc:
                base_enc = str(enc.BaseEncoding).lstrip("/")
            diffs = enc.get("/Differences")
        info.code2uni = _std_encoding_table(base_enc)
        if diffs is not None:
            code = None
            for item in diffs:
                if isinstance(item, pikepdf.Name):
                    if code is not None:
                        u = glyph_to_uni(str(item).lstrip("/"))
                        if u:
                            info.code2uni[code] = u
                        code += 1
                else:
                    code = int(item)
    if subtype == "Type0":
        if desc_font is not None:
            _widths_cid(desc_font, info)
    else:
        _widths_simple(font, info)
    return info
