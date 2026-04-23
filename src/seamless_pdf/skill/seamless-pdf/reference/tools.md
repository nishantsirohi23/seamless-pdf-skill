# Document tools

67 tools. Run with `seamless-pdf run NAME [inputs...] --opt key=value`, or the
MCP tool `run_pdf_tool`. Page ranges are 1-based (`1-3,5`, `odd`, `even`, `last`, `all`).
*In place* tools change the PDF (saved with `-o`); the others create new files.
Generated from the code by `scripts/gen_reference.py`.

## Create

### `create_pdf` (Create PDF)

Write a new PDF from Markdown, HTML or plain text (headings, lists, tables, links), on any paper size. *(no input)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `title` | string |  |  |
| `content` | string |  | The text. Markdown by default. (required) |
| `format` | one of `markdown`, `html`, `text` | `markdown` |  |
| `page_size` | one of `A4`, `Letter`, `Legal`, `A3`, `A5`, `A6`, `B5`, `Tabloid`, `Executive` | `A4` |  |
| `orientation` | one of `portrait`, `landscape` | `portrait` |  |
| `font` | one of `sans`, `serif`, `mono` | `sans` |  |
| `font_size` | number | `11` |  |
| `margin_mm` | number | `20` |  |
| `output_name` | string |  |  |

Also known as: `write_pdf`, `new_pdf`, `text_to_pdf_new`

### `create_pdf_form` (Create PDF form)

Generate a fillable PDF form from a list of fields (label + type), with a title and intro text. *(no input)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `title` | string | `Form` |  |
| `intro` | string |  |  |
| `fields` | array of object |  | Fields in order. (required) |
| `page_size` | one of `A4`, `Letter`, `Legal`, `A3`, `A5`, `A6`, `B5`, `Tabloid`, `Executive` | `A4` |  |

Also known as: `new_form`, `fillable_form`

### `job_application` (Create PDF job application)

Assemble a job application: a cover page (name, position, contact, photo), the cover letter, then your CV, certificates and other files (PDFs, Word, images) merged in order, with bookmarks. *(any files)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `applicant_name` | string |  | (required) |
| `position` | string |  | (required) |
| `company` | string |  |  |
| `email` | string |  |  |
| `phone` | string |  |  |
| `address` | string |  |  |
| `photo` | string |  | Applicant photo (optional). |
| `cover_letter` | string |  | Markdown. |
| `include_cover_page` | boolean | `True` |  |

Also known as: `application_pdf`, `bewerbung`

### `create_card` (Create card)

Design a card: business card, postcard, greeting card, invitation or place card, with title, message and details. *(no input)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `kind` | one of `business_card`, `postcard`, `greeting_card`, `invitation`, `place_card`, `a5_flyer` | `greeting_card` |  |
| `title` | string |  | Main line (name, headline). (required) |
| `subtitle` | string |  |  |
| `message` | string |  |  |
| `details` | string |  | Small print: phone, email, address, date, venue... |
| `accent_color` | string | `#4f46e5` |  |
| `style` | one of `modern`, `classic`, `bold` | `modern` |  |
| `copies_per_a4` | boolean | `False` | Tile copies on an A4 sheet for printing. |

Also known as: `business_card`, `greeting_card`, `invitation`

### `photo_collage` (Create photo collage)

Arrange photos in a grid on one or more pages, with spacing, background colour and an optional title. *(files)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `columns` | integer | `3` |  |
| `page_size` | one of `A4`, `Letter`, `Legal`, `A3`, `A5`, `A6`, `B5`, `Tabloid`, `Executive` | `A4` |  |
| `orientation` | one of `portrait`, `landscape` | `landscape` |  |
| `spacing_mm` | number | `4` |  |
| `fill` | one of `fit`, `crop` | `crop` |  |
| `background` | string | `#ffffff` |  |
| `title` | string |  |  |

Also known as: `collage`, `photo_grid`

### `generate_qr_code` (Generate QR code)

