"""Package-level tests: the CLI and the MCP server run as real subprocesses,
the way an agent uses them, and the Agent Skill's docs are checked against
the code. Run with `python tests/test_package.py` from the repo root."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

import pypdfium2 as pdfium  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
SKILL = os.path.join(ROOT, "src", "seamless_pdf", "skill", "seamless-pdf")
TMP = tempfile.mkdtemp(prefix="seamless-pdf-test-")


def cli(*args, ok=True, stdin=None):
    """Run `python -m seamless_pdf.cli ...`; returns parsed JSON stdout (or stderr on failure)."""
    env = {**os.environ, "PYTHONPATH": os.path.join(ROOT, "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "-m", "seamless_pdf.cli", *args], capture_output=True, text=True,
                       env=env, input=stdin, timeout=600)
    if ok:
        assert r.returncode == 0, (args, r.stderr[-2000:])
        return json.loads(r.stdout)
    assert r.returncode == 1, (args, r.returncode, r.stdout[-500:])
    return json.loads(r.stderr)


def copy(name: str) -> str:
    dst = os.path.join(TMP, name)
    shutil.copy(os.path.join(FIX, name), dst)
    return dst


def page_text(path: str, page: int = 0) -> str:
    return pdfium.PdfDocument(path)[page].get_textpage().get_text_range()


# -- CLI ---------------------------------------------------------------------------------
def test_cli_find_edit_render_roundtrip():
    src = copy("reportlab_arial.pdf")
    info = cli("info", src)
    assert info["page_count"] == 1, info
    hit = cli("find", src, "March 3")["matches"][0]
    out = os.path.join(TMP, "edited.pdf")
    res = cli("edit", src, "--op", "edit_text", "--args",
              json.dumps({"id": hit["id"], "text": "Amount due: $4,120.50 by March 9"}), "-o", out)
    step = res["steps"][0]
    assert step["result"]["font_matched"] and step["result"]["font_used"] == "ArialMT", step
    assert "March 9" in page_text(out) and "March 3" in page_text(src)     # original untouched
    png = cli("render", out, "--page", "0", "--clip", "60,80,320,120", "-o", os.path.join(TMP, "c.png"))
    assert os.path.getsize(png["image"]) > 500
    objs = cli("objects", out, "--page", "0", "--kinds", "text")["objects"]
    assert all(o["kind"] == "text" for o in objs) and any("March 9" in o["text"] for o in objs)


def test_cli_script_follows_renamed_ids_and_is_all_or_nothing():
    src = copy("right_aligned.pdf")
    objs = cli("objects", src, "--kinds", "text")["objects"]
    target = max(objs, key=lambda o: o["bbox"][2])          # a right-aligned value
    steps = [{"op": "edit_text", "args": {"id": target["id"], "text": "$99,999.00"}},
             {"op": "edit_text", "args": {"id": target["id"], "color": "#cc0000"}},  # old id: followed
             {"op": "add_annotation", "args": {"page": 0, "kind": "highlight", "target_text": "$99,999.00"}}]
    path = os.path.join(TMP, "steps.json")
    with open(path, "w") as fh:
        json.dump(steps, fh)
    out = os.path.join(TMP, "scripted.pdf")
    res = cli("edit", src, "--script", path, "-o", out)
    assert [s["op"] for s in res["steps"]] == ["edit_text", "edit_text", "add_annotation"]
    assert "$99,999.00" in page_text(out)
    # from stdin, with a failing last step: nothing is saved
    bad = steps[:1] + [{"op": "edit_text", "args": {"id": "t:0:1.0:1.0", "text": "x"}}]
    out2 = os.path.join(TMP, "never.pdf")
    err = cli("edit", src, "--script", "-", "-o", out2, ok=False, stdin=json.dumps(bad))
    assert "step 2" in err["error"] and not os.path.exists(out2), err


def test_cli_tools_run_in_place_and_new_files():
    tools = cli("tools")
    names = {t["name"] for t in tools}
    assert len(tools) == 67 and "merge_pdfs" in names and "pdf_to_word" not in names
    info = cli("tool-info", "compress")                     # alias resolves
    assert info["name"] == "compress_pdf" and "level" in info["options"]
    a, b = copy("tight_leading.pdf"), copy("style.pdf")
    outdir = os.path.join(TMP, "out")
    r = cli("run", "merge_pdfs", a, b, "--out-dir", outdir)
    merged = r["outputs"][0]["path"]
    assert os.path.dirname(merged) == os.path.abspath(outdir) and len(pdfium.PdfDocument(merged)) == 2
    numbered = os.path.join(TMP, "numbered.pdf")
    r = cli("run", "add_page_numbers", merged, "--opt", "format=Page {n} of {total}", "-o", numbered)
    assert r["saved"] == numbered and "Page 2 of 2" in page_text(numbered, 1)
    r = cli("run", "rotate_pages", numbered, "--options", '{"pages": "1", "angle": 90}', "-o", numbered + ".r.pdf")
    assert pdfium.PdfDocument(numbered + ".r.pdf")[0].get_rotation() == 90
    # -o also names the result of a tool that makes one new file
    small = os.path.join(TMP, "sub", "small.pdf")
    r = cli("run", "compress_pdf", merged, "--opt", "level=high", "-o", small)
    o = r["outputs"][0]
    assert o["path"] == small and o["size"] == os.path.getsize(small) and o["pages"] == 2, o
    # several results go into the folder of -o under their own names
    r = cli("run", "split_pdf", merged, "--opt", "mode=every_n_pages", "--opt", "n=1",
            "-o", os.path.join(TMP, "parts", "x.pdf"))
    assert len(r["outputs"]) == 2 and "note" in r
    assert all(os.path.dirname(o["path"]) == os.path.join(TMP, "parts") and os.path.exists(o["path"])
               for o in r["outputs"])
    r = cli("run", "generate_qr_code", "--opt", "data=https://example.com", "--opt", "format=svg",
            "--out-dir", outdir)
    assert r["outputs"][0]["path"].endswith(".svg")


def test_cli_errors_are_json_with_exit_code_1():
    assert "no such file" in cli("info", os.path.join(TMP, "missing.pdf"), ok=False)["error"]
    src = copy("style.pdf")
    assert "does not exist" in cli("objects", src, "--page", "9", ok=False)["error"]
    assert "unknown tool" in cli("run", "make_coffee", src, ok=False)["error"]
    assert "unknown operation" in cli("edit", src, "--op", "explode", ok=False)["error"]


def test_library_api():
    from seamless_pdf import Workspace
    ws = Workspace()
    doc = ws.add(copy("reportlab_arial.pdf"))
    seg = ws.op(doc, "find_text", {"query": "March 3"})["result"]["matches"][0]
    ws.op(doc, "edit_text", {"id": seg["id"], "text": "Due: April 30"})
    out = ws.save(doc, os.path.join(TMP, "lib.pdf"))
    assert "Due: April 30" in page_text(out)
    r = ws.run_tool("compress_pdf", [out], {"level": "high"}, out_dir=TMP)
    assert os.path.exists(r["outputs"][0]["path"])
    logo = os.path.join(TMP, "logo.png")
    from PIL import Image
    Image.new("RGB", (60, 30), (200, 0, 0)).save(logo)
    ws.op(doc, "insert_image", {"page": 0, "image_path": logo, "rect": [400, 40, 520, 100]})
    assert any(o["kind"] == "image" for o in ws.op(doc, "get_page_objects", {"page": 0})["result"]["objects"])


# -- MCP ---------------------------------------------------------------------------------
def test_mcp_server_over_stdio():
    from mcp import Client, StdioServerParameters

    src = copy("reportlab_arial.pdf")
    out = os.path.join(TMP, "mcp-edited.pdf")
    params = StdioServerParameters(command=sys.executable, args=["-m", "seamless_pdf.mcp_server"],
                                   env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src"),
                                        "PYTHONDONTWRITEBYTECODE": "1"})

    def data(res):
        assert not res.is_error, res.content
        if res.structured_content is not None:
            sc = res.structured_content
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        return json.loads(res.content[0].text)

    async def go():
        async with Client(params) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert {"open_pdf", "find_text", "edit_pdf", "view_page", "save_pdf", "run_pdf_tool",
                    "list_pdf_tools", "undo"} <= set(tools), sorted(tools)
            assert "edit_text(" in tools["edit_pdf"].description
            doc = data(await c.call_tool("open_pdf", {"path": src}))
            did = doc["doc_id"]
            hit = data(await c.call_tool("find_text", {"doc_id": did, "query": "March 3"}))["matches"][0]
            r = data(await c.call_tool("edit_pdf", {"doc_id": did, "steps": [
                {"op": "edit_text", "args": {"id": hit["id"], "text": "Amount due: $4,120.50 by March 9"}},
                {"op": "add_annotation", "args": {"page": 0, "kind": "highlight", "target_text": "March 9"}}]}))
            assert r["pages_changed"] == [0] and r["can_undo"], r
            img = await c.call_tool("view_page", {"doc_id": did, "page": 0})
            assert not img.is_error and img.content[0].type == "image"
            assert base64.b64decode(img.content[0].data)[:4] == b"\x89PNG"
            # one edit_pdf call is one undo step
            data(await c.call_tool("undo", {"doc_id": did}))
            t = data(await c.call_tool("get_text", {"doc_id": did}))["text"]
            assert "March 3" in t and "March 9" not in t, t
            data(await c.call_tool("redo", {"doc_id": did}))
            data(await c.call_tool("save_pdf", {"doc_id": did, "path": out}))
            bad = await c.call_tool("edit_pdf", {"doc_id": did, "steps": [{"op": "edit_text",
                                                                          "args": {"id": "t:0:1.0:1.0"}}]})
            assert bad.is_error and "t:0:1.0:1.0" in bad.content[0].text, bad.content
            cat = data(await c.call_tool("list_pdf_tools", {"category": "Organize"}))["tools"]
            ops = data(await c.call_tool("list_operations", {}))["operations"]
            assert any(o["name"] == "edit_text" for o in ops)
            files = data(await c.call_tool("list_open_files", {}))["files"]
            assert any(f["id"] == did for f in files)
            assert any(t["name"] == "merge_pdfs" for t in cat)
            r = data(await c.call_tool("run_pdf_tool", {"tool": "extract_pages", "inputs": [out],
                                                       "options": {"pages": "1"}, "out_dir": TMP}))
            assert os.path.exists(r["outputs"][0]["path"])
    asyncio.run(go())
    assert "March 9" in page_text(out)


# -- skill --------------------------------------------------------------------------------
def test_skill_frontmatter_and_reference_are_current():
    text = open(os.path.join(SKILL, "SKILL.md")).read()
    m = re.match(r"---\nname: ([a-z0-9-]+)\ndescription: (.+?)\n---\n", text, re.S)
    assert m and m.group(1) == "seamless-pdf", "SKILL.md frontmatter"
    assert len(m.group(2)) <= 1024, len(m.group(2))
    assert len(text.splitlines()) < 500
    for ref in re.findall(r"\]\((reference/[^)]+)\)", text):
        assert os.path.exists(os.path.join(SKILL, ref)), ref
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_reference.py"), "--check"],
                       capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_command_in_the_skill_is_valid():
    """Each `seamless-pdf ...` line in SKILL.md parses with the real CLI, and
    every tool / operation it names exists."""
    from seamless_pdf.cli import build_parser
    from seamless_pdf.ops import OPS
    from seamless_pdf.tools import find_tool
    text = open(os.path.join(SKILL, "SKILL.md")).read() + "\n" + open(os.path.join(ROOT, "README.md")).read()
    parser = build_parser()
    cmds = [ln.strip() for ln in re.findall(r"^\s*(seamless-pdf .+?)(?:\s+#.*)?$", text, re.M)]
    cmds += re.findall(r"`(seamless-pdf [^`]+)`", text)
    assert len(cmds) > 15, cmds
    checked = 0
    for c in cmds:
        if "<" in c:                 # a placeholder such as `run <tool> <inputs>`
            continue
        argv = shlex.split(c)[1:]
        if argv and argv[0] in ("--version", "--help"):
            continue
        a = parser.parse_args(argv)
        checked += 1
        if a.cmd == "run":
            find_tool(a.tool)
        if a.cmd == "edit" and a.op:
            assert a.op in OPS, a.op
            json.loads(a.args)
    for op in re.findall(r'"op": "([a-z_]+)"', text):
        assert op in OPS, op
    for name in re.findall(r"`run ([a-z_]+)", text):
        find_tool(name)
    skill_text = open(os.path.join(SKILL, "SKILL.md")).read()
    for name in re.findall(r"`([a-z]+_[a-z_]+)`", skill_text):
        if name not in OPS and name not in ("in_place", "font_matched", "image_path", "asset_id",
                                            "replace_all", "edit_text", "new_ids", "doc_id",
                                            "match_case", "out_dir", "save_pdf", "open_pdf",
                                            "view_page", "edit_pdf", "list_operations", "list_pdf_tools",
                                            "run_pdf_tool", "list_open_files", "get_text", "find_text",
                                            "get_page_objects", "list_form_fields", "gen_reference"):
            find_tool(name)
    assert checked > 10


def test_install_skill_copies_the_folder():
    dest = os.path.join(TMP, "skills")
    r = cli("install-skill", "--dest", dest)
    assert os.path.exists(os.path.join(r["installed"], "SKILL.md"))
    assert os.path.exists(os.path.join(r["installed"], "reference", "tools.md"))
    assert "exists" in cli("install-skill", "--dest", dest, ok=False)["error"]
    cli("install-skill", "--dest", dest, "--force")


if __name__ == "__main__":
    only = sys.argv[1:]
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn) and (not only or any(o in name for o in only)):
            t = time.time()
            try:
                fn()
                print(f"PASS {name} ({time.time() - t:.1f}s)")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
    shutil.rmtree(TMP, ignore_errors=True)
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
