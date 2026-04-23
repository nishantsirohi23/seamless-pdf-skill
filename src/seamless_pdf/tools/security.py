"""Security & privacy: protect, unlock, redact, remove metadata, generate
password, sign (visual), digital signature, verify signatures."""
from __future__ import annotations

import datetime
import io
import logging
import math
import os
import re
import secrets
import string
import zlib

import pikepdf

from ..engine import EditError
from ..engine.geom import Rect
from ..engine.util import parse_color
from .base import FILE_OPT, NONE, PAGES_OPT, parse_pages, tool
from .render import draw_xobject_display, open_bytes

CAT = "Security"
FONT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")

PERM_KEYS = ["allow_print", "allow_copy", "allow_modify", "allow_annotate", "allow_forms"]


@tool("protect_pdf", "Protect PDF", CAT,
      "Encrypt a PDF with AES-256: a password to open it and/or restrictions on printing, copying and editing.",
      aliases=["encrypt_pdf", "password_protect"],
      options={"user_password": {"type": "string", "format": "password",
                                 "description": "Needed to open the file (leave empty for none)."},
               "owner_password": {"type": "string", "format": "password",
                                  "description": "Needed to change permissions (random if empty)."},
               **{k: {"type": "boolean", "default": True} for k in PERM_KEYS}})
def protect_pdf(ctx, user_password="", owner_password="", **perms):
    if not user_password and all(perms.get(k, True) for k in PERM_KEYS):
        raise EditError("set a user password or restrict at least one permission")
    owner = owner_password or secrets.token_urlsafe(18)
    allow = pikepdf.Permissions(
        print_lowres=perms.get("allow_print", True), print_highres=perms.get("allow_print", True),
        extract=perms.get("allow_copy", True), accessibility=True,
        modify_other=perms.get("allow_modify", True), modify_assembly=perms.get("allow_modify", True),
        modify_annotation=perms.get("allow_annotate", True), modify_form=perms.get("allow_forms", True))
    src = ctx.pdf()
    buf = io.BytesIO()
    src.save(buf, encryption=pikepdf.Encryption(owner=owner, user=user_password or "", R=6, allow=allow))
    ctx.add_pdf(buf.getvalue(), f"{ctx.inputs[0].stem}-protected.pdf", raw=True)
    ctx.result = {"encryption": "AES-256", "needs_password_to_open": bool(user_password),
                  "owner_password": owner if not owner_password else "(as given)"}


@tool("unlock_pdf", "Unlock PDF", CAT, "Remove the password and restrictions from a PDF you have the password for.",
      aliases=["decrypt_pdf", "remove_password"],
      options={"password": {"type": "string", "format": "password", "default": ""}})
def unlock_pdf(ctx, password=""):
    try:
        pdf = pikepdf.open(io.BytesIO(ctx.inputs[0].data), password=password or "")
    except pikepdf.PasswordError:
        raise EditError("wrong password")
    was = pdf.is_encrypted
    buf = io.BytesIO()
    pdf.save(buf, encryption=False)
    ctx.add_pdf(buf.getvalue(), f"{ctx.inputs[0].stem}-unlocked.pdf")
    ctx.result = {"unlocked": True} if was else {"note": "this PDF was not encrypted"}


PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "phone": r"(?<![\w+])\+?\(?\d[\d\s().-]{5,18}\d(?![\w])",
    "iban": r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b",
    "credit_card": r"\b(?:\d[ -]?){13,19}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "date": r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
    "url": r"https?://\S+|www\.\S+",
}


def _match_glyphs(doc, page_no: int, needles: set[str], case_sensitive: bool):
    """[(glyphs, bbox)] for every occurrence of each needle on the page."""
    found = []
    for line in doc._lines(page_no):
        line = sorted(line, key=lambda s: s.origin[0])
        chars, glyphs = [], []
        for s in line:
            if chars:
                chars.append(" ")
                glyphs.append(None)
            for g in s.glyphs:
                chars.append(g.char or "�")
                glyphs.append(g)
        text = "".join(chars)
        hay = text if case_sensitive else text.lower()
        for n in sorted(needles, key=len, reverse=True):
            q = n if case_sensitive else n.lower()
            start = hay.find(q)
            while start >= 0 and q:
                sel = [g for g in glyphs[start:start + len(q)] if g is not None]
                if sel:
                    box = sel[0].bbox
                    for g in sel[1:]:
                        box = box | g.bbox
                    found.append((sel, box))
                start = hay.find(q, start + len(q))
    return found


