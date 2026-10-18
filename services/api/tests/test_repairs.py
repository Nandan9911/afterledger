"""Check, fix, check again: a letter with a number that is not in the case goes back to its writer with the problem
and is checked again; a form value too long for its box is composed again, filled again and read back again; what
still fails after the repairs is held for a person."""
from __future__ import annotations

from typing import Any

import pytest

from afterledger.agents.runner import repair_loop
from conftest import offline_settings

ALLOWED = "Narmada Bank claim of 641870.00 confirmed on 2026-09-10, due 2026-09-25"
GOOD = "Dear Sir, the claim of ₹6,41,870.00 has been due since 25 September 2026. Please settle it."
BAD = "Dear Sir, the claim of ₹7,41,870.00 has been due since 25 September 2026. Please settle it."


def _letters(deps: Any, bodies: list[str]) -> list[dict]:
    """letter_writer answers with these bodies in turn; returns the packs it was given."""
    seen: list[dict] = []
    original = deps.models._oracle

    def oracle_for(name: str):
        if name != "letter_writer":
            return original(name)

        def answer(req: Any) -> dict:
            seen.append(req.payload)
            return {"subject": "Claim", "body": bodies.pop(0), "lang": "en"}
        return answer
    deps.models._oracle = oracle_for
    return seen


async def _case(deps: Any, case_id: str) -> None:
    from afterledger.domain.case import CaseFile
    await deps.cases.put_case(CaseFile(id=case_id, title="Test", created_at=deps.now()))


def _check(out: Any) -> list[str]:
    from afterledger.modules.packs import unsupported
    return unsupported(out.body, ALLOWED)


async def test_a_letter_with_an_invented_amount_is_rewritten_and_passes(deps: Any) -> None:
    await _case(deps, "case-r1")
    seen = _letters(deps, [BAD, GOOD])
    out, still, repairs = await repair_loop(deps, "letter_writer", {"key": "letter_writer/r1", "facts": {}}, _check,
                                            case_id="case-r1", subject="pack:c1:cover", what="the covering letter",
                                            module="forms")
    assert out.body == GOOD and still == [] and repairs == 1
    assert seen[1]["problems"] == ["741870 is not in the case"] and seen[1]["previous_answer"]["body"] == BAD
    ledger = [e for e in await deps.cases.list("case-r1", "ledger") if e.check == "repair"]
    assert sorted(e.result for e in ledger) == ["fail", "pass"]
    lines = [a.text for a in await deps.cases.list("case-r1", "activity")]
    assert any("did not pass its check" in t and "741870 is not in the case" in t for t in lines)
    assert any(t.startswith("Fixed, and checked again") for t in lines)


async def test_a_letter_that_keeps_failing_is_held_for_a_person(deps: Any) -> None:
    await _case(deps, "case-r2")
    _letters(deps, [BAD, BAD, BAD])
    _, still, repairs = await repair_loop(deps, "letter_writer", {"key": "letter_writer/r2", "facts": {}}, _check,
                                            case_id="case-r2", subject="pack:c1:cover", what="the covering letter",
                                            module="forms")
    assert repairs == 2 and still == ["741870 is not in the case"]
    final = [e for e in await deps.cases.list("case-r2", "ledger") if e.check == "repair"]
    assert sum(e.result == "fail" for e in final) == 3          # two rounds, and the verdict after them


async def test_with_repairs_off_a_failing_letter_is_not_sent_back(tmp_path: Any) -> None:
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, repair_rounds=0))
    await _case(deps, "case-r3")
    seen = _letters(deps, [BAD])
    _, still, repairs = await repair_loop(deps, "letter_writer", {"key": "letter_writer/r3"}, _check,
                                          case_id="case-r3", subject="s", what="the letter", module="forms")
    assert repairs == 0 and still and len(seen) == 1


async def test_a_spent_budget_stops_the_repairs(tmp_path: Any) -> None:
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, case_budget_usd=0.01))
    await _case(deps, "case-r4")
    deps.meter.by_case["case-r4"] = __import__("afterledger.agents.meter", fromlist=["Usage"]).Usage(calls=9,
                                                                                                      cost_usd=0.02)
    deps.meter._loaded.add("case-r4")
    seen = _letters(deps, [BAD, GOOD])
    _, still, repairs = await repair_loop(deps, "letter_writer", {"key": "letter_writer/r4"}, _check,
                                          case_id="case-r4", subject="s", what="the letter", module="forms")
    assert still and repairs == 1 and len(seen) == 1                # the repair was stopped before it was asked
    assert any("reached its budget" in a.text for a in await deps.cases.list("case-r4", "activity"))


# ---- forms ----------------------------------------------------------------------------------------------------------

SPEC = {"key": "test/claim_form", "title": "Claim form", "language": "en",
        "blocks": [{"type": "fields", "label_w": 190, "fields": [
            {"id": "deceased_name", "label": "Name of the deceased", "path": "deceased.name"},
            {"id": "address", "label": "Last address", "path": "deceased.address"}]}]}
LONG = "Flat 12, Shanti Apartments, Near Hanuman Temple, Gomti Nagar, Lucknow 226010, Uttar Pradesh, India"


