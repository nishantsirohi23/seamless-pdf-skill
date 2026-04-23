"""In-memory workspace (single-process local server).

Every file the user uploads or a tool produces lives here. Openable PDFs are
held as editable `PdfDoc`s (with undo history); everything else, including
password-protected PDFs, is held as raw bytes until a tool converts it.
"""
from __future__ import annotations

import mimetypes
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .engine import EditError, PdfDoc


@dataclass
class FileItem:
    id: str
    name: str
    data: bytes
    mime: str
    source: str = "upload"
    created: float = field(default_factory=time.time)


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def is_locked_pdf(data: bytes) -> bool:
    import io
    import pikepdf
    try:
        pikepdf.open(io.BytesIO(data)).close()
        return False
    except pikepdf.PasswordError:
        return True
    except Exception:
        return False


class Store:
    def __init__(self) -> None:
        self._docs: dict[str, PdfDoc] = {}
        self._files: dict[str, FileItem] = {}
        self._meta: dict[str, dict] = {}       # id -> {source, created}
        self._convos: dict[str, list] = {}
        self._agent_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    # -- adding -----------------------------------------------------------------
    def add(self, data: bytes, name: str, source: str = "upload") -> str:
        """Open a PDF as an editable document. Raises on anything unopenable."""
        doc = PdfDoc(data, name)
        doc_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._docs[doc_id] = doc
            self._meta[doc_id] = {"source": source, "created": time.time()}
            self._convos[doc_id] = []
            self._agent_locks[doc_id] = threading.Lock()
        return doc_id

    def add_any(self, data: bytes, name: str, source: str = "upload",
                mime: Optional[str] = None, raw: bool = False) -> str:
        """PDFs that open become documents; everything else is stored raw.

        raw=True keeps a PDF's exact bytes (encryption, signatures,
        linearization, PDF/A or compression would not survive a re-save);
        `open_for_editing` turns it into an editable document on request.
        """
        if not raw and (_ext(name) == "pdf" or data[:5] == b"%PDF-"):
            if not is_locked_pdf(data):
                try:
                    return self.add(data, name, source)
                except EditError:
                    pass
        fid = uuid.uuid4().hex[:12]
        with self._lock:
            self._files[fid] = FileItem(fid, name, data, mime or guess_mime(name), source)
            self._meta[fid] = {"source": source, "created": time.time()}
        return fid

    def open_for_editing(self, fid: str) -> str:
        """Make an editable document from a raw PDF file (returns its id)."""
        if self.is_doc(fid):
            return fid
        item = self.get_file(fid)
        if is_locked_pdf(item.data):
            raise EditError(f"{item.name} is password protected; unlock it first")
        return self.add(item.data, item.name, source=f"opened:{fid}")

    # -- reading ----------------------------------------------------------------
    def get(self, doc_id: str) -> PdfDoc:
        with self._lock:
            doc = self._docs.get(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        return doc

    def is_doc(self, fid: str) -> bool:
        with self._lock:
            return fid in self._docs

    def get_file(self, fid: str) -> FileItem:
        with self._lock:
            item = self._files.get(fid)
        if item is None:
            raise KeyError(fid)
        return item

    def file_bytes(self, fid: str) -> tuple[str, bytes, str]:
        """(name, current bytes, mime) for any workspace item."""
        if self.is_doc(fid):
            doc = self.get(fid)
            return doc.name, doc.pdf_bytes()[1], "application/pdf"
        item = self.get_file(fid)
        return item.name, item.data, item.mime

    def info(self, fid: str) -> dict:
        meta = self._meta.get(fid, {})
        if self.is_doc(fid):
            doc = self.get(fid)
            return {"id": fid, "name": doc.name, "kind": "pdf", "editable": True,
                    "pages": doc.page_count, "size": len(doc.pdf_bytes()[1]),
                    "mime": "application/pdf", **meta}
        item = self.get_file(fid)
        kind = _ext(item.name) or "file"
        d = {"id": fid, "name": item.name, "kind": kind, "editable": False,
             "size": len(item.data), "mime": item.mime, **meta}
        if kind == "pdf":
            d["locked"] = is_locked_pdf(item.data)
        return d

    def list_files(self) -> list[dict]:
        with self._lock:
            ids = list(self._docs) + list(self._files)
        out = [self.info(i) for i in ids]
        return sorted(out, key=lambda d: d.get("created", 0))

    def history(self, doc_id: str) -> list:
        with self._lock:
            return self._convos.setdefault(doc_id, [])

    def agent_lock(self, doc_id: str) -> threading.Lock:
        with self._lock:
            return self._agent_locks.setdefault(doc_id, threading.Lock())

    def rename(self, fid: str, name: str) -> None:
        if self.is_doc(fid):
            self.get(fid).name = name
        else:
            self.get_file(fid).name = name

    def remove(self, fid: str) -> None:
        with self._lock:
            self._docs.pop(fid, None)
            self._files.pop(fid, None)
            self._meta.pop(fid, None)
            self._convos.pop(fid, None)
            self._agent_locks.pop(fid, None)


store = Store()
