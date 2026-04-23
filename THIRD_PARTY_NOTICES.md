# Third-party components

Bundled with the package:

| Component | Where | Licence |
|---|---|---|
| DejaVu Sans 2.35 (Unicode fallback font) | `src/seamless_pdf/assets/fonts/DejaVuSans*.ttf` | Bitstream Vera licence + public domain changes, see `DejaVu-LICENSE.txt` |
| Dancing Script (typed signatures) | `src/seamless_pdf/assets/fonts/DancingScript-Regular.ttf` | SIL Open Font License 1.1, see `DancingScript-OFL.txt` |

Python dependencies (installed by pip, not bundled), all permissive: pikepdf
(MPL-2.0), pypdfium2 (Apache-2.0 / BSD-3-Clause; PDFium BSD-3-Clause),
fontTools (MIT), ReportLab (BSD), pdfplumber and pdfminer.six (MIT), pypdf
(BSD), Pillow (MIT-CMU), pillow-heif (BSD), pyHanko (MIT), cryptography
(Apache-2.0 / BSD), factur-x (BSD), segno (BSD), python-docx and python-pptx
(MIT), openpyxl (MIT), Markdown (BSD), NumPy (BSD), mcp (MIT, optional).
factur-x pulls in python-stdnum (LGPL-2.1), used unmodified.

No AGPL or GPL code is linked: no PyMuPDF/MuPDF, pdf2docx or Ghostscript.

Optional external programs, run as separate processes and not distributed:
LibreOffice (MPL-2.0), Tesseract (Apache-2.0), Chrome/Chromium, pandoc (GPL).
