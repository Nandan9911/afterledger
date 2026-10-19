"""A real family's own case, not a sample, through every stage, offline (scripted agents): the story names banks,
an insurer and a lender the directory has never seen; a statement from another unknown bank is read; the
institutions are learned; every holding becomes a claim; a claim no stored plan covers starts on the interim plan
while its rules are researched in the background, and moves to the researched plan; plans are approved, papers
prepared and handed in; an institution's clock runs late and a reminder is drafted by itself; a letter with no reply
is chased. These are the stages the sample cases show, on a case of the family's own."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from conftest import api_client


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Any]:
    from afterledger.adapters.local.stores import FixedClock
    for c in api_client(tmp_path):
        c.deps.clock = FixedClock(date(2026, 10, 19))
        yield c


def settle(client: Any) -> None:
    """Background work, including research that runs outside the task queue, is finished."""
    async def wait() -> None:
        for _ in range(40):
            await client.deps.tasks.drain()
            running = [t for t in client.deps.extras.get("rules_research_tasks", ()) if not t.done()]
            if not running:
                await client.deps.tasks.drain()
                if not [t for t in client.deps.extras.get("rules_research_tasks", ()) if not t.done()]:
                    return
                continue
            await asyncio.gather(*running, return_exceptions=True)
    client.portal.call(wait)


def statement(bank: str, holder: str, rows: list[tuple[str, str, str, str, str]]) -> bytes:
    """A statement as a bank prints it: a letterhead, the holder, then dated rows in columns (date, narration,
    debit, credit, balance), each cell where a reader finds it."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 50), bank, fontsize=14)
    page.insert_text((40, 72), "Statement of account - savings bank", fontsize=10)
    page.insert_text((40, 90), f"Account holder: {holder}", fontsize=10)
    page.insert_text((40, 106), "Account number: XXXXXXXX4321", fontsize=10)
    page.insert_text((40, 122), "Nomination: Not registered", fontsize=10)
    y = 160
    for d, narration, debit, credit, balance in rows:
        if y > 800:
            page = doc.new_page(width=595, height=842)
            y = 60
        page.insert_text((40, y), d, fontsize=9)
        page.insert_text((105, y), narration, fontsize=9)
        if debit:
            page.insert_text((360, y), debit, fontsize=9)
        if credit:
            page.insert_text((430, y), credit, fontsize=9)
        page.insert_text((500, y), balance, fontsize=9)
        y += 16
    return doc.tobytes()


def rows_for(start: float) -> list[tuple[str, str, str, str, str]]:
    """A year of an ordinary account: salary, a yearly PMJJBY premium, a monthly SIP, a loan's instalments to a lender
    the directory does not know, and a few shops."""
    out = []
    bal = start

    def add(month: int, day: int, narration: str, debit: float = 0.0, credit: float = 0.0) -> None:
        nonlocal bal
        bal = round(bal - debit + credit, 2)
        out.append((f"{day:02d}-{month:02d}-26", narration, f"{debit:,.2f}" if debit else "",
                    f"{credit:,.2f}" if credit else "", f"{bal:,.2f}"))
    for month in range(1, 9):
        add(month, 1, "SALARY CREDIT DECCAN WORKS LTD", credit=86500.0)
        add(month, 5, "ACH D- NACH MF SIP CAMS 4471", debit=5000.0)
        add(month, 7, "ACH D- BAJAJ FINANCE LTD EMI 55120", debit=12400.0)
        add(month, 12, "UPI/GROCERY MART/PUNE", debit=1800.0 + month)
        if month == 5:
            add(month, 20, "PMJJBY PREM A/C 4321", debit=436.0)
    return out


