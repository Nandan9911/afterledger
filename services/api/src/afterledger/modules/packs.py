"""Module 7: forms and letters, assembled into one pack per claim.

For each paper the plan needs, the form is found and understood, every value is composed in the
form's script and format, the form is filled with each value in its own box, then read back out of
the finished file. Signature, witness and stamp areas stay empty. A covering letter is written in
the institution's language from a facts block, with a mirror copy for the family that is
translated back and compared. Nothing is sent without the family's approval (checkpoint C6).
"""
from __future__ import annotations

from typing import Any

from ..agents.runner import call_agent, repair_loop
from ..domain.case import CheckResult, Enclosure, FilledField, Pack, PackItem
from ..domain.checks import fidelity_violations
from ..domain.ids import stable_id
from ..domain.plan_runtime import documents_for, route
from ..forms.context import build_context, values_for
from ..forms.render import readback_pdf, render_form
from .common import case_block, knowledge, open_checkpoint, pack_key, snapshot

MODULE = "forms"


def _signers(snap: Any, roles: list[str], claim: Any = None) -> list[str]:
    fam = snap.family()
    claimant = snap.claimant(claim)
    out: list[str] = []
    for r in roles:
        if r in ("claimant", "nominee", "beneficiary") and claimant:
            out.append(claimant.id)
        elif r == "other_heirs":
            out += [p.id for p in fam if "heir" in p.roles and (claimant is None or p.id != claimant.id)]
        elif r == "independent_person":
            out += [p.id for p in fam if "declarant" in p.roles]
        elif r == "executor":
            out += [p.id for p in fam if "executor" in p.roles]
    return list(dict.fromkeys(out))


