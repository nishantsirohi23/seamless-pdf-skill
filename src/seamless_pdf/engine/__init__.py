"""PDF editing engine (pikepdf + pypdfium2; no AGPL code).

`PdfDoc` composes the per-object-type operations on top of `DocCore`.
"""
from .annots import AnnotOps
from .core import DocCore
from .forms import FormOps
from .images import ImageOps
from .ocr import OcrOps
from .text import TextOps
from .util import EditError


class PdfDoc(TextOps, ImageOps, AnnotOps, FormOps, OcrOps, DocCore):
    """A loaded PDF plus every editing operation the UI and agent use."""


__all__ = ["PdfDoc", "EditError"]
