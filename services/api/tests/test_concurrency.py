"""A family's action never waits for the model work behind it, and background work never writes over what the family
did meanwhile (concurrency.py). Until 19 October 2026 a background step held the case while it waited on a model: on
the live site an approval made while papers were being prepared waited up to two minutes."""
from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Awaitable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from conftest import api_client

SLOW = 1.2                               # seconds each slowed model call takes in these tests


# ---- the merge ----------------------------------------------------------------------------------------------

def test_a_merge_keeps_both_sides_and_the_newer_value_where_both_changed() -> None:
    from afterledger.concurrency import merge3
    base = {"a": 1, "b": 1, "c": 1, "d": 1}
    mine = {"a": 2, "b": 1, "c": 3, "d": 1, "e": 9}      # the job changed a and c, and added e
    theirs = {"a": 1, "b": 5, "c": 4}                     # the family changed b and c, and dropped d
    out, kept = merge3(base, mine, theirs)
    assert out == {"a": 2, "b": 5, "c": 4, "e": 9} and kept == ["c"]


# ---- the store's rules, on their own -----------------------------------------------------------------------------

def _family(work: Awaitable[Any]) -> Awaitable[Any]:
    """A family's action, as a request runs it: in a context of its own, not inside the job's."""
    return asyncio.get_running_loop().create_task(work, context=contextvars.Context())


def _case_and_claim(deps: Any, case_id: str = "case_cc") -> Any:
    from afterledger.domain.case import CaseFile, Claim

    async def go() -> None:
        await deps.cases.put_case(CaseFile(id=case_id, title="Test"))
        await deps.cases.put(case_id, "claims", Claim(id="claim_1", case_id=case_id, institution_id="in_x",
                                                      title="A claim", status="planning"))
    return go()


def test_a_jobs_write_keeps_what_the_family_changed_while_it_worked(deps: Any) -> None:
    from afterledger.concurrency import action, job

    async def go() -> Any:
        await _case_and_claim(deps)

        async def family() -> None:
            async with action(deps, "case_cc"):
                c = await deps.cases.get("case_cc", "claims", "claim_1")
                c.status, c.events = "submitted", {"submitted": "2026-10-19"}
                await deps.cases.put("case_cc", "claims", c)

        async with job(deps, "case_cc"):
            c = await deps.cases.get("case_cc", "claims", "claim_1")       # read before the family's action
            await _family(family())                                        # (the job waits on a model meanwhile)
            c.pack_id, c.status = "pack_1", "ready"                        # its own change, and one that clashes
            await deps.cases.put("case_cc", "claims", c)
        return await deps.cases.get("case_cc", "claims", "claim_1")

    c = asyncio.run(go())
    assert c.pack_id == "pack_1"                                  # the job's change is there
    assert c.status == "submitted" and c.events == {"submitted": "2026-10-19"}   # and the family's stands


def test_a_record_the_job_rebuilds_from_scratch_keeps_an_approval_given_meanwhile(deps: Any) -> None:
    """Plans, packs and tasks are rebuilt as new records with the same id: an approval given while the plan was being
    worked out is not lost."""
    from afterledger.concurrency import action, job
    from afterledger.domain.case import CasePlan

    async def go() -> Any:
        await _case_and_claim(deps)
        await deps.cases.put("case_cc", "plans", CasePlan(id="plan_1", case_id="case_cc", plan_key="IN/x", title="T",
                                                          jurisdiction="IN", summary="old"))
        async def approve() -> None:
            async with action(deps, "case_cc"):
                p = await deps.cases.get("case_cc", "plans", "plan_1")
                p.approved_by, p.approved_on = "p_1", date(2026, 10, 19)
                await deps.cases.put("case_cc", "plans", p)

        async with job(deps, "case_cc"):
            await deps.cases.list("case_cc", "plans")                     # the snapshot the planning starts from
            await _family(approve())                                      # the family approves meanwhile
            fresh = CasePlan(id="plan_1", case_id="case_cc", plan_key="IN/x", title="T", jurisdiction="IN",
                             summary="new")                                # rebuilt, knowing nothing of the approval
            await deps.cases.put("case_cc", "plans", fresh)
        return await deps.cases.get("case_cc", "plans", "plan_1")

    p = asyncio.run(go())
    assert p.summary == "new" and p.approved_by == "p_1"


