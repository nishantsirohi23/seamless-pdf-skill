"""Tool registry + runner. Importing this package registers every tool."""
from __future__ import annotations

from typing import Optional

from ..engine import EditError
from .base import (ANY, CATEGORIES, FILES, NONE, PDF, PDF_PLUS, PDFS, TOOLS, Ctx, Input,
                   coerce_options)
from . import organize, edit, optimize, security, view, convert, create, invoice  # noqa: F401,E402


def find_tool(name: str):
    t = TOOLS.get(name)
    if t:
        return t
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    for tt in TOOLS.values():
        if key == tt.name or key in tt.aliases:
            return tt
    raise EditError(f"unknown tool {name!r}; call list_pdf_tools to see the catalog")


def catalog(category: Optional[str] = None) -> list[dict]:
    out = [t.public() for t in TOOLS.values()]
    if category:
        out = [t for t in out if t["category"].lower() == category.lower()]
    order = {c: i for i, c in enumerate(CATEGORIES)}
    return sorted(out, key=lambda t: (order.get(t["category"], 99), t["title"]))


def _load(store, fid: str) -> Input:
    try:
        if store.is_doc(fid):
            doc = store.get(fid)
            return Input(fid, doc.name, doc.pdf_bytes()[1], "application/pdf", doc)
        item = store.get_file(fid)
        return Input(fid, item.name, item.data, item.mime)
    except KeyError:
        raise EditError(f"no workspace file with id {fid!r}; call list_files")


def run_tool(store, name: str, input_ids: Optional[list] = None,
             options: Optional[dict] = None, group: Optional[str] = None) -> dict:
    t = find_tool(name)
    why = t.availability()
    if why:
        raise EditError(f"{t.title} is unavailable on this machine: it {why}")
    opts = coerce_options(t, options or {})
    inputs = [_load(store, i) for i in (input_ids or [])]

    def is_pdf(i: Input) -> bool:
        return i.doc is not None or i.ext == "pdf" or i.data[:5] == b"%PDF-"

    if t.inputs == PDF and (len(inputs) != 1 or not is_pdf(inputs[0])):
        raise EditError(f"{t.title} takes exactly one PDF")
    if t.inputs == PDFS and (not inputs or not all(is_pdf(i) for i in inputs)):
        raise EditError(f"{t.title} takes one or more PDFs")
    if t.inputs == PDF_PLUS and (len(inputs) < 2 or not is_pdf(inputs[0])):
        raise EditError(f"{t.title} takes a PDF plus at least one more file")
    if t.inputs == FILES and not inputs:
        raise EditError(f"{t.title} needs at least one input file")
    if t.inputs == NONE:
        inputs = []

    ctx = Ctx(store, inputs, source=f"tool:{t.name}")
    out = {"tool": t.name, "title": t.title}
    try:
        if t.in_place:
            doc = inputs[0].doc
            if doc is None:
                raise EditError(f"{inputs[0].name} is not an editable PDF (unlock or convert it first)")
            pages_before = doc.page_count
            with doc.transaction(t.title, group=group) as tx:
                t.fn(ctx, doc, **opts)
                pages = sorted(tx.pages)
            out.update({"doc_id": inputs[0].id, "pages_changed": pages,
                        "new_ids": dict(tx.result_ids), **doc.history_state(),
                        "structure_changed": ctx.structure_changed
                        or doc.page_count != pages_before})
        else:
            t.fn(ctx, **opts)
    finally:
        ctx.cleanup()
    out["outputs"] = ctx.outputs
    out["result"] = ctx.result
    return out
