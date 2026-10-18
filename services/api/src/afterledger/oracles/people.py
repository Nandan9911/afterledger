"""Offline answers for the people and institution agents."""
from __future__ import annotations

from ..adapters.llm.models import OracleRequest
from ..domain.checks import normalise
from ..domain.names import initials_compatible, name_similarity, script_of, strip_honorifics, tokens
from . import Ctx, oracle

_REL_WORDS = {"wife": ["wife", "spouse", "पत्नी"], "husband": ["husband", "पति"], "mother": ["mother", "माता"],
              "father": ["father", "पिता"]}
_ROLE_REL = {"spouse": None, "mother": "mother", "father": "father"}


@oracle("name_splitter")
def name_splitter(ctx: Ctx, req: OracleRequest) -> dict:
    out = []
    fam = ctx.family(req)
    readings = {}
    if fam:
        for person in [fam.deceased, *fam.people]:
            for n in person.get("names", []):
                if n.get("reading"):
                    readings[n["text"]] = n["reading"]
    for text in ctx.payload(req).get("names", []):
        clean, _ = strip_honorifics(text)
        script = script_of(clean)
        parts = clean.replace("　", " ").split()
        if script in ("Jpan", "Hani", "Kana") or (fam and fam.key in ("sato", "tanaka") and script != "Latn"):
            family, given, order = (parts[0], " ".join(parts[1:]) or None, "family_given") if parts else (None, None, "unknown")
        elif "," in clean:
            fam_part, given_part = [x.strip() for x in clean.split(",", 1)]
            family, given, order = fam_part, given_part, "family_given"
        else:
            family, given, order = (parts[-1] if parts else None, " ".join(parts[:-1]) or None,
                                    "given_family" if len(parts) > 1 else "unknown")
        initials = "".join(p[0] for p in parts if p and p[0].isalpha()).upper() if script == "Latn" else None
        out.append({"text": text, "script": script, "given": given, "family": family, "initials": initials,
                    "reading": readings.get(text), "order": order})
    return {"names": out}


def _truth_person(fam, text: str):
    t = normalise(text)
    for person in [fam.deceased, *fam.people]:
        for n in person.get("names", []):
            if normalise(n["text"]) == t:
                return person
    best, best_s = None, 0.0
    for person in [fam.deceased, *fam.people]:
        for n in person.get("names", []):
            s = name_similarity(n["text"], text)
            if s > best_s:
                best, best_s = person, s
    return best if best_s >= 0.8 else None


def _case_person(people: list[dict], truth: dict, deceased_id: str | None, truth_deceased_id: str) -> str | None:
    if truth["id"] == truth_deceased_id:
        return deceased_id
    for p in people:
        if p.get("relationship") and p["relationship"] == truth.get("relationship"):
            return p["id"]
    for p in people:
        if normalise(p.get("name", "")) == normalise(truth["display_name"]):
            return p["id"]
    return None


def _evidence(form: dict, identifiers: dict, papers_ids: dict, canonical: str) -> tuple[str, float]:
    pid = form["seen_on"]
    mine = identifiers.get(pid, {})
    for key in ("pan", "id_number"):
        if mine.get(key):
            others = [p for p, ids in identifiers.items() if p != pid and ids.get(key) == mine[key]]
            if others:
                return f"Same {key.upper() if key == 'pan' else 'identity number'} ({mine[key]}) as on another of his papers.", 0.98
    role = form["role"]
    if role == "deceased":
        return "Named as the person who died on the death certificate.", 0.98
    if role in ("spouse", "mother", "father"):
        return f"Named as his {role if role != 'spouse' else 'wife or husband'} on the death certificate.", 0.95
    if role == "nominee":
        return f"Named as nominee{' (' + form['note'] + ')' if form.get('note') else ''} on the policy.", 0.95
    if role == "subject":
        return "The person's own identity card, handed in by the family.", 0.97
    if role == "holder":
        if normalise(form["text"]) == normalise(canonical):
            return "Account holder on one of his papers; the full name matches.", 0.97
        if initials_compatible(form["text"], canonical):
            return "Account holder on one of his papers; the initials and surname match.", 0.95
        if sorted(tokens(form["text"])) == sorted(tokens(canonical)):
            return "Account holder on one of his papers; the same names with the surname first.", 0.94
        return "Account holder on one of his papers; the name is close but spelled differently.", 0.7
    return "Matched by name.", 0.6