def test_a_real_case_goes_through_every_stage(client: Any) -> None:
    deps = client.deps
    cid = client.post("/v1/cases", json={"lang": "en", "starter_name": "Arjun Sharma"}).json()["case"]["id"]
    for text in ("My father Ramesh Sharma died on 3 August 2026 in Pune. I am his son Arjun Sharma.",
                 "He had an account with State Bank of India, an LIC policy and a home loan from Bajaj Finance.",
                 "No, he did not leave a will. My mother Sunita Sharma lives in Pune."):
        r = client.post(f"/v1/cases/{cid}/turns", json={"text": text})
        assert r.status_code == 200, r.text
    case = client.get(f"/v1/cases/{cid}").json()["case"]
    assert case["died_on"] == "2026-08-03"
    summary = [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json() if c["code"] == "C1"]
    if not summary:                                    # one more turn, if the offline planner still asks
        client.post(f"/v1/cases/{cid}/turns", json={"text": "That is all we know for now."})
        summary = [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json() if c["code"] == "C1"]
    assert summary, "the summary is offered"
    assert client.post(f"/v1/cases/{cid}/checkpoints/{summary[0]['id']}/answer",
                       json={"answer": "yes"}).status_code == 200
    settle(client)

    # what the family named is on the money map, with institutions learned for this case
    holdings = client.portal.call(deps.cases.list, cid, "holdings")
    named = {(h.kind, h.source) for h in holdings}
    assert {("savings", "family"), ("life_policy", "family"), ("loan", "family")} <= named, named
    insts = {i.id: i for i in client.portal.call(deps.cases.list, cid, "institutions")}
    sbi = next(i for i in insts.values() if i.name == "State Bank of India")
    assert sbi.learned and sbi.kind == "bank" and sbi.id.startswith(cid + "~")
    assert next(i for i in insts.values() if i.name.startswith("Bajaj Finance")).kind == "lender"

    # every one of them is a claim with a plan; the loan's rules were researched (no stored plan covered them)
    claims = {c.title: c for c in client.portal.call(deps.cases.list, cid, "claims")}
    assert any(t.startswith("State Bank of India") for t in claims), claims.keys()
    loan = next(c for t, c in claims.items() if t.startswith("Bajaj Finance"))
    assert loan.kind == "debt" and loan.research.get("signature") == "IN/loan"
    assert loan.research.get("status") in ("done", "inconclusive")
    plans = {p.claim_id: p for p in client.portal.call(deps.cases.list, cid, "plans")}
    assert plans[loan.id].plan_key in ("IN/loan", "XX/interim_debt")
    bank_plan = next(p for c, p in plans.items() if claims_by_id(client, cid)[c].title.startswith("State Bank"))
    assert bank_plan.plan_key == "IN/bank_deposit"
    assert any("commercial bank" in q for q in bank_plan.questions), bank_plan.questions   # asked in words

    # a statement from a bank the directory has never seen
    pdf = statement("DECCAN SAMPLE BANK", "RAMESH SHARMA", rows_for(150000.0))
    up = client.post(f"/v1/cases/{cid}/papers", files={"files": ("statement.pdf", pdf, "application/pdf")},
                     data={"by": ""})
    assert up.status_code == 200, up.text
    settle(client)
    paper = client.get(f"/v1/cases/{cid}/papers").json()["papers"][0]["paper"]
    assert paper["doc_type"] == "bank_statement" and paper["institution_id"], paper
    deccan = client.portal.call(deps.cases.get, cid, "institutions", paper["institution_id"])
    assert deccan.name == "Deccan Sample Bank" and deccan.kind == "bank"
    claims = claims_by_id(client, cid)
    deccan_claim = next(c for c in claims.values() if c.institution_id == deccan.id)
    assert deccan_claim.kind == "claim"

    # Today asks for what a real case needs next, and the Plans screen lists every claim
    client.post(f"/v1/cases/{cid}/today", json={})
    settle(client)
    today = client.get(f"/v1/cases/{cid}/today").json()
    assert today["tasks"], "Today has things to do"
    rows = client.get(f"/v1/cases/{cid}/plans").json()
    assert len(rows["rows"] if isinstance(rows, dict) else rows) >= 4

    # the Deccan bank's plan: answer what it asks, approve, the papers are prepared, approved and handed in
    plan = plans_by_claim(client, cid)[deccan_claim.id]
    for _ in range(10):                  # each answer can settle or close the others: asked again each time
        open_qs = [q for q in client.get(f"/v1/cases/{cid}/checkpoints").json()
                   if q["code"] == "C5" and q["kind"] == "plan_question" and q["subject_ref"].startswith(f"plan:{plan.id}:")]
        if not open_qs:
            break
        q = open_qs[0]
        value = {"bank_type": "commercial", "has_will": "no", "dispute": "no", "nominee_or_survivor": "no",
                 "heirship_proof": "annex_ix"}.get(q["payload"]["variable"], q["options"][0]["value"])
        assert client.post(f"/v1/cases/{cid}/checkpoints/{q['id']}/answer",
                           json={"answer": value}).status_code == 200
        settle(client)                   # the answer is worked into the plan in the background
    plan = plans_by_claim(client, cid)[deccan_claim.id]
    approve(client, cid, "plan", plan.id)
    settle(client)
    packs = [p for p in client.portal.call(deps.cases.list, cid, "packs") if p.claim_id == deccan_claim.id]
    assert packs and packs[0].plan_key == "IN/bank_deposit" and any(i.kind == "letter" for i in packs[0].items)
    approve(client, cid, "pack", packs[0].id)
    handed = date(2026, 10, 19)
    assert client.post(f"/v1/cases/{cid}/claims/{deccan_claim.id}/events",
                       json={"event": "submitted", "on": handed.isoformat()}).status_code == 200
    assert client.post(f"/v1/cases/{cid}/claims/{deccan_claim.id}/events",
                       json={"event": "complete_set_confirmed", "on": handed.isoformat()}).status_code == 200
    tracker = client.get(f"/v1/cases/{cid}/tracker").json()
    row = next(r for r in tracker["rows"] if r["claim_id"] == deccan_claim.id)
    assert row["status"] == "acknowledged" and any(c["status"] == "running" for c in row["clocks"])

    # three weeks on, the bank is late: a reminder is drafted by itself, and Today says so
    deps.clock.day = handed + timedelta(days=21)
    client.post(f"/v1/cases/{cid}/today", json={})
    settle(client)
    letters = [x for x in client.portal.call(deps.cases.list, cid, "correspondence")
               if x.claim_id == deccan_claim.id and x.direction == "out"]
    assert letters and letters[0].kind == "reminder" and letters[0].status == "draft"
    today = client.get(f"/v1/cases/{cid}/today").json()
    assert today["late"] and today["late"][0]["claim_id"] == deccan_claim.id
    # drafted once: planning Today again adds no second letter
    client.post(f"/v1/cases/{cid}/today", json={})
    settle(client)
    assert len([x for x in client.portal.call(deps.cases.list, cid, "correspondence")
                if x.claim_id == deccan_claim.id and x.direction == "out"]) == 1

    # the family records what happened next, from the tracker: an amount it cannot read is said back plainly, one
    # typed the Indian way is read; a letter asking what is still needed can be drafted at any time
    events = f"/v1/cases/{cid}/claims/{deccan_claim.id}/events"
    bad = client.post(events, json={"event": "settled", "amount": "about a lakh"})
    assert bad.status_code == 422 and "number" in bad.json()["detail"]
    req = client.post(f"/v1/cases/{cid}/claims/{deccan_claim.id}/letters", json={"kind": "request"})
    assert req.status_code == 200 and req.json()["kind"] == "request" and req.json()["status"] == "draft"
    paid = client.post(events, json={"event": "settled", "amount": "₹ 1,20,000"})
    assert paid.status_code == 200 and paid.json()["status"] == "settled"
    assert Decimal(str(paid.json()["settled_amount"])) == Decimal("120000")
    row = next(r for r in client.get(f"/v1/cases/{cid}/tracker").json()["rows"] if r["claim_id"] == deccan_claim.id)
    assert row["status"] == "settled" and row["status_text"].startswith("Paid")


