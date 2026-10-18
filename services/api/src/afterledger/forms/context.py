"""The values a pack's forms and letters are filled from, built from the case by code.

Each value keeps where it came from (a paper and line, a family answer, a rule), so the pack
review can show the source of every field.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from ..domain.money import format_amount
from ..modules.common import Snapshot

RELATION_LABEL = {"wife": "Wife", "husband": "Husband", "son": "Son", "daughter": "Daughter", "mother": "Mother",
                  "father": "Father", "neighbour": "Neighbour"}
LAW = {"hindu": ("Hindu", "Hindu Succession Act, 1956")}
NATURE = {"savings": "SB", "current": "CA", "fixed_deposit": "TD", "recurring_deposit": "RD", "nre": "NRE SB",
          "nro": "NRO SB"}


def _dmy(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _age_on(born: date | None, on: date | None) -> str:
    if not (born and on):
        return ""
    return str(on.year - born.year - ((on.month, on.day) < (born.month, born.day)))


def _value(snap: Snapshot, doc_type: str, field: str) -> tuple[str | None, str | None]:
    for p in snap.papers:
        if p.doc_type == doc_type:
            for v in snap.values_of(p.id):
                if v.field == field and v.value:
                    return v.value, f"{p.title}, {v.label}"
    return None, None


def _latin(text: str | None) -> str | None:
    if not text:
        return text
    parts = [x.strip() for x in text.split(" / ")]
    return next((x for x in parts if x.isascii()), parts[-1])


def build_context(snap: Snapshot, claim: Any, *, institution: dict, plan_facts: dict, today: date,
                  personal_law: str | None = None) -> tuple[dict, dict]:
    """Returns (context, sources): sources maps a dotted path to where its value came from."""
    case = snap.case
    src: dict[str, str] = {}
    dec = snap.deceased
    family = snap.family()
    claimant = (snap.person(claim.claimant_person_id) if claim is not None and claim.claimant_person_id else None) \
        or next((p for p in family if "claimant" in p.roles), None)
    heirs = [p for p in family if "heir" in p.roles]
    declarant = next((p for p in family if "declarant" in p.roles), None)
    inst_id = claim.institution_id if claim else None

    # the deceased's name as this institution's own papers print it
    on_account = None
    for p in snap.papers:
        if p.institution_id == inst_id:
            for v in snap.values_of(p.id):
                if v.field in ("account_holder", "life_assured", "member_name") and v.value:
                    on_account = v.value
                    src["deceased.name"] = f"{p.title}, {v.label}"
                    break
        if on_account:
            break
    cert_name, cert_src = _value(snap, "death_certificate", "deceased_name")
    name = on_account or _latin(cert_name) or (dec.display_name.upper() if dec else case.title.upper())
    if not on_account and cert_src:
        src["deceased.name"] = cert_src
    dod, dod_src = _value(snap, "death_certificate", "date_of_death")
    dod_d = date.fromisoformat(dod) if dod else case.died_on
    src["deceased.date_of_death"] = dod_src or "the family"
    place, _ = _value(snap, "death_certificate", "place_of_death")
    reg_no, reg_src = _value(snap, "death_certificate", "registration_number")
    reg_date, _ = _value(snap, "death_certificate", "registration_date")
    address, _ = _value(snap, "death_certificate", "address")
    age, _ = _value(snap, "death_certificate", "age")
    cert_paper = next((p for p in snap.papers if p.doc_type == "death_certificate"), None)
    law = LAW.get(personal_law or "", (None, None))
    deceased = {
        "name": name, "name_cert": _latin(cert_name), "date_of_death": _dmy(dod_d),
        "place_of_death": _latin(place) or case.died_in,
        "date_place": f"{_dmy(dod_d)}, {(_latin(place) or case.died_in or '').title()}".strip(", "),
        "death_cert_no": reg_no or "", "death_cert_date": _dmy(date.fromisoformat(reg_date)) if reg_date else "",
        "death_cert_authority": (cert_paper.issuer or "").replace(" (synthetic specimen)", "") if cert_paper else "",
        "age": (age or "").split("/")[-1].strip().split(" ")[0] if age else "",
        "marital_status": "Married" if any(p.relationship in ("wife", "husband") for p in family) else "",
        "address": address or "", "religion": law[0] or "", "succession_law": law[1] or "",
    }
    if reg_src:
        src["deceased.death_cert_no"] = reg_src
    pan, pan_src = None, None
    if claimant:
        for p in snap.papers:
            if p.doc_type == "identity_card":
                vals = {v.field: v for v in snap.values_of(p.id)}
                holder = vals.get("holder_name")
                if holder and holder.value and claimant.display_name.upper() in holder.value.upper():
                    pan = vals["pan"].value if "pan" in vals else None
                    pan_src = f"{p.title}, card number"
    claimant_ctx = {
        "name": claimant.display_name.upper() if claimant else "", "relationship": RELATION_LABEL.get(
            (claimant.relationship or "") if claimant else "", (claimant.relationship or "").title() if claimant else ""),
        "address": address or "", "city": (claimant.city if claimant else "") or "", "pan": pan or "",
        "mobile": "", "bank": "", "bank_name": "",
    }
    if pan_src:
        src["claimant.pan"] = pan_src
    dob = None
    heirs_rows, heirs_short = [], []
    for p in heirs:
        is_claimant = claimant is not None and p.id == claimant.id
        rel = RELATION_LABEL.get(p.relationship or "", (p.relationship or "").title())
        heirs_rows.append({"name_address": f"{p.display_name.upper()}, {p.city or ''}".strip(", "),
                           "age": str(p.age) if p.age else "", "relation": rel, "contact": "",
                           "disclaimer": "No" if is_claimant else "Yes"})
        heirs_short.append({"name": p.display_name.upper(), "relation": rel, "age": str(p.age) if p.age else ""})
    holdings = [h for h in snap.holdings if claim and h.id in claim.holding_ids]
    deposits, total = [], Decimal(0)
    for h in holdings:
        acct = None
        for p in snap.papers:
            if p.institution_id == h.institution_id:
                vals = {v.field: v.value for v in snap.values_of(p.id)}
                cand = vals.get("account_number") or vals.get("receipt_number")
                if cand and h.account_mask and cand.replace(" ", "").endswith(h.account_mask):
                    acct = cand
        maturity = ""
        if h.kind == "fixed_deposit":
            mv, _ = _value(snap, "fixed_deposit_receipt", "maturity_date")
            maturity = _dmy(date.fromisoformat(mv)) if mv else ""
        deposits.append({"nature": NATURE.get(h.kind, h.kind), "account": acct or f"ending {h.account_mask}",
                         "amount": format_amount(h.value, h.currency) if h.value is not None else "",
                         "maturity": maturity})
        if h.value is not None:
            total += h.value
    securities, sec_total = [], None
    dp_id, _ = _value(snap, "demat_holding_statement", "dp_id")
    client_id, _ = _value(snap, "demat_holding_statement", "client_id")
    tv, _ = _value(snap, "demat_holding_statement", "total_value")
    if dp_id and any(h.kind == "demat" for h in holdings):
        securities = [{"company": "As per the enclosed statement of holdings", "folio": f"{dp_id} / {client_id}",
                       "qty": "", "cert": "Not applicable (demat)"}]
        sec_total = format_amount(Decimal(tv), "INR") if tv else ""
    ctx = {
        "date": _dmy(today), "today": today.isoformat(),
        "institution": {"name": institution.get("name", ""), "branch": institution.get("branch", ""),
                        "address": institution.get("address", "")},
        "deceased": deceased, "claimant": claimant_ctx, "heirs": heirs_rows, "heirs_short": heirs_short,
        "deposits": deposits, "deposits_total": format_amount(total, holdings[0].currency) if holdings else "",
        "securities": securities, "securities_total": sec_total or "",
        "dp": {"dp_client": f"{dp_id} / {client_id}" if dp_id else ""},
        "declarant": {"name": declarant.display_name.upper() if declarant else "", "address": ""},
        "facts": plan_facts, "signer": {},
        "checks": {
            "death_cert": True, "ovd": True, "no_will": plan_facts.get("has_will") == "no",
            "basis_lhc": plan_facts.get("heirship_proof") == "legal_heir_cert",
            "basis_declaration": plan_facts.get("heirship_proof") == "annex_ix",
            "basis_sc": plan_facts.get("above_basis") == "succession_certificate",
            "indemnity": True, "disclaimer": bool([r for r in heirs_rows if r["disclaimer"] == "Yes"]),
        },
    }
    _ = dob
    _generic(ctx, src, snap, claim, holdings, family, claimant, dod_d, today)
    return ctx, src


REL_JA = {"wife": "妻", "husband": "夫", "son": "長男", "daughter": "長女", "mother": "母", "father": "父",
          "brother": "兄弟", "sister": "姉妹"}
REL_ES = {"wife": "Esposa", "husband": "Esposo", "son": "Hijo", "daughter": "Hija", "mother": "Madre",
          "father": "Padre"}
KIND_JA = {"savings": "普通預金", "current": "当座預金", "fixed_deposit": "定期預金"}


def _date_styles(d: date | None) -> dict[str, str]:
    from ..seed.papers.formats import fmt_date
    if not d:
        return {"dmy": "", "long": "", "jp": "", "us": "", "iso": "", "de": ""}
    return {"dmy": d.strftime("%d/%m/%Y"), "long": fmt_date(d, "d_month_y"), "jp": fmt_date(d, "jp_era"),
            "us": d.strftime("%m/%d/%Y"), "iso": d.isoformat(), "de": d.strftime("%d.%m.%Y")}


def _reading(person: Any) -> str:
    if person is None:
        return ""
    for n in person.names:
        if n.reading:
            return n.reading
    return ""


def _native(person: Any, script: str) -> str:
    if person is None:
        return ""
    for n in person.names:
        if n.script == script or (script == "Jpan" and n.script in ("Hani", "Jpan", "Kana")):
            return n.text
    return ""


def _generic(ctx: dict, src: dict, snap: Snapshot, claim: Any, holdings: list, family: list, claimant: Any,
             dod: date | None, today: date) -> None:
    """Values most countries' forms ask for, in each country's own style."""
    dec = snap.deceased
    born_raw, born_src = _value(snap, "death_certificate", "birth_date")
    if not born_raw:
        born_raw, born_src = _value(snap, "insurance_policy", "birth_date")
    try:
        born = date.fromisoformat(born_raw) if born_raw else (dec.birth_date if dec else None)
    except ValueError:
        born = dec.birth_date if dec else None
    if born_src:
        src["deceased.birth"] = born_src
    address = ctx["deceased"].get("address") or ""
    if not address:
        for p in snap.papers:
            if p.institution_id:
                vals = {v.field: v.value for v in snap.values_of(p.id)}
                if vals.get("address"):
                    address = vals["address"]
                    break
    d = ctx["deceased"]
    d.update({"display": dec.display_name if dec else snap.case.title, "reading": _reading(dec),
              "native": _native(dec, "Jpan") or (dec.display_name if dec else ""),
              "birth": _date_styles(born), "death": _date_styles(dod), "address_any": address,
              "age_at_death": _age_on(born, dod) or d.get("age", ""), "place": d.get("place_of_death") or ""})
    ctx["dates"] = _date_styles(today)
    if claimant is not None:
        c = ctx["claimant"]
        c.update({"display": claimant.display_name, "reading": _reading(claimant),
                  "native": _native(claimant, "Jpan") or claimant.display_name,
                  "address_full": claimant.address or address, "age": str(claimant.age) if claimant.age else "",
                  "relationship_ja": REL_JA.get(claimant.relationship or "", claimant.relationship or ""),
                  "relationship_es": REL_ES.get(claimant.relationship or "", (claimant.relationship or "").title()),
                  "relationship_en": RELATION_LABEL.get(claimant.relationship or "", (claimant.relationship or "").title())})
    heirs = [p for p in family if "heir" in p.roles]
    ctx["heirs_detail"] = [{"name": p.display_name, "native": _native(p, "Jpan") or p.display_name,
                            "reading": _reading(p), "relationship": RELATION_LABEL.get(p.relationship or "",
                                                                                     (p.relationship or "").title()),
                            "relationship_ja": REL_JA.get(p.relationship or "", p.relationship or ""),
                            "address": p.address or "", "age": str(p.age) if p.age else ""} for p in heirs]
    executor = next((p for p in family if "executor" in p.roles), None)
    ctx["executor"] = {"name": executor.display_name if executor else "", "address": (executor.address or "")
                       if executor else "", "relationship": RELATION_LABEL.get(executor.relationship or "", "")
                       if executor else ""}
    accounts, total = [], Decimal(0)
    for h in holdings:
        number = ""
        for p in snap.papers:
            if p.institution_id == h.institution_id:
                vals = {v.field: v.value for v in snap.values_of(p.id)}
                cand = vals.get("account_number") or vals.get("policy_number") or vals.get("receipt_number") or ""
                if cand and (not h.account_mask or cand.replace(" ", "").endswith(h.account_mask)):
                    number = cand
        accounts.append({"kind": h.label, "kind_ja": KIND_JA.get(h.kind, h.label), "number": number or
                         (f"ending {h.account_mask}" if h.account_mask else ""),
                         "amount": format_amount(h.value, h.currency) if h.value is not None else "",
                         "branch": ctx["institution"].get("branch", "")})
        if h.value is not None:
            total += h.value
    ctx["accounts"] = accounts
    ctx["accounts_total"] = format_amount(total, holdings[0].currency) if holdings and total else ""
    pol_no, pol_src = _value(snap, "insurance_policy", "policy_number")
    sum_assured, _ = _value(snap, "insurance_policy", "sum_assured")
    nominee, _ = _value(snap, "insurance_policy", "nominee")
    cur = holdings[0].currency if holdings else "INR"
    ctx["policy"] = {"number": pol_no or "", "sum": format_amount(Decimal(sum_assured), cur) if sum_assured else "",
                     "beneficiary": nominee or ""}
    if pol_src:
        src["policy.number"] = pol_src
    ctx["will"] = {"exists": any(p.doc_type == "will" for p in snap.papers)}
    cause = snap.case.situation.death_cause if snap.case.situation else "unknown"
    ctx["checks"].update({"natural_death": cause == "illness", "accidental_death": cause == "accident"})
    ctx["checks"].update({"will_yes": ctx["will"]["exists"], "will_no": not ctx["will"]["exists"],
                          "spouse": (claimant.relationship if claimant else "") in ("wife", "husband"),
                          "child": (claimant.relationship if claimant else "") in ("son", "daughter"),
                          "parent": (claimant.relationship if claimant else "") in ("mother", "father")})


def resolve(ctx: dict, path: str) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def values_for(spec: dict, ctx: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for block in spec.get("blocks", []):
        if block["type"] == "fields":
            for f in block["fields"]:
                if f.get("path"):
                    out[f["id"]] = resolve(ctx, f["path"])
        elif block["type"] == "table":
            # only the columns the form prints, so every intended value has a box to be read back from
            rows = resolve(ctx, block.get("path", block["id"])) or []
            keys = [c["key"] for c in block["columns"]]
            out[block["id"]] = [{k: r.get(k) for k in keys if r.get(k) not in (None, "")} for r in rows]
        elif block["type"] == "signatures" and block.get("from"):
            rows = resolve(ctx, block["from"]) or []
            out[block["id"]] = [str(r.get(block.get("name_key", "name"), "")) for r in rows]
        elif block["type"] == "checks":
            for c in block["items"]:
                out[c["id"]] = bool(resolve(ctx, c["path"])) if c.get("path") else False
    for fid, path in (spec.get("value_paths") or {}).items():
        out[fid] = resolve(ctx, path)
    return out
