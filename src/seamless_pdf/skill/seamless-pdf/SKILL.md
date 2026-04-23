---
name: seamless-pdf
description: Edit existing PDFs so the change is indistinguishable from the original (change text, numbers, dates and names in the document's own font; move, delete or insert text and images; annotations; fill and create forms; OCR and edit scanned pages), and run 67 whole-document PDF tools (merge, split, compress, convert to and from PDF, protect, unlock, redact, sign, digitally sign, compare, watermark, page numbers, e-invoices). Use whenever a task involves reading, changing, creating or converting a PDF file.
---

# Seamless PDF

Everything goes through the `seamless-pdf` command. It prints JSON. Check it
is installed with `seamless-pdf --version`; if it is not, install it:

```bash
pip install "seamless-pdf @ git+https://github.com/nishantsirohi23/seamless-pdf-skill.git"
```

## Two kinds of work

1. **Edit what is on a page** (change a date, fix a typo, move a logo, fill a
   form): `find` / `objects` to get object ids, then `edit`.
2. **Whole-document jobs** (merge, compress, convert, protect, sign, redact,
   OCR, ...): `seamless-pdf run <tool> <inputs> --opt key=value`.

## Editing workflow

```bash
seamless-pdf info in.pdf                       # pages, sizes, scanned pages, forms
seamless-pdf text in.pdf                       # all the text (to read or verify a file)
seamless-pdf find in.pdf "March 3"             # matching text segments with ids
seamless-pdf objects in.pdf --page 0           # every object on a page: ids, bboxes, fonts
seamless-pdf edit in.pdf --op edit_text --args '{"id": "t:0:72.0:92.0", "text": "Due: April 30"}' -o out.pdf
seamless-pdf render out.pdf --page 0 -o check.png   # look at the result
```

- Text is split into **segments** (a run of same-style text on one line; table
  cells are separate). `edit_text` replaces a whole segment, so pass its full
  new text, not just the changed word. If a date and an amount sit in the
  same segment (`find` shows it as one `text`), change both in one
  `edit_text`: `"Amount due: $4,120.50 by March 3"` becomes `"Amount due:
  $5,310.00 by April 30"`. For a word that appears many times use
  `replace_all`.
- Several changes: write a JSON list of steps and run them in one go. Steps
  run in order and each sees the result of the ones before it (so you can
  highlight text an earlier step wrote). Ids renamed by an earlier step are
  followed automatically. If any step fails, nothing is saved.

  ```bash
  cat > steps.json <<'JSON'
  [
    {"op": "replace_all", "args": {"find": "2025", "replace": "2026"}},
    {"op": "edit_text", "args": {"id": "t:0:446.6:192.0", "text": "$1,250.00"}},
    {"op": "add_annotation", "args": {"page": 0, "kind": "highlight", "target_text": "$1,250.00"}}
  ]
  JSON
  seamless-pdf edit in.pdf --script steps.json -o out.pdf
  ```
- **Always check visually** after editing: `render` the page (use `--clip
  x0,y0,x1,y1` to zoom) and look at the PNG.
- Edits keep the original font. If a character is missing from an embedded
  subset font, the result says `font_matched: false`; the character comes from
  a close fallback font. Mention it to the user.
- Scanned pages: run `seamless-pdf run ocr_pdf in.pdf -o in-ocr.pdf` first;
  then scanned words are editable segments like any other text.
- Images: `insert_image` / `replace_image` take `"image_path": "logo.png"`.

`seamless-pdf ops` prints every operation with its JSON schema; the summary is
in [reference/operations.md](reference/operations.md).

## Conventions (important)

- **Pages:** editing commands and operations use **0-based** page indexes,
  and so do the `page` fields in their output (`objects`, `find`, `fields`).
  Document tools take **1-based** ranges like `"1-3,5"`, `"odd"`, `"last"`,
  `"all"`, and report 1-based page numbers in their results. So the first
  page is `"page": 0` from `fields` but `"page": 1` from `make_fillable`.
- **Coordinates:** PDF points (1/72 inch), origin at the **top-left** of the
  unrotated page; rects are `[x0, y0, x1, y1]`.
- Output: `-o FILE` names the result of `edit` and of any `run` that makes
  one file. Without `-o`, `edit` writes `<name>-edited.pdf` and tools write
  next to the first input (or into `--out-dir`). Always take the real path
  from the JSON (`saved`, or `outputs[].path`). Never overwrite the user's
  original unless asked.

## Document tools

```bash
seamless-pdf tools                       # catalog (name, category, inputs)
seamless-pdf tool-info compress_pdf      # one tool's options
seamless-pdf run merge_pdfs a.pdf b.pdf --out-dir out/
seamless-pdf run compress_pdf big.pdf --opt level=high
seamless-pdf run protect_pdf in.pdf --opt user_password=secret
seamless-pdf run redact_pdf in.pdf --opt 'presets=["email","phone"]' -o redacted.pdf
seamless-pdf run convert_to_pdf report.docx photo.heic notes.md --out-dir out/
seamless-pdf run pdf_to_excel tables.pdf
```

- `--opt` values are parsed as JSON when possible (numbers, `true`, lists);
  otherwise they are strings. `--options '{"k": v}'` passes them all at once.
- Tools marked `in_place` change the given PDF and save it to `-o` (default
  `<name>-<tool>.pdf`). The others create new files; with `-o` a single
  result is saved there, and several results (split, PDF to images) go into
  that file's folder under their own names.
- Read every result path from the JSON (`saved` for in-place tools,
  `outputs[].path` otherwise) instead of guessing names. Several results are
  named `<input-stem>-page-N.<ext>` and similar.
- Order matters when combining: number pages *after* merging if the numbers
  should run across the whole merged file (`Page 1 of 2`, `Page 2 of 2`).
  Fill forms before you split or merge; form fields survive both (clashing
  field names are renamed), and `flatten_pdf` locks the filled values in.
- Some tools need system programs: LibreOffice (Office formats), Chrome or
  Chromium (HTML, Markdown, web pages, invoices), Tesseract (OCR), pandoc
  (EPUB). `tools` marks unavailable ones with the reason.
- Not offered: PDF to Word, PDF/A conversion.

The full catalog by category is in [reference/tools.md](reference/tools.md).

## Common tasks

| Task | Command |
|---|---|
| Read a PDF | `text in.pdf` (or `text in.pdf --pages 0,2`) |
| Change a date/amount/name | `find`, then `edit --op edit_text` with the segment's full new text |
| Same word everywhere | `edit --op replace_all --args '{"find": "Acme", "replace": "Globex"}'` |
| Fill a form | `fields in.pdf`, then `run fill_form_fields in.pdf --options '{"values": {"name": "Jane"}}'` |
| Blanks (`____`, lines, boxes) to fields | `run make_fillable in.pdf -o fillable.pdf`, then `fields fillable.pdf` for the names |
| Sign | `run sign_pdf in.pdf --opt name="Jane Doe"` (visual) or `run digital_sign_pdf` (cryptographic) |
| Remove sensitive data | `run redact_pdf in.pdf --opt 'presets=["email","phone"]'`: the text is removed from the file and a black box drawn over it (`--opt fill=#ffffff` for white, `--opt label=REDACTED` to label boxes). Punctuation next to a match, such as a comma, stays. |
| Make smaller | `run compress_pdf --opt level=high` (big gains only when there are images) |
| Page numbers | `run add_page_numbers in.pdf --opt 'format=Page {n} of {total}' -o numbered.pdf` |
| Highlight text | `edit --op add_annotation --args '{"page": 0, "kind": "highlight", "target_text": "$5,310.00"}'` |
| Scanned to searchable | `run ocr_pdf` |
| A page as an image | one page: `render in.pdf --page 0 -o page1.png`; many: `run pdf_to_png in.pdf --opt pages=1-3 --opt dpi=200` |
| Pages | `merge_pdfs`, `split_pdf`, `rearrange_pages`, `rotate_pages`, `remove_pages`, `extract_pages` |

## When something fails

Errors are JSON on stderr with exit code 1, e.g. `{"error": "page 3 does not
exist (document has 2 pages, 0-based)"}`. Read the message, fix the argument,
retry. A failed `edit` leaves the file unchanged.
