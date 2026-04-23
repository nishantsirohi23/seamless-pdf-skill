"""Invoices: create (PDF), electronic invoice (Factur-X / ZUGFeRD, EN 16931
CII + UBL XML), PDF invoice -> e-invoice, XML e-invoice -> PDF, validate."""
from __future__ import annotations

import datetime
import html as _html
import logging
from decimal import ROUND_HALF_UP, Decimal

from ..engine import EditError
from .base import FILE_OPT, FILES, NONE, PDF, tool
from .render import html_to_pdf

CAT = "Invoices"
D2 = Decimal("0.01")

PARTY = {"type": "object", "properties": {
    "name": {"type": "string"}, "address": {"type": "string"}, "postcode": {"type": "string"},
    "city": {"type": "string"}, "country_code": {"type": "string", "description": "ISO, e.g. DE, FR, US"},
    "vat_id": {"type": "string"}, "email": {"type": "string"}, "phone": {"type": "string"}}}
ITEM = {"type": "object", "properties": {
    "description": {"type": "string"}, "quantity": {"type": "number"},
    "unit": {"type": "string", "description": "piece, hour, day, kg, ... or a UN/ECE code"},
    "unit_price": {"type": "number", "description": "Net price per unit"},
    "vat_rate": {"type": "number", "description": "Percent, e.g. 19"}}}
INVOICE_OPTS = {
    "invoice_number": {"type": "string"},
    "issue_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD (default today)"},
    "due_date": {"type": "string", "format": "date"},
    "delivery_date": {"type": "string", "format": "date"},
    "currency": {"type": "string", "default": "EUR"},
    "seller": PARTY, "buyer": PARTY,
    "items": {"type": "array", "items": ITEM},
    "iban": {"type": "string"}, "bic": {"type": "string"},
    "buyer_reference": {"type": "string", "description": "PO number / Leitweg-ID"},
    "payment_terms": {"type": "string"}, "notes": {"type": "string", "format": "textarea"},
    "logo": {**FILE_OPT, "description": "Logo image (optional)."},
    "accent_color": {"type": "string", "format": "color", "default": "#1f3a8a"},
}
REQ = ["invoice_number", "seller", "buyer", "items"]
UNITS = {"piece": "H87", "pcs": "H87", "pc": "H87", "hour": "HUR", "hours": "HUR", "h": "HUR",
         "day": "DAY", "days": "DAY", "kg": "KGM", "g": "GRM", "m": "MTR", "km": "KMT", "l": "LTR",
         "month": "MON", "week": "WEE", "unit": "C62", "": "C62", "lump sum": "LS"}


def _d(v) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else 0))
    except Exception:
        raise EditError(f"not a number: {v!r}")


def _norm(o: dict) -> dict:
    """Validate invoice data and compute line, VAT and grand totals."""
    items = o.get("items") or []
    if not items:
        raise EditError("an invoice needs at least one item")
    lines, vat = [], {}
    for i, it in enumerate(items, 1):
        qty, price, rate = _d(it.get("quantity", 1)), _d(it.get("unit_price")), _d(it.get("vat_rate", 0))
        net = (qty * price).quantize(D2, ROUND_HALF_UP)
        unit = str(it.get("unit") or "")
        lines.append({"no": i, "description": it.get("description") or f"Item {i}", "qty": qty,
                      "unit": unit, "unit_code": UNITS.get(unit.lower(), unit if len(unit) == 3 and unit.isupper() else "C62"),
                      "price": price, "rate": rate, "net": net})
        vat.setdefault(rate, Decimal(0))
        vat[rate] += net
    breakdown = [{"rate": r, "base": b.quantize(D2), "tax": (b * r / 100).quantize(D2, ROUND_HALF_UP)}
                 for r, b in sorted(vat.items())]
    net_total = sum((l["net"] for l in lines), Decimal(0))
    tax_total = sum((b["tax"] for b in breakdown), Decimal(0))
    today = datetime.date.today()
    issue = o.get("issue_date") or today.isoformat()
    for k in ("issue_date", "due_date", "delivery_date"):
        if o.get(k):
            try:
                datetime.date.fromisoformat(o[k])
            except ValueError:
                raise EditError(f"{k} must be YYYY-MM-DD")
    for side in ("seller", "buyer"):
        if not (o.get(side) or {}).get("name"):
            raise EditError(f"{side}.name is required")
    return {**o, "lines": lines, "breakdown": breakdown, "net_total": net_total, "tax_total": tax_total,
            "grand_total": net_total + tax_total, "issue_date": issue,
            "currency": (o.get("currency") or "EUR").upper()}