Make a QR code for a link, text, Wi-Fi login, contact card, etc. as PNG, SVG or PDF. *(no input)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `data` | string |  | URL or text to encode. (required) |
| `format` | one of `png`, `svg`, `pdf` | `png` |  |
| `error_correction` | one of `L`, `M`, `Q`, `H` | `M` |  |
| `scale` | integer | `10` |  |
| `color` | string | `#000000` |  |
| `background` | string | `#ffffff` |  |

Also known as: `qr_code`, `qr`

## Edit

### `add_qr_code` (Add QR code to PDF)

Stamp a QR code onto a page of the open PDF at a position or rect. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `data` | string |  | (required) |
| `page` | integer | `1` |  |
| `position` | one of `top-left`, `top-right`, `bottom-left`, `bottom-right` | `bottom-right` |  |
| `size` | number | `72` | Side length in points. |
| `rect` | array of number |  | Overrides position. |

### `add_page_numbers` (Add page numbers)

Number the pages, e.g. 'Page {n} of {total}' at the bottom centre. {n} is the page number, {total} the count. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `format` | string | `{n}` | e.g. {n}, Page {n} of {total}, - {n} - |
| `position` | one of `top-left`, `top-center`, `top-right`, `bottom-left`, `bottom-center`, `bottom-right` | `bottom-center` |  |
| `start_at` | integer | `1` |  |
| `font_size` | number | `10` |  |
| `color` | string | `#000000` |  |
| `margin` | number | `28` |  |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `page_numbers`, `number_pages`

### `add_watermark` (Add watermark)

Stamp a text or image watermark on pages, e.g. CONFIDENTIAL diagonally across every page, behind or over the content. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `text` | string |  | Watermark text (or use image_file). |
| `image_file` | string |  | Image to use instead of text. |
| `font_size` | number | `60` |  |
| `color` | string | `#cc0000` |  |
| `opacity` | number | `0.25` |  |
| `rotation` | number | `45` | Degrees counter-clockwise. |
| `position` | one of `center`, `top-left`, `top-center`, `top-right`, `bottom-left`, `bottom-center`, `bottom-right`, `tile` | `center` |  |
| `behind_content` | boolean | `False` |  |
| `image_width` | number | `200` | Image width in points. |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `watermark`, `stamp`

### `set_metadata` (Change PDF document information)

Read or set title, author, subject, keywords, creator and producer. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `title` | string |  |  |
| `author` | string |  |  |
| `subject` | string |  |  |
| `keywords` | string |  |  |
| `creator` | string |  |  |
| `producer` | string |  |  |

Also known as: `document_information`, `metadata`

### `change_page_size` (Change PDF page size)

Resize pages to a paper size (A4, Letter, ...), scaling the content to fit. Annotations and form fields are flattened into the page. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `size` | string | `A4` | A4, Letter, Legal, A3, A5 ... or e.g. 210x297mm |
| `orientation` | one of `auto`, `portrait`, `landscape` | `auto` |  |
| `fit` | one of `fit`, `stretch` | `fit` |  |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `resize_pages`, `page_size`

### `crop_pages` (Crop PDF)

Crop pages by margins (in mm) or automatically trim white space around the content. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `mode` | one of `margins`, `auto_trim` | `margins` |  |
| `top` | number | `10` |  |
| `right` | number | `10` |  |
| `bottom` | number | `10` |  |
| `left` | number | `10` |  |
| `padding` | number | `5` | mm kept around content (auto_trim). |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `crop`, `trim`

### `fill_form_fields` (Fill out PDF)

Fill several form fields at once by field name. Checkboxes take true/false. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `values` | object |  | {field name: value} (required) |

Also known as: `fill_out_pdf`, `fill_form`

### `flatten_pdf` (Flatten PDF)

Burn annotations and/or form fields into the page so they can no longer be edited. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `annotations` | boolean | `True` |  |
| `form_fields` | boolean | `True` |  |

Also known as: `flatten`

### `make_fillable` (Make PDF fillable)