def _black_out_images(doc, page_no: int, boxes: list[Rect]) -> int:
    """Paint the redacted areas black inside any image under them."""
    from PIL import ImageDraw
    n = 0
    for pl in doc.model(page_no).images:
        if pl.obj is None or pl.inline:
            continue
        hits = [b for b in boxes if b.intersects(pl.bbox)]
        if not hits:
            continue
        try:
            im = pikepdf.PdfImage(pl.obj).as_pil_image().convert("RGB")
        except Exception:
            continue
        to_unit = (pl.ctm * doc.model(page_no).P).inverse()
        draw = ImageDraw.Draw(im)
        for b in hits:
            u = b.transform(to_unit)
            draw.rectangle([u.x0 * im.width, (1 - u.y1) * im.height, u.x1 * im.width, (1 - u.y0) * im.height],
                           fill=(0, 0, 0))
        obj = pl.obj
        obj.write(zlib.compress(im.tobytes(), 6), filter=pikepdf.Name.FlateDecode)
        obj.Width, obj.Height, obj.BitsPerComponent = im.width, im.height, 8
        obj.ColorSpace = pikepdf.Name.DeviceRGB
        for k in ("/DecodeParms", "/Decode"):
            if k in obj:
                del obj[k]
        n += 1
    return n


@tool("redact_pdf", "Redact PDF", CAT,
      "Permanently remove sensitive content: given words/phrases, regex matches, or presets (emails, phone "
      "numbers, IBANs, card numbers, ...). The text is deleted from the file, not just covered.",
      in_place=True, aliases=["redact", "blackout"],
      options={"terms": {"type": "array", "items": {"type": "string"}, "description": "Words or phrases to redact."},
               "presets": {"type": "array", "items": {"type": "string", "enum": list(PATTERNS)},
                           "description": "Built-in patterns."},
               "regex": {"type": "string", "description": "Custom regular expression."},
               "case_sensitive": {"type": "boolean", "default": False},
               "fill": {"type": "string", "format": "color", "default": "#000000"},
               "label": {"type": "string", "description": "Optional text shown on each box, e.g. REDACTED."},
               "pages": PAGES_OPT})
def redact_pdf(ctx, doc, terms=None, presets=None, regex=None, case_sensitive=False, fill="#000000",
               label=None, pages="all"):
    from ..engine.writer import append_content, color_op
    pats = [PATTERNS[p] for p in (presets or [])]
    if regex:
        try:
            re.compile(regex)
        except re.error as e:
            raise EditError(f"invalid regex: {e}")
        pats.append(regex)
    if not terms and not pats:
        raise EditError("give terms, presets or regex")
    flags = 0 if case_sensitive else re.IGNORECASE
    rgb = parse_color(fill)
    report = []
    for p in parse_pages(pages, doc.page_count):
        text = doc.page_text(p)
        needles = {t for t in (terms or []) if t}
        for pat in pats:
            for m in re.finditer(pat, text, flags):
                hit = m.group(0).strip()
                if pat == PATTERNS["phone"] and not 7 <= sum(c.isdigit() for c in hit) <= 15:
                    continue
                if hit:
                    needles.add(hit)
        found = _match_glyphs(doc, p, needles, case_sensitive)
        if not found:
            continue
        doc._erase(p, [g for gl, _ in found for g in gl])
        boxes = [b for _, b in found]
        images = _black_out_images(doc, p, boxes)
        Pi = doc.model(p).P.inverse()
        ops = []
        for b in boxes:
            r = b.transform(Pi)
            ops.append(f"q {color_op(rgb)} {r.x0:.3f} {r.y0:.3f} {r.width:.3f} {r.height:.3f} re f Q")
        append_content(doc.pdf, doc.page(p), ("\n".join(ops) + "\n").encode())
        if label:
            from .render import draw_text_display
            from ..engine.interp import rotation_matrix
            _, w, h, rot = doc.geometry(p)
            R = rotation_matrix(rot, w, h)
            for b in boxes:
                d = b.transform(R)
                size = min(8.0, d.height * 0.7)
                draw_text_display(doc, p, label, d.x0 + 1, d.y1 - (d.height - size) / 2 - size * 0.15, size,
                                  (1, 1, 1) if sum(rgb) < 1.5 else (0, 0, 0))
        doc._touch(p)
        report.append({"page": p + 1, "areas": len(boxes), "images_redacted": images,
                       "matches": sorted(needles)[:50]})
    if not report:
        raise EditError("nothing matched; nothing was redacted")
    ctx.result = {"redacted": report}