def _money(v: Decimal, cur: str) -> str:
    return f"{v:,.2f}&#160;{cur}"  # never wrap between amount and currency


def _invoice_html(n: dict, logo_uri: str | None) -> tuple[str, str]:
    e = lambda s: _html.escape(str(s or ""))  # noqa: E731
    cur, acc = n["currency"], n.get("accent_color") or "#1f3a8a"
    s, b = n["seller"], n["buyer"]

    def party(p):
        return "<br/>".join(e(x) for x in [p.get("name"), p.get("address"),
                                           " ".join(filter(None, [p.get("postcode"), p.get("city")])),
                                           p.get("country_code"), p.get("vat_id") and f"VAT ID {p['vat_id']}",
                                           p.get("email"), p.get("phone")] if x)
    rows = "".join(
        f"<tr><td>{l['no']}</td><td>{e(l['description'])}</td><td class='r'>{l['qty'].normalize():f} {e(l['unit'])}</td>"
        f"<td class='r'>{_money(l['price'], cur)}</td><td class='r'>{l['rate'].normalize():f}%</td>"
        f"<td class='r'>{_money(l['net'], cur)}</td></tr>" for l in n["lines"])
    vat_rows = "".join(f"<tr><td>VAT {bd['rate'].normalize():f}%</td>"
                       f"<td class='r'>{_money(bd['tax'], cur)}</td></tr>" for bd in n["breakdown"])
    meta = "".join(f"<tr><td>{k}</td><td class='r'>{e(v)}</td></tr>" for k, v in [
        ("Invoice no.", n["invoice_number"]), ("Date", n["issue_date"]), ("Due date", n.get("due_date")),
        ("Delivery date", n.get("delivery_date")), ("Your reference", n.get("buyer_reference"))] if v)
    pay = " · ".join(e(x) for x in [n.get("iban") and f"IBAN {n['iban']}", n.get("bic") and f"BIC {n['bic']}"] if x)
    html = f"""<html><body>
      <table class='hd'><tr><td>{f"<img src='{logo_uri}' height='48'/>" if logo_uri else ''}
      <div class='sn'>{e(s.get('name'))}</div></td><td class='r'><h1>INVOICE</h1></td></tr></table>
      <table class='pt'><tr><td><div class='lbl'>Bill to</div>{party(b)}</td>
      <td><div class='lbl'>From</div>{party(s)}</td><td><table class='meta'>{meta}</table></td></tr></table>
      <table class='items'><tr><th>#</th><th>Description</th><th class='r'>Qty</th><th class='r'>Unit price</th>
      <th class='r'>VAT</th><th class='r'>Net</th></tr>{rows}</table>
      <table class='tot'><tr><td>Net total</td><td class='r'>{_money(n['net_total'], cur)}</td></tr>{vat_rows}
      <tr class='g'><td>Total due</td><td class='r'>{_money(n['grand_total'], cur)}</td></tr></table>
      {f"<p class='note'>{e(n.get('payment_terms'))}</p>" if n.get('payment_terms') else ''}
      {f"<p class='note'>{pay}</p>" if pay else ''}
      {f"<p class='note'>{e(n.get('notes'))}</p>" if n.get('notes') else ''}
      </body></html>"""
    css = f"""
      body {{ font-size: 9.5pt; }} h1 {{ color: {acc}; font-size: 24pt; letter-spacing: 2pt; margin: 0; }}
      .sn {{ font-size: 13pt; font-weight: bold; margin-top: 4pt; }} .r {{ text-align: right; }}
      table, td, th {{ border: none; }} .hd td {{ vertical-align: top; }}
      .pt {{ margin: 18pt 0; }} .pt td {{ vertical-align: top; width: 33%; padding-right: 10pt; }}
      .lbl {{ color: {acc}; font-size: 8pt; text-transform: uppercase; font-weight: bold; margin-bottom: 3pt; }}
      .meta td {{ padding: 1pt 0; }}
      .items th {{ background: {acc}; color: white; padding: 5pt; border: none; }}
      .items td {{ padding: 5pt; border-bottom: 0.5pt solid #d9dce1; }}
      .tot {{ width: 62%; margin-left: 38%; margin-top: 10pt; }} .tot td {{ padding: 3pt 5pt; }}

      .tot .g td {{ font-weight: bold; font-size: 11pt; border-top: 1pt solid {acc}; }}
      .note {{ color: #4b5563; margin-top: 10pt; }}"""
    return html, css