async def prepare_pack(deps: Any, case_id: str, claim_id: str) -> Pack | None:
    snap = await snapshot(deps, case_id)
    case = snap.case
    claim = snap.claim(claim_id)
    cp = snap.plan_for_claim(claim_id)
    if claim is None or cp is None:
        return None
    k = knowledge(deps)
    plan = await k.plan(cp.plan_key)
    if plan is None:
        return None
    inst = await k.institution_raw(claim.institution_id) or {}
    facts = {kk: v for kk, v in cp.facts.items() if not kk.startswith("__")}
    route_steps = route(plan, facts)
    draft = False
    if not route_steps:
        # nothing is decided yet (Japan's choice to accept or renounce, say): papers for the submission
        # that waits on the decision are prepared as a draft, never as a pack to send
        from ..domain.plan_runtime import run as plan_run
        waiting = [sid for sid in plan_run(plan, facts).blocked if plan.step(sid).kind == "submit"]
        route_steps, draft = waiting[:1], bool(waiting)
    personal_law = None
    if case.demo_key:
        from ..seed.families import load_family
        personal_law = load_family(case.demo_key, case.story_day).deceased.get("personal_law")
    ctx, sources = build_context(snap, claim, institution=inst, plan_facts=facts, today=deps.today_for(case),
                                 personal_law=personal_law)
    items: list[PackItem] = []
    enclosures: list[Enclosure] = []
    checks: list[CheckResult] = []
    total_checked = total_matched = layout_only = 0
    have_types = {p.doc_type for p in snap.papers}
    seen: set[str] = set()
    for sid in route_steps:
        step = plan.step(sid)
        for doc, _verdict, alts in documents_for(plan, step, facts):
            if doc.id in seen:
                continue
            seen.add(doc.id)
            if not doc.form_key:
                status = "have" if _have(doc.id, have_types) else "missing"
                if "notarised" in doc.certification:
                    status = "needs_notarising" if status == "have" else status
                if "apostille" in doc.certification and status != "have":
                    status = "needs_apostille"
                enclosures.append(Enclosure(name=doc.name + (" (or " + ", ".join(plan.document(a).name for a in alts) + ")"
                                                             if alts else ""), status=status,  # type: ignore[arg-type]
                                            note=doc.note or _enclosure_note(doc.id, status)))
                continue
            spec = await k.form(doc.form_key)
            scout = await call_agent(deps, "form_scout", {"key": pack_key("form_scout", case, claim_id, doc.form_key),
                                                          "case": case_block(case), "form_key": doc.form_key,
                                                          "institution": inst.get("name"), "found": bool(spec)},
                                     case_id=case_id)
            if spec is None or scout.letter_instead:
                enclosures.append(Enclosure(name=doc.name, status="missing", note="No form in the library; a letter "
                                            "asks the institution for it."))
                continue
            await call_agent(deps, "form_understander", {"key": pack_key("form_understander", case, doc.form_key),
                                                         "case": case_block(case), "form": {"key": spec["key"],
                                                         "fields": [f["id"] for b in spec.get("blocks", []) if b["type"] == "fields"
                                                                    for f in b["fields"]]}}, case_id=case_id)
            signer_ids = _signers(snap, doc.signed_by, claim)
            targets = [None]
            if spec.get("per_signer"):
                targets = [pid for pid in signer_ids] or [None]
            for target in targets:
                local_ctx = dict(ctx)
                if target:
                    person = snap.person(target)
                    local_ctx["signer"] = {"name": person.display_name.upper() if person else "",
                                           "relationship": (person.relationship or "").title() if person else ""}
                values = values_for(spec, local_ctx)
                compose_pack = {
                    "key": pack_key("value_composer", case, claim_id, doc.form_key, target or ""),
                    "case": case_block(case), "form_key": doc.form_key,
                    "values": {kk: v for kk, v in values.items() if not isinstance(v, list)},
                    "sources": sources}
                composed = await call_agent(deps, "value_composer", compose_pack, case_id=case_id)
                filled, rb, critic, composed, still = await fill_and_check(
                    deps, case, spec, values, compose_pack, composed,
                    critic_key=pack_key("visual_critic", case, claim_id, doc.form_key, target or ""),
                    subject=f"pack:{claim_id}:{doc.form_key}{':' + target if target else ''}")
                notes = {c.field_id: c for c in composed.values}
                total_checked += rb["checked"]
                total_matched += rb["matched"]
                layout_only += rb["layout_only"]
                suffix = f"_{target}" if target else ""
                ref = await deps.blobs.put(f"cases/{case_id}/packs/{claim_id}/{doc.form_key.replace('/', '_')}{suffix}.pdf",
                                           filled.pdf, "application/pdf")
                fields = []
                for b in spec.get("blocks", []):
                    if b["type"] == "fields":
                        for f in b["fields"]:
                            v = values.get(f["id"])
                            if v in (None, ""):
                                continue
                            n = notes.get(f["id"])
                            placed = (filled.placed.get(f["id"]) or [None])[0]
                            fields.append(FilledField(key=f["id"], label=f["label"], value=str(v),
                                                      source=sources.get(f.get("path", ""), "the case"),
                                                      readback_ok=f["id"] not in rb["failures"],
                                                      certainty=n.certainty if n else "sure", note=n.note if n else "",
                                                      page=placed.page if placed else None,
                                                      box=_norm_box(placed.box) if placed else None))
                person = snap.person(target) if target else None
                title = spec["title"] + (f" - {person.display_name}" if person else "")
                signers = [target] if target else signer_ids
                held = ("A person must check this form before it is sent: " + "; ".join(still) + ". ") if still else ""
                items.append(PackItem(id=stable_id("item", claim_id, doc.form_key, target or ""), kind="form",
                                      title=title, form_key=doc.form_key, language=spec.get("language", "en"),
                                      file=ref, fields=fields, signers=signers, status="draft" if still else "ready",
                                      note=held + _form_note(spec, doc, signers, snap) + ("" if critic.ok or still else
                                                                                          " " + "; ".join(critic.problems))))
    # a declaration that two spellings are the same person, when the account spells the name differently
    variance = _name_variance(ctx)
    if variance:
        items.append(PackItem(id=stable_id("item", claim_id, "name_variance"), kind="declaration",
                              title="Declaration that both spellings name the same person", language="en",
                              text=variance, signers=_signers(snap, ["claimant"], claim), status="ready",
                              note="Not required by the rule; added so the institution can match the names."))
    # covering letter in the institution's language, with a mirror copy for the family
    letter_facts = " ".join([claim.title, cp.title, inst.get("name") or "", inst.get("branch") or "",
                             await _through_bank(deps, snap, claim, inst) or ""]
                            + [i.title for i in items] + [e.name for e in enclosures])
    allowed = _facts_text(ctx, claim) + " " + letter_facts
    letter, back, repairs = await _covering_letter(deps, snap, claim, cp, inst, items, enclosures, allowed)
    items.append(letter)
    viol = fidelity_violations(letter.text or "", allowed)
    if viol:                              # still failing after its repairs: held for a person
        letter.status = "draft"
        letter.note = ("A person must check this letter before it is sent: " + ", ".join(sorted(viol))
                       + " is not in the case. " + letter.note)
    if total_checked:                     # a pack of letters alone has no filled form to read back
        checks.append(CheckResult(name="readback", result="pass" if total_matched == total_checked else "fail",
                                  detail=f"{total_matched} of {total_checked} values match"
                                  + (f" ({layout_only} in scripts PDF text cannot carry, checked against the layout)"
                                     if layout_only else ""),
                                  params={"matched": total_matched, "checked": total_checked,
                                          "layout_only": layout_only}))
    checks.append(CheckResult(name="letter_fidelity", result="pass" if not viol else "fail",
                              detail=("no number or date that is not in the case" if not viol else ", ".join(sorted(viol)))
                              + (f" (after {repairs} repair{'s' if repairs != 1 else ''})" if repairs else ""),
                              params={"unsupported": sorted(viol), "repairs": repairs}))
    if letter.mirror_text:
        checks.append(mirror_check(letter.text or "", letter.mirror_text, letter.mirror_language or "", back))
    stale = []
    for aid in plan.all_cites():
        a = await k.assertion(aid)
        if a and not k.is_fresh(a, deps.today_for(case)):
            stale.append(aid)
    checked_on = plan.verified_on or deps.today_for(case)
    checks.append(CheckResult(name="rules_fresh", result="pass" if not stale else "fail",
                              detail="fresh, last checked " + checked_on.isoformat()
                              if not stale else "stale: " + ", ".join(stale),
                              params={"checked_on": checked_on.isoformat(), "stale": stale}))
    pending = [e for e in enclosures if e.status in ("missing", "unknown")]
    from .rules_research import is_interim
    interim = is_interim(cp.plan_key)
    # the interim plan's letter is a pack of its own, so the claim's papers (prepared once its rules are researched)
    # do not replace the letter the family already sent
    pack = Pack(id=stable_id("pack", claim_id, "interim") if interim else stable_id("pack", claim_id), case_id=case_id,
                claim_id=claim_id, plan_key=cp.plan_key, title=claim.title, items=items, enclosures=enclosures,
                checks=checks, status="draft" if draft else "ready",
                summary=_summary(cp, items, snap), submit_how="Hand in at any branch and ask for a written "
                "acknowledgement." if plan.institution_kind == "bank" else "Send as the institution directs.")
    prev = await deps.cases.get(case_id, "packs", pack.id)
    if prev and prev.status in ("approved", "submitted"):
        pack.status, pack.approved_by, pack.approved_on = prev.status, prev.approved_by, prev.approved_on
    await deps.cases.put(case_id, "packs", pack)
    claim.pack_id = pack.id
    claim.signers = {it.form_key or it.id: it.signers for it in items}
    if claim.status == "planning" and not draft:
        claim.status = "ready"
        claim.next_action = "Approve and sign the papers"
    await deps.cases.put(case_id, "claims", claim)
    if pack.status == "ready":
        await open_checkpoint(deps, case, code="C6", kind="pack_approval", title=f"Review the papers: {pack.title}",
                              question="Are these papers right? Approve them to sign and send.",
                              options=[("approve", "Approve these papers"), ("change", "Something needs changing")],
                              subject_ref=f"pack:{pack.id}", why=pack.summary, payload={"claim_id": claim_id})
    n_items = f"{len(items)} {'paper' if len(items) == 1 else 'papers'}"
    n_pending = f"{len(pending)} {'paper' if len(pending) == 1 else 'papers'}"
    await deps.activity(case_id, MODULE, f"Prepared the papers for {claim.title}: {n_items}, "
                        f"{total_matched} of {total_checked} values read back from the finished files."
                        + (f" {n_pending} still to get." if pending else ""), kind="done",
                        claim_id=claim_id)
    deps.changed(case_id, "packs", "claims", "checkpoints")
    return pack