@tool("remove_metadata", "Remove PDF metadata", CAT,
      "Strip author/title/dates, XMP metadata and optionally JavaScript, attachments, thumbnails and hidden text.",
      in_place=True, aliases=["scrub", "remove_pdf_metadata", "sanitize"],
      options={"javascript": {"type": "boolean", "default": True},
               "attachments": {"type": "boolean", "default": True},
               "thumbnails": {"type": "boolean", "default": True},
               "hidden_text": {"type": "boolean", "default": False},
               "links": {"type": "boolean", "default": False}})
def remove_metadata(ctx, doc, javascript=True, attachments=True, thumbnails=True, hidden_text=False, links=False):
    pdf = doc.pdf
    before = {str(k).lstrip("/"): str(v) for k, v in pdf.docinfo.items() if str(v)}
    for k in list(pdf.docinfo.keys()):
        del pdf.docinfo[k]
    if "/Metadata" in pdf.Root:
        del pdf.Root["/Metadata"]
    names = pdf.Root.get("/Names")
    if names is not None:
        if javascript and "/JavaScript" in names:
            del names["/JavaScript"]
        if attachments and "/EmbeddedFiles" in names:
            del names["/EmbeddedFiles"]
    if javascript:
        oa = pdf.Root.get("/OpenAction")
        if isinstance(oa, pikepdf.Dictionary) and str(oa.get("/S")) == "/JavaScript":
            del pdf.Root["/OpenAction"]
        if "/AA" in pdf.Root:
            del pdf.Root["/AA"]
    for p, page in enumerate(pdf.pages):
        if thumbnails and "/Thumb" in page.obj:
            del page.obj["/Thumb"]
        if "/Metadata" in page.obj:
            del page.obj["/Metadata"]
        annots = page.obj.get("/Annots")
        if annots is not None:
            keep = []
            for a in annots:
                sub = str(a.get("/Subtype"))
                if (attachments and sub == "/FileAttachment") or (links and sub == "/Link"):
                    continue
                for k in ("/T", "/M", "/CreationDate"):
                    if k in a and sub != "/Widget":
                        del a[k]
                keep.append(a)
            page.obj.Annots = pikepdf.Array(keep)
        if hidden_text:
            inv = [g for g in doc.model(p).glyphs if g.render == 3]
            if inv:
                doc._erase(p, inv)
    doc.touch_all()
    ctx.result = {"removed_metadata": before}


@tool("generate_password", "Generate password", CAT, "Generate strong random passwords.",
      inputs=NONE,
      options={"length": {"type": "integer", "default": 20}, "symbols": {"type": "boolean", "default": True},
               "count": {"type": "integer", "default": 5}})
def generate_password(ctx, length=20, symbols=True, count=5):
    length = max(8, min(128, length))
    alphabet = string.ascii_letters + string.digits + ("!@#$%^&*()-_=+[]{};:,.?" if symbols else "")
    pw = []
    for _ in range(max(1, min(50, count))):
        while True:
            s = "".join(secrets.choice(alphabet) for _ in range(length))
            if (any(c.islower() for c in s) and any(c.isupper() for c in s) and any(c.isdigit() for c in s)
                    and (not symbols or any(not c.isalnum() for c in s))):
                break
        pw.append(s)
    ctx.result = {"passwords": pw}