Detect blanks in a form (underscore lines, drawn lines, boxes) and turn them into fillable text fields and checkboxes, named after their labels. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |
| `font_size` | number | `0` | 0 = auto |

Also known as: `make_pdf_fillable`, `detect_form_fields`

### `overlay_pdf` (PDF Overlay)

Lay the pages of another PDF (letterhead, stamp, form) over or under this one's pages. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `overlay_file` | string |  | PDF to lay over/under. (required) |
| `mode` | one of `foreground`, `background` | `foreground` |  |
| `page_mapping` | one of `first_page_on_all`, `page_by_page`, `cycle` | `first_page_on_all` |  |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `overlay`, `letterhead`

### `viewer_preferences` (Set PDF viewer preferences)

Control how readers open the PDF: page layout, side panel, full screen, fit window, show title, start page. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `page_layout` | one of `SinglePage`, `OneColumn`, `TwoColumnLeft`, `TwoColumnRight`, `TwoPageLeft`, `TwoPageRight` |  |  |
| `page_mode` | one of `UseNone`, `UseOutlines`, `UseThumbs`, `FullScreen` |  |  |
| `fit_window` | boolean |  |  |
| `center_window` | boolean |  |  |
| `hide_toolbar` | boolean |  |  |
| `hide_menubar` | boolean |  |  |
| `display_doc_title` | boolean |  |  |
| `open_page` | integer |  | 1-based page to open at. |

## Organize

### `assemble_pdf` (Assemble PDF)

Build a new PDF from chosen pages of several PDFs, in any order. selection lists file:pages parts, e.g. '1:1-3, 2:all, 1:5' (file numbers follow the input order). *(one or more PDFs)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `selection` | string |  | e.g. 1:1-3, 2:all, 1:5 (required) |
| `output_name` | string |  | Name of the output file (optional). |

### `bookmarks` (Bookmark PDF)

List, add, replace or clear bookmarks (outline), or create them automatically from headings. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `action` | one of `list`, `add`, `replace`, `clear`, `auto_from_headings` | `list` |  |
| `items` | array of object |  | Bookmarks for add/replace. |

Also known as: `bookmark_pdf`, `outline`, `toc`

### `extract_text` (Copy from PDF)

Extract the text of a PDF (or some pages) as plain text, Markdown, or structured JSON with positions. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |
| `format` | one of `text`, `markdown`, `json` | `text` |  |

Also known as: `copy_from_pdf`, `copy_text`

### `extract_images` (Extract PDF images)

Save the images embedded in a PDF as separate files (plus a zip). *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `format` | one of `original`, `png`, `jpg` | `original` |  |
| `min_size` | integer | `32` | Skip images smaller than this (px). |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

### `extract_pages` (Extract PDF pages)

Copy the given pages into a new PDF (or one PDF per page). *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string |  | Pages, 1-based: all, 1-3,5, odd, even, last (required) |
| `separate_files` | boolean | `False` |  |

### `halve_pages` (Halve PDF pages)

Cut each page into two halves (left/right or top/bottom), e.g. for scanned book spreads. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `direction` | one of `vertical_cut`, `horizontal_cut` | `vertical_cut` | vertical_cut = left and right halves |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `split_pages_in_half`

### `insert_blank_pages` (Insert blank pages)

Insert empty pages after a page (0 = at the start). *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `after` | integer | `0` | Insert after this page (0 = start). |
| `count` | integer | `1` |  |
| `size` | one of `same`, `A4`, `Letter`, `Legal`, `A3`, `A5`, `A6`, `B5`, `Tabloid`, `Executive` | `same` |  |

### `merge_pdfs` (Merge PDF)

Combine several PDFs into one, in the given order. *(one or more PDFs)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `output_name` | string |  | Name of the output file (optional). |
| `bookmarks` | boolean | `True` | Add a bookmark for each source file. |

Also known as: `merge`, `combine`, `join_pdfs`

### `pages_per_sheet` (Pages per sheet)

