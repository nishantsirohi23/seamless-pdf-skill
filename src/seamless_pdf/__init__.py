"""Seamless PDF: edit PDFs so nobody can tell, plus 67 document tools.

Library use:

    from seamless_pdf import Workspace
    ws = Workspace()
    doc = ws.add("contract.pdf")
    seg = ws.op(doc, "find_text", {"query": "March 3"})["result"]["matches"][0]
    ws.op(doc, "edit_text", {"id": seg["id"], "text": "Due: April 30"})
    ws.save(doc, "contract-edited.pdf")
    ws.run_tool("compress_pdf", ["contract-edited.pdf"], {"level": "high"})
"""
__version__ = "1.0.0"

from .engine import EditError, PdfDoc  # noqa: E402
from .workspace import Workspace  # noqa: E402

__all__ = ["EditError", "PdfDoc", "Workspace", "__version__"]
