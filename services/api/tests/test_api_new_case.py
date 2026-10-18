"""A case of the family's own, and the live challenge, through the HTTP API (offline): the story is heard one
question at a time, a paper is read, research ends in a plan and a letter, and the service says what it runs on."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from conftest import api_client


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Any]:
    yield from api_client(tmp_path)


def test_health_and_what_this_copy_runs_on(client: Any) -> None:
    assert client.get("/v1/health").json()["ok"] is True
    meta = client.get("/v1/meta").json()
    assert meta["mode"] == "offline" and meta["llm"] == "scripted" and meta["live_gateway"] is None
    assert meta["services"]["llm"] == "scripted" and meta["services"]["voice"] == "browser_speech"
    assert meta["services"]["store"] == "local" and meta["services"]["ocr"] == "pdf_text_and_sidecars"
    cal = meta["calibration"]            # the starting tables, or tables refitted by `afterledger calibrate --write`
    assert (cal["status"], cal["fitted_on"]) == ("default", None) or (cal["status"] == "fitted" and cal["fitted_on"])
    assert meta["languages"] == ["en", "hi", "ja", "es", "ml", "ar"]


def test_a_phone_on_the_same_wifi_may_call_a_local_service(client: Any) -> None:
    """A phone opens the laptop's web app by the laptop's private address; its pages may call the local service. A
    public site may not, and a deployed service takes only its own web app's pages."""
    from afterledger.app.main import lan_origins

    lan = "http://192.168.1.23:3000"
    assert client.get("/v1/health", headers={"Origin": lan}).headers.get("access-control-allow-origin") == lan
    assert "access-control-allow-origin" not in client.get("/v1/health", headers={"Origin": "https://example.com"}).headers
    assert "access-control-allow-origin" not in client.get("/v1/health", headers={"Origin": "http://8.8.8.8:3000"}).headers
    assert lan_origins(["https://afterledger-web-123456789012.asia-south1.run.app"]) is None


def test_demo_families_are_listed_before_they_are_prepared(client: Any) -> None:
    families = client.get("/v1/demo/families").json()
    assert len(families) == 6 and not any(f["seeded"] for f in families)
    assert client.get("/v1/cases/demo-verma").status_code == 404
    assert client.post("/v1/demo/nobody/reset").status_code == 404


def test_a_new_case_hears_the_story_and_reads_a_paper(client: Any) -> None:
    r = client.post("/v1/cases", json={"lang": "en"})
    assert r.status_code == 200
    cid, first = r.json()["case"]["id"], r.json()["reply"]["text"]
    assert first
    turn = client.post(f"/v1/cases/{cid}/turns", json={"text": "My mother, Margaret Ellis, died on 3 October 2026 "
                                                                 "in York."}).json()
    assert turn["heard"]["text"].startswith("My mother") and turn["reply"]["text"] != first
    known = {k["label"]: k["text"] for k in turn["conversation"]["known"]}
    assert any("Margaret Ellis" in v for v in known.values()), known

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "YORKSHIRE COUNTY BANK\nStatement for MARGARET ELLIS\nBalance 12,345.67",
                               fontsize=12)
    up = client.post(f"/v1/cases/{cid}/papers", files={"files": ("statement.pdf", doc.tobytes(), "application/pdf")},
                     data={"by": ""})
    assert up.status_code == 200 and up.json()[0]["title"]
    client.settle()
    papers = client.get(f"/v1/cases/{cid}/papers").json()
    assert not papers["reading"] and papers["papers"][0]["paper"]["status"] != "reading"
    assert client.get(f"/v1/cases/{cid}/today").status_code == 200

    files = client.deps.settings.state_dir / "blobs" / "cases" / cid
    assert any(files.rglob("*.pdf"))
    assert client.delete(f"/v1/cases/{cid}").status_code == 200
    assert client.get(f"/v1/cases/{cid}").status_code == 404
    assert not files.exists()                      # the papers go with the case