def _norm_box(box: tuple[float, float, float, float], size: tuple[float, float] = (595.0, 842.0)) -> list[float]:
    x0, y0, x1, y1 = box
    return [round(x0 / size[0], 4), round(y0 / size[1], 4), round(x1 / size[0], 4), round(y1 / size[1], 4)]


def _have(doc_id: str, have_types: set[str]) -> bool:
    mapping = {"death_cert": "death_certificate", "claimant_ovd": "identity_card", "nominee_id": "identity_card",
               "policy_document": "insurance_policy", "cml_claimant": "demat_holding_statement_claimant",
               "will": "will", "death_report": "death_certificate", "certified_death_cert": "death_certificate",
               "death_cert_copy": "death_certificate"}
    return mapping.get(doc_id, "__none__") in have_types


def _enclosure_note(doc_id: str, status: str) -> str:
    if status == "have":
        return "Among the papers already uploaded."
    return {"cml_claimant": "The claimant needs a demat account of her own for the shares to be moved into.",
            "legal_heir_cert": "Issued by the revenue authority."}.get(doc_id, "")


def _form_note(spec: dict, doc: Any, signers: list[str], snap: Any) -> str:
    names = [snap.person(s).display_name for s in signers if snap.person(s)]
    parts = []
    if names:
        parts.append(" and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names) + " sign" +
                     ("s" if len(names) == 1 else "") + ".")
    if spec.get("stamp_paper"):
        parts.append("On stamp paper.")
    if spec.get("notarised"):
        parts.append("Before a notary.")
    return " ".join(parts)


