# Editing operations

Used with `seamless-pdf edit --op NAME --args JSON` (or as steps in `--script`),
and by the MCP tool `edit_pdf`. Pages are 0-based; coordinates are PDF points
with a top-left origin. Generated from the code by `scripts/gen_reference.py`.

`insert_image` and `replace_image` also accept `"image_path": "logo.png"` (a local
file) in place of `asset_id`.

## Reading (no changes)

### `get_document_info`

Overview: page count and size, which pages are scanned images, whether there is a form, uploaded image assets, and undo state. Call first when you know nothing about the document.

### `get_page_objects`

List every editable object on a page with its id, kind, bbox and style: text segments (text, font, size, colour), images, annotations and form fields. Use this to find ids and exact coordinates before editing, moving or inserting near something.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | yes | 0-based page index. |
| `kinds` | array of one of `text`, `image`, `annotation`, `field` |  | Only these kinds (default: all). |

### `find_text`

Find text segments containing a string (case-insensitive unless match_case). Returns matching segments with ids, plus phrases that span several segments (edit each segment of those).

| Argument | Type | Required | Notes |
|---|---|---|---|
| `query` | string | yes |  |
| `match_case` | boolean |  |  |

### `get_document_text`

Plain text of the document (or of the given pages), for understanding content. Does not give ids; use find_text or get_page_objects for that.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `pages` | array of integer |  |  |

### `list_form_fields`

All form fields in the document with ids, types, names, current values and options.

### `view_page`

Look at a rendered image of a page (optionally a clip rect, which zooms in). Use it to check layout before placing things and to verify your edits look right.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | yes | 0-based page index. |
| `clip` | array of number |  | [x0, y0, x1, y1] in PDF points, origin top-left of the page. |

## Changing the document

### `edit_text`

Replace and/or restyle one text segment in place, seamlessly: same font program, size, colour, spacing and baseline unless you change them. anchor=auto keeps right-aligned/centred columns aligned. Works on scanned (OCR) words too.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Object id from get_page_objects / find_text. |
| `text` | string |  | New text for the whole segment. Omit to keep. |
| `font_size` | number |  |  |
| `color` | string |  | Colour as #rrggbb. |
| `anchor` | one of `auto`, `left`, `right`, `center` |  |  |
| `font` | one of `helvetica`, `times`, `courier` |  | Switch to a standard font family. Omit to keep the document's own font. |
| `bold` | boolean |  |  |
| `italic` | boolean |  |  |

### `replace_all`

Replace every occurrence of a string inside text segments across the document (or given pages), seamlessly. Ideal for names, dates, amounts that repeat.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `find` | string | yes |  |
| `replace` | string | yes |  |
| `match_case` | boolean |  |  |
| `pages` | array of integer |  |  |

### `insert_text`

Add new text. (x, y) is the baseline start of the first line in PDF points. By default it copies the font, size and colour of the nearest existing text (or of match_style_of) so it blends in. Use \n for new lines; give width to word-wrap and align.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | yes | 0-based page index. |
| `x` | number | yes |  |
| `y` | number | yes |  |
| `text` | string | yes |  |
| `font_size` | number |  |  |
| `color` | string |  | Colour as #rrggbb. |
| `font` | one of `helvetica`, `times`, `courier` |  | Switch to a standard font family. Omit to keep the document's own font. |
| `bold` | boolean |  |  |
| `italic` | boolean |  |  |
| `width` | number |  | Wrap width in points. |
| `align` | one of `left`, `center`, `right` |  |  |
| `match_style_of` | string |  | Text id to copy style from. |
| `line_height` | number |  |  |

### `delete_objects`

Delete objects of any kind (text segments, images, annotations, fields) by id. Text and images are removed without leaving a box.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `ids` | array of string | yes | Object ids. |

### `move_objects`

Move objects of any kind by (dx, dy) PDF points (positive dy = down). Text keeps its font; images keep their pixels; scanned words move their pixels. Returns new ids for moved text.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `ids` | array of string | yes | Object ids. |
| `dx` | number | yes |  |
| `dy` | number | yes |  |

### `set_object_rect`

Resize/reposition an image, annotation or form field to an exact rect.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Object id from get_page_objects / find_text. |
| `rect` | array of number | yes | [x0, y0, x1, y1] in PDF points, origin top-left of the page. |

### `insert_image`

Place an uploaded image asset (see get_document_info assets) into rect on a page, keeping its aspect ratio.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | yes | 0-based page index. |
| `asset_id` | string | yes |  |
| `rect` | array of number | yes | [x0, y0, x1, y1] in PDF points, origin top-left of the page. |

### `replace_image`

Swap an image's picture for an uploaded asset, keeping its position.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Object id from get_page_objects / find_text. |
| `asset_id` | string | yes |  |

### `add_annotation`

Add an annotation. Text markup (highlight, underline, strikeout, squiggly) needs target_text (all matches on the page), ids (text segments) or rect (words inside it). note needs point; freetext, rect, circle need rect; line uses rect as [x1, y1, x2, y2].

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | yes | 0-based page index. |
| `kind` | one of `highlight`, `underline`, `strikeout`, `squiggly`, `note`, `freetext`, `rect`, `circle`, `line` | yes |  |
| `rect` | array of number |  | [x0, y0, x1, y1] in PDF points, origin top-left of the page. |
| `point` | array of number |  | [x, y] in PDF points. |
| `text` | string |  | Note/free-text content or comment. |
| `color` | string |  | Colour as #rrggbb. |
| `fill` | string |  | Colour as #rrggbb. |
| `target_text` | string |  |  |
| `ids` | array of string |  | Object ids. |
| `font_size` | number |  |  |
| `opacity` | number |  |  |
| `line_width` | number |  |  |

### `update_annotation`

Change an annotation's text, colour or opacity.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Object id from get_page_objects / find_text. |
| `text` | string |  |  |
| `color` | string |  | Colour as #rrggbb. |
| `opacity` | number |  |  |

### `set_field_value`

Fill a form field. Checkboxes/radios take "true"/"false"; choice fields take one of their options.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Object id from get_page_objects / find_text. |
| `value` | string | yes |  |

### `add_form_field`

Create a fillable form field.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | yes | 0-based page index. |
| `rect` | array of number | yes | [x0, y0, x1, y1] in PDF points, origin top-left of the page. |
| `field_type` | one of `text`, `checkbox`, `combobox`, `listbox`, `radio` | yes |  |
| `name` | string | yes |  |
| `value` | string |  |  |
| `options` | array of string |  |  |
| `font_size` | number |  |  |

### `flatten_forms`

Burn all form field values into the pages and remove the fields (not fillable afterwards). Only when the user asks to flatten.

### `ocr_page`

Run OCR on a scanned page to make its words searchable and editable text objects. Needed before editing text on a scanned page.

| Argument | Type | Required | Notes |
|---|---|---|---|
| `page` | integer | yes | 0-based page index. |
| `language` | string |  | Tesseract language code(s), e.g. eng or eng+deu. |

## History

### `undo`

Undo the most recent change.

### `redo`

Redo the most recently undone change.

Reading operations are also CLI commands: `info`, `objects`, `find`, `text`, `fields`, `render`.
