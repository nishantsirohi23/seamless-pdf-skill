"""`seamless-pdf` command line. Every command prints JSON on stdout (except
`render`, which writes an image) so agents can parse the result; errors go
to stderr as JSON with exit code 1.

Page numbers: editing commands (objects, find, edit, render) use 0-based
page indexes, exactly as the operation schemas do. Document tools take
1-based page ranges such as "1-3,5" or "all", like a print dialog.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .engine import EditError
from .workspace import Workspace


def _out(obj) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def _value(v: str):
    """--opt key=value: JSON if it parses (numbers, true/false, lists), else a string."""
    try:
        return json.loads(v)
    except ValueError:
        return v


def _options(args) -> dict:
    opts = {}
    if getattr(args, "options", None):
        opts.update(json.loads(args.options))
    for kv in getattr(args, "opt", None) or []:
        if "=" not in kv:
            raise EditError(f"--opt expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        opts[k.strip()] = _value(v)
    return opts


def _default_out(src: str, suffix: str) -> str:
    stem, _ = os.path.splitext(os.path.abspath(src))
    return f"{stem}-{suffix}.pdf"


# -- commands ---------------------------------------------------------------------------
def cmd_tools(ws, a):
    tools = ws.tools(a.category)
    if a.json:
        return _out(tools)
    _out([{"name": t["name"], "category": t["category"], "title": t["title"],
           "inputs": t["inputs"], "in_place": t["in_place"],
           **({"unavailable": t["unavailable"]} if t["unavailable"] else {})} for t in tools])


def cmd_tool_info(ws, a):
    _out(ws.tool_info(a.name))


def cmd_ops(ws, a):
    _out(ws.operations())


def cmd_run(ws, a):
    import shutil
    import tempfile
    t = ws.tool_info(a.tool)
    if t["in_place"]:
        res = ws.run_tool(t["name"], a.inputs, _options(a), out_dir=a.out_dir)
        dest = a.output or _default_out(a.inputs[0], t["name"].replace("_", "-"))
        res["saved"] = ws.save(a.inputs[0], dest)
    elif a.output:
        # -o names the result of a tool that makes one new file; several files
        # (split, PDF to images) go into the folder of -o under their own names.
        dest = os.path.abspath(os.path.expanduser(a.output))
        with tempfile.TemporaryDirectory(prefix="seamless-pdf-") as tmp:
            res = ws.run_tool(t["name"], a.inputs, _options(a), out_dir=tmp)
            outs = res["outputs"]
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            if len(outs) == 1:
                shutil.move(outs[0]["path"], dest)
                outs[0]["path"] = dest
            else:
                folder = os.path.dirname(dest) or "."
                for o in outs:
                    target = os.path.join(folder, os.path.basename(o["path"]))
                    shutil.move(o["path"], target)
                    o["path"] = target
                res["note"] = f"{len(outs)} files were produced, so they were saved in {folder} under their own names"
            for o in outs:
                ws.paths[o["id"]] = o["path"]
    else:
        res = ws.run_tool(t["name"], a.inputs, _options(a), out_dir=a.out_dir)
    for k in ("new_ids", "can_undo", "can_redo", "undo_label", "redo_label", "doc_id", "version"):
        res.pop(k, None)
    _out(res)


def cmd_info(ws, a):
    fid = ws.resolve(a.file)
    _out(ws.op(fid, "get_document_info")["result"])


def cmd_objects(ws, a):
    kinds = a.kinds.split(",") if a.kinds else None
    _out(ws.op(a.file, "get_page_objects", {"page": a.page, "kinds": kinds})["result"])


def cmd_find(ws, a):
    _out(ws.op(a.file, "find_text", {"query": a.query, "match_case": a.match_case})["result"])


def cmd_text(ws, a):
    pages = [int(p) for p in a.pages.split(",")] if a.pages else None
    _out(ws.op(a.file, "get_document_text", {"pages": pages})["result"])


def cmd_fields(ws, a):
    _out(ws.op(a.file, "list_form_fields")["result"])


def cmd_render(ws, a):
    fid = ws.resolve(a.file)
    doc = ws.doc(fid)
    clip = [float(v) for v in a.clip.split(",")] if a.clip else None
    png = doc.render_page(a.page, a.scale, clip)
    out = os.path.abspath(a.output or _default_out(a.file, f"page{a.page}").replace(".pdf", ".png"))
    with open(out, "wb") as fh:
        fh.write(png)
    _out({"image": out, "page": a.page, "scale": a.scale})


def cmd_edit(ws, a):
    fid = ws.resolve(a.file)
    if a.script:
        steps = json.load(sys.stdin if a.script == "-" else open(a.script))
        if isinstance(steps, dict):
            steps = steps.get("steps", [steps])
    elif a.op:
        steps = [{"op": a.op, "args": json.loads(a.args) if a.args else {}}]
    else:
        raise EditError("give --op NAME [--args JSON] or --script FILE.json")
    results = ws.script(fid, steps)
    dest = a.output or _default_out(a.file, "edited")
    saved = ws.save(fid, dest) if any(r.get("pages_changed") is not None for r in results) else None
    _out({"steps": [{"op": r["op"], "result": r.get("result"), "pages_changed": r.get("pages_changed"),
                     "new_ids": r.get("new_ids")} for r in results], "saved": saved})


def cmd_mcp(ws, a):
    from .mcp_server import main
    main()


def cmd_skill(ws, a):
    """Install the Agent Skill folder for Claude Code / Claude apps."""
    import shutil
    src = os.path.join(os.path.dirname(__file__), "skill", "seamless-pdf")
    dest_root = os.path.expanduser(a.dest or "~/.claude/skills")
    dest = os.path.join(dest_root, "seamless-pdf")
    if os.path.exists(dest):
        if not a.force:
            raise EditError(f"{dest} exists; pass --force to replace it")
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    _out({"installed": dest})


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="seamless-pdf", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"seamless-pdf {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("tools", help="list document tools (merge, compress, convert, sign, ...)")
    s.add_argument("--category")
    s.add_argument("--json", action="store_true", help="full catalog with option schemas")
    s.set_defaults(fn=cmd_tools)

    s = sub.add_parser("tool-info", help="one tool's description and option schema")
    s.add_argument("name")
    s.set_defaults(fn=cmd_tool_info)

    s = sub.add_parser("run", help="run a document tool")
    s.add_argument("tool")
    s.add_argument("inputs", nargs="*", help="input files (PDFs, images, Office files, ...)")
    s.add_argument("--opt", action="append", metavar="KEY=VALUE", help="tool option (repeatable)")
    s.add_argument("--options", metavar="JSON", help="all options as a JSON object")
    s.add_argument("-o", "--output", help="where to save the result (a tool that makes several files "
                   "puts them in this file's folder)")
    s.add_argument("--out-dir", help="folder for new files (default: next to the first input)")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("ops", help="list editing operations and their JSON schemas")
    s.set_defaults(fn=cmd_ops)

    s = sub.add_parser("info", help="page count, sizes, scanned pages, forms")
    s.add_argument("file")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("objects", help="editable objects on a page, with ids and boxes")
    s.add_argument("file")
    s.add_argument("--page", type=int, default=0, help="0-based page index")
    s.add_argument("--kinds", help="comma list: text,image,annotation,field")
    s.set_defaults(fn=cmd_objects)

    s = sub.add_parser("find", help="find text segments (ids for editing)")
    s.add_argument("file")
    s.add_argument("query")
    s.add_argument("--match-case", action="store_true")
    s.set_defaults(fn=cmd_find)

    s = sub.add_parser("text", help="plain text of the document")
    s.add_argument("file")
    s.add_argument("--pages", help="comma list of 0-based page indexes")
    s.set_defaults(fn=cmd_text)

    s = sub.add_parser("fields", help="form fields with ids, names and values")
    s.add_argument("file")
    s.set_defaults(fn=cmd_fields)

    s = sub.add_parser("render", help="render a page to PNG (to check an edit)")
    s.add_argument("file")
    s.add_argument("--page", type=int, default=0, help="0-based page index")
    s.add_argument("--scale", type=float, default=2.0, help="2.0 = 144 dpi")
    s.add_argument("--clip", help="x0,y0,x1,y1 in points, top-left origin")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("edit", help="apply editing operations and save")
    s.add_argument("file")
    s.add_argument("--op", help="operation name (see `seamless-pdf ops`)")
    s.add_argument("--args", metavar="JSON", help="operation arguments as JSON")
    s.add_argument("--script", metavar="FILE", help='JSON list of {"op", "args"} steps ("-" = stdin)')
    s.add_argument("-o", "--output", help="where to save (default: <name>-edited.pdf)")
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("mcp", help="run the MCP server on stdio")
    s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("install-skill", help="copy the Agent Skill into ~/.claude/skills")
    s.add_argument("--dest", help="skills folder (default ~/.claude/skills)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_skill)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    ws = Workspace()
    try:
        a.fn(ws, a)
        return 0
    except EditError as e:
        json.dump({"error": str(e)}, sys.stderr)
        sys.stderr.write("\n")
        return 1
    except (OSError, ValueError) as e:
        json.dump({"error": f"{type(e).__name__}: {e}"}, sys.stderr)
        sys.stderr.write("\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