def _name_variance(ctx: dict) -> str | None:
    """A declaration only when the spellings really differ (Varma and Verma); initials, titles and
    word order (R K VERMA, MRS M A CLARKE) are matched by the institution without one."""
    from ..domain.names import initials_compatible, strip_honorifics
    a, b = ctx["deceased"].get("name"), ctx["deceased"].get("name_cert")
    if not (a and b) or a.replace(" ", "").upper() == b.replace(" ", "").upper():
        return None
    from ..domain.names import script_of
    if script_of(a) != "Latn" or script_of(b) != "Latn":
        return None                       # katakana on the account, kanji on the certificate: the same name
    a2, b2 = strip_honorifics(a)[0], strip_honorifics(b)[0]
    if initials_compatible(a2, b2):
        return None
    if a and b:
        return (f"I, {ctx['claimant']['name']}, declare that {a}, the name in which the account is held, and {b}, "
                f"the name on the death certificate, are one and the same person.")
    return None


def _facts_text(ctx: dict, claim: Any) -> str:
    import json
    return json.dumps(ctx, default=str) + f" {claim.amount_due}"


def _summary(cp: Any, items: list[PackItem], snap: Any) -> str:
    signers = sorted({snap.person(s).display_name for it in items for s in it.signers if snap.person(s)})
    return (cp.summary + (" Signed by " + ", ".join(signers) + "." if signers else "")).strip()


async def _through_bank(deps: Any, snap: Any, claim: Any, inst: dict) -> str | None:
    """A scheme claimed through a bank account (India's PMJJBY, say) is claimed at that bank's branch: the bank
    whose own paper showed the scheme, when it is not the scheme itself."""
    if inst.get("kind") != "government":
        return None
    for h in snap.holdings:
        if h.claim_id != claim.id:
            continue
        for ev in h.evidence:
            paper = snap.paper(ev.paper_id) if ev.paper_id else None
            if paper and paper.institution_id and paper.institution_id != claim.institution_id:
                bank = await knowledge(deps).institution_raw(paper.institution_id) or {}
                if bank.get("kind") == "bank" and bank.get("name"):
                    return str(bank["name"])
    return None


def _form_issues(values: dict, filled: Any, rb: dict, critic: Any) -> tuple[dict[str, dict], list[str]]:
    """What the form's checks found, by field: a value too long for its box, or one that did not read back from the
    finished file; and the visual critic's findings."""
    fields: dict[str, dict] = {}
    for fid in filled.overflow:
        fields[fid] = {"issue": "too long for its box", "intended": str(values.get(fid, "")),
                       "box_holds_characters": filled.capacity.get(fid)}
    for fid in rb.get("failures", []):
        if isinstance(values.get(fid), (str, int, float)):
            fields.setdefault(fid, {"issue": "did not read back from the finished file", "intended": str(values[fid])})
    problems = [f"{fid}: {x['intended']!r} {x['issue']}" for fid, x in fields.items()]
    problems += [p for p in (critic.problems if not critic.ok else []) if not any(fid in p for fid in fields)]
    return fields, problems


