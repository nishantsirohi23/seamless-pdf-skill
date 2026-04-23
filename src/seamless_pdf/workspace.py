"""File-path front end to the engine, shared by the CLI and the MCP server.

A Workspace holds open documents in memory. PDFs are opened by path and get
a short id; edits go through the same operation registry the editor uses
(atomic, undoable). Document tools read workspace files and write their
results to an output folder on disk.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .engine import EditError, PdfDoc
from .ops import OPS, run_op
from .store import Store
from .tools import catalog, find_tool, run_tool


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "output")
    return re.sub(r"[^\w.\- ]+", "_", name).strip() or "output"


def _unique(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{stem}-{i}{ext}"):
        i += 1
    return f"{stem}-{i}{ext}"


def _page_count(path: str) -> Optional[int]:
    if not path.lower().endswith(".pdf"):
        return None
    try:
        import pikepdf
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except Exception:          # encrypted outputs cannot be counted without the password
        return None


class Workspace:
    def __init__(self) -> None:
        self.store = Store()
        self.paths: dict[str, str] = {}      # workspace id -> source path

    # -- files -------------------------------------------------------------------------
    def add(self, path: str) -> str:
        """Add a file from disk. PDFs become editable documents."""
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise EditError(f"no such file: {path}")
        with open(path, "rb") as fh:
            data = fh.read()
        fid = self.store.add_any(data, os.path.basename(path))
        self.paths[fid] = path
        return fid

    def doc(self, doc_id: str) -> PdfDoc:
        try:
            return self.store.get(doc_id)
        except KeyError:
            raise EditError(f"no open document with id {doc_id!r}; open it first")

    def resolve(self, ref: str) -> str:
        """A workspace id, or a path (added on first use)."""
        if ref in self.paths or self.store.is_doc(ref):
            return ref
        try:
            self.store.get_file(ref)
            return ref
        except KeyError:
            pass
        path = os.path.abspath(os.path.expanduser(ref))
        for fid, p in self.paths.items():
            if p == path:
                return fid
        return self.add(path)

    def files(self) -> list[dict]:
        return [{**f, "path": self.paths.get(f["id"])} for f in self.store.list_files()]

    def save(self, ref: str, path: str) -> str:
        """Write a workspace item (current state for documents) to `path`."""
        fid = self.resolve(ref)
        path = os.path.abspath(os.path.expanduser(path))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if self.store.is_doc(fid):
            data = self.store.get(fid).to_bytes()
        else:
            data = self.store.get_file(fid).data
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    # -- editing ---------------------------------------------------------------------------
    def op(self, ref: str, name: str, args: Optional[dict] = None) -> dict:
        """Run one editing operation (see `operations()`). Image operations
        also accept `image_path` instead of an uploaded `asset_id`."""
        fid = self.resolve(ref)
        doc = self.doc(fid)
        args = dict(args or {})
        if "image_path" in args:
            p = os.path.abspath(os.path.expanduser(args.pop("image_path")))
            with open(p, "rb") as fh:
                args["asset_id"] = doc.add_asset(fh.read(), os.path.basename(p)).id
        return run_op(doc, name, args)

    def script(self, ref: str, steps: list[dict]) -> list[dict]:
        """Run several operations in order. Ids renamed by an earlier step
        (an edited segment gets a new id) are followed automatically."""
        renamed: dict[str, str] = {}

        def remap(v):
            if isinstance(v, str):
                while v in renamed:
                    v = renamed[v]
                return v
            if isinstance(v, list):
                return [remap(x) for x in v]
            return v
        results = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict) or "op" not in step:
                raise EditError(f"step {i + 1}: expected {{\"op\": name, \"args\": {{...}}}}")
            args = {k: remap(v) if k in ("id", "ids") else v for k, v in (step.get("args") or {}).items()}
            try:
                res = self.op(ref, step["op"], args)
            except EditError as e:
                raise EditError(f"step {i + 1} ({step['op']}): {e}")
            for old, new in (res.get("new_ids") or {}).items():
                if new and new != old:
                    renamed[old] = new
            results.append({"op": step["op"], **res})
        return results

    @staticmethod
    def operations() -> list[dict]:
        return [{"name": o.name, "description": o.description, "mutating": o.mutating,
                 "parameters": {"type": "object", "properties": o.properties, "required": o.required}}
                for o in OPS.values() if o.agent or o.history]

    # -- document tools ------------------------------------------------------------------
    @staticmethod
    def tools(category: Optional[str] = None) -> list[dict]:
        return catalog(category)

    @staticmethod
    def tool_info(name: str) -> dict:
        return find_tool(name).public()

    def run_tool(self, name: str, inputs: Optional[list[str]] = None, options: Optional[dict] = None,
                 out_dir: Optional[str] = None) -> dict:
        """Run a document tool. Inputs are paths or workspace ids. New files
        are written to `out_dir` (default: next to the first input, or the
        current folder). In-place tools change the open document; save it
        with `save`."""
        t = find_tool(name)
        ids = [self.resolve(r) for r in (inputs or [])]
        res = run_tool(self.store, t.name, ids, options or {})
        if out_dir is None:
            first = self.paths.get(ids[0]) if ids else None
            out_dir = os.path.dirname(first) if first else os.getcwd()
        out_dir = os.path.abspath(os.path.expanduser(out_dir))
        saved = []
        for o in res.get("outputs", []):
            path = _unique(os.path.join(out_dir, _safe_name(o["name"])))
            self.save(o["id"], path)
            self.paths[o["id"]] = path
            saved.append({"id": o["id"], "name": o["name"], "path": path, "size": os.path.getsize(path),
                          "pages": o.get("pages") or _page_count(path)})
        res["outputs"] = saved
        return res
