"""MCP server (stdio): seamless PDF editing and 67 document tools for any
MCP client (Claude Desktop, Claude Code, Cursor, ...).

Run with `seamless-pdf mcp` or `seamless-pdf-mcp`. Documents are opened by
local path and kept in memory until saved with save_pdf.
"""
from __future__ import annotations

from typing import Any, Optional

try:
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError as e:  # pragma: no cover
    raise SystemExit("the MCP server needs the mcp package: pip install 'seamless-pdf[mcp]'") from e

from . import __version__
from .engine import EditError
from .ops import OPS
from .workspace import Workspace

INSTRUCTIONS = """\
Seamless PDF editing. Edits reuse the document's own fonts, so changed text is
indistinguishable from the original.

Workflow: open_pdf(path) -> doc_id. Find what to change with find_text or
get_page_objects (they return object ids and boxes), change it with edit_pdf,
check it with view_page, then save_pdf. One edit_pdf call is one undo step.

Pages: edit_pdf, get_page_objects and view_page take 0-based page indexes.
Document tools (run_pdf_tool) take 1-based ranges like "1-3,5" or "all".
Coordinates are PDF points, origin at the top-left of the unrotated page.

For whole-document jobs (merge, split, compress, convert, protect, sign,
redact, OCR, invoices, ...) call list_pdf_tools, then run_pdf_tool. Tools
marked in_place change the open document; others write new files to out_dir.
"""

ws = Workspace()
server = MCPServer("seamless-pdf", title="Seamless PDF", version=__version__, instructions=INSTRUCTIONS)


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except EditError as e:
        raise ToolError(str(e))


def _ops_summary() -> str:
    lines = []
    for o in OPS.values():
        if not o.mutating:
            continue
        params = ", ".join(f"{k}{'' if k in o.required else '?'}" for k in o.properties)
        lines.append(f"- {o.name}({params}): {o.description.split('. ')[0].rstrip('.')}.")
    return "\n".join(lines)


@server.tool()
def open_pdf(path: str) -> dict:
    """Open a PDF from a local path for editing. Returns doc_id plus an
    overview (pages, sizes, scanned pages, form fields)."""
    def go():
        fid = ws.resolve(path)
        if not ws.store.is_doc(fid):
            raise EditError(f"{path} is not an editable PDF (password protected? run unlock_pdf first)")
        return {"doc_id": fid, **ws.op(fid, "get_document_info")["result"]}
    return _call(go)


@server.tool()
def list_open_files() -> dict:
    """Documents and files in this session (opened PDFs and tool outputs) with ids and paths."""
    return {"files": ws.files()}


@server.tool()
def get_page_objects(doc_id: str, page: int, kinds: Optional[list[str]] = None) -> dict:
    """Every editable object on a page (0-based) with id, kind, bbox and style:
    text segments, images, annotations, form fields. kinds filters by
    text/image/annotation/field."""
    return _call(lambda: ws.op(doc_id, "get_page_objects", {"page": page, "kinds": kinds})["result"])


@server.tool()
def find_text(doc_id: str, query: str, match_case: bool = False) -> dict:
    """Find text segments containing query; returns ids to pass to edit_pdf."""
    return _call(lambda: ws.op(doc_id, "find_text", {"query": query, "match_case": match_case})["result"])


@server.tool()
def get_text(doc_id: str, pages: Optional[list[int]] = None) -> dict:
    """Plain text of the document or of the given 0-based pages (no ids)."""
    return _call(lambda: ws.op(doc_id, "get_document_text", {"pages": pages})["result"])


@server.tool()
def list_form_fields(doc_id: str) -> dict:
    """All form fields with ids, names, types, values and options."""
    return _call(lambda: ws.op(doc_id, "list_form_fields")["result"])


@server.tool()
def view_page(doc_id: str, page: int, clip: Optional[list[float]] = None, scale: float = 1.5) -> Image:
    """Render a page (0-based) to an image to check layout or an edit. clip
    [x0,y0,x1,y1] zooms into an area."""
    def go():
        return Image(data=ws.doc(ws.resolve(doc_id)).render_page(page, scale, clip), format="png")
    return _call(go)


@server.tool()
def list_operations() -> dict:
    """Full JSON schemas of the editing operations accepted by edit_pdf."""
    return {"operations": ws.operations()}


@server.tool(description="Apply one or more editing operations to an open PDF, atomically "
             "(all succeed or none apply; one undo step). Each step is "
             '{"op": name, "args": {...}}. Ids renamed by an earlier step are followed. '
             "Image operations accept image_path (a local file) instead of asset_id.\n"
             "Operations (? = optional; list_operations has full schemas):\n" + _ops_summary())
def edit_pdf(doc_id: str, steps: list[dict[str, Any]]) -> dict:
    def go():
        bad = [s.get("op") for s in steps if isinstance(s, dict) and s.get("op") in ("undo", "redo")]
        if bad:
            raise EditError("undo/redo are separate tools, not edit_pdf steps")
        fid = ws.resolve(doc_id)
        doc = ws.doc(fid)
        with doc.transaction("Agent edit") as tx:
            results = ws.script(fid, steps)
        return {"steps": [{"op": r["op"], "result": r.get("result"), "new_ids": r.get("new_ids")}
                          for r in results],
                "pages_changed": sorted(tx.pages), **doc.history_state()}
    return _call(go)


@server.tool()
def undo(doc_id: str) -> dict:
    """Undo the last edit_pdf call or in-place tool on this document."""
    return _call(lambda: ws.op(doc_id, "undo")["result"])


@server.tool()
def redo(doc_id: str) -> dict:
    """Redo the most recently undone change."""
    return _call(lambda: ws.op(doc_id, "redo")["result"])


@server.tool()
def save_pdf(doc_id: str, path: str) -> dict:
    """Save the current state of an open document (or any session file) to a local path."""
    return _call(lambda: {"saved": ws.save(doc_id, path)})


@server.tool()
def list_pdf_tools(category: Optional[str] = None) -> dict:
    """Catalog of document tools (merge, split, compress, convert, protect,
    sign, redact, OCR, compare, invoices, ...), with option schemas. Categories:
    Create, Edit, Organize, Optimize, Security, View & check, Convert to PDF,
    Convert from PDF, Invoices."""
    return {"tools": [{k: t[k] for k in ("name", "title", "category", "description", "inputs", "in_place",
                                         "options", "required", "unavailable")} for t in ws.tools(category)]}


@server.tool()
def run_pdf_tool(tool: str, inputs: Optional[list[str]] = None, options: Optional[dict[str, Any]] = None,
                 out_dir: Optional[str] = None) -> dict:
    """Run a document tool. inputs are local paths or ids from open_pdf /
    earlier outputs. New files are written to out_dir (default: next to the
    first input) and returned with their paths. in_place tools change the open
    document instead; save it with save_pdf."""
    return _call(lambda: ws.run_tool(tool, inputs, options, out_dir))


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