Put several pages on each sheet (n-up), e.g. 2 or 4 pages per sheet. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `per_sheet` | one of `2`, `4`, `6`, `8`, `9`, `16` | `2` |  |
| `paper` | one of `A4`, `Letter`, `Legal`, `A3`, `A5`, `A6`, `B5`, `Tabloid`, `Executive` | `A4` |  |
| `order` | one of `rows`, `columns` | `rows` |  |
| `margin` | number | `18` |  |
| `border` | boolean | `False` |  |

Also known as: `n_up`, `nup`

### `rearrange_pages` (Rearrange PDF pages)

Put the pages in a new order, e.g. '3,1,2,4-10' or 'reverse'. Pages left out are removed; repeated pages are duplicated. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `order` | string |  | e.g. 3,1,2,4-10 or reverse (required) |

Also known as: `reorder_pages`, `sort_pages`

### `remove_pages` (Remove PDF pages)

Delete the given pages. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string |  | Pages, 1-based: all, 1-3,5, odd, even, last (required) |

Also known as: `delete_pages`

### `rotate_pages` (Rotate PDF pages)

Rotate pages by 90, 180 or 270 degrees clockwise. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |
| `angle` | one of `90`, `180`, `270`, `-90` | `90` |  |

Also known as: `rotate`

### `split_pdf` (Split PDF)

Split a PDF into several files: every N pages, at given ranges, into single pages, or one file per top-level bookmark. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `mode` | one of `every_n_pages`, `ranges`, `single_pages`, `bookmarks` | `every_n_pages` |  |
| `n` | integer | `1` | Pages per file (every_n_pages). |
| `ranges` | string |  | Groups separated by ';', e.g. 1-3; 4-6; 7- |

Also known as: `split`

## Optimize

### `compress_pdf` (Compress PDF)

Make a PDF smaller: downsample and recompress images to the resolution they are shown at, and compact the file structure. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `level` | one of `low`, `medium`, `high`, `extreme` | `medium` |  |
| `grayscale` | boolean | `False` |  |

Also known as: `compress`, `reduce_size`, `shrink`

### `ocr_pdf` (PDF OCR)

Recognise text on scanned pages so it becomes searchable, selectable and editable (adds an invisible text layer). *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `language` | string | `eng` | Tesseract language(s), e.g. eng, deu, eng+fra |
| `pages` | string | `scanned` | 'scanned' (pages without text), 'all', or e.g. 1-3 |
| `dpi` | integer | `300` |  |

Also known as: `ocr`, `make_searchable`

### `rasterize_pdf` (Rasterize PDF)

Turn every page into an image (flattens everything; text is no longer selectable unless you run OCR). *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `dpi` | integer | `150` |  |
| `format` | one of `jpg`, `png` | `jpg` |  |
| `quality` | integer | `80` |  |
| `grayscale` | boolean | `False` |  |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `flatten_to_images`

### `repair_pdf` (Repair PDF)

Rebuild a damaged PDF (broken cross-reference table, truncated file, bad objects) into a clean, valid file. *(one PDF)*

Also known as: `fix_pdf`, `repair`

### `web_optimize` (Web optimize PDF)

Linearize the PDF (fast web view) so browsers can show page 1 before the whole file has downloaded. *(one PDF)*

Also known as: `linearize`, `fast_web_view`

## Security

### `digital_sign_pdf` (Digitally sign PDF)

Apply a cryptographic (PAdES) signature that proves who signed and that the file has not changed since. Uses your .p12/.pfx certificate, or creates a self-signed one. Do this as the very last step. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `signer_name` | string | `Signer` |  |
| `reason` | string |  |  |
| `location` | string |  |  |
| `certificate_file` | string |  | PKCS#12 (.p12/.pfx) with your key. |
| `certificate_password` | string |  |  |
| `visible` | boolean | `False` |  |
| `page` | integer | `1` |  |
| `rect` | array of number |  | Visible box [x0,y0,x1,y1], top-left origin. |

Also known as: `certify_pdf`, `pades_sign`

### `generate_password` (Generate password)