def test_a_record_removed_meanwhile_is_not_written_back_nor_one_changed_meanwhile_removed(deps: Any) -> None:
    from afterledger.concurrency import action, job
    from afterledger.domain.case import Claim

    async def go() -> tuple[Any, Any]:
        await _case_and_claim(deps)
        await deps.cases.put("case_cc", "claims", Claim(id="claim_2", case_id="case_cc", institution_id="in_y",
                                                        title="Another"))
        async def family() -> None:
            async with action(deps, "case_cc"):
                await deps.cases.delete("case_cc", "claims", "claim_1")           # the family removes one
                two = await deps.cases.get("case_cc", "claims", "claim_2")
                two.title = "Renamed by the family"
                await deps.cases.put("case_cc", "claims", two)                    # and changes the other

        async with job(deps, "case_cc"):
            one = await deps.cases.get("case_cc", "claims", "claim_1")
            await deps.cases.list("case_cc", "claims")
            await _family(family())
            one.title = "Changed by the job"
            await deps.cases.put("case_cc", "claims", one)                        # not brought back
            await deps.cases.delete("case_cc", "claims", "claim_2")               # not removed
        return (await deps.cases.get("case_cc", "claims", "claim_1"),
                await deps.cases.get("case_cc", "claims", "claim_2"))

    one, two = asyncio.run(go())
    assert one is None and two is not None and two.title == "Renamed by the family"


def test_work_begun_before_a_case_was_deleted_writes_nothing_more_to_it(deps: Any) -> None:
    from afterledger.concurrency import job
    from afterledger.domain.case import CaseFile, Claim

    async def go() -> tuple[Any, Any, Any]:
        await _case_and_claim(deps)
        async with job(deps, "case_cc"):
            await deps.cases.get("case_cc", "claims", "claim_1")
            await deps.cases.delete_case("case_cc")                              # someone deletes the case
        async with job(deps, "case_cc"):
            pass
        stale = job(deps, "case_cc", exclusive=False)
        # a job that began before the deletion (its start is earlier than the deletion's number)
        async with stale as w:
            w.start = 0
            await deps.cases.put("case_cc", "claims", Claim(id="claim_9", case_id="case_cc", institution_id="i",
                                                            title="Late"))
            await deps.activity("case_cc", "test", "a late line")
        late = (await deps.cases.raw_records("case_cc", "claims")).get("claim_9")
        # a sample reset makes it again: allowed, for a writer that begins after the deletion
        await deps.cases.put_case(CaseFile(id="case_cc", title="Again"))
        return late, await deps.cases.get_case("case_cc"), await deps.cases.list("case_cc", "activity")

    late, again, lines = asyncio.run(go())
    assert late is None and lines == [] and again is not None and again.title == "Again"


def test_actions_and_job_blocks_inside_each_other_do_not_wait_on_themselves(deps: Any) -> None:
    from afterledger.concurrency import action, job
    from afterledger.modules.common import case_lock

    async def go() -> list[str]:
        await _case_and_claim(deps)
        async with case_lock(deps, "case_cc"), case_lock(deps, "case_cc"):        # an action calling another
            c = await deps.cases.get("case_cc", "claims", "claim_1")
            c.title = "one"
            await deps.cases.put("case_cc", "claims", c)
        async with job(deps, "case_cc"), action(deps, "case_cc"):                 # a block of a job's writes
            c = await deps.cases.get("case_cc", "claims", "claim_1")
            c.title = "two"
            await deps.cases.put("case_cc", "claims", c)
            await deps.activity("case_cc", "test", "inside")
        return [c.title for c in await deps.cases.list("case_cc", "claims")]

    assert asyncio.run(asyncio.wait_for(go(), timeout=10)) == ["two"]


