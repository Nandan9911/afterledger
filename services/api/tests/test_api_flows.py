"""What a family does through the API, on a copy of the Vermas' case: answer a lead and a plan question, approve a
pack, approve and send a letter, record the bank's reply, share a claim with the bank's desk, ask a question,
add a family member; and a demo case cannot be deleted."""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from typing import Any

import pytest

from conftest import DemoState, api_client

pytestmark = pytest.mark.slow

CID = "demo-verma"


@pytest.fixture(scope="module")
def client(demo_state: DemoState, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    root = tmp_path_factory.mktemp("flows") / "state"
    shutil.copytree(demo_state.root, root)
    yield from api_client(root)


@pytest.fixture(scope="module")
def amit(client: Any) -> str:
    return client.get(f"/v1/cases/{CID}").json()["viewer"]["id"]


def checkpoints(c: Any) -> list[dict]:
    return c.get(f"/v1/cases/{CID}/checkpoints").json()


def test_a_lead_is_answered_once(client: Any, amit: str) -> None:
    locker = next(x for x in checkpoints(client) if x["code"] == "C4" and "locker" in x["title"].lower())
    r = client.post(f"/v1/cases/{CID}/checkpoints/{locker['id']}/answer", json={"answer": "know", "by": amit})
    assert r.status_code == 200 and r.json()["status"] == "answered" and r.json()["answer"] == "know"
    again = client.post(f"/v1/cases/{CID}/checkpoints/{locker['id']}/answer", json={"answer": "know", "by": amit})
    assert again.status_code == 409
    client.settle()
    assert locker["id"] not in {x["id"] for x in checkpoints(client)}


def test_approving_a_pack_moves_the_claim_on(client: Any, amit: str) -> None:
    pack = next(x for x in checkpoints(client) if x["code"] == "C6")
    r = client.post(f"/v1/cases/{CID}/checkpoints/{pack['id']}/answer", json={"answer": "approve", "by": amit})
    assert r.status_code == 200
    client.settle()
    rows = client.get(f"/v1/cases/{CID}/packs").json()["rows"]
    assert any(row["pack"]["status"] == "approved" for row in rows), [row["pack"]["status"] for row in rows]


def test_a_letter_is_approved_sent_and_its_pdf_served(client: Any, amit: str) -> None:
    letter = client.get(f"/v1/cases/{CID}/tracker").json()["letters"][0]["letter"]
    assert client.post(f"/v1/cases/{CID}/letters/{letter['id']}/approve", json={"by": amit}).status_code == 200
    sent = client.post(f"/v1/cases/{CID}/letters/{letter['id']}/sent", json={"by": amit, "channel": "email"})
    assert sent.status_code == 200
    pdf = client.get(f"/v1/cases/{CID}/letters/{letter['id']}/file")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    client.settle()
    tracker = client.get(f"/v1/cases/{CID}/tracker").json()
    assert next(x for x in tracker["letters"] if x["letter"]["id"] == letter["id"])["letter"]["status"] == "sent"


def test_the_banks_reply_is_read_against_the_plan(client: Any, amit: str) -> None:
    """A reply that says the claim was paid is not recorded by itself: the family is asked first, and a no records
    nothing."""
    rows = client.get(f"/v1/cases/{CID}/tracker").json()["rows"]
    narmada = next(r for r in rows if "Narmada" in r["title"])
    r = client.post(f"/v1/cases/{CID}/claims/{narmada['claim_id']}/replies", json={
        "title": "Narmada Bank: claim settled",
        "text": "We have settled the claim of Rs 6,41,870 plus compensation of Rs 835.31 for the delay, credited "
                "on 14 October 2026."})
    assert r.status_code == 200 and r.json()["summary"]
    tracker = client.get(f"/v1/cases/{CID}/tracker").json()
    asked = [c for c in tracker["confirm"] if c["payload"]["claim_id"] == narmada["claim_id"]]
    assert [(c["payload"]["event"], c["payload"]["amount"], c["payload"]["source"]) for c in asked] == \
        [("settled", "641870.00", "reply")]
    assert next(x for x in tracker["rows"] if x["claim_id"] == narmada["claim_id"])["status"] != "settled"
    assert client.get(f"/v1/cases/{CID}").json()["nav"]["tracker_confirm"] == 1
    no = client.post(f"/v1/cases/{CID}/checkpoints/{asked[0]['id']}/answer", json={"answer": "no", "by": amit})
    assert no.status_code == 200 and no.json()["answer"] == "no"
    client.settle()
    tracker = client.get(f"/v1/cases/{CID}/tracker").json()
    assert not tracker["confirm"]
    assert next(x for x in tracker["rows"] if x["claim_id"] == narmada["claim_id"])["status"] != "settled"


def test_a_claim_is_shared_with_the_banks_desk(client: Any, amit: str) -> None:
    claims = client.get(f"/v1/cases/{CID}/tracker").json()["rows"]
    claim_id = next(r for r in claims if "Narmada" in r["title"])["claim_id"]
    share = client.post(f"/v1/cases/{CID}/claims/{claim_id}/share", json={"days": 31, "by": amit})
    assert share.status_code == 200
    desk = client.get(f"/v1/desk/{share.json()['token']}")
    assert desk.status_code == 200
    view = desk.json()
    assert view["deceased"] and view["requirements"] and view["claim"]["id"] == claim_id
    # a clock says what started it: "every document confirmed" only once the bank has confirmed it
    clocks = [r for r in view["requirements"]
              if r["in_pack"].startswith(("Every document confirmed", "Claim received", "Counted from"))]
    assert clocks and all(view["claim"]["complete_set_on"] or not r["in_pack"].startswith("Every document")
                          for r in clocks)
    assert client.get("/v1/desk/not-a-token").status_code == 404
    # the bank records its payment on the desk: the family is asked, and the claim is settled when they say yes
    token = share.json()["token"]
    assert client.post(f"/v1/desk/{token}/settle", json={"amount": "about six lakh"}).status_code == 422
    assert client.post(f"/v1/desk/{token}/settle", json={"amount": "0"}).status_code == 422
    first = client.post(f"/v1/desk/{token}/settle", json={"amount": "6,14,870"})
    assert first.status_code == 200 and first.json()["awaiting_family"]["text"] == "₹6,14,870"
    # the family says it has not come: the desk is told, and can record the payment again
    asked = client.get(f"/v1/cases/{CID}/tracker").json()["confirm"]
    assert [c["payload"]["amount_text"] for c in asked] == ["₹6,14,870"]
    no = client.post(f"/v1/cases/{CID}/checkpoints/{asked[0]['id']}/answer", json={"answer": "no", "by": amit})
    assert no.status_code == 200
    client.settle()
    told = client.get(f"/v1/desk/{token}").json()
    assert told["awaiting_family"] is None and any("₹6,14,870" in n and "not reached" in n for n in told["notes"])
    # the same payment twice is one; a mistyped amount is corrected on the desk, and the family is asked once,
    # about the new one (the entry it replaces says so)
    def entries() -> list[str]:
        return [x["letter"]["summary"] for x in client.get(f"/v1/cases/{CID}/tracker").json()["received"]
                if x["letter"]["title"].endswith("recorded a payment on its desk")]
    client.post(f"/v1/desk/{token}/settle", json={"amount": "6,14,870"})
    assert client.post(f"/v1/desk/{token}/settle", json={"amount": "6,14,870"}).status_code == 200
    assert len(entries()) == 2
    paid = client.post(f"/v1/desk/{token}/settle", json={"amount": "6,41,870"})
    assert paid.status_code == 200
    assert paid.json()["awaiting_family"]["text"] == "₹6,41,870" and paid.json()["claim"]["status"] != "settled"
    assert not any("not reached" in n for n in paid.json()["notes"])
    asked = [c for c in client.get(f"/v1/cases/{CID}/tracker").json()["confirm"] if c["payload"]["source"] == "desk"]
    assert len(asked) == 1 and asked[0]["payload"]["claim_id"] == claim_id
    assert asked[0]["payload"]["amount_text"] == "₹6,41,870"
    assert len(entries()) == 3 and sum("It corrected this to ₹6,41,870." in e for e in entries()) == 1
    yes = client.post(f"/v1/cases/{CID}/checkpoints/{asked[0]['id']}/answer", json={"answer": "yes", "by": amit})
    assert yes.status_code == 200
    client.settle()
    after = client.get(f"/v1/desk/{token}").json()
    assert after["claim"]["status"] == "settled" and after["claim"]["settled_amount"] in ("641870", "641870.00")
    assert after["awaiting_family"] is None
    assert client.post(f"/v1/desk/{token}/settle", json={"amount": "100"}).status_code == 409


def test_a_question_is_answered_from_the_case(client: Any, amit: str) -> None:
    r = client.post(f"/v1/cases/{CID}/ask", json={"question": "When will Narmada Bank pay?", "person_id": amit})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"]["text"] and body["sources"]


def test_a_family_member_is_added_edited_and_invited(client: Any, amit: str) -> None:
    r = client.post(f"/v1/cases/{CID}/people", json={"display_name": "Priya Verma", "relationship": "daughter-in-law",
                                                     "by": amit})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.patch(f"/v1/cases/{CID}/people/{pid}", json={"city": "Pune", "by": amit}).status_code == 200
    assert client.post(f"/v1/cases/{CID}/people/{pid}/invite", json={"by": amit}).status_code == 200
    people = client.get(f"/v1/cases/{CID}/people").json()
    assert "Priya Verma" in str(people)


def test_a_demo_case_cannot_be_deleted(client: Any) -> None:
    assert client.delete(f"/v1/cases/{CID}").status_code == 409


def test_the_activity_feed_says_what_happened(client: Any) -> None:
    items = client.get(f"/v1/cases/{CID}/activity", params={"limit": 50}).json()["items"]
    assert items and all(i["text"] for i in items)