Generate strong random passwords. *(no input)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `length` | integer | `20` |  |
| `symbols` | boolean | `True` |  |
| `count` | integer | `5` |  |

### `protect_pdf` (Protect PDF)

Encrypt a PDF with AES-256: a password to open it and/or restrictions on printing, copying and editing. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `user_password` | string |  | Needed to open the file (leave empty for none). |
| `owner_password` | string |  | Needed to change permissions (random if empty). |
| `allow_print` | boolean | `True` |  |
| `allow_copy` | boolean | `True` |  |
| `allow_modify` | boolean | `True` |  |
| `allow_annotate` | boolean | `True` |  |
| `allow_forms` | boolean | `True` |  |

Also known as: `encrypt_pdf`, `password_protect`

### `redact_pdf` (Redact PDF)

Permanently remove sensitive content: given words/phrases, regex matches, or presets (emails, phone numbers, IBANs, card numbers, ...). The text is deleted from the file, not just covered. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `terms` | array of string |  | Words or phrases to redact. |
| `presets` | array of one of `email`, `phone`, `iban`, `credit_card`, `ssn`, `date`, `url` |  | Built-in patterns. |
| `regex` | string |  | Custom regular expression. |
| `case_sensitive` | boolean | `False` |  |
| `fill` | string | `#000000` |  |
| `label` | string |  | Optional text shown on each box, e.g. REDACTED. |
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `redact`, `blackout`

### `remove_metadata` (Remove PDF metadata)

Strip author/title/dates, XMP metadata and optionally JavaScript, attachments, thumbnails and hidden text. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `javascript` | boolean | `True` |  |
| `attachments` | boolean | `True` |  |
| `thumbnails` | boolean | `True` |  |
| `hidden_text` | boolean | `False` |  |
| `links` | boolean | `False` |  |

Also known as: `scrub`, `remove_pdf_metadata`, `sanitize`

### `sign_pdf` (Sign PDF)

Place a handwritten-style signature: an image (drawn or uploaded) or a typed name in a script font, optionally with the date, at a position or rect on a page. For a cryptographic signature use digital_sign_pdf. *(one PDF, in place)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `signature_image` | string |  | Image of the signature (PNG with transparency is best). |
| `name` | string |  | Typed signature (used when no image). |
| `page` | integer | `0` | 1-based page; 0 = last page. |
| `rect` | array of number |  | [x0,y0,x1,y1] in points, top-left origin. Default: bottom right. |
| `add_date` | boolean | `True` |  |
| `color` | string | `#1a237e` |  |

Also known as: `signature`, `esign`

### `unlock_pdf` (Unlock PDF)

Remove the password and restrictions from a PDF you have the password for. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `password` | string |  |  |

Also known as: `decrypt_pdf`, `remove_password`

### `verify_signatures` (Verify PDF signatures)

Check every digital signature: is the file unchanged since signing, who signed, when, and whether the signer is trusted. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `trusted_certificate` | string |  | Optional PEM/DER certificate to trust. |

Also known as: `validate_signatures`, `check_signature`

## View & check

### `check_pdfa` (Check PDF/A)

Check whether a PDF claims PDF/A and run the key conformance checks (embedded fonts, no encryption, output intent, no JavaScript). This is a quick check, not a full veraPDF validation. *(one PDF)*

Also known as: `validate_pdfa`

### `compare_pdfs` (Compare PDFs)

Compare two PDFs: a text diff per page plus a visual diff PDF with changes highlighted in red. *(one or more PDFs)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `visual` | boolean | `True` |  |
| `sensitivity` | integer | `40` | Grey-level difference that counts as a change (0-255). |

Also known as: `diff_pdfs`, `compare`

### `document_info` (Document info)

Everything about a PDF: pages and sizes, metadata, fonts (embedded or not), images, forms, annotations, signatures, encryption, attachments, bookmarks, PDF/A claim. *(one PDF)*

Also known as: `pdf_info`, `inspect_pdf`, `properties`

### `search_pdfs` (Search in PDFs)