async def fill_and_check(deps: Any, case: Any, spec: dict, values: dict, compose_pack: dict, composed: Any, *,
                         critic_key: str, subject: str) -> tuple[Any, dict, Any, Any, list[str]]:
    """Fill the form, read every value back out of the finished file and let the visual critic look at it; a value that
    is too long for its box or does not read back goes back to value_composer with the field, the intended value and
    what was found, and the form is filled and checked again, up to AL_REPAIR_ROUNDS times. A re-composed value may
    shorten or re-spell a value but never change a number in it. Returns the form, its read-back, the critic's view,
    the last composition and what still fails."""
    from ..agents.context import working_on
    from ..agents.meter import BudgetExceeded
    title = spec.get("title", compose_pack.get("form_key", "form"))
    rounds = max(0, int(getattr(deps.settings, "repair_rounds", 0) or 0))
    n = 0
    while True:
        filled = render_form(spec, values)
        rb = readback_pdf(filled.pdf, filled, values)
        critic = await call_agent(deps, "visual_critic", {
            "key": critic_key + (f"/check{n}" if n else ""), "case": case_block(case), "form": title,
            "overflow": filled.overflow, "readback": rb, "keep_empty": _keep_empty(spec)}, case_id=case.id,
            images=_page_images(filled.pdf) if deps.models.mode != "scripted" else None)
        fields, problems = _form_issues(values, filled, rb, critic)
        if problems and not fields:
            # what the critic saw is not a value that can be composed again (a layout, an area it doubts): no repair
            # round can change it, so a person checks the form instead
            await deps.ledger(case.id, subject, "visual_check", "unknown",
                              method=f"the form {title}, looked at by the visual critic",
                              detail="; ".join(problems)[:1000])
            break
        if not problems or n >= rounds:
            break
        n += 1
        await deps.ledger(case.id, subject, "repair", "fail", method=f"the form {title}, filled and read back (round {n})",
                          detail="; ".join(problems)[:1000])
        await deps.activity(case.id, MODULE, f"The form “{title}” did not pass its check: {'; '.join(problems[:3])}. "
                            f"Sent back to have {'those values' if len(fields) != 1 else 'that value'} composed again.",
                            agent="value_composer", kind="check")
        try:
            with working_on(case.id, "repair"):
                composed = await call_agent(deps, "value_composer", {
                    **compose_pack, "key": f"{compose_pack['key']}/repair{n}",
                    "previous_answer": composed.model_dump(mode="json"), "problems": problems, "fix_fields": fields,
                    "repair": "Compose again only the fields in fix_fields: shorter forms that fit their boxes (standard "
                              "abbreviations, no lost information) or the same words spelled so they read back. Never "
                              "change a number, a name or a date."}, case_id=case.id)
        except BudgetExceeded:
            await deps.activity(case.id, MODULE, f"The case reached its budget for model work, so the form “{title}” "
                                "was not composed again. A person checks it before it can be approved.", kind="warning")
            break
        for c in composed.values:
            old = values.get(c.field_id)
            if c.field_id in fields and c.value and isinstance(old, str) and c.value != old \
                    and not fidelity_violations(c.value, old):
                values[c.field_id] = c.value
    if n:
        await deps.ledger(case.id, subject, "repair", "pass" if not problems else "fail",
                          method=f"the form {title}, filled and read back again after {n} repair{'s' if n != 1 else ''}",
                          detail="; ".join(problems)[:1000] if problems else "every value fits and reads back")
        if not problems:
            await deps.activity(case.id, MODULE, f"Composed again and checked: every value on “{title}” fits its box and "
                                "reads back from the finished file.", agent="value_composer", kind="check")
    return filled, rb, critic, composed, problems


def unsupported(body: str, allowed: str) -> list[str]:
    """What a letter's check finds: each number or date in it that is not in the case."""
    return [f"{n} is not in the case" for n in sorted(fidelity_violations(body, allowed))]


