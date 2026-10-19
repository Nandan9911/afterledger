"""Offline answers for the reading agents."""
from __future__ import annotations

import re

from ..adapters.llm.models import OracleRequest
from . import Ctx, oracle

_KEYWORDS = [
    ("death_certificate", ["death", "मृत्यु", "死亡", "sterbeurkunde", "décès", "defunción", "مرگ", "وفاة", "मरण"]),
    ("passbook", ["pass book", "passbook", "通帳"]),
    ("bank_statement", ["statement of account", "account statement", "kontoauszug", "取引明細", "statement"]),
    ("fixed_deposit_receipt", ["fixed deposit", "term deposit", "fdr", "定期預金"]),
    ("insurance_policy", ["policy", "sum assured", "保険証券", "life assured"]),
    ("demat_holding_statement", ["holding", "isin", "dp id"]),
    ("card_statement", ["card", "outstanding", "ご利用明細"]),
    ("cpf_statement", ["cpf"]),
    ("payslip", ["payslip", "pay slip", "net pay"]),
    ("will", ["last will", "testament", "executor"]),
    ("pension_letter", ["pension"]),
]
_SCRIPTS = [("Deva", r"[ऀ-ॿ]"), ("Mlym", r"[ഀ-ൿ]"), ("Jpan", r"[぀-ヿ一-鿿]"),
            ("Arab", r"[؀-ۿ]"), ("Latn", r"[A-Za-z]")]
_LANG = {"Deva": "hi", "Mlym": "ml", "Jpan": "ja", "Arab": "ar", "Latn": "en"}


def _fixture(p: dict) -> str | None:
    return (p.get("paper") or {}).get("fixture")