@tool("sign_pdf", "Sign PDF", CAT,
      "Place a handwritten-style signature: an image (drawn or uploaded) or a typed name in a script font, "
      "optionally with the date, at a position or rect on a page. For a cryptographic signature use digital_sign_pdf.",
      in_place=True, aliases=["signature", "esign"],
      options={"signature_image": {**FILE_OPT, "description": "Image of the signature (PNG with transparency is best)."},
               "name": {"type": "string", "description": "Typed signature (used when no image)."},
               "page": {"type": "integer", "default": 0, "description": "1-based page; 0 = last page."},
               "rect": {"type": "array", "items": {"type": "number"},
                        "description": "[x0,y0,x1,y1] in points, top-left origin. Default: bottom right."},
               "add_date": {"type": "boolean", "default": True},
               "color": {"type": "string", "format": "color", "default": "#1a237e"}})
def sign_pdf(ctx, doc, signature_image=None, name=None, page=0, rect=None, add_date=True, color="#1a237e"):
    from ..engine.interp import rotation_matrix
    from ..engine.writer import append_content, font_resource, text_block
    from .render import display_size, draw_text_display
    if not signature_image and not name:
        raise EditError("give signature_image or name")
    n = doc.page_count
    p = (n - 1) if page in (0, None) else max(0, min(n, page) - 1)
    P, pw, ph, rot = doc.geometry(p)
    R = rotation_matrix(rot, pw, ph)
    W, H = display_size(doc, p)
    d = Rect.of(rect).transform(R) if rect else Rect(W - 250, H - 130, W - 50, H - 70)
    rgb = parse_color(color)
    if signature_image:
        from ..engine.images import image_xobject
        _, data, _ = ctx.store.file_bytes(signature_image)
        stream, _, _ = image_xobject(doc.pdf, data)
        draw_xobject_display(doc, p, doc.pdf.make_indirect(stream), d.list())
    else:
        obj, info = doc.fontbook.embedded(os.path.join(FONT_DIR, "DancingScript-Regular.ttf"), name)
        text = "".join(c if c in info.uni2code() else "?" for c in name)
        unit = sum(info.width(info.uni2code()[c]) for c in text) or 1
        size = min(d.height * 0.75, d.width / unit)
        to_user = R.inverse() * P.inverse()
        ox, oy = to_user.apply(d.x0, d.y1 - d.height * 0.25)
        dx, dy = to_user.apply_vec(1, 0)
        nn = math.hypot(dx, dy) or 1
        dx, dy = dx / nn, dy / nn
        res = font_resource(doc.page(p), obj)
        append_content(doc.pdf, doc.page(p),
                       text_block([(text, info, res)], [dx, dy, -dy, dx, ox, oy], size, rgb))
    # signature line and date, upright as displayed
    to_user = R.inverse() * P.inverse()
    x1, y1 = to_user.apply(d.x0, d.y1 - d.height * 0.12)
    x2, y2 = to_user.apply(d.x1, d.y1 - d.height * 0.12)
    append_content(doc.pdf, doc.page(p), f"q 0.4 G 0.6 w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S Q\n".encode())
    if add_date:
        draw_text_display(doc, p, datetime.date.today().strftime("%d %b %Y"), d.x0, d.y1 + 6, 8,
                          (0.35, 0.35, 0.35))
    doc._touch(p)
    ctx.result = {"page": p + 1}


def _self_signed(name: str, org: str = "Seamless PDF Editor (self-signed)"):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, org)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()))


@tool("digital_sign_pdf", "Digitally sign PDF", CAT,
      "Apply a cryptographic (PAdES) signature that proves who signed and that the file has not changed since. "
      "Uses your .p12/.pfx certificate, or creates a self-signed one. Do this as the very last step.",
      aliases=["certify_pdf", "pades_sign"],
      options={"signer_name": {"type": "string", "default": "Signer"},
               "reason": {"type": "string"}, "location": {"type": "string"},
               "certificate_file": {**FILE_OPT, "description": "PKCS#12 (.p12/.pfx) with your key."},
               "certificate_password": {"type": "string", "format": "password"},
               "visible": {"type": "boolean", "default": False},
               "page": {"type": "integer", "default": 1},
               "rect": {"type": "array", "items": {"type": "number"}, "description": "Visible box [x0,y0,x1,y1], top-left origin."}})