@oracle("identity_resolver")
def identity_resolver(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam = ctx.family(req)
    people = p.get("people", [])
    deceased_id = p.get("deceased_id")
    identifiers = p.get("identifiers", {})
    merges = []
    for form in p.get("forms", []):
        if fam:
            truth = _truth_person(fam, form["text"])
            canonical = fam.deceased["display_name"]
            if truth is None:
                continue
            if form.get("role") == "payee":
                ask = truth.get("payee_question") or {}
                pid = next((p["id"] for p in people if normalise(p.get("name", "")) == normalise(truth["display_name"])),
                           None)
                merges.append({"name_text": form["text"], "seen_on": form["seen_on"], "person_id": pid,
                               "new_person": None if pid else truth["display_name"],
                               "relationship": ask.get("guess", truth.get("relationship")), "confidence": 0.55,
                               "evidence": ask.get("evidence", "Paid regularly from his account."),
                               "question": ask.get("question"), "question_local": ask.get("question_local"),
                               "why": ask.get("why")})
                continue
            pid = _case_person(people, truth, deceased_id, fam.deceased["id"])
            evidence, conf = _evidence(form, identifiers, {}, canonical if truth["id"] == fam.deceased["id"]
                                       else truth["display_name"])
            merges.append({"name_text": form["text"], "seen_on": form["seen_on"], "person_id": pid,
                           "new_person": None if pid else truth["display_name"],
                           "relationship": truth.get("relationship"), "confidence": conf, "evidence": evidence})
            continue
        # baseline: roles on the paper decide; a similar name is only a hint
        role = form["role"]
        if role == "payee":
            merges.append({"name_text": form["text"], "seen_on": form["seen_on"], "person_id": None,
                           "new_person": " ".join(t.capitalize() for t in tokens(form["text"])), "relationship": None,
                           "confidence": 0.5, "evidence": f"Paid {form.get('amount')} {form.get('cadence')} from his account."})
            continue
        if role in ("deceased", "holder"):
            merges.append({"name_text": form["text"], "seen_on": form["seen_on"], "person_id": deceased_id,
                           "confidence": 0.8, "evidence": "Account holder or the person named on their own paper."})
            continue
        rel = {"spouse": "spouse", "mother": "mother", "father": "father", "nominee": None, "subject": None}.get(role)
        match = None
        for person in people:
            if (rel and person.get("relationship") in (rel, "wife", "husband") and rel == "spouse") or \
                    (rel and person.get("relationship") == rel):
                match = person
                break
            if name_similarity(person.get("name", ""), form["text"]) >= 0.85:
                match = person
                break
        merges.append({"name_text": form["text"], "seen_on": form["seen_on"],
                       "person_id": match["id"] if match else None,
                       "new_person": None if match else " ".join(t.capitalize() for t in tokens(form["text"])),
                       "relationship": rel or (match or {}).get("relationship"), "confidence": 0.7,
                       "evidence": "Named on the paper in this role."})
    return {"merges": merges}


@oracle("merge_critic")
def merge_critic(ctx: Ctx, req: OracleRequest) -> dict:
    verdicts = []
    for m in ctx.payload(req).get("merges", []):
        conf = m.get("confidence", 0.7)
        text = m["name_text"]
        if "spelled differently" in m.get("evidence", "") and conf < 0.8:
            verdicts.append({"name_text": text, "keep": False, "reason": "Only the name links these; nothing else does.",
                             "ask_family": True, "question": f"Is “{text}” on this paper the same person?"})
            continue
        reason = "The evidence holds: " + m.get("evidence", "")
        if "PAN" in m.get("evidence", "") and script_of(text) == "Latn":
            reason = "The spelling differs from his other papers, but the PAN matches, so this is the same person."
        verdicts.append({"name_text": text, "keep": True, "reason": reason, "ask_family": False})
    return {"verdicts": verdicts}


@oracle("institution_resolver")
def institution_resolver(ctx: Ctx, req: OracleRequest) -> dict:
    # The module has already tried the directory by name; offline there is nothing more to search.
    return {"matches": [{"text": t.get("text", ""), "institution_id": None, "confidence": 0.0,
                         "reason": "Not in the institution directory; a live run searches the web."}
                        for t in ctx.payload(req).get("texts", [])]}
