"""Layouts for the demo families' papers. Each takes an expanded paper spec and returns the rendered
document, the line boxes, and the truth about what is printed where.

Every institution named on these papers is fictional, every page carries a 'synthetic test
document' watermark, and government papers use a generic layout rather than any real form.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .layout import Doc, Line, Style

PASSBOOK = (430.0, 300.0)
CARD = (330.0, 210.0)


@dataclass
class Rendered:
    pdf: bytes
    pages: int
    lines: list[Line]
    fields: dict[str, dict] = field(default_factory=dict)        # name -> {value, line_ids, page}
    rows: list[dict] = field(default_factory=list)                # statement rows with line ids
    size: tuple[float, float] = (595.0, 842.0)


def _style(spec: dict) -> Style:
    st = spec.get("style", {})
    return Style(font=st.get("font", "sans-serif"), size=float(st.get("size", 9)),
                 accent=st.get("accent", "#1D4E89"), lang=st.get("lang", "en"), rtl=bool(st.get("rtl", False)),
                 margin=float(st.get("margin", 42)))


def _collect(doc: Doc, spec: dict) -> dict[str, dict]:
    """Field truth: the value as printed and the lines that carry it."""
    out: dict[str, dict] = {}
    declared = spec.get("fields", {})
    for line in doc.lines:
        if line.field:
            item = out.setdefault(line.field, {"value": declared.get(line.field, {}).get("value") if isinstance(
                declared.get(line.field), dict) else declared.get(line.field), "as_printed": line.text,
                "line_ids": [], "page": line.page})
            item["line_ids"].append(line.id)
    for name, val in declared.items():
        if name not in out:
            out[name] = {"value": val.get("value") if isinstance(val, dict) else val, "as_printed": None,
                         "line_ids": [], "page": None}
    return out


def _brand(doc: Doc, spec: dict) -> None:
    b = spec.get("brand")
    if not b:
        return
    st = doc.style
    w = doc.size[0]
    doc.page.draw_rect((0, 0, w, 6), color=None, fill=_hex(st.accent))
    doc.y = st.margin - 10
    doc.put((st.margin, doc.y, w - st.margin, doc.y + 20), b["name"], size=16, bold=True, color=st.accent,
            lang=b.get("lang"), field_name=b.get("field"))
    if b.get("name_local"):
        doc.put((w / 2, doc.y + 2, w - st.margin, doc.y + 18), b["name_local"], size=11, bold=True, color=st.accent,
                align="right", lang=b.get("local_lang"))
    doc.y += 21
    if b.get("line"):
        doc.put((st.margin, doc.y, w - st.margin, doc.y + 11), b["line"], size=7.5, color=st.muted, lang=b.get("lang"))
        doc.y += 12
    doc.rule(doc.y + 2, width=0.8, color=st.accent)
    doc.y += 9


def _hex(c: str) -> tuple[float, float, float]:
    h = c.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------
def statement(spec: dict) -> Rendered:
    """Bank, card, CPF and pension statements: header, details, then a running table."""
    doc = Doc(style=_style(spec))
    doc.footer = spec.get("footer", "Page {page} of {pages}")
    _brand(doc, spec)
    if spec.get("heading"):
        doc.title(spec["heading"], size=12, lang=spec.get("heading_lang"))
    if spec.get("kv"):
        doc.kv([tuple(x) + (None,) * (3 - len(x)) for x in spec["kv"]], cols=spec.get("kv_cols", 2),
               label_w=spec.get("label_w", 95), size=8.5)
    cols = spec["columns"]
    rows_out: list[dict] = []
    groups = spec["row_groups"]            # list of lists of row dicts (one group per page)

    def cont(d: Doc) -> None:
        d.y = d.style.margin - 6
        d.put((d.style.margin, d.y, d.size[0] - d.style.margin, d.y + 12),
              spec.get("continued", "Statement of account (continued)"), size=8, color=d.style.muted)
        d.y += 13
        if spec.get("continued_kv"):
            d.kv([tuple(x) + (None,) * (3 - len(x)) for x in spec["continued_kv"]], cols=2, label_w=80, size=8)

    for gi, group in enumerate(groups):
        if gi > 0:
            doc.new_page()
            cont(doc)
        cells = doc.table(cols, group, size=spec.get("table_size", 8.2), page_header=cont)
        for r, c in zip(group, cells, strict=True):
            if r.get("_kind") == "txn":
                rows_out.append({**{k: v for k, v in r.items() if not k.startswith("_") or k in ("_row", "_meta")},
                                 "line_ids": {k: ln.id for k, ln in c.items()},
                                 "page": next(iter(c.values())).page if c else doc.page_no})
    if spec.get("closing_note"):
        doc.gap(4)
        doc.text(spec["closing_note"], size=8, color=doc.style.muted)
    if spec.get("fine_print"):
        doc.gap(6)
        doc.paragraph(spec["fine_print"], size=7, width_chars=120, color=doc.style.muted)
    pdf = doc.finish()
    return Rendered(pdf=pdf, pages=doc.page_no, lines=doc.lines, fields=_collect(doc, spec), rows=rows_out,
                    size=doc.size)


def passbook(spec: dict) -> Rendered:
    """A savings passbook: each passbook page is one small page of rows (photographed later)."""
    doc = Doc(style=_style(spec), size=PASSBOOK)
    doc.style.margin = 22
    doc.watermark = True
    rows_out: list[dict] = []
    for gi, group in enumerate(spec["row_groups"]):
        if gi > 0:
            doc.new_page()
        st = doc.style
        w = doc.size[0]
        doc.y = 14
        doc.put((st.margin, doc.y, w / 2, doc.y + 14), spec["brand"]["name"], size=11, bold=True, color=st.accent)
        doc.put((w / 2, doc.y, w - st.margin, doc.y + 14), spec.get("heading", "SB PASS BOOK"), size=9, bold=True,
                align="right", color=st.accent)
        doc.y += 16
        for label, value, fname in spec.get("kv", []):
            doc.put((st.margin, doc.y, st.margin + 34, doc.y + 11), f"{label}:", size=8)
            doc.put((st.margin + 36, doc.y, w - st.margin, doc.y + 11), value, size=8, field_name=fname)
            doc.y += 11
        doc.y += 3
        cells = doc.table(spec["columns"], group, size=7.6, header_fill="#EFE8D2", zebra=None)
        for r, c in zip(group, cells, strict=True):
            if r.get("_kind") == "txn":
                rows_out.append({**{k: v for k, v in r.items() if not k.startswith("_") or k in ("_row", "_meta")},
                                 "line_ids": {k: ln.id for k, ln in c.items()},
                                 "page": next(iter(c.values())).page if c else doc.page_no})
    pdf = doc.finish()
    return Rendered(pdf=pdf, pages=doc.page_no, lines=doc.lines, fields=_collect(doc, spec), rows=rows_out,
                    size=doc.size)


def certificate(spec: dict) -> Rendered:
    """Certificates, policies, receipts and slips: titled forms of labelled values."""
    size = tuple(spec.get("page_size", (595.0, 842.0)))
    doc = Doc(style=_style(spec), size=size)  # type: ignore[arg-type]
    _brand(doc, spec)
    w = doc.size[0]
    st = doc.style
    if spec.get("pre_title"):
        doc.text(spec["pre_title"], size=8.5, align="center", color=st.muted, lang=spec.get("pre_title_lang"))
    for t in spec.get("titles", []):
        doc.title(t["text"], size=t.get("size", 14), align="center", lang=t.get("lang"))
    if spec.get("subtitle"):
        doc.text(spec["subtitle"], size=8.5, align="center", color=st.muted, lang=spec.get("subtitle_lang"))
    doc.gap(6)
    for section in spec.get("sections", []):
        if section.get("heading"):
            doc.text(section["heading"], size=9.5, bold=True, color=st.accent, lang=section.get("lang"), gap=4)
        if section.get("kv"):
            doc.kv([tuple(x) + (None,) * (3 - len(x)) for x in section["kv"]], cols=section.get("cols", 1),
                   label_w=section.get("label_w", 170), size=section.get("size", 9.2), lang=section.get("lang"))
        if section.get("table"):
            t = section["table"]
            cells = doc.table(t["columns"], t["rows"], size=t.get("size", 8.4), lang=section.get("lang"))
            for r, c in zip(t["rows"], cells, strict=True):
                for k, ln in c.items():
                    if r.get("_fields", {}).get(k):
                        ln.field = r["_fields"][k]
        if section.get("text"):
            doc.paragraph(section["text"], size=section.get("size", 8.8), width_chars=section.get("wrap", 100),
                          lang=section.get("lang"))
    if spec.get("stamp"):
        s = spec["stamp"]
        x0 = w - st.margin - 150
        doc.ensure(70)
        doc.stamp(s["text"], (x0, doc.y + 6, x0 + 140, doc.y + 52), lang=s.get("lang"), round_=s.get("round", False),
                  field_name=s.get("field"))
    if spec.get("signature"):
        sg = spec["signature"]
        doc.signature(sg["label"], name=sg.get("name"), lang=sg.get("lang"))
    if spec.get("note"):
        doc.gap(8)
        doc.paragraph(spec["note"], size=7.5, width_chars=110, lang=spec.get("note_lang"), color=st.muted)
    pdf = doc.finish()
    return Rendered(pdf=pdf, pages=doc.page_no, lines=doc.lines, fields=_collect(doc, spec), size=doc.size)


def letter(spec: dict) -> Rendered:
    doc = Doc(style=_style(spec))
    _brand(doc, spec)
    for k in ("date", "ref"):
        if spec.get(k):
            doc.text(spec[k], size=9, align="right", field_name=spec.get(f"{k}_field"), lang=spec.get("lang"))
    doc.gap(6)
    for ln in spec.get("to", []):
        doc.text(ln, size=9, lang=spec.get("lang"), gap=1)
    doc.gap(8)
    if spec.get("subject"):
        doc.text(spec["subject"], size=9.5, bold=True, lang=spec.get("lang"), field_name=spec.get("subject_field"))
        doc.gap(4)
    for para in spec.get("body", []):
        if isinstance(para, dict):
            doc.paragraph(para["text"], size=9, width_chars=spec.get("wrap", 98), lang=spec.get("lang"),
                          field_name=para.get("field"))
        else:
            doc.paragraph(para, size=9, width_chars=spec.get("wrap", 98), lang=spec.get("lang"))
    if spec.get("kv"):
        doc.kv([tuple(x) + (None,) * (3 - len(x)) for x in spec["kv"]], cols=1, label_w=170, size=9,
               lang=spec.get("lang"))
    for para in spec.get("after", []):
        doc.paragraph(para, size=9, width_chars=spec.get("wrap", 98), lang=spec.get("lang"))
    if spec.get("signature"):
        sg = spec["signature"]
        doc.gap(6)
        doc.signature(sg["label"], name=sg.get("name"), lang=spec.get("lang"))
    pdf = doc.finish()
    return Rendered(pdf=pdf, pages=doc.page_no, lines=doc.lines, fields=_collect(doc, spec), size=doc.size)


def notes(spec: dict) -> Rendered:
    """Handwritten notes on a ruled page (photographed later)."""
    doc = Doc(style=_style(spec), size=(420.0, 560.0))
    doc.watermark = True
    w, h = doc.size
    y = 50.0
    while y < h - 30:
        doc.page.draw_line((20, y), (w - 20, y), color=(0.72, 0.80, 0.90), width=0.5)
        y += 26
    doc.page.draw_line((48, 20), (48, h - 20), color=(0.9, 0.55, 0.55), width=0.6)
    y = 30.0
    for i, item in enumerate(spec["lines"]):
        text = item["text"] if isinstance(item, dict) else item
        indent = 58 + (i % 3) * 2
        doc.put((indent, y, w - 24, y + 24), text, size=item.get("size", 15) if isinstance(item, dict) else 15,
                color="#1F3B8A", italic=True, font="serif", lang=spec.get("style", {}).get("lang"),
                field_name=item.get("field") if isinstance(item, dict) else None)
        y += 26
    pdf = doc.finish()
    return Rendered(pdf=pdf, pages=1, lines=doc.lines, fields=_collect(doc, spec), size=doc.size)


def holdings(spec: dict) -> Rendered:
    """A depository holding statement: account details and the securities held."""
    doc = Doc(style=_style(spec))
    doc.footer = "Page {page} of {pages}"
    _brand(doc, spec)
    doc.title(spec.get("heading", "Statement of Holdings"), size=12)
    doc.kv([tuple(x) + (None,) * (3 - len(x)) for x in spec["kv"]], cols=2, label_w=100, size=8.5)
    cells = doc.table(spec["columns"], spec["rows"], size=8.2)
    for r, c in zip(spec["rows"], cells, strict=True):
        for k, ln in c.items():
            if r.get("_fields", {}).get(k):
                ln.field = r["_fields"][k]
    if spec.get("closing_note"):
        doc.text(spec["closing_note"], size=8, color=doc.style.muted)
    pdf = doc.finish()
    return Rendered(pdf=pdf, pages=doc.page_no, lines=doc.lines, fields=_collect(doc, spec), size=doc.size)


def card(spec: dict) -> Rendered:
    """A small identity card, clearly marked as a synthetic specimen."""
    doc = Doc(style=_style(spec), size=CARD)
    st = doc.style
    st.margin = 16
    w, h = doc.size
    doc.page.draw_rect((0, 0, w, h), color=None, fill=_hex(spec.get("background", "#E9F1F7")))
    doc.page.draw_rect((0, 0, w, 30), color=None, fill=_hex(st.accent))
    doc.put((16, 8, w - 16, 24), spec["header"], size=9.5, bold=True, color="#FFFFFF", lang=spec.get("header_lang"))
    doc.y = 40
    for label, value, fname in spec["kv"]:
        doc.put((16, doc.y, 110, doc.y + 11), label, size=7, color=st.muted)
        doc.put((110, doc.y, w - 16, doc.y + 12), value, size=9, bold=True, field_name=fname)
        doc.y += 17
    doc.put((16, h - 36, w - 16, h - 24), "SPECIMEN · SAMPLE · NOT VALID", size=8, bold=True, color="#B03A2E",
            align="center", record=False)
    doc.watermark = False
    pdf = doc.finish()
    return Rendered(pdf=pdf, pages=1, lines=doc.lines, fields=_collect(doc, spec), size=doc.size)


TEMPLATES: dict[str, Any] = {
    "statement": statement, "passbook": passbook, "certificate": certificate, "letter": letter,
    "notes": notes, "holdings": holdings, "card": card,
}