@oracle("triage")
def triage(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam, fx = ctx.family(req), _fixture(p)
    if fam and fx:
        spec = fam.paper(fx)
        issuer = (spec.get("brand") or {}).get("name") or spec.get("pre_title")
        return {"doc_type": spec["doc_type"], "country": spec.get("country"), "languages": spec.get("languages", []),
                "scripts": spec.get("scripts", []), "issuer": issuer, "is_photo": spec.get("origin") == "photo",
                "quality": "fair" if spec.get("origin") == "photo" else "good", "title": spec["title"],
                "known_schema": True}
    text = " ".join(ln.get("text", "") for ln in p.get("lines", [])).lower()
    doc_type = next((t for t, words in _KEYWORDS if any(w in text for w in words)), "other")
    scripts = [s for s, rx in _SCRIPTS if re.search(rx, text)]
    # the issuer: an organisation's name printed near the top (a statement's letterhead)
    from ..modules.institutions import looks_like_org
    issuer = next((ln.get("text", "").strip() for ln in p.get("lines", [])[:6]
                   if looks_like_org(ln.get("text", "")) and len(ln.get("text", "")) <= 80
                   and not re.search(r"statement|account holder|account number", ln.get("text", ""), re.I)), None)
    return {"doc_type": doc_type, "country": None, "languages": [_LANG[s] for s in scripts] or ["en"],
            "scripts": scripts or ["Latn"], "issuer": issuer,
            "is_photo": (p.get("paper") or {}).get("origin") == "photo",
            "quality": "fair", "title": "", "known_schema": doc_type != "other"}


def _kind_of(name: str) -> str:
    if name.endswith(("_date", "_since", "_until", "_on")):
        return "date"
    if name.endswith(("_fee", "_amount", "balance", "_premium")):
        return "amount"
    return "identifier" if name.endswith(("_number", "_no")) else "text"


@oracle("schema_inducer")
def schema_inducer(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam, fx = ctx.family(req), _fixture(p)
    if fam and fx:                    # a demo paper: the layout a careful reader works out from its labels
        built = fam.built(fx)
        labels = {row[2]: row[0] for sec in fam.paper(fx).get("sections", []) for row in sec.get("kv", [])
                  if len(row) > 2}
        fields = [{"name": name, "label": labels.get(name) or name.replace("_", " "), "kind": _kind_of(name),
                   "required": False, "tier": "C"} for name in built.rendered.fields]
        return {"doc_type": fam.paper(fx)["doc_type"], "fields": fields, "has_transactions": bool(built.rendered.rows)}
    fields = []
    for ln in p.get("lines", [])[:40]:
        m = re.match(r"^\s*([^:：]{2,40})[:：]\s*\S", ln.get("text", ""))
        if m:
            label = m.group(1).strip()
            name = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or f"field_{len(fields) + 1}"
            if name not in {f["name"] for f in fields}:
                fields.append({"name": name, "label": label, "kind": "text", "required": False, "tier": "C"})
    return {"doc_type": p.get("doc_type", "other"), "fields": fields, "has_transactions": False}


def _map_ids(paper_id: str, ids: list[str]) -> list[str]:
    return [f"{paper_id}-{i}" for i in ids]


def _truth_reading(ctx: Ctx, req: OracleRequest, reader: str) -> dict | None:
    p = ctx.payload(req)
    fam, fx = ctx.family(req), _fixture(p)
    if not (fam and fx):
        return None
    paper_id = p["paper"]["id"]
    built = fam.built(fx)
    spec = fam.paper(fx)
    misreads = ((spec.get("misreads") or {}).get(reader) or {})
    schema_fields = {f["name"] for f in (p.get("schema") or {}).get("fields", [])}
    fields = []
    for name, info in built.rendered.fields.items():
        if schema_fields and name not in schema_fields:
            continue
        value = info.get("as_printed")
        if value is None:
            continue
        value = (misreads.get("fields") or {}).get(name, value)
        if " / " in value and name.endswith(("_name", "place_of_death", "sex", "age")):
            pass   # bilingual values are returned as printed; names are split later
        fields.append({"field": name, "value": value, "line_ids": _map_ids(paper_id, info["line_ids"]),
                       "page": info.get("page"), "confidence": 0.95})
    rows = []
    row_mis = (misreads.get("rows") or {})
    for r in built.rendered.rows:
        direction = r.get("_dir") or ("debit" if r.get("debit") else "credit")
        amount = r.get("debit") or r.get("credit") or ""
        balance = r.get("balance")
        override = row_mis.get(r.get("_row")) or row_mis.get(str(r.get("_row"))) or {}
        if override.get("balance"):
            balance = override["balance"]
        if override.get("amount"):
            amount = override["amount"]
        rows.append({"row": r["_row"], "date": r["date"], "narration": r["narration"], "amount": amount,
                     "direction": direction, "balance": balance, "page": r.get("page"),
                     "line_ids": _map_ids(paper_id, list(r.get("line_ids", {}).values()))})
    return {"fields": fields, "rows": rows, "notes": ""}


_DATE = re.compile(r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\b")
_AMT = re.compile(r"-?\d[\d,]*\.\d{2}\b")


def _baseline_reading(p: dict) -> dict:
    """Reads 'label: value' pairs and dated rows from OCR lines (Latin and CJK text layers)."""
    lines = p.get("lines", [])
    schema = p.get("schema") or {}
    fields = []
    for f in schema.get("fields", []):
        label = f.get("label", "").lower()
        keys = [label, f["name"].replace("_", " ")]
        for i, ln in enumerate(lines):
            t = ln.get("text", "")
            if any(k and k in t.lower() for k in keys):
                m = re.split(r"[:：]", t, maxsplit=1)
                value = m[1].strip() if len(m) > 1 and m[1].strip() else None
                if value is None and i + 1 < len(lines):
                    nxt = lines[i + 1]
                    if abs(nxt.get("box", [0, 0])[1] - ln.get("box", [0, 0])[1]) < 0.01:
                        value = nxt.get("text")
                if value:
                    fields.append({"field": f["name"], "value": value, "line_ids": [ln["id"]],
                                   "page": ln.get("page"), "confidence": 0.6})
                    break
    rows = []
    if schema.get("has_transactions"):
        by_y: dict[tuple[int, int], list[dict]] = {}
        for ln in lines:
            key = (ln.get("page", 1), round(ln.get("box", [0, 0])[1] * 200))
            by_y.setdefault(key, []).append(ln)
        n = 0
        for key in sorted(by_y):
            cells = sorted(by_y[key], key=lambda c: c.get("box", [0])[0])
            text = " ".join(c["text"] for c in cells)
            dm = _DATE.search(cells[0]["text"]) if cells else None
            amts = _AMT.findall(text)
            if dm and len(amts) >= 2:
                narration = " ".join(c["text"] for c in cells[1:] if not _AMT.fullmatch(c["text"].strip()))
                rows.append({"row": n, "date": dm.group(1), "narration": narration.strip(), "amount": amts[-2],
                             "direction": "credit" if re.search(r"\b(CR|CREDIT|DEP|INT)\b", text.upper()) else "debit",
                             "balance": amts[-1], "page": key[0], "line_ids": [c["id"] for c in cells]})
                n += 1
    return {"fields": fields, "rows": rows, "notes": "Offline baseline: text-layer rules, not a model."}


@oracle("reader_a")
def reader_a(ctx: Ctx, req: OracleRequest) -> dict:
    return _truth_reading(ctx, req, "reader_a") or _baseline_reading(ctx.payload(req))


@oracle("reader_b")
def reader_b(ctx: Ctx, req: OracleRequest) -> dict:
    truth = _truth_reading(ctx, req, "reader_b")
    if truth is not None:
        return truth
    return {"fields": [], "rows": [], "notes": "Reading from the image needs a live model; offline there is no second path."}


@oracle("reader_c")
def reader_c(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam, fx = ctx.family(req), _fixture(p)
    field = p.get("field", "")
    if fam and fx:
        built = fam.built(fx)
        m = re.match(r"row_(\d+)_balance", field)
        if m:
            row = next((r for r in built.rendered.rows if r.get("_row") == int(m.group(1))), None)
            if row:
                return {"field": field, "value": row["balance"], "confidence": 0.9,
                        "reason": "On the photo the third digit is a clear 8, and the running balance agrees."}
        info = built.rendered.fields.get(field)
        if info:
            return {"field": field, "value": info.get("as_printed"), "confidence": 0.9,
                    "reason": "Read again from the crop at a larger size."}
    readings = [r for r in p.get("readings", []) if r]
    return {"field": field, "value": readings[0] if len(set(readings)) == 1 else None, "confidence": 0.5,
            "reason": "Offline baseline cannot look at the image; the family is asked instead."}