async def _covering_letter(deps: Any, snap: Any, claim: Any, cp: Any, inst: dict, items: list[PackItem],
                           enclosures: list[Enclosure], allowed: str) -> tuple[PackItem, Any, int]:
    case = snap.case
    claimant = snap.claimant(claim)
    from .challenge import COUNTRY_LANG
    # the institution's language: Spanish to a bank in Spain, Japanese in Japan, English in India
    lang = COUNTRY_LANG.get((claim.country or "").split("-")[0], "en")
    through = await _through_bank(deps, snap, claim, inst)
    out, _still, repairs = await repair_loop(deps, "letter_writer", {
        "key": pack_key("letter_writer", case, claim.id), "case": case_block(case), "lang": lang,
        "facts": {"institution": inst.get("name"), "branch": inst.get("branch"), "deceased": case.title,
                  "through": through,
                  "country": (claim.country or inst.get("country") or "").split("-")[0],
                  "claimant": claimant.display_name if claimant else None, "claim": claim.title,
                  "items": [i.title for i in items], "enclosures": [e.name for e in enclosures],
                  "plan": cp.title}}, lambda o: unsupported(o.body, allowed), case_id=case.id,
        subject=f"pack:{claim.id}:cover", what="the covering letter", module=MODULE)
    mirror_lang = claimant.languages[0] if claimant and claimant.languages and claimant.languages[0] != lang else None
    mirror, back = None, None
    if mirror_lang:
        mirror = (await deps.translator.translate([out.body], mirror_lang, lang))[0]
        if mirror != out.body:
            back = await call_agent(deps, "back_translator", {
                "key": pack_key("back_translator", case, claim.id, "cover"), "case": case_block(case),
                "original": out.body, "translation": mirror, "lang": mirror_lang}, case_id=case.id)
    from .research import language_name
    has_mirror = bool(mirror and mirror != out.body)
    who = claimant.display_name.split()[0] if claimant else "the family"
    note = language_name(lang) + (f", with a {language_name(mirror_lang)} copy for {who}." if has_mirror else
                                  f". A {language_name(mirror_lang)} copy for {who} needs a live run." if mirror_lang else ".")
    return PackItem(id=stable_id("item", claim.id, "cover"), kind="letter", title="Covering letter", language=lang,
                    text=out.body, mirror_text=mirror if has_mirror else None,
                    mirror_language=mirror_lang if has_mirror else None,
                    signers=[claimant.id] if claimant else [], status="ready", note=note), back, repairs


def mirror_check(original: str, mirror: str, lang: str, back: Any) -> CheckResult:
    """The family's copy of a letter must say what the letter says: translated back and compared by an agent,
    and every number in one found in the other, by code."""
    from .research import language_name
    numbers = fidelity_violations(mirror, original) | fidelity_violations(original, mirror)
    problems = ([f"numbers differ: {', '.join(sorted(numbers))}"] if numbers else []) + \
        (list(back.differences) if back is not None and not back.ok else [])
    return CheckResult(name="back_translation", result="fail" if problems else "pass",
                       detail="; ".join(problems) if problems else
                       f"the {language_name(lang)} copy was translated back and compared, and its numbers match",
                       params={"language": lang, "numbers": sorted(numbers)})


async def approve_pack(deps: Any, case_id: str, pack_id: str, *, by: str | None) -> Pack:
    pack: Pack = await deps.cases.get(case_id, "packs", pack_id)
    case = await deps.cases.get_case(case_id)
    pack.status, pack.approved_by, pack.approved_on = "approved", by or "family", deps.today_for(case)
    await deps.cases.put(case_id, "packs", pack)
    for c in await deps.cases.list(case_id, "checkpoints"):
        if c.subject_ref == f"pack:{pack_id}" and c.status == "open":
            c.status, c.answer, c.answered_by, c.answered_at = "answered", "approve", by, deps.now()
            await deps.cases.put(case_id, "checkpoints", c)
    person = await deps.cases.get(case_id, "people", by) if by else None
    await deps.activity(case_id, MODULE, f"{person.display_name if person else 'The family'} approved the papers for "
                        f"{pack.title}.", kind="decision")
    deps.changed(case_id, "packs", "checkpoints")
    return pack


def _page_images(pdf: bytes, dpi: int = 110) -> list[tuple[bytes, str]]:
    """The filled pages as images, for the visual critic to look at (live models only)."""
    import pymupdf
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return [(page.get_pixmap(dpi=dpi).tobytes("png"), "image/png") for page in doc]


def _keep_empty(spec: dict) -> list[str]:
    """Areas of a form that must stay empty when we fill it: signatures, seals, witnesses, office use."""
    out = []
    for b in spec.get("blocks", []):
        if b.get("type") in ("signature", "signatures", "seal", "witness", "office"):
            out.append(b.get("label") or b.get("type"))
        for f in b.get("fields", []) if b.get("type") == "fields" else []:
            if f.get("fill_by") in ("family", "institution", "witness", "notary"):
                out.append(f.get("label") or f["id"])
    return out
