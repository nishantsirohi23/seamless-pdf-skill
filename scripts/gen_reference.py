"""Regenerate the skill's reference files from the live registries.

    python scripts/gen_reference.py          # write
    python scripts/gen_reference.py --check  # exit 1 if they are stale (used by the tests)
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

from seamless_pdf.ops import OPS  # noqa: E402
from seamless_pdf.tools import catalog  # noqa: E402
from seamless_pdf.tools.base import CATEGORIES  # noqa: E402

REF = os.path.join(ROOT, "src", "seamless_pdf", "skill", "seamless-pdf", "reference")


def _type(spec: dict) -> str:
    t = spec.get("type", "")
    if "enum" in spec:
        return "one of " + ", ".join(f"`{v}`" for v in spec["enum"])
    if t == "array":
        inner = spec.get("items", {})
        return f"array of {_type(inner) if inner else 'values'}"
    return t or "any"


def operations_md() -> str:
    out = ["# Editing operations", "",
           "Used with `seamless-pdf edit --op NAME --args JSON` (or as steps in `--script`),",
           "and by the MCP tool `edit_pdf`. Pages are 0-based; coordinates are PDF points",
           "with a top-left origin. Generated from the code by `scripts/gen_reference.py`.", "",
           "`insert_image` and `replace_image` also accept `\"image_path\": \"logo.png\"` (a local",
           "file) in place of `asset_id`.", ""]
    read = [o for o in OPS.values() if not o.mutating and not o.history]
    write = [o for o in OPS.values() if o.mutating]
    hist = [o for o in OPS.values() if o.history]
    for title, group in (("Reading (no changes)", read), ("Changing the document", write),
                         ("History", hist)):
        out += [f"## {title}", ""]
        for o in group:
            out += [f"### `{o.name}`", "", o.description, ""]
            if o.properties:
                out += ["| Argument | Type | Required | Notes |", "|---|---|---|---|"]
                for k, spec in o.properties.items():
                    notes = spec.get("description", "").replace("|", "\\|")
                    out.append(f"| `{k}` | {_type(spec)} | {'yes' if k in o.required else ''} | {notes} |")
                out.append("")
    if read:
        out += ["Reading operations are also CLI commands: `info`, `objects`, `find`, `text`, `fields`, `render`.", ""]
    return "\n".join(out).rstrip() + "\n"


def tools_md() -> str:
    tools = catalog()
    out = ["# Document tools", "",
           f"{len(tools)} tools. Run with `seamless-pdf run NAME [inputs...] --opt key=value`, or the",
           "MCP tool `run_pdf_tool`. Page ranges are 1-based (`1-3,5`, `odd`, `even`, `last`, `all`).",
           "*In place* tools change the PDF (saved with `-o`); the others create new files.",
           "Generated from the code by `scripts/gen_reference.py`.", ""]
    inputs = {"none": "no input", "pdf": "one PDF", "pdfs": "one or more PDFs", "files": "files",
              "pdf+": "a PDF plus files", "any": "any files"}
    for cat in CATEGORIES:
        group = [t for t in tools if t["category"] == cat]
        if not group:
            continue
        out += [f"## {cat}", ""]
        for t in group:
            flags = [inputs.get(t["inputs"], t["inputs"])]
            if t["in_place"]:
                flags.append("in place")
            out += [f"### `{t['name']}` ({t['title']})", "", f"{t['description']} *({', '.join(flags)})*", ""]
            if t["options"]:
                out += ["| Option | Type | Default | Notes |", "|---|---|---|---|"]
                for k, spec in t["options"].items():
                    req = " (required)" if k in t["required"] else ""
                    d = spec.get("default", "")
                    d = f"`{d}`" if d not in ("", None) else ""
                    notes = (spec.get("description", "") + req).replace("|", "\\|").strip()
                    out.append(f"| `{k}` | {_type(spec)} | {d} | {notes} |")
                out.append("")
            if t["aliases"]:
                out += ["Also known as: " + ", ".join(f"`{a}`" for a in t["aliases"]), ""]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    files = {"operations.md": operations_md(), "tools.md": tools_md()}
    if "--check" in sys.argv:
        stale = [n for n, text in files.items()
                 if not os.path.exists(os.path.join(REF, n)) or open(os.path.join(REF, n)).read() != text]
        if stale:
            print("stale:", ", ".join(stale), "- run python scripts/gen_reference.py")
            return 1
        print("reference up to date")
        return 0
    os.makedirs(REF, exist_ok=True)
    for n, text in files.items():
        with open(os.path.join(REF, n), "w") as fh:
            fh.write(text)
        print("wrote", n, len(text.splitlines()), "lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
