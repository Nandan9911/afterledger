"""Every screen of every demo case through the HTTP API, as the web app calls it: each answers, the files it links
to are served, and the words the server writes come back in the page's language."""
from __future__ import annotations

import io
import re
import shutil
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest

from afterledger.seed.families import family_keys, load_family
from conftest import DemoState, api_client

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def client(demo_state: DemoState, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    root = tmp_path_factory.mktemp("screens") / "state"
    shutil.copytree(demo_state.root, root)
    yield from api_client(root)


def ok(c: Any, path: str, **kw: Any) -> Any:
    r = c.get(path, **kw)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:300]}"
    return r


@pytest.mark.parametrize("key", family_keys())
def test_every_screen_of_a_demo_case(client: Any, key: str) -> None:
    cid = f"demo-{key}"
    header = ok(client, f"/v1/cases/{cid}").json()
    assert header["mode"] == "scripted" and header["nav"]
    ok(client, f"/v1/cases/{cid}/conversation")

    today = ok(client, f"/v1/cases/{cid}/today").json()
    assert today["greeting"]
    assert len(today["tasks"]) == len((load_family(key).get("expected_today") or {}).get("tasks", []))

    papers = ok(client, f"/v1/cases/{cid}/papers").json()["papers"]
    assert papers and not any(p["paper"]["status"] == "reading" for p in papers)
    for row in papers:
        view = ok(client, f"/v1/cases/{cid}/papers/{row['paper']['id']}").json()
        image = ok(client, view["pages"][0]["image_url"])
        assert image.headers["content-type"].startswith("image/")

    money = ok(client, f"/v1/cases/{cid}/money").json()
    assert money["countries"]
    ok(client, f"/v1/cases/{cid}/leads")
    ok(client, f"/v1/cases/{cid}/people")

    plans = ok(client, f"/v1/cases/{cid}/plans").json()["rows"]
    for row in plans:
        if row["plan"]:
            view = ok(client, f"/v1/cases/{cid}/plans/{row['plan']['id']}").json()
            assert all(cite["quote"] and cite["source_url"] for cite in view["cites"].values())

    for row in ok(client, f"/v1/cases/{cid}/packs").json()["rows"]:
        pack = ok(client, f"/v1/cases/{cid}/packs/{row['pack']['id']}").json()
        for item in pack["items"]:
            if item["file_url"]:
                assert ok(client, item["file_url"]).headers["content-type"] == "application/pdf"
        archive = zipfile.ZipFile(io.BytesIO(ok(client, pack["download_url"]).content))
        assert archive.namelist() and archive.testzip() is None

    tracker = ok(client, f"/v1/cases/{cid}/tracker").json()
    for letter in tracker["letters"]:
        assert ok(client, f"/v1/cases/{cid}/letters/{letter['letter']['id']}/file").content.startswith(b"%PDF")
    assert ok(client, f"/v1/cases/{cid}/sources").json()["rows"]
    assert ok(client, f"/v1/cases/{cid}/activity", params={"limit": 5}).json()["items"]
    ok(client, f"/v1/cases/{cid}/checkpoints")


def test_the_late_claim_and_what_the_bank_owes(client: Any) -> None:
    """Verma: Narmada Bank is five days past the regulator's limit; compensation at Bank Rate + 4% a year."""
    want = load_family("verma")["expected_today"]["narmada_compensation"]
    rows = ok(client, "/v1/cases/demo-verma/plans").json()["rows"]
    late = [r for r in rows if r["late"]]
    assert len(late) == 1
    view = ok(client, f"/v1/cases/demo-verma/plans/{late[0]['plan']['id']}").json()
    comp = next(c for c in view["compensation"] if c["owed"])
    assert comp["per_day"]["text"] == f"₹{want['per_day']}"
    assert comp["owed"]["text"] == f"₹{want['owed']}"
    assert comp["base_rate_pct"] == want["base_rate_pct"]


def test_a_late_bank_claim_can_go_to_the_rbi_ombudsman(client: Any) -> None:
    """The tracker's next step for Narmada Bank's late claim is the RBI Ombudsman, with its 30 and 90 days quoted
    from the Integrated Ombudsman Scheme, 2026, and in Hindi from the reviewed wording."""
    tracker = ok(client, "/v1/cases/demo-verma/tracker").json()
    steps = [e for es in tracker["escalation"].values() for e in es]
    rbi = [e for e in steps if "RBI Ombudsman" in e["text"]]
    assert rbi and rbi[0]["verified"] and "in_rbi_ios_90_days" in rbi[0]["cites"]
    assert set(tracker["cites"]) >= set(rbi[0]["cites"])
    hindi = ok(client, "/v1/cases/demo-verma/tracker", headers={"accept-language": "hi-IN,hi;q=0.9"}).json()
    assert any("आरबीआई लोकपाल" in e["text"] for es in hindi["escalation"].values() for e in es)


SCRIPTS = {"hi": r"[\u0900-\u097F]", "ja": r"[\u3040-\u30FF\u4E00-\u9FFF]", "ml": r"[\u0D00-\u0D7F]"}


@pytest.mark.parametrize(("key", "lang"), [("verma", "hi"), ("sato", "ja"), ("nair", "ml"), ("morales", "es"),
                                           ("tanaka", "de")])
def test_each_family_reads_today_in_its_own_language(client: Any, key: str, lang: str) -> None:
    today = ok(client, f"/v1/cases/demo-{key}/today").json()
    assert today["greeting_lang"] == lang and today["greeting_local"] != today["greeting"]
    assert today["tasks"]
    for t in today["tasks"]:
        task = t["task"]
        assert task["lang_local"] == lang and task["title_local"] and task["title_local"] != task["title"], task
        if lang in SCRIPTS:
            assert re.search(SCRIPTS[lang], task["title_local"]), task["title_local"]


def test_the_server_writes_in_the_page_language(client: Any) -> None:
    """Deadlines and open questions are written by the server in English and translated for the page."""
    today = ok(client, "/v1/cases/demo-verma/today", headers={"Accept-Language": "hi"}).json()
    texts = [d["title"] for d in today["deadlines"]] + [u["text"] for u in today["uncertain"]]
    assert texts and all(re.search(SCRIPTS["hi"], x) for x in texts), texts
    english = ok(client, "/v1/cases/demo-verma/today").json()
    assert [d["title"] for d in english["deadlines"]] != [d["title"] for d in today["deadlines"]]


def test_demo_families_and_the_evaluation_report(client: Any) -> None:
    families = ok(client, "/v1/demo/families").json()
    assert [f["key"] for f in families] == family_keys() and all(f["seeded"] for f in families)
    report = ok(client, "/v1/eval").json()
    assert report["running"] is False