def test_the_header_says_a_job_is_under_way_from_when_it_is_queued(deps: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A job starts a moment after the action that queued it: the case says so meanwhile, the first step that
    applies wins, and a job queued long ago that never started (a task lost on its way) stops showing."""
    from afterledger import concurrency as cc

    cc.working(deps, "c1", "w1", "planning", queued=True)
    assert cc.work_now(deps, "c1")["step"] == "planning"
    cc.working(deps, "c1", "w2", "reading")
    assert cc.work_now(deps, "c1")["step"] == "reading"
    cc.working(deps, "c1", "w2", None)
    real = time.monotonic
    monkeypatch.setattr(cc.time, "monotonic", lambda: real() + cc.QUEUED_FOR + 1)
    assert cc.work_now(deps, "c1") is None
    cc.working(deps, "c1", "w3", "preparing")                     # one that started is shown however long it takes
    assert cc.work_now(deps, "c1")["step"] == "preparing"


# ---- through the API, with slow models ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path: Path) -> Iterator[Any]:
    from afterledger.adapters.local.stores import FixedClock
    for c in api_client(tmp_path):
        c.deps.clock = FixedClock(date(2026, 10, 19))
        yield c


def _settle(client: Any) -> None:
    async def wait() -> None:
        for _ in range(40):
            await client.deps.tasks.drain()
            running = [t for t in client.deps.extras.get("rules_research_tasks", ()) if not t.done()]
            if not running:
                await client.deps.tasks.drain()
                return
            await asyncio.gather(*running, return_exceptions=True)
    client.portal.call(wait)


def _slow_models(monkeypatch: pytest.MonkeyPatch, names: set[str]) -> list[str]:
    """The named agents take SLOW seconds, as live models do."""
    import afterledger.agents.runner as runner
    original = runner.run_workflow
    calls: list[str] = []

    async def slow(deps: Any, wf: Any, *a: Any, **k: Any) -> Any:
        name = getattr(wf, "name", "").removeprefix("call_")
        calls.append(name)
        if name in names:
            await asyncio.sleep(SLOW)
        return await original(deps, wf, *a, **k)
    monkeypatch.setattr(runner, "run_workflow", slow)
    return calls


def _real_case(client: Any) -> str:
    cid = client.post("/v1/cases", json={"lang": "en", "starter_name": "Arjun Sharma"}).json()["case"]["id"]
    for text in ("My father Ramesh Sharma died on 3 August 2026 in Pune. I am his son Arjun Sharma.",
                 "He had an account with State Bank of India, an LIC policy and a home loan from Bajaj Finance.",
                 "No, he did not leave a will. My mother Sunita Sharma lives in Pune."):
        assert client.post(f"/v1/cases/{cid}/turns", json={"text": text}).status_code == 200
    summary = [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json() if c["code"] == "C1"]
    if not summary:
        client.post(f"/v1/cases/{cid}/turns", json={"text": "That is all we know for now."})
        summary = [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json() if c["code"] == "C1"]
    assert client.post(f"/v1/cases/{cid}/checkpoints/{summary[0]['id']}/answer",
                       json={"answer": "yes"}).status_code == 200
    _settle(client)
    return cid


def _open(client: Any, cid: str, kind: str) -> list[dict]:
    return [c for c in client.get(f"/v1/cases/{cid}/checkpoints").json()
            if c["status"] == "open" and c["kind"] == kind]


def _answer_every_question(client: Any, cid: str) -> None:
    for _ in range(30):
        qs = _open(client, cid, "plan_question")
        if not qs:
            return
        q = qs[0]
        r = client.post(f"/v1/cases/{cid}/checkpoints/{q['id']}/answer", json={"answer": q["options"][0]["value"]})
        assert r.status_code in (200, 409), r.text
        _settle(client)


def _wait_for(client: Any, test: Any, seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return
        time.sleep(0.05)
    raise AssertionError("background work did not start")


def test_an_approval_never_waits_for_papers_being_prepared(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = _real_case(client)
    _answer_every_question(client, cid)
    approvals = _open(client, cid, "plan_approval")
    assert len(approvals) >= 2, approvals
    _slow_models(monkeypatch, {"letter_writer", "escalation_writer", "form_scout", "form_understander",
                               "value_composer", "visual_critic", "supervisor", "explainer"})
    first, second = approvals[0], approvals[1]
    t0 = time.monotonic()
    assert client.post(f"/v1/cases/{cid}/checkpoints/{first['id']}/answer",
                       json={"answer": "approve"}).status_code == 200
    took_first = time.monotonic() - t0
    # the first plan's papers are being prepared now (slow models): the header says so
    _wait_for(client, lambda: client.get(f"/v1/cases/{cid}").json()["working"] == "preparing")
    t0 = time.monotonic()
    assert client.post(f"/v1/cases/{cid}/checkpoints/{second['id']}/answer",
                       json={"answer": "approve"}).status_code == 200
    took_second = time.monotonic() - t0
    assert took_first < SLOW and took_second < SLOW, (took_first, took_second)
    _settle(client)
    plans = {p.id: p for p in client.portal.call(client.deps.cases.list, cid, "plans")}
    for cp in (first, second):
        assert plans[cp["subject_ref"].split(":")[1]].approved_by is not None or plans[
            cp["subject_ref"].split(":")[1]].approved_on is not None
    packs = {p.claim_id for p in client.portal.call(client.deps.cases.list, cid, "packs")}
    assert {first["payload"]["claim_id"], second["payload"]["claim_id"]} <= packs
    assert client.get(f"/v1/cases/{cid}").json()["working"] is None


def test_an_answer_returns_at_once_and_the_plan_follows(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = _real_case(client)
    qs = _open(client, cid, "plan_question")
    assert qs, "a plan asks something"
    q = qs[0]
    _slow_models(monkeypatch, {"explainer", "supervisor"})
    t0 = time.monotonic()
    r = client.post(f"/v1/cases/{cid}/checkpoints/{q['id']}/answer", json={"answer": q["options"][0]["value"]})
    took = time.monotonic() - t0
    assert r.status_code == 200 and r.json()["status"] == "answered"
    assert took < SLOW, took
    plan_id = q["subject_ref"].split(":")[1]
    plan = client.portal.call(client.deps.cases.get, cid, "plans", plan_id)
    assert plan.facts.get(q["payload"]["variable"]) == q["options"][0]["value"]     # kept at once, on the plan
    # the screens refetch straight after the answer: the header already says the plans are being worked out
    assert client.get(f"/v1/cases/{cid}").json()["working"] == "planning"
    _settle(client)
    assert client.get(f"/v1/cases/{cid}").json()["working"] is None
    after = client.portal.call(client.deps.cases.get, cid, "plans", plan_id)
    assert q["payload"]["variable"] not in [v for v in after.facts if v.startswith("__") is False and
                                            after.facts[v] in (None, "unknown")]


def test_an_approval_given_while_the_plans_are_worked_out_again_stands(client: Any,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """A person added makes every plan be explained again (who signs changes); the family approves a plan meanwhile.
    The plan written back afterwards keeps the approval."""
    cid = _real_case(client)
    _answer_every_question(client, cid)
    approvals = _open(client, cid, "plan_approval")
    assert approvals
    target = approvals[0]
    plan_id = target["subject_ref"].split(":")[1]
    calls = _slow_models(monkeypatch, {"explainer"})
    r = client.post(f"/v1/cases/{cid}/people", json={"display_name": "Kiran Sharma", "relationship": "son",
                                                     "roles": ["heir"]})
    assert r.status_code == 200, r.text
    _wait_for(client, lambda: "explainer" in calls)          # the plans are being explained again, slowly
    t0 = time.monotonic()
    assert client.post(f"/v1/cases/{cid}/checkpoints/{target['id']}/answer",
                       json={"answer": "approve"}).status_code == 200
    assert time.monotonic() - t0 < SLOW
    _settle(client)
    plan = client.portal.call(client.deps.cases.get, cid, "plans", plan_id)
    assert plan.approved_on is not None, "the approval given while the plan was rewritten stands"
    assert not [c for c in _open(client, cid, "plan_approval") if c["subject_ref"] == target["subject_ref"]]


def _count_today(monkeypatch: pytest.MonkeyPatch, followup: Any) -> list[str]:
    original = followup.plan_today
    runs: list[str] = []

    async def counted(deps: Any, case_id: str, viewer_id: str | None = None) -> Any:
        runs.append(case_id)
        return await original(deps, case_id, viewer_id)
    monkeypatch.setattr(followup, "plan_today", counted)
    return runs


def test_today_is_planned_once_for_a_burst_of_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from afterledger.adapters.local.stores import FixedClock
    from afterledger.modules import followup
    for client in api_client(tmp_path, today_debounce_s=1):
        client.deps.clock = FixedClock(date(2026, 10, 19))
        cid = _real_case(client)
        claims = [c for c in client.portal.call(client.deps.cases.list, cid, "claims") if c.plan_id]
        assert len(claims) >= 2
        runs = _count_today(monkeypatch, followup)
        for c in claims[:2]:                                  # two letters marked sent, a moment apart
            r = client.post(f"/v1/cases/{cid}/claims/{c.id}/events", json={"event": "submitted"})
            assert r.status_code == 200, r.text
        r = client.post(f"/v1/cases/{cid}/today", json={})     # and the viewer chosen again
        assert r.status_code == 200
        _settle(client)
        assert len(runs) == 1, runs


def test_a_case_deleted_while_its_papers_are_worked_out_stays_deleted(client: Any,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    cid = _real_case(client)
    calls = _slow_models(monkeypatch, {"explainer", "supervisor"})
    r = client.post(f"/v1/cases/{cid}/people", json={"display_name": "Kiran Sharma", "relationship": "son"})
    assert r.status_code == 200
    _wait_for(client, lambda: "explainer" in calls)
    assert client.delete(f"/v1/cases/{cid}").status_code == 200
    _settle(client)
    assert client.portal.call(client.deps.cases.get_case, cid) is None
    assert not (client.deps.settings.state_dir / "cases" / cid).exists()


def test_no_model_is_asked_while_a_familys_action_holds_the_case(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every model call of a whole case, from the first words to the money received, is made without the case held:
    the conversation's turns, a correction to the summary, the answers, approvals, a person added, a lead marked,
    payments and letters. (Until 19 October 2026 a conversation turn held the case for its five model calls.)"""
    import afterledger.agents.runner as runner
    from afterledger.concurrency import WRITER
    from afterledger.modules import discovery

    original = runner.run_workflow
    held: list[str] = []

    async def watched(deps: Any, wf: Any, *a: Any, **k: Any) -> Any:
        w = WRITER.get()
        if w is not None and w.holding:
            held.append(getattr(wf, "name", "?"))
        return await original(deps, wf, *a, **k)
    monkeypatch.setattr(runner, "run_workflow", watched)
    monkeypatch.setattr(discovery, "run_workflow", watched)

    cid = client.post("/v1/cases", json={"lang": "en", "starter_name": "Arjun Sharma"}).json()["case"]["id"]
    for text in ("My father Ramesh Sharma died on 3 August 2026 in Pune. I am his son Arjun Sharma.",
                 "He had an account with State Bank of India, an LIC policy and a home loan from Bajaj Finance."):
        assert client.post(f"/v1/cases/{cid}/turns", json={"text": text}).status_code == 200
    for _ in range(3):
        summary = [c for c in _open(client, cid, "summary")]
        if summary:
            break
        client.post(f"/v1/cases/{cid}/turns", json={"text": "No, he did not leave a will. That is all we know."})
    assert summary, "the summary is offered"
    r = client.post(f"/v1/cases/{cid}/checkpoints/{summary[0]['id']}/answer",
                    json={"answer": "change", "note": "My mother Sunita Sharma lives in Pune too."})
    assert r.status_code == 200, r.text
    for _ in range(3):
        again = _open(client, cid, "summary")
        if again:
            break
        client.post(f"/v1/cases/{cid}/turns", json={"text": "That is all."})
    assert client.post(f"/v1/cases/{cid}/checkpoints/{again[0]['id']}/answer", json={"answer": "yes"}).status_code == 200
    _settle(client)
    _answer_every_question(client, cid)
    for cp in _open(client, cid, "plan_approval"):
        assert client.post(f"/v1/cases/{cid}/checkpoints/{cp['id']}/answer", json={"answer": "approve"}).status_code == 200
    _settle(client)
    assert client.post(f"/v1/cases/{cid}/people", json={"display_name": "Kiran Sharma", "relationship": "son",
                                                        "roles": ["heir"]}).status_code == 200
    leads = client.get(f"/v1/cases/{cid}/leads").json()
    for lead in leads["open"][:2]:
        assert client.post(f"/v1/cases/{cid}/leads/{lead['lead']['id']}/mark", json={"mark": "check"}).status_code == 200
    for item in leads["coverage"][:1]:
        client.post(f"/v1/cases/{cid}/coverage/{item['id']}/answer", json={"answer": "no"})
    claims = client.portal.call(client.deps.cases.list, cid, "claims")
    claim = next(c for c in claims if c.plan_id)
    for event in ({"event": "submitted"}, {"event": "acknowledged"}, {"event": "settled", "amount": "50,000"}):
        assert client.post(f"/v1/cases/{cid}/claims/{claim.id}/events", json=event).status_code == 200, event
    client.post(f"/v1/cases/{cid}/claims/{claim.id}/letters", json={"kind": "request"})
    _settle(client)
    assert held == []