def digital_sign_pdf(ctx, signer_name="Signer", reason=None, location=None, certificate_file=None,
                     certificate_password=None, visible=False, page=1, rect=None):
    logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
    from pyhanko.keys import load_certs_from_pemder_data, load_private_key_from_pemder_data
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    from pyhanko_certvalidator.registry import SimpleCertificateStore
    if certificate_file:
        _, p12, _ = ctx.store.file_bytes(certificate_file)
        path = os.path.join(ctx.tmpdir(), "cert.p12")
        with open(path, "wb") as fh:
            fh.write(p12)
        signer = signers.SimpleSigner.load_pkcs12(path, passphrase=(certificate_password or "").encode() or None)
        if signer is None:
            raise EditError("could not load the certificate (wrong password?)")
        self_signed = False
    else:
        cert_pem, key_pem = _self_signed(signer_name)
        cert = next(iter(load_certs_from_pemder_data(cert_pem)))
        key = load_private_key_from_pemder_data(key_pem, passphrase=None)
        signer = signers.SimpleSigner(signing_cert=cert, signing_key=key,
                                      cert_registry=SimpleCertificateStore.from_certs([cert]))
        self_signed = True
    src = ctx.pdf()
    if src.is_encrypted:
        raise EditError("unlock the PDF before signing")
    buf = io.BytesIO()
    src.save(buf)
    w = IncrementalPdfFileWriter(io.BytesIO(buf.getvalue()))
    field_name = f"Signature_{datetime.datetime.now():%Y%m%d%H%M%S}"
    if visible:
        pg = src.pages[max(0, min(len(src.pages), page) - 1)]
        mb = [float(v) for v in (pg.cropbox if "/CropBox" in pg.obj else pg.mediabox)]
        r = Rect.of(rect) if rect else Rect(mb[2] - mb[0] - 250, mb[3] - mb[1] - 120, mb[2] - mb[0] - 50, mb[3] - mb[1] - 60)
        h = mb[3]
        fields.append_signature_field(w, fields.SigFieldSpec(
            field_name, on_page=max(0, min(len(src.pages), page) - 1),
            box=(int(mb[0] + r.x0), int(h - r.y1), int(mb[0] + r.x1), int(h - r.y0))))
    meta = signers.PdfSignatureMetadata(field_name=field_name, reason=reason, location=location, name=signer_name)
    out = signers.sign_pdf(w, meta, signer=signer)
    ctx.add_pdf(out.getvalue(), f"{ctx.inputs[0].stem}-signed.pdf", raw=True)
    if self_signed:
        ctx.add_file(cert_pem, f"{signer_name.replace(' ', '_')}-certificate.pem", "application/x-pem-file")
    ctx.result = {"field": field_name, "self_signed": self_signed,
                  "note": ("Self-signed: readers show the signature as valid but the signer as not trusted unless "
                           "they trust the included certificate.") if self_signed else "Signed with your certificate."}


@tool("verify_signatures", "Verify PDF signatures", CAT,
      "Check every digital signature: is the file unchanged since signing, who signed, when, and whether the "
      "signer is trusted.",
      aliases=["validate_signatures", "check_signature"],
      options={"trusted_certificate": {**FILE_OPT, "description": "Optional PEM/DER certificate to trust."}})
def verify_signatures(ctx, trusted_certificate=None):
    logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
    logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)
    from pyhanko.keys import load_certs_from_pemder_data
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    roots = []
    if trusted_certificate:
        _, data, _ = ctx.store.file_bytes(trusted_certificate)
        roots = list(load_certs_from_pemder_data(data))
    r = PdfFileReader(io.BytesIO(ctx.inputs[0].data))
    sigs = []
    for s in r.embedded_signatures:
        try:
            st = validate_pdf_signature(s, ValidationContext(trust_roots=roots or None, allow_fetching=False))
            sigs.append({"field": s.field_name,
                         "signer": st.signing_cert.subject.human_friendly if st.signing_cert else None,
                         "signed_at": str(getattr(st, "signer_reported_dt", "") or ""),
                         "unchanged_since_signing": bool(st.intact and st.valid),
                         "covers_whole_file": str(getattr(st, "coverage", "")),
                         "trusted": bool(st.trusted),
                         "summary": st.summary() if hasattr(st, "summary") else ""})
        except Exception as e:
            sigs.append({"field": s.field_name, "error": str(e)[:300]})
    ctx.result = {"signatures": sigs, "count": len(sigs)}
    if not sigs:
        ctx.result["note"] = "this PDF has no digital signatures"