def _render(ctx, n: dict) -> bytes:
    files, uri = {}, None
    if n.get("logo"):
        _, data, _ = ctx.store.file_bytes(n["logo"])
        files["logo.img"] = data
        uri = "logo.img"
    html, css = _invoice_html(n, uri)
    return html_to_pdf(html, css, files=files, margin=46)


def _facturx_dict(n: dict) -> dict:
    def party(p):
        d = {"name": p["name"], "country_code": (p.get("country_code") or "DE").upper()}
        for k, src in (("addr_l1", "address"), ("city", "city"), ("postcode", "postcode")):
            if p.get(src):
                d[k] = p[src]
        if p.get("vat_id"):
            d["vat_identifier"] = p["vat_id"]
        return d
    s = lambda v: f"{v:.2f}"  # noqa: E731
    d = {"BT-1": n["invoice_number"], "BT-2": n["issue_date"], "BT-3": "380", "BT-5": n["currency"],
         "BT-72": n.get("delivery_date") or n["issue_date"],
         "BG-4": party(n["seller"]), "BG-7": party(n["buyer"]),
         "BG-25": [{"BT-126": str(l["no"]), "BT-129": f"{l['qty'].normalize():f}", "BT-130": l["unit_code"],
                    "BT-131": s(l["net"]), "BT-146": f"{l['price'].normalize():f}",
                    "BT-151": "S" if l["rate"] > 0 else "Z", "BT-152": f"{l['rate'].normalize():f}",
                    "BT-153": l["description"]} for l in n["lines"]],
         "BG-23": [{"BT-116": s(b["base"]), "BT-117": s(b["tax"]), "BT-118": "S" if b["rate"] > 0 else "Z",
                    "BT-119": f"{b['rate'].normalize():f}"} for b in n["breakdown"]],
         "BT-106": s(n["net_total"]), "BT-109": s(n["net_total"]), "BT-110": s(n["tax_total"]),
         "BT-112": s(n["grand_total"]), "BT-115": s(n["grand_total"])}
    if n.get("due_date"):
        d["BT-9"] = n["due_date"]
    if n.get("buyer_reference"):
        d["BT-10"] = n["buyer_reference"]
    if n.get("iban"):
        d["BT-81"] = "58"
        d["BT-84"] = n["iban"].replace(" ", "")
        if n.get("bic"):
            d["BT-86"] = n["bic"]
    if n.get("payment_terms"):
        d["BT-20"] = n["payment_terms"]
    if n.get("notes"):
        d["BG-1"] = [{"BT-22": n["notes"]}]
    return d


def _xml(n: dict, syntax: str = "cii") -> bytes:
    import facturx
    logging.getLogger("facturx").setLevel(logging.ERROR)
    d = _facturx_dict(n)
    try:
        x = facturx.generate_cii_xml(d, level="en16931", check_schematron=False) if syntax == "cii" \
            else facturx.generate_ubl_xml(d, level="en16931", check_schematron=False)
    except Exception as e:
        raise EditError(f"e-invoice data is not valid: {str(e)[:400]}")
    return x if isinstance(x, bytes) else x.encode()