async def test_a_value_too_long_for_its_box_is_composed_again_and_reads_back(deps: Any) -> None:
    from afterledger.agents.runner import call_agent
    from afterledger.domain.case import CaseFile
    from afterledger.modules.packs import fill_and_check
    case = CaseFile(id="case-f1", title="Test", created_at=deps.now())
    await deps.cases.put_case(case)
    values = {"deceased_name": "RAJESH KUMAR VERMA", "address": LONG}
    pack = {"key": "value_composer/f1", "case": {"id": case.id}, "form_key": SPEC["key"], "values": dict(values),
            "sources": {}}
    composed = await call_agent(deps, "value_composer", pack, case_id=case.id)
    filled, rb, critic, _composed, still = await fill_and_check(deps, case, SPEC, values, pack, composed,
                                                               critic_key="visual_critic/f1", subject="pack:c1:form")
    assert still == [] and not filled.overflow and critic.ok
    assert rb["matched"] == rb["checked"] == 2
    assert values["address"] != LONG and "226010" in values["address"]       # shorter, and no number changed
    lines = [a.text for a in await deps.cases.list(case.id, "activity")]
    assert any("did not pass its check" in t for t in lines) and any("Composed again and checked" in t for t in lines)


async def test_what_only_the_critic_saw_is_held_for_a_person_not_composed_again(deps: Any) -> None:
    """A live visual critic took the signer's printed name for typed text in the signature area, twice: a finding
    that is not a value to compose again sends nothing back, and a person checks the form."""
    from afterledger.agents.runner import call_agent
    from afterledger.domain.case import CaseFile
    from afterledger.modules.packs import fill_and_check
    case = CaseFile(id="case-f2", title="Test", created_at=deps.now())
    await deps.cases.put_case(case)
    values = {"deceased_name": "RAJESH KUMAR VERMA", "address": "Gomti Nagar, Lucknow"}
    pack = {"key": "value_composer/f2", "case": {"id": case.id}, "form_key": SPEC["key"], "values": dict(values),
            "sources": {}}
    composed = await call_agent(deps, "value_composer", pack, case_id=case.id)
    asked: list[str] = []
    original = deps.models._oracle

    def oracle_for(name: str):
        if name == "value_composer":
            def composer(req: Any) -> dict:
                asked.append(req.payload.get("key"))
                return original(name)(req)
            return composer
        if name != "visual_critic":
            return original(name)
        return lambda req: {"ok": False, "problems": ["The signature field contains typed text."]}
    deps.models._oracle = oracle_for
    _f, _rb, critic, _c, still = await fill_and_check(deps, case, SPEC, values, pack, composed,
                                                       critic_key="visual_critic/f2", subject="pack:c2:form")
    assert not critic.ok and still == ["The signature field contains typed text."] and asked == []
    checks = [(e.check, e.result) for e in await deps.cases.list(case.id, "ledger")]
    assert checks == [("visual_check", "unknown")]


@pytest.mark.slow
def test_the_vermas_reminder_is_fixed_before_anyone_sees_it(demo_state: Any) -> None:
    """The Vermas' first reminder is drafted with one wrong figure (scripted, to show the check); the check sends it
    back, the repair passes, and the letter the family sees has every number from the case."""
    import asyncio

    from afterledger.runtime import build_deps

    async def go() -> tuple[list, list, list]:
        deps = build_deps(offline_settings(demo_state.root))
        return (await deps.cases.list("demo-verma", "ledger"), await deps.cases.list("demo-verma", "activity"),
                await deps.cases.list("demo-verma", "correspondence"))

    ledger, activity, corr = asyncio.run(go())
    reminder = [e for e in ledger if e.subject.endswith(":letter:reminder")]
    assert [e.result for e in reminder if e.check == "repair"] == ["fail", "pass"]
    assert [e.result for e in reminder if e.check == "fidelity"] == ["pass"]
    assert any("did not pass its check" in a.text and "scripted" in a.text for a in activity)
    drafted = [c for c in corr if c.direction == "out" and c.status == "draft"]
    assert drafted and "A person must check" not in drafted[0].summary


@pytest.mark.slow
async def test_a_family_copy_that_fails_its_check_says_so(demo_copy: Any) -> None:
    """Live, Cloud Translation turned "ending 0932" into "dated 0932" in the Vermas' Hindi copy of a reminder; the
    back-translation check caught it, and the family is now told on the letter itself, not only in the ledger."""
    from afterledger.modules import followup
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(demo_copy))
    claims = [c for c in await deps.cases.list("demo-verma", "claims") if c.institution_id == "in_narmada_bank"]

    async def translate(texts: list[str], target: str, source: str | None = None) -> list[str]:
        return ["प्रिय महोदय, खाता दिनांक 0932 के बारे में।" for _ in texts]
    deps.translator.translate = translate
    original = deps.models._oracle

    def oracle_for(name: str):
        if name != "back_translator":
            return original(name)
        return lambda req: {"back_translation": "Dear Sir, about the account dated 0932.", "ok": False,
                            "differences": ["The account ending 0932 is called a date."]}
    deps.models._oracle = oracle_for
    corr = await followup.draft_letter(deps, "demo-verma", claims[0].id, "reminder")
    assert "machine translation" in corr.summary and "called a date" in corr.summary
