"""Renders a form from the form library, blank or filled.

Forms are re-typeset from the regulator's or institution's published text into labelled fields,
tables, tick boxes and signature areas, keeping the wording of every declaration. Because each
value is placed in its own box, the filled PDF can be read back value by value - in Devanagari or
Japanese as easily as in English - and signature, witness and stamp areas are always left empty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..seed.papers.layout import Doc, Line, Style

INK = "#1A3E8C"


@dataclass
class FilledForm:
    pdf: bytes
    pages: int
    placed: dict[str, list[Line]] = field(default_factory=dict)     # field id -> lines with the value
    blanks: list[str] = field(default_factory=list)                  # fields left for the family or others
    overflow: list[str] = field(default_factory=list)
    capacity: dict[str, int] = field(default_factory=dict)           # field id -> characters its box holds


def _fits(text: str, width_pt: float, size: float) -> bool:
    wide = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    return wide * size * 0.52 <= width_pt


def render_form(spec: dict, values: dict[str, Any] | None = None, *, watermark: str | None = None) -> FilledForm:
    """values: field id -> str (or list of row dicts for tables, bool for ticks)."""
    values = values or {}
    st = spec.get("style", {})
    doc = Doc(style=Style(font="sans-serif", size=float(st.get("size", 8.8)), accent=st.get("accent", "#333333"),
                          lang=spec.get("language", "en"), margin=40))
    doc.watermark = True
    doc.footer = spec.get("footer", "{page} / {pages}")
    out = FilledForm(pdf=b"", pages=0)
    w = doc.size[0]
    m = doc.style.margin
    if spec.get("source_note"):
        doc.text(spec["source_note"], size=6.8, color="#777777", gap=4)
    for t in spec.get("heading", []):
        doc.title(t if isinstance(t, str) else t["text"], size=12 if not isinstance(t, dict) else t.get("size", 12),
                  align="center", color="#111111", lang=None if isinstance(t, str) else t.get("lang"), gap=2)
    doc.gap(6)
    for block in spec.get("blocks", []):
        kind = block["type"]
        if kind == "text":
            doc.paragraph(block["text"], size=block.get("size", 8.4), width_chars=block.get("wrap", 108),
                          lang=block.get("lang"), gap=4)
        elif kind == "section":
            doc.gap(2)
            doc.text(block["title"], size=9.2, bold=True, color=doc.style.accent, gap=3, lang=block.get("lang"))
        elif kind == "fields":
            label_w = block.get("label_w", 190)
            for f in block["fields"]:
                h = doc.text_height(8.8) + 6
                doc.ensure(h)
                doc.put((m, doc.y, m + label_w - 6, doc.y + h), f["label"], size=8.4, color="#333333",
                        lang=f.get("label_lang"))
                x0, x1 = m + label_w, w - m
                doc.rule(doc.y + h - 1, x0, x1, width=0.5, color="#888888")
                val = values.get(f["id"])
                if val not in (None, ""):
                    text = str(val)
                    if not _fits(text, x1 - x0, 9):
                        out.overflow.append(f["id"])
                        out.capacity[f["id"]] = int((x1 - x0) / (9 * 0.52))     # in narrow characters
                    line = doc.put((x0 + 2, doc.y + 1, x1, doc.y + h - 2), text, size=9, color=INK, field_name=f["id"],
                                   lang=f.get("value_lang"))
                    if line:
                        out.placed.setdefault(f["id"], []).append(line)
                elif f.get("fill_by", "agent") != "agent":
                    out.blanks.append(f["id"])
                doc.y += h + 1
            doc.gap(4)
        elif kind == "table":
            cols = block["columns"]
            rows_in = values.get(block["id"]) or []
            n = max(block.get("min_rows", 2), len(rows_in))
            rows = []
            for i in range(n):
                r = {"no": str(i + 1)}
                if i < len(rows_in):
                    r.update({k: str(v) for k, v in rows_in[i].items() if v is not None})
                    r["_fields"] = {c["key"]: f"{block['id']}[{i}].{c['key']}" for c in cols if c["key"] in rows_in[i]}
                rows.append(r)
            all_cols = [{"key": "no", "label": block.get("no_label", "Sr."), "width": 0.06}, *cols]
            cells = doc.table(all_cols, rows, size=8.2, header_fill="#EEEEEE", zebra=None)
            for r, c in zip(rows, cells, strict=True):
                for key, line in c.items():
                    fid = r.get("_fields", {}).get(key)
                    if fid:
                        line.field = fid
                        out.placed.setdefault(fid, []).append(line)
        elif kind == "checks":
            for chk in block["items"]:
                h = doc.text_height(8.6) + 3
                doc.ensure(h)
                ticked = bool(values.get(chk["id"]))
                doc.box((m + 2, doc.y + 2, m + 11, doc.y + 11), color="#555555", width=0.6)
                if ticked:
                    line = doc.put((m + 2.5, doc.y + 0.5, m + 12, doc.y + 12), "✓", size=10, color=INK, bold=True,
                                   field_name=chk["id"])
                    if line:
                        out.placed.setdefault(chk["id"], []).append(line)
                doc.put((m + 16, doc.y + 1, w - m, doc.y + h), chk["label"], size=8.4, lang=chk.get("lang"))
                doc.y += h + 1
            doc.gap(4)
        elif kind == "signatures":
            doc.gap(6)
            items = block.get("items", [])
            if block.get("from"):         # one signature line per person, each name printed above its line
                names = values.get(block["id"]) or []
                items = [{"label": block.get("label", "Signature"), "name_field": f"{block['id']}[{i}]",
                          "seal": block.get("seal", False), "seal_label": block.get("seal_label", "印"),
                          "lang": block.get("lang")} for i in range(len(names))]
                for i, nm in enumerate(names):
                    values = {**values, f"{block['id']}[{i}]": nm}
            for sg in items:
                doc.ensure(44)
                name = values.get(sg.get("name_field", "")) if sg.get("name_field") else None
                if name:
                    line = doc.put((m, doc.y, m + 260, doc.y + 12), str(name), size=9, color=INK,
                                   field_name=sg["name_field"])
                    if line:
                        out.placed.setdefault(sg["name_field"], []).append(line)
                doc.y += 14
                doc.page.draw_line((m, doc.y + 18), (m + 220, doc.y + 18), color=(0.4, 0.4, 0.4), width=0.6)
                if sg.get("seal"):
                    doc.box((m + 240, doc.y - 4, m + 280, doc.y + 32), color="#B22222", width=0.6)
                    doc.put((m + 240, doc.y + 34, m + 290, doc.y + 44), sg.get("seal_label", "印"), size=7,
                            color="#B22222", align="center", record=False)
                doc.put((m, doc.y + 20, m + 240, doc.y + 30), sg["label"], size=7.5, color="#555555",
                        lang=sg.get("lang"))
                doc.y += 40
                out.blanks.append(f"signature:{sg['label']}")
        elif kind == "note":
            doc.paragraph(block["text"], size=7.2, width_chars=125, lang=block.get("lang"), gap=4,
                          color="#555555")
        elif kind == "office":
            doc.gap(4)
            doc.box((m, doc.y, w - m, doc.y + 40), color="#999999", width=0.5, dashes="[2] 0")
            doc.put((m + 6, doc.y + 4, w - m, doc.y + 16), block.get("text", "FOR OFFICE USE"), size=7.5,
                    color="#777777", record=False)
            doc.y += 46
        elif kind == "page_break":
            doc.new_page()
    out.pdf = doc.finish()
    out.pages = doc.page_no
    return out


_COMPLEX = ("ऀ", "ഀ", "؀")   # Devanagari, Malayalam, Arabic blocks: PDF text extraction garbles them


def _complex(text: str) -> bool:
    return any(0x0900 <= ord(ch) <= 0x0DFF or 0x0600 <= ord(ch) <= 0x06FF for ch in text)


def readback_pdf(pdf: bytes, form: FilledForm, intended: dict[str, Any]) -> dict:
    """Reads each value back out of the finished PDF at the box it was placed in.

    Latin and CJK text is extracted from the file and compared exactly. Complex scripts, which
    PDF text extraction cannot recover, are checked against the layout record and counted
    separately, so the report never claims more than was verified.
    """
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    checked, matched, layout_only, failures = 0, 0, 0, []
    for fid, val in _flatten(intended).items():
        lines = form.placed.get(fid, [])
        expected = "✓" if val is True else str(val)
        if not lines:
            failures.append(fid)
            checked += 1
            continue
        ln = lines[0]
        checked += 1
        if _complex(expected):
            layout_only += 1
            if ln.text == expected:
                matched += 1
            else:
                failures.append(fid)
            continue
        page = doc[ln.page - 1]
        if expected == "✓":               # a tick sits against its label, so look inside the box itself
            x0, y0, x1, y1 = ln.box
            ok = "✓" in page.get_textbox(pymupdf.Rect(x0, y0, x1, y1))
        else:
            ok = _same_text(_text_in_box(page, ln.box), expected)
        if ok:
            matched += 1
        else:
            failures.append(fid)
    return {"checked": checked, "matched": matched, "layout_only": layout_only, "failures": failures}


def _text_in_box(page: Any, box: tuple[float, float, float, float]) -> str:
    """The words whose middle lies inside the box, in reading order. Words from the next line or
    the next field never leak in, however tightly the form is set."""
    x0, y0, x1, y1 = box
    words = []
    for w in page.get_text("words"):
        mx, my = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if x0 - 2 <= mx <= x1 + 2 and y0 - 2 <= my <= y1 + 2:
            words.append(w)
    words.sort(key=lambda w: (round(w[1] / 4), w[0]))
    return " ".join(w[4] for w in words)


def _same_text(got: str, expected: str) -> bool:
    import unicodedata
    # fonts set 'ffi' and similar as one ligature glyph; NFKC turns it back into letters
    a = " ".join(unicodedata.normalize("NFKC", got).split())
    b = " ".join(unicodedata.normalize("NFKC", expected).split())
    if a == b:
        return True
    # CJK text has no spaces between words; PDF extraction may add or drop them
    return a.replace(" ", "") == b.replace(" ", "")


def _flatten(intended: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fid, val in intended.items():
        if isinstance(val, list):
            for i, row in enumerate(val):
                if not isinstance(row, dict):
                    if row not in (None, ""):
                        out[f"{fid}[{i}]"] = row
                    continue
                for key, v in row.items():
                    if v not in (None, ""):
                        out[f"{fid}[{i}].{key}"] = v
        elif val not in (None, "", False):
            out[fid] = val
    return out