Find text (or a regular expression) across one or more PDFs, with page numbers and context. *(one or more PDFs)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `query` | string |  | (required) |
| `regex` | boolean | `False` |  |
| `case_sensitive` | boolean | `False` |  |

Also known as: `search`, `find_in_pdfs`

## Convert to PDF

### `convert_to_pdf` (Convert to PDF)

Convert files to PDF: Word, PowerPoint, Excel, Publisher, OpenDocument, RTF, images, text, Markdown, HTML, EPUB, CBZ comics. Each file becomes a PDF (or all are merged into one). *(files)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `merge` | boolean | `False` | Merge all results into one PDF. |
| `page_size` | one of `A4`, `Letter`, `Legal`, `A3`, `A5`, `A6`, `B5`, `Tabloid`, `Executive` | `A4` | For text/Markdown/HTML/EPUB input. |

Also known as: `word_to_pdf`, `docx_to_pdf`, `doc_to_pdf`, `powerpoint_to_pdf`, `pptx_to_pdf`, `ppt_to_pdf`, `excel_to_pdf`, `xlsx_to_pdf`, `xls_to_pdf`, `publisher_to_pdf`, `pub_to_pdf`, `odt_to_pdf`, `ods_to_pdf`, `odp_to_pdf`, `odg_to_pdf`, `rtf_to_pdf`, `text_to_pdf`, `txt_to_pdf`, `markdown_to_pdf`, `md_to_pdf`, `html_to_pdf`, `epub_to_pdf`, `svg_to_pdf`, `view_as_pdf`, `cbz_to_pdf`

### `images_to_pdf` (Images to PDF)

Combine images (JPG, PNG, WEBP, HEIC, TIFF, GIF, BMP, SVG ...) into one PDF, one image per page. *(files)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `page_size` | one of `fit`, `A4`, `Letter`, `Legal`, `A3`, `A5`, `A6`, `B5`, `Tabloid`, `Executive` | `fit` |  |
| `orientation` | one of `auto`, `portrait`, `landscape` | `auto` |  |
| `margin` | number | `0` |  |
| `output_name` | string |  |  |

Also known as: `jpg_to_pdf`, `png_to_pdf`, `webp_to_pdf`, `heic_to_pdf`, `tiff_to_pdf`, `photos_to_pdf`, `image_to_pdf`

### `webpage_to_pdf` (Webpage to PDF)

Save a web page (URL) as a PDF, as Chrome would print it. *(no input)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `url` | string |  | https://... (required) |
| `output_name` | string |  |  |

Also known as: `url_to_pdf`, `website_to_pdf`

## Convert from PDF

### `pdf_to_epub` (PDF to EPUB)

Convert the PDF to EPUB. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

### `pdf_to_excel` (PDF to Excel)

Tables found in the PDF, one sheet each (text lines if there are no tables). *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `pdf_to_xls`

### `pdf_to_html` (PDF to HTML)

Convert the PDF to HTML. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

### `pdf_to_jpg` (PDF to JPG)

Convert the PDF to JPG. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |
| `dpi` | integer | `150` |  |

Also known as: `pdf_to_jpeg`

### `pdf_to_md` (PDF to Markdown)

Convert the PDF to Markdown. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `pdf_to_markdown`

### `pdf_to_odp` (PDF to ODP)

Convert the PDF to ODP. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

### `pdf_to_ods` (PDF to ODS)

Convert the PDF to ODS. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

### `pdf_to_png` (PDF to PNG)

Convert the PDF to PNG. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |
| `dpi` | integer | `150` |  |

Also known as: `pdf_to_images`, `pdf_to_image`

### `pdf_to_powerpoint` (PDF to PowerPoint)

One slide per page (page image; page text in the speaker notes). *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `pdf_to_ppt`

### `pdf_to_tiff` (PDF to TIFF)

Convert the PDF to TIFF. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |
| `dpi` | integer | `150` |  |

### `pdf_to_txt` (PDF to Text)

Convert the PDF to Text. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |

Also known as: `pdf_to_text`

### `pdf_to_webp` (PDF to WEBP)

Convert the PDF to WEBP. *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `pages` | string | `all` | Pages, 1-based: all, 1-3,5, odd, even, last |
| `dpi` | integer | `150` |  |

## Invoices

### `create_e_invoice` (Create electronic invoice)

Create an e-invoice: a readable PDF with embedded EN 16931 XML (Factur-X / ZUGFeRD), plus the XML as CII or UBL (XRechnung-style). *(no input)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `invoice_number` | string |  | (required) |
| `issue_date` | string |  | YYYY-MM-DD (default today) |
| `due_date` | string |  |  |
| `delivery_date` | string |  |  |
| `currency` | string | `EUR` |  |
| `seller` | object |  | (required) |
| `buyer` | object |  | (required) |
| `items` | array of object |  | (required) |
| `iban` | string |  |  |
| `bic` | string |  |  |
| `buyer_reference` | string |  | PO number / Leitweg-ID |
| `payment_terms` | string |  |  |
| `notes` | string |  |  |
| `logo` | string |  | Logo image (optional). |
| `accent_color` | string | `#1f3a8a` |  |
| `syntax` | one of `cii`, `ubl` | `cii` | Extra XML file syntax. |

Also known as: `e_invoice`, `zugferd`, `facturx`, `xrechnung`

### `create_invoice` (Create invoice)

Create a professional invoice PDF from seller, buyer and line items; VAT and totals are calculated. Edit it visually afterwards in the editor. *(no input)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `invoice_number` | string |  | (required) |
| `issue_date` | string |  | YYYY-MM-DD (default today) |
| `due_date` | string |  |  |
| `delivery_date` | string |  |  |
| `currency` | string | `EUR` |  |
| `seller` | object |  | (required) |
| `buyer` | object |  | (required) |
| `items` | array of object |  | (required) |
| `iban` | string |  |  |
| `bic` | string |  |  |
| `buyer_reference` | string |  | PO number / Leitweg-ID |
| `payment_terms` | string |  |  |
| `notes` | string |  |  |
| `logo` | string |  | Logo image (optional). |
| `accent_color` | string | `#1f3a8a` |  |

Also known as: `invoice`, `create_invoice_visually`

### `pdf_invoice_to_e_invoice` (PDF invoice to e-invoice)

Turn an existing PDF invoice into a Factur-X/ZUGFeRD e-invoice by embedding EN 16931 XML built from the invoice data (read the data from the PDF first, e.g. with extract_text, and pass it here). *(one PDF)*

| Option | Type | Default | Notes |
|---|---|---|---|
| `invoice_number` | string |  | (required) |
| `issue_date` | string |  | YYYY-MM-DD (default today) |
| `due_date` | string |  |  |
| `delivery_date` | string |  |  |
| `currency` | string | `EUR` |  |
| `seller` | object |  | (required) |
| `buyer` | object |  | (required) |
| `items` | array of object |  | (required) |
| `iban` | string |  |  |
| `bic` | string |  |  |
| `buyer_reference` | string |  | PO number / Leitweg-ID |
| `payment_terms` | string |  |  |
| `notes` | string |  |  |
| `logo` | string |  | Logo image (optional). |
| `accent_color` | string | `#1f3a8a` |  |

Also known as: `convert_invoice_to_einvoice`

### `validate_e_invoice` (Validate e-invoice)

Check an e-invoice (Factur-X/ZUGFeRD PDF or CII/UBL XML): XML schema validity, profile, and whether line items, VAT and totals add up. *(files)*

Also known as: `check_e_invoice`, `validate_zugferd`, `validate_xrechnung`

### `xml_e_invoice_to_pdf` (XML e-invoice to PDF)

Render an XML e-invoice (CII/Factur-X/ZUGFeRD or UBL/XRechnung) as a readable PDF; CII input is also embedded, making a Factur-X PDF. *(files)*

Also known as: `xrechnung_to_pdf`, `view_e_invoice`
