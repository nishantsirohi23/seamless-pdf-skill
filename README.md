# Seamless PDF Skill

**Edit PDFs so nobody can tell, plus 67 document tools, for AI agents.**

Seamless PDF lets an AI agent (or you) change what is written in an existing
PDF: a date, an amount, a name, a typo. The new text is drawn with the
document's **own embedded font**, at the original baseline, size, colour and
spacing, and in its original place in the drawing order. Nothing else on the
page moves, and a watermark stays behind the table drawn over it.

It also has 67 whole-document tools: merge, split, compress, convert to and
from PDF, protect, redact, sign, OCR, compare, watermark, forms and
e-invoices.

You can use it in three ways:

| Way | For | Start with |
|---|---|---|
| **Agent Skill** | Claude Code and other agents that read `SKILL.md` and run shell commands | `seamless-pdf install-skill` |
| **MCP server** | Claude Desktop, Claude Code, Cursor, any MCP client | `seamless-pdf-mcp` |
| **CLI / Python library** | scripts, pipelines, your own agent | `seamless-pdf --help`, `from seamless_pdf import Workspace` |

Everything runs locally. No API key is needed, and nothing is uploaded anywhere.

---

## Contents

- [Features](#features)
- [Install](#install)
- [Use as an Agent Skill](#use-as-an-agent-skill)
- [Use as an MCP server](#use-as-an-mcp-server)
- [CLI guide](#cli-guide)
- [Python library](#python-library)
- [Editing operations](#editing-operations)
- [Document tools](#document-tools)
- [Conventions](#conventions)
- [How the seamless edit works](#how-the-seamless-edit-works)
- [Recipes](#recipes)
- [Testing](#testing)
- [Limits](#limits)
- [Licence](#licence)

---

## Features

### Editing what is on the page

- **Text:** replace, restyle (size, colour, bold, italic, font family), move,
  delete or insert text. Find-and-replace across the document. Right-aligned
  and centred columns stay aligned when the text gets longer or shorter.
- **Fonts:** edits reuse the document's own font program, including
  subsetted TrueType, Type1 and Type0/CID fonts. A character the subset
  lacks falls back, for that character alone, to the closest standard font,
  then to an embedded DejaVu Sans subset (Latin, Greek, Cyrillic, ₹, →, ...).
- **Images:** insert, replace, move, resize or delete images, without
  re-encoding them and with masks kept.
- **Annotations:** highlight, underline, strikeout, squiggly, sticky note,
  free text, rectangle, circle and line, each with a real appearance stream so
  every viewer shows it.
- **Forms:** list, fill, add, move and delete form fields; flatten forms.
- **Scanned pages:** OCR them (Tesseract), then edit or move scanned words.
  The paper and ink colours are sampled so the patch blends in.
- **Undo/redo:** every operation is atomic. A batch of steps is all-or-nothing.
- **Look before and after:** render any page or zoomed area to PNG.

### 67 document tools

| Category | Tools |
|---|---|
| Create | Create PDF from Markdown/HTML/text, cards (business, postcard, greeting, invitation, place card), photo collage, fillable PDF form, job application bundle, QR code |
| Edit | Watermark (text/image, tiled), page numbers, overlay/letterhead, crop, change page size, metadata, viewer preferences, flatten, make fillable (auto-detects blanks), bulk form fill, add QR code |
| Organize | Merge, assemble pages from several PDFs, split (every N, ranges, bookmarks), rearrange, remove, extract, rotate, insert blank pages, n-up, halve pages, bookmarks (incl. auto from headings), extract images, extract text/Markdown |
| Optimize | Compress (4 levels, grayscale), web optimize (linearize), OCR, repair, rasterize |
| Security | Protect (AES-256 + permissions), unlock, redact (terms, regex, presets for email/phone/IBAN/card/SSN/date/URL; the text is removed from the file), remove metadata, generate passwords, visual signature, digital signature (PAdES, your .p12 or self-signed), verify signatures |
| View & check | Compare PDFs (text diff + visual diff PDF), search across PDFs, basic PDF/A check, document info |
| Convert to PDF | Word, PowerPoint, Excel, Publisher, OpenDocument, RTF, images (JPG, PNG, WEBP, HEIC, TIFF, GIF, BMP, SVG), text, Markdown, HTML, EPUB, CBZ, web pages |
| Convert from PDF | Excel (tables), PowerPoint, ODS, ODP, EPUB, HTML, Markdown, text, PNG, JPG, TIFF, WEBP |
| Invoices | Create invoice, e-invoice (Factur-X/ZUGFeRD EN 16931, CII and UBL XML), PDF invoice to e-invoice, XML e-invoice to PDF, validate e-invoice |

Every tool, with all options, is listed in
[`reference/tools.md`](src/seamless_pdf/skill/seamless-pdf/reference/tools.md).
PDF to Word and PDF/A conversion are not included, because every free
converter of good quality for them is AGPL.

---

## Install

Python 3.11 or newer.

```bash
pip install "seamless-pdf[mcp] @ git+https://github.com/nishantsirohi23/seamless-pdf-skill.git"
seamless-pdf --version
```

Leave out `[mcp]` if you only need the CLI and skill. To work on the code:

```bash
git clone https://github.com/nishantsirohi23/seamless-pdf-skill.git
cd seamless-pdf-skill
python -m venv .venv && .venv/bin/pip install -e ".[mcp]"
```

### Optional system programs

The editor and most tools need nothing else. A few tools call these
programs, and `seamless-pdf tools` marks a tool unavailable (with the reason)
when its program is missing:

| Program | Used for | macOS | Debian/Ubuntu |
|---|---|---|---|
| Tesseract | OCR of scanned pages | `brew install tesseract` | `apt install tesseract-ocr` |
| LibreOffice | Word/Excel/PowerPoint/OpenDocument to PDF, PDF to ODS/ODP | `brew install --cask libreoffice` | `apt install libreoffice` |
| Chrome or Chromium | HTML, Markdown, text, cards, invoices and web pages to PDF | Google Chrome | `apt install chromium` |
| pandoc | EPUB in and out | `brew install pandoc` | `apt install pandoc` |

---

## Use as an Agent Skill

The skill is a folder with a `SKILL.md` that teaches the agent the workflow,
the conventions and the commands, plus generated reference files listing
every operation and tool.

**Claude Code:** install it into your personal skills folder:

```bash
seamless-pdf install-skill            # copies to ~/.claude/skills/seamless-pdf
```

Use `--dest .claude/skills` to install it into one project instead. Claude
then uses it whenever a task involves a PDF, for example *"change the due
date in invoice.pdf to 30 April and compress it for email"*.

**Other agents:** point them at
[`src/seamless_pdf/skill/seamless-pdf/SKILL.md`](src/seamless_pdf/skill/seamless-pdf/SKILL.md).
It only assumes the agent can run shell commands and read JSON.

---

## Use as an MCP server

The server speaks MCP over stdio. Documents are opened by local path, kept in
memory while you edit, and written back with `save_pdf`.

**Claude Code**

```bash
claude mcp add seamless-pdf -s user -- seamless-pdf-mcp
```

**Claude Desktop:** add to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`), using the full path from
`which seamless-pdf-mcp`:

```json
{
  "mcpServers": {
    "seamless-pdf": { "command": "/full/path/to/seamless-pdf-mcp" }
  }
}
```

**Any other MCP client:** command `seamless-pdf-mcp` (or `seamless-pdf mcp`),
transport stdio, no arguments, no environment variables.

### MCP tools

| Tool | What it does |
|---|---|
| `open_pdf(path)` | Open a PDF; returns `doc_id` and an overview |
| `get_page_objects(doc_id, page, kinds?)` | Text segments, images, annotations, fields with ids and boxes |
| `find_text(doc_id, query, match_case?)` | Segments containing a string, with ids |
| `get_text(doc_id, pages?)` | Plain text |
| `list_form_fields(doc_id)` | Fields with names, types and values |
| `view_page(doc_id, page, clip?, scale?)` | PNG of a page or a zoomed area |
| `edit_pdf(doc_id, steps)` | Apply editing operations atomically, as one undo step |
| `list_operations()` | Full JSON schemas of the editing operations |
| `undo(doc_id)` / `redo(doc_id)` | History |
| `save_pdf(doc_id, path)` | Write the current state to disk |
| `list_pdf_tools(category?)` | Catalog of the 67 document tools with option schemas |
| `run_pdf_tool(tool, inputs?, options?, out_dir?)` | Run a document tool on paths or ids |
| `list_open_files()` | Everything opened or produced in this session |

A typical agent turn: `open_pdf`, `find_text`, `edit_pdf`, `view_page` to
check, then `save_pdf`.

---

## CLI guide

Every command prints JSON on stdout (`render` writes a PNG and prints its
path). Errors are JSON on stderr with exit code 1, so agents can read them
and retry.

### Inspect

```bash
seamless-pdf info contract.pdf                       # pages, sizes, scanned pages, form
seamless-pdf text contract.pdf --pages 0,1           # plain text
seamless-pdf find contract.pdf "March 3"             # text segments + ids
seamless-pdf objects contract.pdf --page 0 --kinds text,image
seamless-pdf fields form.pdf                         # form fields
seamless-pdf render contract.pdf --page 0 --clip 60,80,320,120 -o zoom.png
```

`find` returns segments like:

```json
{"id": "t:0:72.0:92.0", "kind": "text", "page": 0,
 "text": "Amount due: $4,120.50 by March 3",
 "bbox": [72.0, 83.26, 259.45, 94.53], "font": "ArialMT", "size": 12.0, "color": "#000000"}
```

### Edit

One operation:

```bash
seamless-pdf edit contract.pdf --op edit_text --args '{"id": "t:0:72.0:92.0", "text": "Amount due: $4,120.50 by April 30"}' -o contract-v2.pdf
```

Several, as one batch (all-or-nothing; ids renamed by an earlier step are
followed). Pass `--script -` to read the steps from stdin:

```bash
seamless-pdf edit contract.pdf --script steps.json -o contract-v2.pdf
```

```json
[
  {"op": "replace_all", "args": {"find": "Acme GmbH", "replace": "Globex AG"}},
  {"op": "edit_text", "args": {"id": "t:0:446.6:192.0", "text": "$1,250.00", "color": "#cc0000"}},
  {"op": "insert_text", "args": {"page": 0, "x": 72, "y": 760, "text": "Approved 23 April 2026"}},
  {"op": "insert_image", "args": {"page": 0, "image_path": "stamp.png", "rect": [420, 700, 540, 780]}},
  {"op": "add_annotation", "args": {"page": 0, "kind": "highlight", "target_text": "$1,250.00"}}
]
```

`seamless-pdf ops` prints every operation with its JSON schema.

### Document tools

```bash
seamless-pdf tools                                   # catalog
seamless-pdf tools --category Security
seamless-pdf tool-info redact_pdf                    # one tool's options
seamless-pdf run merge_pdfs a.pdf b.pdf c.pdf --out-dir out/
seamless-pdf run compress_pdf scan.pdf --opt level=high
seamless-pdf run protect_pdf report.pdf --opt user_password=s3cret --opt allow_print=false
seamless-pdf run redact_pdf hr.pdf --opt 'presets=["email","phone","iban"]' -o hr-redacted.pdf
seamless-pdf run ocr_pdf scan.pdf -o scan-searchable.pdf
seamless-pdf run convert_to_pdf letter.docx photo.heic notes.md --opt merge=true
seamless-pdf run pdf_to_excel statement.pdf
seamless-pdf run digital_sign_pdf contract.pdf --opt signer_name="Jane Doe" --opt visible=true
```

- `--opt key=value` values are parsed as JSON when they can be (numbers,
  `true`/`false`, lists, objects) and are strings otherwise.
  `--options '{...}'` gives all options at once.
- **In-place** tools (watermark, page numbers, redact, sign, OCR, rotate, ...)
  change the PDF and save it to `-o` (default `<name>-<tool>.pdf`).
- The other tools create new files. With `-o`, a single result is saved
  there; several results (split, PDF to images) go into that file's folder
  under their own names. Without `-o` they go to `--out-dir` (default: next
  to the first input). The JSON lists every file with its real path, size and
  page count. Your input file is never overwritten unless you pass its own
  path to `-o`.

---

## Python library

```python
from seamless_pdf import Workspace

ws = Workspace()
doc = ws.add("invoice.pdf")                     # PDFs become editable documents

hit = ws.op(doc, "find_text", {"query": "March 3"})["result"]["matches"][0]
ws.op(doc, "edit_text", {"id": hit["id"], "text": "Amount due: $4,120.50 by April 30"})
ws.script(doc, [                                # several steps; renamed ids followed
    {"op": "add_annotation", "args": {"page": 0, "kind": "highlight", "target_text": "April 30"}},
])
ws.save(doc, "invoice-v2.pdf")

out = ws.run_tool("compress_pdf", ["invoice-v2.pdf"], {"level": "high"}, out_dir="out")
print(out["outputs"][0]["path"])

png = ws.doc(doc).render_page(0, scale=2.0)     # bytes of a PNG
```

`PdfDoc` (from `seamless_pdf`) is the engine itself, if you want to work at
that level: `PdfDoc(data).text_objects(0)`, `.edit_text(id, text)`, `.undo()`,
`.to_bytes()`.

---

## Editing operations

| Operation | Purpose |
|---|---|
| `get_document_info`, `get_page_objects`, `find_text`, `get_document_text`, `list_form_fields`, `view_page` | Read (no changes) |
| `edit_text` | Replace and/or restyle one text segment in place |
| `replace_all` | Find and replace across the document |
| `insert_text` | New text at a point (matches nearby style) |
| `delete_objects`, `move_objects`, `set_object_rect` | Any object by id |
| `insert_image`, `replace_image` | Images (`image_path` or `asset_id`) |
| `add_annotation`, `update_annotation` | Markup, notes, shapes |
| `set_field_value`, `add_form_field`, `flatten_forms` | Forms |
| `ocr_page` | Make a scanned page editable |
| `undo`, `redo` | History |

Arguments and types for each:
[`reference/operations.md`](src/seamless_pdf/skill/seamless-pdf/reference/operations.md).

---

## Document tools

See the [features table](#67-document-tools) above and the full generated
reference, [`reference/tools.md`](src/seamless_pdf/skill/seamless-pdf/reference/tools.md),
which lists every option, default and alias.

---

## Conventions

- **Pages:** editing commands and operations use **0-based** indexes.
  Document tools use **1-based** ranges (`1-3,5`, `odd`, `even`, `last`,
  `all`), like a print dialog.
- **Coordinates:** PDF points (1/72 inch), origin at the **top-left** of the
  unrotated page, rects as `[x0, y0, x1, y1]`.
- **Ids:** `t:` text segment, `i:` image, `a:` annotation, `w:` form field.
  Ids are stable across runs for unchanged content; an edited segment may get
  a new id (the result's `new_ids` maps old to new).
- **Segments:** a run of same-style text on one line; table cells are
  separate segments. `edit_text` replaces a whole segment's text.

---

## How the seamless edit works

1. **Locate.** A content-stream interpreter tracks the complete graphics and
   text state (CTM, text matrix, font, size, character and word spacing,
   horizontal scaling, rise, render mode, colour, transparency) through
   every page and form XObject. It records each glyph's position and its
   exact place in the stream. Glyph positions agree with PDFium's to within
   0.13 pt in the tests (0.00 pt on most fonts).
2. **Erase exactly.** The edited glyphs' bytes are replaced in their `TJ`
   arrays by spacing numbers equal to their advance. Nothing else moves, and
   nothing above or below is clipped, however tight the line spacing.
3. **Redraw with the same font.** The new text uses the original font
   resource with the same matrix, size and spacing, and goes into the same
   content stream right after the original text object, so it keeps its
   place in the stacking order. Unicode is mapped to the font's codes through
   `/ToUnicode` or its encoding. For subset fonts only glyphs the document
   already contains are used. Anything else falls back per character, and
   the result reports `font_matched: false`.
4. **Keep the layout.** Right-aligned and centred text keeps its edge or
   centre fixed.

Built only on permissively licensed libraries: pikepdf/qpdf (object model),
PDFium via pypdfium2 (rendering), fontTools, ReportLab, pdfplumber and
pyHanko.

---

## Recipes

**Update a date everywhere and sign**

```bash
seamless-pdf edit agreement.pdf --op replace_all --args '{"find": "1 March 2026", "replace": "23 April 2026"}' -o a2.pdf
seamless-pdf run sign_pdf a2.pdf --opt name="Jane Doe" -o a2-signed.pdf
```

**Make a scanned letter editable and fix a word**

```bash
seamless-pdf run ocr_pdf letter-scan.pdf -o letter-ocr.pdf
seamless-pdf find letter-ocr.pdf "recieved"
seamless-pdf edit letter-ocr.pdf --op edit_text --args '{"id": "t:0:118.4:233.0", "text": "received"}' -o letter-fixed.pdf
```

**Share a document safely**

```bash
seamless-pdf run redact_pdf contract.pdf --opt 'presets=["email","phone"]' --opt 'terms=["Project Falcon"]' -o r.pdf
seamless-pdf run remove_metadata r.pdf -o r-clean.pdf
seamless-pdf run protect_pdf r-clean.pdf --opt user_password=open-me
```

**Photos to one small PDF**

```bash
seamless-pdf run images_to_pdf IMG_1.heic IMG_2.heic IMG_3.jpg --opt page_size=A4 --out-dir out/
seamless-pdf run compress_pdf out/images.pdf --opt level=high
```

---

## Testing

```bash
.venv/bin/python tests/test_engine.py     # engine: pixel diffs, glyph positions, fonts, stacking order, OCR
.venv/bin/python tests/test_tools.py      # every document tool on real inputs
.venv/bin/python tests/test_package.py    # CLI + MCP server as subprocesses, skill docs vs code
```

The tests read outputs back with PDFium and pdfminer, never with the code
under test. They check that an edited line changes no pixels outside itself,
that unchanged glyphs keep their x positions in subset fonts, and that edited
text stays behind content drawn over it. Every command in `SKILL.md` and this
README must parse with the real CLI and name a real tool, and the reference
files must match the code (`python scripts/gen_reference.py --check`). Adding
a tool without a test fails the suite.

---

## Limits

- An edit redraws one segment; it does not reflow a paragraph. Longer text
  extends along the baseline.
- Vector drawings (lines, shapes) are not selectable objects.
- Scanned-word edits use Helvetica or Times, matched to the ink. There is no
  font in a scan to reuse, so unusual typefaces will not match exactly.
- The Unicode fallback has no CJK or Indic glyphs; those characters show as `?`.
- Editing a digitally signed PDF invalidates its signature. That is how
  signatures are meant to work.
- Not included: PDF to Word, PDF/A conversion, XPS/MOBI input.

## Licence

MIT, see [LICENSE](LICENSE). Bundled fonts and dependency licences are listed
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