def _embed(pdf: bytes, xml: bytes, n: dict) -> bytes:
    import facturx
    return facturx.generate_from_binary(pdf, xml, flavor="factur-x", level="en16931", check_xsd=True,
                                        pdf_metadata={"author": n["seller"]["name"],
                                                      "title": f"Invoice {n['invoice_number']}",
                                                      "subject": f"Invoice {n['invoice_number']}",
                                                      "keywords": "Factur-X, ZUGFeRD, invoice"})


@tool("create_invoice", "Create invoice", CAT,
      "Create a professional invoice PDF from seller, buyer and line items; "
      "VAT and totals are calculated. Edit it visually afterwards in the editor.",
      inputs=NONE, options=INVOICE_OPTS, required=REQ, aliases=["invoice", "create_invoice_visually"])
def create_invoice(ctx, **o):
    n = _norm(o)
    ctx.add_pdf(_render(ctx, n), f"Invoice-{n['invoice_number']}")
    ctx.result = {"net": str(n["net_total"]), "vat": str(n["tax_total"]), "total": str(n["grand_total"]),
                  "currency": n["currency"]}


@tool("create_e_invoice", "Create electronic invoice", CAT,
      "Create an e-invoice: a readable PDF with embedded EN 16931 XML "
      "(Factur-X / ZUGFeRD), plus the XML as CII or UBL (XRechnung-style).",
      inputs=NONE, options={**INVOICE_OPTS, "syntax": {"type": "string", "enum": ["cii", "ubl"], "default": "cii",
                                                        "description": "Extra XML file syntax."}},
      required=REQ, aliases=["e_invoice", "zugferd", "facturx", "xrechnung"])
def create_e_invoice(ctx, syntax="cii", **o):
    n = _norm(o)
    cii = _xml(n, "cii")
    pdf = _embed(_render(ctx, n), cii, n)
    ctx.add_pdf(pdf, f"E-Invoice-{n['invoice_number']}", raw=True)
    xml = cii if syntax == "cii" else _xml(n, "ubl")
    ctx.add_file(xml, f"Invoice-{n['invoice_number']}-{syntax}.xml", "application/xml")
    ctx.result = {"profile": "Factur-X / ZUGFeRD EN 16931", "total": str(n["grand_total"]),
                  "currency": n["currency"], "xml_syntax": syntax}


@tool("pdf_invoice_to_e_invoice", "PDF invoice to e-invoice", CAT,
      "Turn an existing PDF invoice into a Factur-X/ZUGFeRD e-invoice by "
      "embedding EN 16931 XML built from the invoice data (read the data from "
      "the PDF first, e.g. with extract_text, and pass it here).",
      inputs=PDF, options=INVOICE_OPTS, required=REQ, aliases=["convert_invoice_to_einvoice"])
def pdf_invoice_to_e_invoice(ctx, **o):
    o.pop("logo", None)
    o.pop("accent_color", None)
    n = _norm(o)
    xml = _xml(n, "cii")
    ctx.add_pdf(_embed(ctx.inputs[0].data, xml, n), f"{ctx.inputs[0].stem}-e-invoice", raw=True)
    ctx.add_file(xml, f"{ctx.inputs[0].stem}-factur-x.xml", "application/xml")
    ctx.result = {"total": str(n["grand_total"]), "currency": n["currency"]}


def _from_parsed(p: dict) -> dict:
    def party(x):
        x = x or {}
        return {"name": x.get("name"), "address": x.get("addr_l1"), "postcode": x.get("postcode"),
                "city": x.get("city"), "country_code": x.get("country_code"), "vat_id": x.get("vat_identifier")}
    ds = lambda v: v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)  # noqa: E731
    return {"invoice_number": p.get("BT-1"), "issue_date": ds(p.get("BT-2")), "due_date": ds(p.get("BT-9")),
            "delivery_date": ds(p.get("BT-72")), "currency": p.get("BT-5") or "EUR",
            "seller": party(p.get("BG-4")), "buyer": party(p.get("BG-7")),
            "iban": p.get("BT-84"), "buyer_reference": p.get("BT-10"), "payment_terms": p.get("BT-20"),
            "items": [{"description": l.get("BT-153"), "quantity": l.get("BT-129") or 1,
                       "unit": l.get("BT-130") or "", "unit_price": l.get("BT-146") or 0,
                       "vat_rate": l.get("BT-152") or 0} for l in (p.get("BG-25") or [])]}