def approve(client: Any, cid: str, kind: str, record_id: str) -> None:
    """Approves a plan or a pack the way the screens do: by answering its approval question (C5 or C6)."""
    cp = next(c for c in client.get(f"/v1/cases/{cid}/checkpoints").json()
              if c["status"] == "open" and c["kind"] == {"plan": "plan_approval", "pack": "pack_approval"}[kind]
              and c["subject_ref"].split(":")[1] == record_id)
    r = client.post(f"/v1/cases/{cid}/checkpoints/{cp['id']}/answer", json={"answer": "approve"})
    assert r.status_code == 200, r.text


def claims_by_id(client: Any, cid: str) -> dict:
    return {c.id: c for c in client.portal.call(client.deps.cases.list, cid, "claims")}


def plans_by_claim(client: Any, cid: str) -> dict:
    return {p.claim_id: p for p in client.portal.call(client.deps.cases.list, cid, "plans")}


def test_an_interim_letter_is_chased_when_no_reply_comes(client: Any) -> None:
    """A claim no stored plan covers starts on the interim plan: its letter, once sent, starts a wait for a reply;
    with no reply by the day expected, a reminder is drafted by itself. The letter counts as telling the
    institution, not as the claim itself, so a researched plan later still has its own claim to make."""
    deps = client.deps
    deps.settings = deps.settings.model_copy(update={"claim_research": "off"})
    cid = client.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
    for text in ("My husband Vikram Rao died on 1 September 2026 in Chennai.",
                 "He had a car loan from Sundaram Finance and an account with Karur Vysya Bank."):
        client.post(f"/v1/cases/{cid}/turns", json={"text": text})
    cps = [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json() if c["code"] == "C1"]
    while not cps:
        client.post(f"/v1/cases/{cid}/turns", json={"text": "No will. Our son Kiran lives in Chennai."})
        cps = [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json() if c["code"] == "C1"]
    client.post(f"/v1/cases/{cid}/checkpoints/{cps[0]['id']}/answer", json={"answer": "yes"})
    settle(client)
    loan = next(c for c in claims_by_id(client, cid).values() if c.kind == "debt")
    plan = plans_by_claim(client, cid)[loan.id]
    assert plan.plan_key == "XX/interim_debt" and plan.unverified and loan.research.get("status") == "off"
    approve(client, cid, "plan", plan.id)
    settle(client)
    pack = next(p for p in client.portal.call(deps.cases.list, cid, "packs") if p.claim_id == loan.id)
    assert pack.plan_key == "XX/interim_debt" and pack.id.endswith(pack.id[-12:])
    approve(client, cid, "pack", pack.id)
    sent = date(2026, 10, 19)
    client.post(f"/v1/cases/{cid}/claims/{loan.id}/events", json={"event": "submitted", "on": sent.isoformat()})
    loan = claims_by_id(client, cid)[loan.id]
    assert loan.events.get("notified") == sent.isoformat() and loan.submitted_on is None
    assert any(c.clock_id == "reply_expected" and c.status == "running" for c in loan.clocks)
    deps.clock.day = sent + timedelta(days=31)
    client.post(f"/v1/cases/{cid}/today", json={})
    settle(client)
    chase = [x for x in client.portal.call(deps.cases.list, cid, "correspondence") if x.claim_id == loan.id]
    assert chase and chase[0].kind == "chase" and "reply" in chase[0].letter_text.lower()
    tasks = client.get(f"/v1/cases/{cid}/today").json()["tasks"]
    assert any(t["task"]["claim_id"] == loan.id for t in tasks)