def test_the_conversation_never_asks_the_same_thing_twice(client: Any) -> None:
    cid = client.post("/v1/cases", json={"lang": "en", "starter_name": "Karim"}).json()["case"]["id"]
    asked = []
    for text in ["My mother died last month in Lyon.", "Marie Dubois", "On 12 September 2026, in Lyon, France.",
                 "She had an account at Crédit Rhône Bank.", "My brother Paul lives in Paris.", "No will."]:
        reply = client.post(f"/v1/cases/{cid}/turns", json={"text": text}).json()["reply"]
        if reply:
            asked.append(reply["text"])
    assert len(asked) == len(set(asked)), asked
    conversation = client.get(f"/v1/cases/{cid}/conversation").json()
    known = " ".join(k["text"] for k in conversation["known"])
    assert "Marie Dubois" in known and "Lyon" in known


def test_a_phone_photo_in_heic_becomes_a_page_a_browser_can_show(client: Any) -> None:
    """An iPhone's photos are HEIC, which no browser shows and no reader takes: the page becomes a JPEG (the original
    is kept). A browser on Windows often sends such a file with no type, so the name decides."""
    import io

    from PIL import Image

    cid = client.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(buf, format="HEIF")
    up = client.post(f"/v1/cases/{cid}/papers", data={"by": ""},
                     files={"files": ("IMG_0001.HEIC", buf.getvalue(), "application/octet-stream")})
    assert up.status_code == 200, up.text
    paper = up.json()[0]
    assert paper["origin"] == "photo" and paper["file"].endswith("IMG_0001.HEIC")
    page = paper["pages"][0]
    assert page["image"].endswith(".jpg") and (page["width"], page["height"]) == (640, 480)


def test_a_change_to_the_summary_is_heard_and_the_summary_offered_again(client: Any) -> None:
    """"Change something" is not a confirmation: the family's correction is heard like anything else they say, the
    summary is rebuilt from it and offered again, and the case moves on to the papers only on a yes."""
    cid = client.post("/v1/cases", json={"lang": "en", "starter_name": "Karim"}).json()["case"]["id"]
    for text in ["My mother died last month in Lyon.", "Marie Dubois", "On 12 September 2026, in Lyon, France.",
                 "She had an account at Crédit Rhône Bank.", "My brother Paul lives in Paris.", "No will."]:
        client.post(f"/v1/cases/{cid}/turns", json={"text": text})
    summary = client.get(f"/v1/cases/{cid}/conversation").json()["summary"]
    assert summary["status"] == "open" and not any("Claire" in line for line in summary["payload"]["lines"])

    note = "My sister Claire Dubois lives in Nice."
    r = client.post(f"/v1/cases/{cid}/checkpoints/{summary['id']}/answer", json={"answer": "change", "note": note})
    assert r.status_code == 200
    conversation = client.get(f"/v1/cases/{cid}/conversation").json()
    assert any(u["role"] == "family" and u["text"] == note for u in conversation["utterances"])
    again = conversation["summary"]
    assert again["id"] == summary["id"] and again["status"] == "open" and again["answer"] is None
    assert any("Claire" in line for line in again["payload"]["lines"]), again["payload"]["lines"]
    assert conversation["stage"] == "intake"

    client.post(f"/v1/cases/{cid}/checkpoints/{summary['id']}/answer", json={"answer": "yes"})
    conversation = client.get(f"/v1/cases/{cid}/conversation").json()
    assert conversation["summary"]["answer"] == "yes" and conversation["stage"] == "papers"