def _parse(xml: bytes) -> dict:
    import facturx
    try:
        return facturx.parse_ubl_cii_xml(xml, check_xsd=False)
    except Exception as e:
        raise EditError(f"could not read the e-invoice XML: {str(e)[:300]}")


@tool("xml_e_invoice_to_pdf", "XML e-invoice to PDF", CAT,
      "Render an XML e-invoice (CII/Factur-X/ZUGFeRD or UBL/XRechnung) as a "
      "readable PDF; CII input is also embedded, making a Factur-X PDF.",
      inputs=FILES, accept=".xml", aliases=["xrechnung_to_pdf", "view_e_invoice"])
def xml_e_invoice_to_pdf(ctx):
    for inp in ctx.inputs:
        n = _norm(_from_parsed(_parse(inp.data)))
        pdf = _render(ctx, n)
        try:
            pdf = _embed(pdf, inp.data, n)
            raw = True
        except Exception:
            raw = False  # UBL cannot be embedded as Factur-X; keep the plain PDF
        ctx.add_pdf(pdf, f"{inp.stem}", raw=raw)
    ctx.result = {"invoices": len(ctx.inputs)}


@tool("validate_e_invoice", "Validate e-invoice", CAT,
      "Check an e-invoice (Factur-X/ZUGFeRD PDF or CII/UBL XML): XML schema "
      "validity, profile, and whether line items, VAT and totals add up.",
      inputs=FILES, accept=".pdf,.xml", aliases=["check_e_invoice", "validate_zugferd", "validate_xrechnung"])
def validate_e_invoice(ctx):
    import facturx
    logging.getLogger("facturx").setLevel(logging.CRITICAL)
    reports = []
    for inp in ctx.inputs:
        rep = {"file": inp.name, "errors": [], "warnings": []}
        xml = inp.data
        if inp.data[:5] == b"%PDF-":
            try:
                fname, xml = facturx.get_xml_from_pdf(inp.data, check_xsd=False)
                rep["embedded_xml"] = fname
            except Exception as e:
                xml = None
                rep["errors"].append(f"no e-invoice XML embedded in this PDF ({str(e)[:120]})")
        if xml:
            try:
                from lxml import etree
                root = etree.fromstring(xml)
                rep["flavor"] = facturx.get_flavor(root)
                if rep["flavor"] in ("factur-x", "zugferd"):
                    rep["level"] = facturx.get_level(root)
            except Exception as e:
                rep["errors"].append(f"not a recognised e-invoice XML ({str(e)[:120]})")
            try:
                facturx.xml_check_xsd(xml)
                rep["xsd_valid"] = True
            except Exception as e:
                rep["xsd_valid"] = False
                rep["errors"].append(f"XML schema: {str(e)[:400]}")
            try:
                p = _parse(xml)
                lines = p.get("BG-25") or []
                line_sum = sum(_d(l.get("BT-131")) for l in lines)
                if p.get("BT-106") and line_sum != _d(p["BT-106"]):
                    rep["errors"].append(f"line totals {line_sum} != sum of lines BT-106 {p['BT-106']}")
                if p.get("BT-109") and p.get("BT-110") and p.get("BT-112") and \
                        _d(p["BT-109"]) + _d(p["BT-110"]) != _d(p["BT-112"]):
                    rep["errors"].append("net + VAT does not equal the grand total")
                rep["summary"] = {"number": p.get("BT-1"), "seller": (p.get("BG-4") or {}).get("name"),
                                  "buyer": (p.get("BG-7") or {}).get("name"), "total": p.get("BT-112"),
                                  "currency": p.get("BT-5"), "lines": len(lines)}
            except EditError as e:
                rep["errors"].append(str(e))
        rep["valid"] = not rep["errors"]
        reports.append(rep)
    ctx.result = {"reports": reports, "all_valid": all(r["valid"] for r in reports),
                  "note": "Schema + arithmetic checks. Country rules (e.g. XRechnung schematron) "
                          "need the official validator."}
