"""Module 4: people and institutions.

Names arrive in many scripts and orders, with initials, honorifics and misspellings. Each name
form read from a paper is split, matched to a person only on evidence (an identifier, an account,
a role on the same paper), challenged by a critic, and asked about only when no paper can settle
it (checkpoint C3). Issuers and payees are matched to the institution directory.
"""
from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import Any

from ..agents.runner import call_agent
from ..domain.calibration import value_certainty
from ..domain.case import Institution, NameForm, Person
from ..domain.checks import normalise
from ..domain.ids import new_id
from ..domain.money import format_amount
from ..domain.names import script_of
from .common import case_block, knowledge, open_checkpoint, pack_key, person_brief, snapshot

MODULE = "people"

NAME_FIELDS = {
    "deceased_name": "deceased", "account_holder": "holder", "life_assured": "holder", "member_name": "holder",
    "employee_name": "holder", "testator": "deceased", "insured": "holder",
    "spouse_name": "spouse", "mother_name": "mother", "father_name": "father", "nominee": "nominee",
    "holder_name": "subject", "executor": "executor", "beneficiary": "nominee",
}
SUBJECT_DOCS = {"identity_card"}         # papers about a family member, not the deceased


def _split_bilingual(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\s+/\s+", text) if p.strip()]
    return parts or [text]


def _clean(text: str) -> tuple[str, str | None]:
    """Removes a trailing '(WIFE)' or '（妻）' relationship note, returning it separately, and a title
    or honorific such as Mrs or 様, which is how the paper addresses the person, not their name."""
    import unicodedata

    from ..domain.names import strip_honorifics
    t = unicodedata.normalize("NFKC", text).strip()
    note = None
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", t)
    if m:
        t, note = m.group(1).strip(), m.group(2).strip().lower()
    bare, _ = strip_honorifics(t)
    return (bare.strip() or t), note


def person_for_name(people: list, text: str | None) -> Any:
    """The person a printed name refers to, by any of their name forms (titles and notes ignored)."""
    from ..domain.checks import normalise
    from ..domain.names import name_similarity
    if not text:
        return None
    want = normalise(_clean(text)[0])
    for p in people:
        if any(normalise(_clean(n.text)[0]) == want for n in p.names) or normalise(p.display_name) == want:
            return p
    best = max(((max([name_similarity(n.text, text) for n in p.names] or [0]), p) for p in people),
               default=(0, None), key=lambda x: x[0])
    return best[1] if best[0] >= 0.85 else None


def _fits_name(text: str, person: Any) -> bool:
    """A printed name that can only be this person's, spelled another way: the same words, or initials for some of
    them (R K VERMA, RAJESH K VERMA for Rajesh Kumar Verma). One word alone (VERMA) fits too many people."""
    from ..domain.names import initials_compatible, tokens
    if len(tokens(text)) < 2:
        return False
    return any(initials_compatible(text, n.text) or initials_compatible(n.text, text)
               for n in [*person.names, NameForm(text=person.display_name)] if script_of(n.text) == script_of(text))


def settle_merges(merges: list, forms: list[dict], people: list, deceased_id: str | None) -> None:
    """What the paper itself settles, whatever the model proposed (a live model made two new people of
    'मोहन लाल वर्मा / MOHAN LAL VERMA', and a new person of the holder printed on his own passbook):
    - the forms of one printed value, split at its ' / ', are one person;
    - the holder printed on an account paper of this case, whose name fits the deceased's, is the person who died.
    Changes the merges in place; the merge critic still reviews every one."""
    form_of = {(f["text"], f["seen_on"]): f for f in forms}
    deceased = next((p for p in people if p.id == deceased_id), None)
    for m in merges:
        f = form_of.get((m.name_text, m.seen_on))
        if f is None or deceased is None or m.person_id == deceased_id:
            continue
        if f["role"] in ("holder", "deceased") and not m.person_id and _fits_name(m.name_text, deceased):
            m.person_id, m.new_person, m.relationship = deceased_id, None, None
            m.evidence = (f"The holder printed on {f['paper']}, a paper of this case, with a name that fits "
                          f"{deceased.display_name}'s.")
    by_value: dict[str, list] = {}
    for m in merges:
        f = form_of.get((m.name_text, m.seen_on))
        if f is not None and f.get("value_id"):
            by_value.setdefault(f["value_id"], []).append(m)
    for group in by_value.values():
        anchor = next((m for m in group if m.person_id), None) or next((m for m in group if m.new_person), None)
        if anchor is None:
            continue
        for m in group:
            if m is not anchor and (m.person_id, m.new_person) != (anchor.person_id, anchor.new_person):
                m.person_id, m.new_person = anchor.person_id, anchor.new_person
                m.relationship = m.relationship or anchor.relationship
                m.evidence = f"Printed as one name with {anchor.name_text} on the same line of the paper."


async def resolve_people(deps: Any, case_id: str) -> None:
    snap = await snapshot(deps, case_id, ["people", "papers", "values"])
    case = snap.case
    forms: list[dict] = []
    identifiers: dict[str, dict] = {}
    for paper in snap.papers:
        vals = snap.values_of(paper.id)
        identifiers[paper.id] = {v.field: v.value for v in vals if v.kind == "identifier" and v.value}
        for v in vals:
            role = NAME_FIELDS.get(v.field)
            if not role or not v.value:
                continue
            if paper.doc_type in SUBJECT_DOCS:
                if role not in ("holder", "deceased", "subject"):
                    continue              # a father named on his wife's PAN card is her father, not one of his family
                role = "subject"
            for part in _split_bilingual(v.value):
                text, note = _clean(part)
                forms.append({"text": text, "script": script_of(text), "seen_on": paper.id, "paper": paper.title,
                              "doc_type": paper.doc_type, "field": v.field, "role": role, "note": note,
                              "value_id": v.id})
    known = {(p.id, n.text) for p in snap.people for n in p.names}
    forms = [f for f in forms if not any(f["text"] == t for _, t in known)]
    if not forms:
        return
    base = {"case": case_block(case)}
    split = await call_agent(deps, "name_splitter", {"key": pack_key("name_splitter", case, str(len(forms))), **base,
                                                     "names": [f["text"] for f in forms]}, case_id=case_id)
    parts = {n.text: n for n in split.names}
    people_pack = [{**person_brief(p), "names": [n.text for n in p.names]} for p in snap.people]
    ident = await call_agent(deps, "identity_resolver", {
        "key": pack_key("identity_resolver", case, str(len(snap.papers))), **base, "forms": forms,
        "people": people_pack, "deceased_id": case.deceased_id, "identifiers": identifiers}, case_id=case_id)
    settle_merges(ident.merges, forms, snap.people, case.deceased_id)
    critic = await call_agent(deps, "merge_critic", {
        "key": pack_key("merge_critic", case, str(len(snap.papers))), **base,
        "merges": [m.model_dump() for m in ident.merges], "people": people_pack}, case_id=case_id)
    verdicts = {v.name_text: v for v in critic.verdicts}

    people = {p.id: p for p in snap.people}
    merged = 0
    for m in ident.merges:
        verdict = verdicts.get(m.name_text)
        if verdict and not verdict.keep:
            if verdict.ask_family and verdict.question:
                src = next((f for f in forms if f["text"] == m.name_text), None)
                await open_checkpoint(deps, case, code="C3", kind="identity", title="Who is this?",
                                      question=verdict.question, options=[("yes", "Yes"), ("no", "No"),
                                                                          ("other", "Someone else")],
                                      subject_ref=f"name:{m.name_text}", why=verdict.reason,
                                      payload={"person_id": m.person_id, "seen_on": src["seen_on"] if src else m.seen_on,
                                               "paper": src["paper"] if src else None, "evidence": m.evidence})
            continue
        form = next((f for f in forms if f["text"] == m.name_text), None)
        p_parts = parts.get(m.name_text)
        nf = NameForm(text=m.name_text, script=(p_parts.script if p_parts else script_of(m.name_text)),
                      reading=p_parts.reading if p_parts else None, seen_on=form["seen_on"] if form else m.seen_on,
                      parts={k: v for k, v in {"given": p_parts.given if p_parts else None,
                                                "family": p_parts.family if p_parts else None,
                                                "initials": p_parts.initials if p_parts else None}.items() if v})
        target = people.get(m.person_id) if m.person_id else None
        if target is None and m.new_person:
            target = next((p for p in people.values() if p.display_name == m.new_person), None)
            if target is None:
                target = Person(id=new_id("p"), case_id=case_id, display_name=m.new_person,
                                relationship=m.relationship, roles=[], certainty="sure")
                people[target.id] = target
                await deps.activity(case_id, MODULE, f"Added {m.new_person} ({m.relationship or 'relationship not known'}), "
                                    f"named on {form['paper'] if form else 'a paper'}.", agent="identity_resolver")
        if target is None:
            continue
        nf.why = m.evidence or None
        if not any(n.text == nf.text for n in target.names):
            target.names.append(nf)
            merged += 1
        signal = "identifier" if "PAN" in m.evidence or "identity" in m.evidence.lower() else \
            "account_number" if "account" in m.evidence.lower() else \
            "same_paper_role" if "certificate" in m.evidence.lower() else "name_only"
        p = deps.calibrator.probability("people", {"evidence": signal})
        await deps.ledger(case_id, f"name:{nf.text}", "identity_match", "pass" if value_certainty(p) == "sure" else "unknown",
                          method=signal, detail=f"{nf.text} -> {target.display_name}: {m.evidence}")
    await deps.cases.put_many(case_id, "people", list(people.values()))
    if merged:
        await deps.activity(case_id, MODULE, f"Matched {merged} name form{'s' if merged != 1 else ''} to people, "
                            "each on evidence such as an identifier or an account, never the name alone.",
                            agent="identity_resolver", kind="check")
    deps.changed(case_id, "people")


async def resolve_institutions(deps: Any, case_id: str, *, lookups: Any = None) -> None:
    """Each paper's issuer: the directory's, else matched by the resolver agent, else identified on the web (all of
    a case's papers side by side, within `lookups`' time and count), else kept as printed."""
    snap = await snapshot(deps, case_id, ["papers"])
    case = snap.case
    k = knowledge(deps)
    unmatched = []
    for paper in snap.papers:
        if paper.institution_id or not paper.issuer:
            continue
        inst, _score = await k.find_institution(paper.issuer, paper.country, case_id=case_id)
        if inst:
            paper.institution_id = inst.id
            await deps.cases.put(case_id, "papers", paper)
            await _ensure_institution(deps, case_id, inst)
        else:
            unmatched.append(paper)
    if unmatched:
        countries = {(p.country or "").split("-")[0] for p in unmatched} - {""}
        directory = [{"id": i.id, "name": i.name, "aliases": i.aliases, "country": i.country, "kind": i.kind}
                     for i in await k.institutions()
                     if not countries or (i.country or "").split("-")[0] in countries]
        out = await call_agent(deps, "institution_resolver", {
            "key": pack_key("institution_resolver", case, *[p.id for p in unmatched]), "case": case_block(case),
            "texts": [{"text": p.issuer, "country": p.country, "paper": p.id} for p in unmatched],
            "directory": directory}, case_id=case_id)
        by_text = {normalise(m.text): m for m in out.matches}
        still = []
        for i, paper in enumerate(unmatched):   # matched by the printed text; by position if the text was changed
            match = by_text.get(normalise(paper.issuer or "")) or (out.matches[i] if i < len(out.matches) else None)
            inst = await k.institution(match.institution_id) if match is not None and match.institution_id else None
            if inst:
                paper.institution_id = inst.id
                await deps.cases.put(case_id, "papers", paper)
                await _ensure_institution(deps, case_id, inst)
            else:
                still.append(paper)
        # a bank, insurer or lender the directory has never seen: identified on the web now (or kept as printed),
        # so its paper's account, policy or loan is claimed like any other
        from .institutions import DOC_ISSUER_KIND, Lookups, resolve
        lookups = lookups or Lookups()
        # a death certificate, an identity card: no institution holds anything
        todo = [p for p in still if DOC_ISSUER_KIND.get(p.doc_type) is not None]
        found = await asyncio.gather(*(          # side by side (one after another until 19 October 2026)
            resolve(deps, case, paper.issuer, country=paper.country, kind=DOC_ISSUER_KIND[paper.doc_type],
                    lookups=lookups, context=f"the issuer of a {paper.doc_type.replace('_', ' ')}",
                    source=f"named on “{paper.title}”", must_be_org=False) for paper in todo))
        for paper, inst in zip(todo, found, strict=True):
            if inst is not None:
                paper.institution_id = inst.id
                await deps.cases.put(case_id, "papers", paper)
                await _ensure_institution(deps, case_id, inst)
    deps.changed(case_id, "papers", "institutions")


async def _ensure_institution(deps: Any, case_id: str, inst: Institution) -> None:
    if await deps.cases.get(case_id, "institutions", inst.id) is None:
        await deps.cases.put(case_id, "institutions", inst)


async def ensure_institution_id(deps: Any, case_id: str, inst_id: str) -> Institution | None:
    inst = await knowledge(deps).institution(inst_id)
    if inst:
        await _ensure_institution(deps, case_id, inst)
    return inst


# ---------------------------------------------------------------------------------------------
# People the family adds or corrects by hand (the People screen)
# ---------------------------------------------------------------------------------------------

EDITABLE = {"display_name", "relationship", "roles", "languages", "channel", "country", "city", "age", "birth_date",
            "notes", "initials", "color", "address", "names", "name_en"}
COLORS = ["#F3D9C4", "#CFE3F5", "#E6DCF2", "#D8EFD9", "#F7E3B5", "#E2E8E4", "#F5D0D6"]


def _initials(name: str) -> str:
    parts = [p for p in name.replace(".", " ").split() if p]
    if not parts:
        return "?"
    if script_of(name) != "Latn":
        return name[:1]
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


async def add_person(deps: Any, case_id: str, *, display_name: str, relationship: str | None = None,
                     roles: list[str] | None = None, languages: list[str] | None = None, city: str | None = None,
                     country: str | None = None, notes: str = "", by: str | None = None) -> Person:
    """Someone the papers do not name, such as a neighbour who signs a declaration."""
    from ..domain.checks import normalise
    from ..domain.ids import stable_id
    people = await deps.cases.list(case_id, "people")
    existing = next((p for p in people if normalise(p.display_name) == normalise(display_name)), None)
    p = existing or Person(id=stable_id("p", case_id, normalise(display_name)), case_id=case_id,
                           display_name=display_name, initials=_initials(display_name),
                           color=COLORS[len(people) % len(COLORS)])
    p.relationship = relationship or p.relationship
    p.roles = sorted(set(p.roles) | set(roles or []))  # type: ignore[arg-type]
    p.languages = languages or p.languages
    p.city, p.country = city or p.city, country or p.country
    p.notes = notes or p.notes
    if not any(normalise(n.text) == normalise(display_name) for n in p.names):
        p.names.append(NameForm(text=display_name, script=script_of(display_name), seen_on="family"))
    await deps.cases.put(case_id, "people", p)
    adder = await deps.cases.get(case_id, "people", by) if by else None
    await deps.activity(case_id, MODULE, f"{adder.display_name.split()[0] if adder else 'The family'} added "
                        f"{display_name}{', ' + relationship if relationship else ''}"
                        f"{' - ' + notes if notes else ''}.", kind="decision")
    deps.changed(case_id, "people")
    return p


async def update_person(deps: Any, case_id: str, person_id: str, changes: dict, *, by: str | None = None) -> Person:
    p: Person = await deps.cases.get(case_id, "people", person_id)
    if p is None:
        raise KeyError(person_id)
    data = p.model_dump()
    for k, v in changes.items():
        if k in EDITABLE and v is not None:
            data[k] = v
    p = Person.model_validate(data)
    await deps.cases.put(case_id, "people", p)
    deps.changed(case_id, "people")
    return p


async def invite_person(deps: Any, case_id: str, person_id: str, *, by: str | None = None) -> Person:
    """A family member is invited to the case and joins it (in the demo, at once)."""
    p: Person = await deps.cases.get(case_id, "people", person_id)
    if p is None:
        raise KeyError(person_id)
    case = await deps.cases.get_case(case_id)
    p.access, p.joined_on = "member", deps.today_for(case)
    await deps.cases.put(case_id, "people", p)
    inviter = await deps.cases.get(case_id, "people", by) if by else None
    await deps.activity(case_id, MODULE, f"{p.display_name} joined the case"
                        f"{', invited by ' + inviter.display_name if inviter else ''}.", kind="step")
    deps.changed(case_id, "people")
    return p


async def answer_identity(deps: Any, case_id: str, checkpoint_id: str, answer: str, *, by: str | None,
                          person_id: str | None = None, relationship: str | None = None) -> None:
    """The family says whether a name form on a paper is the person we think (checkpoint C3)."""
    cp = await deps.cases.get(case_id, "checkpoints", checkpoint_id)
    if cp is None or cp.code != "C3":
        return
    cp.status, cp.answer, cp.answered_by, cp.answered_at = "answered", answer, by, deps.now()
    await deps.cases.put(case_id, "checkpoints", cp)
    name = cp.subject_ref.split(":", 1)[-1]
    target_id = person_id or (cp.payload or {}).get("person_id")
    if cp.subject_ref.startswith("payee:"):
        p = await deps.cases.get(case_id, "people", target_id) if target_id else None
        if p is not None:
            guess = (cp.payload or {}).get("relationship_guess")
            if answer == "yes" and guess:
                p.relationship, p.certainty = guess, "sure"
                if guess in HEIR_RELATIONSHIPS - {"father"}:
                    p.roles = sorted(set(p.roles) | {"heir"})  # type: ignore[arg-type]
                p.notes = f"{guess.capitalize()}, confirmed by the family."
            elif answer == "no":
                p.certainty, p.notes = "sure", "Not family, the family says."
            elif answer == "other" and relationship:
                rel = relationship.strip().lower()
                p.relationship, p.certainty = rel, "sure"
                if rel in HEIR_RELATIONSHIPS - {"father"}:
                    p.roles = sorted(set(p.roles) | {"heir"})  # type: ignore[arg-type]
                p.notes = f"{rel.capitalize()}, as the family says."
            await deps.cases.put(case_id, "people", p)
        await deps.activity(case_id, MODULE, f"The family answered about {name}: {answer}.", kind="decision")
        from .pipeline import refresh_claims
        await refresh_claims(deps, case_id)       # an heir more or less changes who signs
        deps.changed(case_id, "people", "checkpoints", "plans")
        return
    if answer == "yes" and target_id:
        p = await deps.cases.get(case_id, "people", target_id)
        if p and not any(n.text == name for n in p.names):
            p.names.append(NameForm(text=name, script=script_of(name), seen_on=(cp.payload or {}).get("seen_on")))
            await deps.cases.put(case_id, "people", p)
    await deps.ledger(case_id, f"name:{name}", "family_check", "pass", detail=f"answer={answer} by={by}")
    await deps.activity(case_id, MODULE, f"The family answered about “{name}”: {answer}.", kind="decision")
    deps.changed(case_id, "people", "checkpoints")


async def confirm_people(deps: Any, case_id: str, *, by: str | None) -> None:
    """The family looked through the people in the case and confirmed them."""
    for cp in await deps.cases.list(case_id, "checkpoints"):
        # name matches only; who someone seen in the payments is stays a question of its own
        if cp.code == "C3" and cp.status == "open" and (cp.subject_ref or "").startswith("name:"):
            await answer_identity(deps, case_id, cp.id, "yes", by=by)
    who = await deps.cases.get(case_id, "people", by) if by else None
    await deps.activity(case_id, MODULE, f"{who.display_name if who else 'The family'} confirmed the people in the case.",
                        kind="decision")



# ---------------------------------------------------------------------------------------------
# People only the payments show (money sent every month to someone nobody has mentioned)
# ---------------------------------------------------------------------------------------------

HEIR_RELATIONSHIPS = {"mother", "father", "wife", "husband", "son", "daughter", "spouse"}


async def payees(deps: Any, case_id: str) -> None:
    """A regular payment to a person the family has not mentioned may mean a relative, and a relative
    may be an heir who has to sign. The identity resolver suggests who it might be; the family is
    asked (checkpoint C3), never told."""
    from ..domain.checks import normalise
    from ..domain.ids import stable_id
    snap = await snapshot(deps, case_id, ["groups", "people", "transactions", "papers", "checkpoints"])
    case = snap.case
    txns = {t.id: t for t in snap.transactions}
    forms = []
    for g in snap.groups:
        if g.counterparty_kind not in ("family", "person") or g.direction != "debit" or g.count < 3 or not g.counterparty:
            continue
        if person_for_name(snap.people, g.counterparty):
            continue
        if any(c.subject_ref == f"payee:{g.counterparty}" for c in snap.checkpoints):
            continue
        first = next((txns[t] for t in g.transaction_ids if t in txns), None)
        paper = snap.paper(first.paper_id) if first else None
        forms.append({"text": g.counterparty, "role": "payee", "seen_on": first.paper_id if first else None,
                      "paper": paper.title if paper else None, "group_id": g.id, "count": g.count,
                      "amount": str(g.amount_typical), "currency": g.currency, "cadence": g.cadence,
                      "narration": first.narration if first else None,
                      "date": first.date.isoformat() if first else None, "field": "payee", "doc_type": "statement",
                      "script": script_of(g.counterparty), "note": None, "value_id": None})
    if not forms:
        return
    base = {"case": case_block(case)}
    people_pack = [{**person_brief(p), "names": [n.text for n in p.names]} for p in snap.people]
    ident = await call_agent(deps, "identity_resolver", {
        "key": pack_key("identity_resolver", case, "payees", *[f["text"] for f in forms]), **base, "forms": forms,
        "people": people_pack, "deceased_id": case.deceased_id, "identifiers": {}}, case_id=case_id)
    dec = snap.deceased
    first_name = (dec.name_en or dec.display_name).split()[0] if dec else ""
    whose = f"{first_name}'s" if first_name else "the"          # no "his" or "her": the deceased may be anyone
    asked_of = snap.claimant()
    for m in ident.merges:
        form = next((f for f in forms if f["text"] == m.name_text), None)
        if form is None or m.person_id:
            continue
        name = m.new_person or " ".join(w.capitalize() for w in m.name_text.split())
        pid = stable_id("p", case_id, normalise(name))
        p = await deps.cases.get(case_id, "people", pid) or Person(
            id=pid, case_id=case_id, display_name=name, roles=[], certainty="unknown", initials="?", color="#E2E8E4",
            names=[NameForm(text=m.name_text, script=script_of(m.name_text), seen_on=form["seen_on"])])
        amount = format_amount(Decimal(form["amount"]), form["currency"]) if form.get("amount") else ""
        p.notes = f"Paid {amount} {form['cadence']} from {whose} account; relationship not known yet."
        await deps.cases.put(case_id, "people", p)
        rel = m.relationship
        question = m.question or (f"Is {name} {whose} {rel}?" if rel else f"Who is {name}?")
        options = [("yes", f"Yes, {whose} {rel}"), ("no", "No"), ("other", "Someone else")] if rel else \
            [("family", "A relative"), ("other", "Someone else")]
        lang = (asked_of.languages[0] if asked_of and asked_of.languages else "en")
        await open_checkpoint(deps, case, code="C3", kind="identity", title=f"Who is {name}?", question=question,
                              options=options, for_person_id=asked_of.id if asked_of else None,
                              subject_ref=f"payee:{m.name_text}",
                              why=m.why or m.evidence, question_local=m.question_local if lang != "en" else None,
                              language=lang,
                              payload={"person_id": pid, "relationship_guess": rel, "group_id": form["group_id"],
                                       "evidence": m.evidence, "paper": form.get("paper"), "date": form.get("date"),
                                       "narration": form.get("narration"), "amount": form.get("amount")})
        await deps.activity(case_id, MODULE, f"{name} received {amount} {form['cadence']} from {whose} account. "
                            f"Asked the family who {name.split()[0]} is, because a parent or child would be an heir.",
                            agent="identity_resolver", kind="question")
    deps.changed(case_id, "people", "checkpoints")
