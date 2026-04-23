"""Document tool framework.

A tool is a whole-document operation (merge, compress, convert, watermark,
sign, ...). Each declares:
  - what it takes: no files, one PDF, several PDFs, or arbitrary files
  - an option schema (JSON-schema subset) that drives both the UI form and
    the agent's arguments
  - whether it edits the open document *in place* (undoable, live in the
    editor) or produces new workspace files
  - external programs it needs (LibreOffice, Chrome, Ghostscript, ...)

The same registry backs `/api/tools` and the agent's `run_pdf_tool`.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

import io

import pikepdf

from ..engine import EditError, PdfDoc

CATEGORIES = ["Create", "Edit", "Organize", "Optimize", "Security", "View & check",
              "Convert to PDF", "Convert from PDF", "Invoices"]

# inputs: what a tool consumes
NONE, PDF, PDFS, FILES, PDF_PLUS, ANY = "none", "pdf", "pdfs", "files", "pdf+", "any"


@dataclass
class Tool:
    name: str
    title: str
    category: str
    description: str
    fn: Callable
    inputs: str = PDF
    options: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    in_place: bool = False
    requires: list = field(default_factory=list)
    accept: str = ""          # file-picker hint, e.g. ".docx,.pptx" or "image/*"
    aliases: list = field(default_factory=list)   # other names users use

    def availability(self) -> Optional[str]:
        for prog in self.requires:
            if not find_program(prog):
                return f"needs {PROGRAM_HINTS.get(prog, prog)}"
        return None

    def public(self) -> dict:
        return {"name": self.name, "title": self.title, "category": self.category,
                "description": self.description, "inputs": self.inputs,
                "options": self.options, "required": self.required,
                "in_place": self.in_place, "accept": self.accept,
                "aliases": self.aliases, "unavailable": self.availability()}


TOOLS: dict[str, Tool] = {}


def tool(name: str, title: str, category: str, description: str, *, inputs: str = PDF,
         options: Optional[dict] = None, required: Optional[list] = None,
         in_place: bool = False, requires: Optional[list] = None, accept: str = "",
         aliases: Optional[list] = None):
    def deco(fn):
        TOOLS[name] = Tool(name, title, category, description, fn, inputs, options or {},
                           required or [], in_place, requires or [], accept, aliases or [])
        return fn
    return deco


# -- external programs -------------------------------------------------------------
PROGRAM_HINTS = {
    "soffice": "LibreOffice (brew install --cask libreoffice)",
    "gs": "Ghostscript (brew install ghostscript)",
    "chrome": "Google Chrome or Chromium",
    "tesseract": "Tesseract (brew install tesseract)",
    "pandoc": "pandoc (brew install pandoc)",
}
_CHROME_PATHS = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
_EXTRA_BIN = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/anaconda3/bin",
              "/Applications/LibreOffice.app/Contents/MacOS"]


def find_program(name: str) -> Optional[str]:
    if name == "chrome":
        for n in ("google-chrome", "chromium", "chromium-browser", "chrome"):
            p = shutil.which(n)
            if p:
                return p
        return next((p for p in _CHROME_PATHS if os.path.exists(p)), None)
    p = shutil.which(name)
    if p:
        return p
    for d in _EXTRA_BIN:
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return cand
    return None


# -- inputs / outputs ----------------------------------------------------------------
@dataclass
class Input:
    id: str
    name: str
    data: bytes
    mime: str
    doc: Optional[PdfDoc] = None

    @property
    def ext(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    @property
    def stem(self) -> str:
        return self.name.rsplit(".", 1)[0] if "." in self.name else self.name

    def open_pdf(self) -> pikepdf.Pdf:
        """A fresh, independent copy of this PDF (never the live document)."""
        try:
            return pikepdf.open(io.BytesIO(self.data))
        except pikepdf.PasswordError:
            raise EditError(f"{self.name} is password protected; unlock it first")
        except Exception as e:
            raise EditError(f"{self.name} is not a readable PDF: {e}")

    def engine(self) -> PdfDoc:
        """This PDF as an engine document (glyph model, rendering)."""
        return PdfDoc(self.data, self.name)


class Ctx:
    def __init__(self, store, inputs: list[Input], source: str):
        self.store = store
        self.inputs = inputs
        self.source = source
        self.outputs: list[dict] = []
        self.result: dict = {}
        self.structure_changed = False
        self._tmp: Optional[str] = None

    def tmpdir(self) -> str:
        if self._tmp is None:
            self._tmp = tempfile.mkdtemp(prefix="pdftool-")
        return self._tmp

    def cleanup(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def pdf(self, i: int = 0) -> pikepdf.Pdf:
        return self.inputs[i].open_pdf()

    def add_pdf(self, doc, name: str, raw: bool = False) -> dict:
        """Add a PDF output. raw=True keeps the exact bytes (see Store.add_any)."""
        data = doc if isinstance(doc, (bytes, bytearray)) else pdf_bytes(doc)
        fid = self.store.add_any(bytes(data), _pdf_name(name), source=self.source,
                                 mime="application/pdf", raw=raw)
        info = self.store.info(fid)
        self.outputs.append(info)
        return info

    def add_file(self, data: bytes, name: str, mime: Optional[str] = None,
                 raw: bool = False) -> dict:
        fid = self.store.add_any(data, name, source=self.source, mime=mime, raw=raw)
        info = self.store.info(fid)
        self.outputs.append(info)
        return info

    def add_bundle(self, files: list[tuple[str, bytes]], zip_name: str) -> None:
        """Add files individually, plus a zip when there are several."""
        for name, data in files:
            self.add_file(data, name)
        if len(files) > 1:
            import io
            import zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for name, data in files:
                    z.writestr(name, data)
            self.add_file(buf.getvalue(), zip_name, "application/zip")


def pdf_bytes(pdf: pikepdf.Pdf) -> bytes:
    buf = io.BytesIO()
    pdf.save(buf, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    return buf.getvalue()


def _pdf_name(name: str) -> str:
    return name if name.lower().endswith(".pdf") else name + ".pdf"


# -- option handling -------------------------------------------------------------------
def coerce_options(t: Tool, options: dict) -> dict:
    out = {}
    props = t.options
    for k, v in (options or {}).items():
        if k not in props:
            raise EditError(f"{t.name}: unknown option {k!r}; valid: {sorted(props)}")
        if v is None or v == "":
            continue
        out[k] = _coerce(k, props[k], v)
    for k, spec in props.items():
        if k not in out and "default" in spec:
            out[k] = spec["default"]
    missing = [k for k in t.required if k not in out]
    if missing:
        raise EditError(f"{t.name}: missing option(s) {missing}")
    return out


def _coerce(key: str, spec: dict, v):
    typ = spec.get("type", "string")
    try:
        if typ == "integer":
            v = int(float(v))
        elif typ == "number":
            v = float(v)
        elif typ == "boolean":
            v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
        elif typ == "string":
            v = str(v)
        elif typ == "array":
            if isinstance(v, str):
                v = [s.strip() for s in v.split(",") if s.strip()]
            item = spec.get("items", {})
            v = [_coerce(key, item, x) if item.get("type") != "object" else x for x in v]
    except (TypeError, ValueError):
        raise EditError(f"option {key!r} must be {typ}, got {v!r}")
    if "enum" in spec and v not in spec["enum"]:
        raise EditError(f"option {key!r} must be one of {spec['enum']}, got {v!r}")
    return v


# -- page ranges ---------------------------------------------------------------------------
def parse_pages(spec, n: int) -> list[int]:
    """1-based user page spec -> sorted 0-based indices.

    Accepts: "all", "odd", "even", "last", "1-3,5", "4-" (to end), "-2"
    (first two), "last-2" (third from last). Lists of ints work too.
    """
    if spec is None or spec == "" or (isinstance(spec, str) and spec.strip().lower() == "all"):
        return list(range(n))
    if isinstance(spec, (list, tuple)):
        spec = ",".join(str(x) for x in spec)
    spec = str(spec).strip().lower()
    if spec == "odd":
        return list(range(0, n, 2))
    if spec == "even":
        return list(range(1, n, 2))
    out: list[int] = []

    def num(tok: str, default: int) -> int:
        tok = tok.strip()
        if not tok:
            return default
        m = re.fullmatch(r"last(?:-(\d+))?", tok)
        if m:
            return n - int(m.group(1) or 0)
        return int(tok)

    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part and not re.fullmatch(r"last-\d+", part):
                a, b = part.split("-", 1) if not part.startswith("last") else (part, part)
                lo, hi = num(a, 1), num(b, n)
            else:
                lo = hi = num(part, 1)
        except ValueError:
            raise EditError(f"bad page range {part!r}")
        if lo > hi:
            lo, hi = hi, lo
        for p in range(max(1, lo), min(n, hi) + 1):
            out.append(p - 1)
    if not out:
        raise EditError(f"page range {spec!r} selects no pages (document has {n})")
    return sorted(set(out))


def parse_order(spec: str, n: int) -> list[int]:
    """Like parse_pages but keeps order and duplicates ("3,1,2", "reverse")."""
    s = str(spec).strip().lower()
    if s == "reverse":
        return list(range(n - 1, -1, -1))
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a or 1), int(b or n)
            rng = range(lo, hi + 1) if lo <= hi else range(lo, hi - 1, -1)
            out.extend(p - 1 for p in rng)
        else:
            out.append(int(part) - 1)
    bad = [p + 1 for p in out if not 0 <= p < n]
    if bad:
        raise EditError(f"pages {bad} do not exist (document has {n})")
    return out


PAPER = {"a3": (842, 1191), "a4": (595, 842), "a5": (420, 595), "a6": (298, 420),
         "letter": (612, 792), "legal": (612, 1008), "tabloid": (792, 1224),
         "b5": (499, 709), "executive": (522, 756)}


def paper(size: str, orientation: str = "portrait") -> tuple[float, float]:
    s = (size or "a4").strip().lower()
    if s in PAPER:
        w, h = PAPER[s]
    else:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|in|pt)?", s)
        if not m:
            raise EditError(f"unknown paper size {size!r}; use A4, Letter, ... or e.g. 210x297mm")
        k = {"mm": 72 / 25.4, "in": 72.0, "pt": 1.0, None: 72 / 25.4}[m.group(3)]
        w, h = float(m.group(1)) * k, float(m.group(2)) * k
    if orientation == "landscape" and h > w or orientation == "portrait" and w > h:
        w, h = h, w
    return w, h


PAPER_ENUM = ["A4", "Letter", "Legal", "A3", "A5", "A6", "B5", "Tabloid", "Executive"]
PAGES_OPT = {"type": "string", "description": "Pages, 1-based: all, 1-3,5, odd, even, last",
             "default": "all", "format": "pages"}
PAGES_REQ = {k: v for k, v in PAGES_OPT.items() if k != "default"}
FILE_OPT = {"type": "string", "format": "file", "description": "Workspace file id."}
COLOR_OPT = {"type": "string", "format": "color", "default": "#000000"}
