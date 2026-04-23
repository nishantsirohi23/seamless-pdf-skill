"""One registry of document operations, shared by the HTTP API and the agent.

Every capability the UI has, the agent has too (and vice versa): each entry
is a name, a JSON schema (sent to Claude as a strict tool), and a handler.
Mutating operations run in a document transaction, so they are atomic,
undoable, and report which pages changed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .engine import EditError, PdfDoc


@dataclass
class Op:
    name: str
    description: str
    properties: dict
    required: list
    fn: Callable
    mutating: bool = False
    label: str = ""
    agent: bool = True        # exposed to Claude as a tool
    history: bool = False     # undo / redo: manage history themselves

    @property
    def optional_count(self) -> int:
        return len(set(self.properties) - set(self.required))

    def tool(self, strict: bool) -> dict:
        t = {"name": self.name, "description": self.description,
             "input_schema": {"type": "object", "additionalProperties": False,
                              "properties": self.properties,
                              "required": self.required}}
        if strict:
            t["strict"] = True
        return t


# -- schema fragments -------------------------------------------------------------
PAGE = {"type": "integer", "description": "0-based page index."}
ID = {"type": "string", "description": "Object id from get_page_objects / find_text."}
IDS = {"type": "array", "items": {"type": "string"}, "description": "Object ids."}
RECT = {"type": "array", "items": {"type": "number"},
        "description": "[x0, y0, x1, y1] in PDF points, origin top-left of the page."}
POINT = {"type": "array", "items": {"type": "number"}, "description": "[x, y] in PDF points."}
COLOR = {"type": "string", "description": "Colour as #rrggbb."}
FONT = {"type": "string", "enum": ["helvetica", "times", "courier"],
        "description": "Switch to a standard font family. Omit to keep the document's own font."}

OPS: dict[str, Op] = {}


def _op(name, description, properties=None, required=None, mutating=False, label="",
        agent=True, history=False):
    def deco(fn):
        OPS[name] = Op(name, description, properties or {}, required or [], fn,
                       mutating, label or name.replace("_", " ").capitalize(), agent, history)
        return fn
    return deco


# -- reading ----------------------------------------------------------------------
@_op("get_document_info",
     "Overview: page count and size, which pages are scanned images, whether "
     "there is a form, uploaded image assets, and undo state. Call first when "
     "you know nothing about the document.")
def _info(doc: PdfDoc):
    return doc.summary()


@_op("get_page_objects",
     "List every editable object on a page with its id, kind, bbox and style: "
     "text segments (text, font, size, colour), images, annotations and form "
     "fields. Use this to find ids and exact coordinates before editing, moving "
     "or inserting near something.",
     {"page": PAGE,
      "kinds": {"type": "array", "items": {"type": "string",
                                           "enum": ["text", "image", "annotation", "field"]},
                "description": "Only these kinds (default: all)."}},
     ["page"])
def _objects(doc: PdfDoc, page, kinds=None):
    res = doc.page_objects(page)
    if kinds:
        res["objects"] = [o for o in res["objects"] if o["kind"] in kinds]
    return res


@_op("find_text",
     "Find text segments containing a string (case-insensitive unless "
     "match_case). Returns matching segments with ids, plus phrases that span "
     "several segments (edit each segment of those).",
     {"query": {"type": "string"}, "match_case": {"type": "boolean"}}, ["query"])
def _find(doc: PdfDoc, query, match_case=False):
    return doc.find_text(query, match_case)


@_op("get_document_text",
     "Plain text of the document (or of the given pages), for understanding "
     "content. Does not give ids; use find_text or get_page_objects for that.",
     {"pages": {"type": "array", "items": {"type": "integer"}}})
def _text(doc: PdfDoc, pages=None):
    return {"text": doc.document_text(pages)[:40000]}


@_op("list_form_fields", "All form fields in the document with ids, types, names, "
     "current values and options.")
def _fields(doc: PdfDoc):
    return doc.list_form_fields()


@_op("view_page",
     "Look at a rendered image of a page (optionally a clip rect, which zooms "
     "in). Use it to check layout before placing things and to verify your "
     "edits look right.",
     {"page": PAGE, "clip": RECT}, ["page"])
def _view(doc: PdfDoc, page, clip=None):
    return {"__image_png_b64__": doc.page_png_b64(page, clip),
            "note": f"page {page}" + (f" clip {clip}" if clip else "")}


# -- text -------------------------------------------------------------------------
@_op("edit_text",
     "Replace and/or restyle one text segment in place, seamlessly: same font "
     "program, size, colour, spacing and baseline unless you change them. "
     "anchor=auto keeps right-aligned/centred columns aligned. Works on scanned "
     "(OCR) words too.",
     {"id": ID,
      "text": {"type": "string", "description": "New text for the whole segment. Omit to keep."},
      "font_size": {"type": "number"}, "color": COLOR,
      "anchor": {"type": "string", "enum": ["auto", "left", "right", "center"]},
      "font": FONT, "bold": {"type": "boolean"}, "italic": {"type": "boolean"}},
     ["id"], mutating=True, label="Edit text")
def _edit(doc: PdfDoc, id, **kw):
    return doc.edit_text(id, **kw)


@_op("replace_all",
     "Replace every occurrence of a string inside text segments across the "
     "document (or given pages), seamlessly. Ideal for names, dates, amounts "
     "that repeat.",
     {"find": {"type": "string"}, "replace": {"type": "string"},
      "match_case": {"type": "boolean"},
      "pages": {"type": "array", "items": {"type": "integer"}}},
     ["find", "replace"], mutating=True, label="Replace all")
def _replace_all(doc: PdfDoc, find, replace, match_case=False, pages=None):
    return doc.replace_all(find, replace, match_case, pages)


@_op("insert_text",
     "Add new text. (x, y) is the baseline start of the first line in PDF "
     "points. By default it copies the font, size and colour of the nearest "
     "existing text (or of match_style_of) so it blends in. Use \\n for new "
     "lines; give width to word-wrap and align.",
     {"page": PAGE, "x": {"type": "number"}, "y": {"type": "number"},
      "text": {"type": "string"}, "font_size": {"type": "number"}, "color": COLOR,
      "font": FONT, "bold": {"type": "boolean"}, "italic": {"type": "boolean"},
      "width": {"type": "number", "description": "Wrap width in points."},
      "align": {"type": "string", "enum": ["left", "center", "right"]},
      "match_style_of": {"type": "string", "description": "Text id to copy style from."},
      "line_height": {"type": "number"}},
     ["page", "x", "y", "text"], mutating=True, label="Insert text")
def _insert_text(doc: PdfDoc, page, x, y, text, **kw):
    return doc.insert_text(page, x, y, text, **kw)


# -- any object ----------------------------------------------------------------------
@_op("delete_objects",
     "Delete objects of any kind (text segments, images, annotations, fields) "
     "by id. Text and images are removed without leaving a box.",
     {"ids": IDS}, ["ids"], mutating=True, label="Delete")
def _delete(doc: PdfDoc, ids):
    return doc.delete_objects(ids)


@_op("move_objects",
     "Move objects of any kind by (dx, dy) PDF points (positive dy = down). "
     "Text keeps its font; images keep their pixels; scanned words move their "
     "pixels. Returns new ids for moved text.",
     {"ids": IDS, "dx": {"type": "number"}, "dy": {"type": "number"}},
     ["ids", "dx", "dy"], mutating=True, label="Move")
def _move(doc: PdfDoc, ids, dx, dy):
    return doc.move_objects(ids, dx, dy)


@_op("set_object_rect",
     "Resize/reposition an image, annotation or form field to an exact rect.",
     {"id": ID, "rect": RECT}, ["id", "rect"], mutating=True, label="Resize")
def _set_rect(doc: PdfDoc, id, rect):
    return doc.set_object_rect(id, rect)


# -- images --------------------------------------------------------------------------
@_op("insert_image",
     "Place an uploaded image asset (see get_document_info assets) into rect "
     "on a page, keeping its aspect ratio.",
     {"page": PAGE, "asset_id": {"type": "string"}, "rect": RECT},
     ["page", "asset_id", "rect"], mutating=True, label="Insert image")
def _insert_image(doc: PdfDoc, page, asset_id, rect):
    return doc.insert_image(page, asset_id, rect)


@_op("replace_image",
     "Swap an image's picture for an uploaded asset, keeping its position.",
     {"id": ID, "asset_id": {"type": "string"}}, ["id", "asset_id"],
     mutating=True, label="Replace image")
def _replace_image(doc: PdfDoc, id, asset_id):
    return doc.replace_image(id, asset_id)


# -- annotations ---------------------------------------------------------------------
@_op("add_annotation",
     "Add an annotation. Text markup (highlight, underline, strikeout, "
     "squiggly) needs target_text (all matches on the page), ids (text "
     "segments) or rect (words inside it). note needs point; freetext, rect, "
     "circle need rect; line uses rect as [x1, y1, x2, y2].",
     {"page": PAGE,
      "kind": {"type": "string", "enum": ["highlight", "underline", "strikeout", "squiggly",
                                          "note", "freetext", "rect", "circle", "line"]},
      "rect": RECT, "point": POINT,
      "text": {"type": "string", "description": "Note/free-text content or comment."},
      "color": COLOR, "fill": COLOR, "target_text": {"type": "string"}, "ids": IDS,
      "font_size": {"type": "number"}, "opacity": {"type": "number"},
      "line_width": {"type": "number"}},
     ["page", "kind"], mutating=True, label="Add annotation")
def _add_annot(doc: PdfDoc, page, kind, **kw):
    return doc.add_annotation(page, kind, **kw)


@_op("update_annotation", "Change an annotation's text, colour or opacity.",
     {"id": ID, "text": {"type": "string"}, "color": COLOR, "opacity": {"type": "number"}},
     ["id"], mutating=True, label="Edit annotation")
def _update_annot(doc: PdfDoc, id, **kw):
    return doc.update_annotation(id, **kw)


# -- forms ---------------------------------------------------------------------------
@_op("set_field_value",
     "Fill a form field. Checkboxes/radios take \"true\"/\"false\"; choice "
     "fields take one of their options.",
     {"id": ID, "value": {"type": "string"}}, ["id", "value"],
     mutating=True, label="Fill field")
def _set_field(doc: PdfDoc, id, value):
    return doc.set_field_value(id, value)


@_op("add_form_field", "Create a fillable form field.",
     {"page": PAGE, "rect": RECT,
      "field_type": {"type": "string", "enum": ["text", "checkbox", "combobox", "listbox", "radio"]},
      "name": {"type": "string"}, "value": {"type": "string"},
      "options": {"type": "array", "items": {"type": "string"}},
      "font_size": {"type": "number"}},
     ["page", "rect", "field_type", "name"], mutating=True, label="Add field")
def _add_field(doc: PdfDoc, page, rect, field_type, name, **kw):
    return doc.add_form_field(page, rect, field_type, name, **kw)


@_op("flatten_forms",
     "Burn all form field values into the pages and remove the fields "
     "(not fillable afterwards). Only when the user asks to flatten.",
     mutating=True, label="Flatten form")
def _flatten(doc: PdfDoc):
    return doc.flatten_forms()


# -- scans ---------------------------------------------------------------------------
@_op("ocr_page",
     "Run OCR on a scanned page to make its words searchable and editable "
     "text objects. Needed before editing text on a scanned page.",
     {"page": PAGE, "language": {"type": "string",
                                 "description": "Tesseract language code(s), e.g. eng or eng+deu."}},
     ["page"], mutating=True, label="OCR page")
def _ocr(doc: PdfDoc, page, language="eng"):
    return doc.ocr_page(page, language or "eng")


# -- history -------------------------------------------------------------------------
@_op("undo", "Undo the most recent change.", history=True)
def _undo(doc: PdfDoc):
    return doc.undo()


@_op("redo", "Redo the most recently undone change.", history=True)
def _redo(doc: PdfDoc):
    return doc.redo()


# -- dispatch ------------------------------------------------------------------------
def run_op(doc: PdfDoc, name: str, args: Optional[dict] = None,
           group: Optional[str] = None) -> dict:
    """Execute an operation. Raises EditError for user-facing failures."""
    op = OPS.get(name)
    if op is None:
        raise EditError(f"unknown operation {name!r}")
    args = {k: v for k, v in (args or {}).items() if v is not None}
    unknown = set(args) - set(op.properties)
    if unknown:
        raise EditError(f"{name}: unexpected argument(s) {sorted(unknown)}")
    missing = [k for k in op.required if k not in args]
    if missing:
        raise EditError(f"{name}: missing argument(s) {missing}")
    if op.history:
        with doc.lock:
            result = op.fn(doc)
            return {"result": result, "pages_changed": result["pages_changed"],
                    "new_ids": {}, **doc.history_state()}
    if not op.mutating:
        with doc.lock:
            return {"result": op.fn(doc, **args)}
    with doc.transaction(op.label, group=group) as tx:
        result = op.fn(doc, **args)
    return {"result": result, "pages_changed": sorted(tx.pages),
            "new_ids": dict(tx.result_ids), **doc.history_state()}


# Strict tool use caps a request at 20 strict tools and 24 optional parameters
# in total. Give strictness to the tools with the fewest optional parameters
# until either budget runs out; the rest are validated by run_op instead.
MAX_STRICT_TOOLS = 20
MAX_STRICT_OPTIONAL = 24


def agent_tools() -> list[dict]:
    ops = [op for op in OPS.values() if op.agent]
    strict: set[str] = set()
    optional = 0
    for op in sorted(ops, key=lambda o: o.optional_count):
        if len(strict) < MAX_STRICT_TOOLS and optional + op.optional_count <= MAX_STRICT_OPTIONAL:
            strict.add(op.name)
            optional += op.optional_count
    return [op.tool(op.name in strict) for op in ops]