async def test_a_second_screen_joins_a_replay_instead_of_playing_it_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """A family's replay runs on the server whether or not anyone watches. Opening "Watch" again (a second screen,
    or the same page loaded again) follows the replay already playing rather than paying for the story twice, and
    the demo case cannot be reset under it."""
    import asyncio
    from types import SimpleNamespace

    from afterledger.app import demo

    deps, started, gate = SimpleNamespace(extras={}), [], asyncio.Event()

    async def replay(d: Any, key: str, *, delay: float) -> None:
        started.append(key)
        await gate.wait()
        for q in list(demo.listeners(d, key)):
            await q.put({"type": "done"})

    monkeypatch.setattr(demo, "watch", replay)
    first, second = asyncio.Queue(), asyncio.Queue()
    assert demo.follow(deps, "clarke", first, delay=0) is True
    await asyncio.sleep(0)
    assert demo.follow(deps, "clarke", second, delay=0) is False
    with pytest.raises(demo.Conflict):
        await demo.reset(deps, "clarke")
    gate.set()
    assert (await first.get())["type"] == "done" and (await second.get())["type"] == "done"
    await asyncio.sleep(0)
    assert started == ["clarke"] and not demo.watching(deps, "clarke")
    assert demo.follow(deps, "clarke", asyncio.Queue(), delay=0) is True     # once finished, it can be played again
    await asyncio.sleep(0.01)


def test_unknown_cases_and_records_are_404(client: Any) -> None:
    assert client.get("/v1/cases/case_missing/today").status_code == 404
    assert client.get("/v1/knowledge/assertions/no_such_rule").status_code == 404
    assert client.get("/v1/challenges/ch_missing").status_code == 404


def _events(client: Any, path: str) -> list[dict]:
    out = []
    with client.stream("GET", path) as s:
        for line in s.iter_lines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:]))
    return out


def test_the_live_challenge_researches_checks_and_saves_a_case(client: Any) -> None:
    presets = client.get("/v1/challenges/presets").json()
    assert presets and all(p["country_name"] for p in presets)
    run = client.post("/v1/challenges", json={"country": "FR", "lang": "fr", "holding_kind": "life_policy",
                                              "question": "Mon père est décédé à Lyon en août. Avait-il une "
                                                          "assurance-vie ?"}).json()
    events = _events(client, f"/v1/challenges/{run['id']}/events")
    stages = [e["stage"] for e in events]
    assert stages[-1] == "finished" and "citation_check" in stages and "adjudicator" in stages
    view = client.get(f"/v1/challenges/{run['id']}").json()
    assert view["status"] == "done" and view["gold"]["match"] is True
    assert [d["id"] for d in view["dropped"]] == ["fr_trap_agira_delay"]    # the misquote never reaches the plan
    assert view["plan"]["key"] == "FR/life_insurance_search" and view["letter"]["lang"] == "fr"
    saved = client.post(f"/v1/challenges/{run['id']}/case").json()
    case = client.get(f"/v1/cases/{saved['case_id']}").json()
    assert case["case"]["country_codes"] == ["FR"] and case["case"]["title"] != "New case"
    assert any(c["id"] == run["id"] for c in client.get("/v1/challenges").json())


def test_research_without_an_official_page_says_so(client: Any) -> None:
    run = client.post("/v1/challenges", json={"country": "EG", "lang": "ar", "holding_kind": "savings",
                                              "question": "توفي والدي في القاهرة. ما المستندات التي يطلبها البنك؟"}).json()
    _events(client, f"/v1/challenges/{run['id']}/events")
    view = client.get(f"/v1/challenges/{run['id']}").json()
    assert view["status"] == "done" and view["plan"]["unverified"] is True and view["plan"]["level"] == "draft"


def test_knowledge_is_served_with_its_quote_checked(client: Any) -> None:
    a = client.get("/v1/knowledge/assertions/in_rbi_319_15_days").json()
    assert a["quote_found"] is True and a["cite"]["quote"] and a["cite"]["source_url"].startswith("https://")
    page = client.get("/v1/knowledge/pages", params={"url": a["cite"]["source_url"]})
    assert page.status_code == 200


def test_the_evaluation_page_before_any_run(client: Any) -> None:
    report = client.get("/v1/eval").json()
    assert report["running"] is False and "No evaluation has run yet" in report["note"]