def test_the_date_of_death_comes_from_the_death_certificate(client: Any) -> None:
    """A family that adds the death certificate before saying when the death was: the date is read from it, the
    person who died is named from it, and the statement rows after that date are marked."""
    cid = client.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
    doc = pymupdf.open()
    page = doc.new_page()
    for i, text in enumerate(["GOVERNMENT OF MAHARASHTRA", "Certificate of death",
                              "Name of the person who died: RAMESH KUMAR SHARMA", "Date of death: 03/08/2026",
                              "Place of death: Pune", "Registration number: D-2026-04411"]):
        page.insert_text((60, 80 + 22 * i), text, fontsize=11)
    up = client.post(f"/v1/cases/{cid}/papers", files={"files": ("death.pdf", doc.tobytes(), "application/pdf")},
                     data={"by": ""})
    assert up.status_code == 200
    settle(client)
    header = client.get(f"/v1/cases/{cid}").json()
    assert header["case"]["died_on"] == "2026-08-03"
    assert header["case"]["title"] == "RAMESH KUMAR SHARMA" and header["deceased"]["display_name"]


def test_a_long_statement_has_every_payee_screened(client: Any) -> None:
    """Hundreds of payees: the most telling are read closely, and every other one is screened quickly, so an
    insurer paid once a year among the shops is still read closely."""
    deps = client.deps
    cid = client.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
    rows = []
    bal = 500000.0
    for i in range(90):                                # ninety one-off payees, each paid once
        bal -= 100 + i
        shop = chr(65 + i // 26) + chr(65 + i % 26)    # SHOP AA, SHOP AB...: each a payee of its own
        rows.append((f"{(i % 28) + 1:02d}-0{(i // 28) + 1}-26", f"UPI/SHOP {shop} STORES/PUNE", f"{100 + i:,.2f}",
                     "", f"{bal:,.2f}"))
    bal -= 15000
    rows.append(("15-06-26", "NEFT TATA AIA LIFE INSURANCE PREMIUM", "15,000.00", "", f"{bal:,.2f}"))
    pdf = statement("KONKAN SAMPLE BANK", "RAMESH SHARMA", rows)
    client.post(f"/v1/cases/{cid}/papers", files={"files": ("long.pdf", pdf, "application/pdf")}, data={"by": ""})
    settle(client)
    groups = client.portal.call(deps.cases.list, cid, "groups")
    assert len(groups) > 60
    insurer = next(g for g in groups if "TATA AIA" in g.label)
    assert insurer.interpretation, "the insurer was read closely though it was paid only once"
    assert any("screened" in a.text for a in client.portal.call(deps.cases.list, cid, "activity"))


def test_the_person_who_started_the_case_signs_until_a_claimant_is_named(client: Any) -> None:
    """No one has been named claimant yet: the person who started the case signs the family's letters (until 19
    October 2026 they were signed by no one), the questions about a plan open that plan, and a letter drafted before
    a claim was settled is no longer offered for sending once it is."""
    deps = client.deps
    deps.settings = deps.settings.model_copy(update={"claim_research": "off"})
    cid = client.post("/v1/cases", json={"lang": "en", "starter_name": "Meena"}).json()["case"]["id"]
    for text in ("My husband Vikram Rao died on 1 September 2026 in Chennai.",
                 "He had a savings account with Karur Vysya Bank and a car loan from Sundaram Finance."):
        client.post(f"/v1/cases/{cid}/turns", json={"text": text})
    cps = [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json() if c["code"] == "C1"]
    while not cps:
        client.post(f"/v1/cases/{cid}/turns", json={"text": "No will. Our son Kiran lives in Chennai."})
        cps = [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json() if c["code"] == "C1"]
    client.post(f"/v1/cases/{cid}/checkpoints/{cps[0]['id']}/answer", json={"answer": "yes"})
    settle(client)
    from afterledger.modules.common import snapshot
    snap = client.portal.call(snapshot, deps, cid)
    assert snap.claimant() is not None and snap.claimant().display_name.startswith("Meena")
    client.post(f"/v1/cases/{cid}/today", json={})
    settle(client)
    tasks = client.get(f"/v1/cases/{cid}/today").json()["tasks"]
    asking = [t for t in tasks if t["task"]["title"].startswith("Answer ")]
    assert all(t["task"]["actions"] and t["task"]["actions"][0]["target"].startswith(("plan:", "plans:")) for t in asking)
    bank = next(c for c in claims_by_id(client, cid).values() if c.kind == "claim")
    letter = client.post(f"/v1/cases/{cid}/claims/{bank.id}/letters", json={"kind": "request"}).json()
    assert "Meena" in letter["letter_text"]
    tracker = client.get(f"/v1/cases/{cid}/tracker").json()
    signer = next(lv["signer"] for lv in tracker["letters"] if lv["letter"]["id"] == letter["id"])
    assert signer and signer["name"].startswith("Meena")
    client.post(f"/v1/cases/{cid}/claims/{bank.id}/events", json={"event": "submitted"})
    client.post(f"/v1/cases/{cid}/claims/{bank.id}/events", json={"event": "settled", "amount": "50,000"})
    tracker = client.get(f"/v1/cases/{cid}/tracker").json()
    assert all(lv["letter"]["id"] != letter["id"] for lv in tracker["letters"])
