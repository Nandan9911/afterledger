"""The Google Cloud adapters, against fake clients: no network and no credentials.

Each fake implements only the calls the adapter makes, with the same shapes as the real client library, and
the protocol-buffer types (Document AI, check grounding, Cloud Tasks, Gemini) are the libraries' own.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from afterledger.adapters.local.services import GlossaryTranslator, LexicalGrounding
from afterledger.config import REPO_ROOT
from afterledger.domain.case import Activity, CaseFile
from afterledger.ports import PageSnapshot

KNOWLEDGE = REPO_ROOT / "data" / "knowledge"


# ---------------------------------------------------------------------------------------------
# Firestore
# ---------------------------------------------------------------------------------------------

class _Snap:
    def __init__(self, doc_id: str, data: dict | None):
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return dict(self._data) if self._data is not None else None


class _Doc:
    def __init__(self, db: FakeFirestore, path: str):
        self.db, self.path = db, path

    @property
    def id(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    async def set(self, data: dict) -> None:
        self.db.docs[self.path] = dict(data)
        self.db.writes += 1

    async def get(self) -> _Snap:
        self.db.reads += 1
        return _Snap(self.id, self.db.docs.get(self.path))

    async def delete(self) -> None:
        self.db.docs.pop(self.path, None)

    def collection(self, name: str) -> _Col:
        return _Col(self.db, f"{self.path}/{name}")


class _Col:
    def __init__(self, db: FakeFirestore, path: str):
        self.db, self.path = db, path

    def document(self, doc_id: str) -> _Doc:
        assert "/" not in doc_id, "a Firestore document id cannot contain '/'"
        return _Doc(self.db, f"{self.path}/{doc_id}")

    async def stream(self):
        self.db.reads += 1
        prefix = self.path + "/"
        for p, d in sorted(self.db.docs.items()):
            rest = p[len(prefix):]
            if p.startswith(prefix) and "/" not in rest:
                yield _Snap(rest, d)


class _Batch:
    def __init__(self, db: FakeFirestore):
        self.db, self.ops = db, []

    def set(self, ref: _Doc, data: dict) -> None:
        self.ops.append((ref, data))

    async def commit(self) -> None:
        assert len(self.ops) <= 500, "Firestore allows 500 writes in one batch"
        for ref, data in self.ops:
            await ref.set(data)
        self.db.commits += 1


class FakeFirestore:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.writes = self.reads = self.commits = 0

    def collection(self, name: str) -> _Col:
        return _Col(self, name)

    def batch(self) -> _Batch:
        return _Batch(self)

    async def recursive_delete(self, ref: _Doc) -> None:
        for p in [p for p in self.docs if p == ref.path or p.startswith(ref.path + "/")]:
            del self.docs[p]


def _activity(case_id: str, i: int) -> Activity:
    return Activity(id=f"act_{i:04d}", case_id=case_id, at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC), module="reading",
                    text=f"Read page {i}", detail={"n": i, "nested": [[1, 2], [3]]})


async def test_firestore_case_store_round_trip() -> None:
    from afterledger.adapters.gcp.firestore import FirestoreCaseStore

    db = FakeFirestore()
    store = FirestoreCaseStore(None, client=db)
    case = CaseFile(id="case_1", title="佐藤 健二", title_en="Kenji Sato", country_codes=["JP"])
    await store.put_case(case)
    await store.put(case.id, "activity", _activity(case.id, 1))
    await store.put_many(case.id, "activity", [_activity(case.id, i) for i in range(2, 1002)])
    assert db.commits == 3                                  # 1,000 records in batches of at most 450

    fresh = FirestoreCaseStore(None, client=db)             # a new instance reads what was written
    got = await fresh.get_case("case_1")
    assert got is not None and got.title == "佐藤 健二" and got.country_codes == ["JP"]
    items = await fresh.list("case_1", "activity")
    assert len(items) == 1001
    one = await fresh.get("case_1", "activity", "act_0001")
    assert one.detail == {"n": 1, "nested": [[1, 2], [3]]}  # nested arrays survive: records are JSON strings
    assert [c.id for c in await fresh.list_cases()] == ["case_1"]

    reads = db.reads
    await fresh.list("case_1", "activity")
    assert db.reads == reads                                # served from the write-through cache

    await fresh.delete("case_1", "activity", "act_0001")
    assert await fresh.get("case_1", "activity", "act_0001") is None
    await fresh.delete_case("case_1")
    assert await FirestoreCaseStore(None, client=db).get_case("case_1") is None
    assert not db.docs


def test_firestore_document_ids() -> None:
    from afterledger.adapters.gcp.firestore import _doc_id

    assert _doc_id("IN/rbi_deposit") == "IN~rbi_deposit"    # plan keys hold '/', which ids cannot
    assert _doc_id(".") == "_." and _doc_id("..") == "_.."
    assert _doc_id("act_0001") == "act_0001"


async def test_firestore_knowledge_overlay(tmp_path: Path) -> None:
    from afterledger.adapters.gcp.firestore import FirestoreKnowledgeStore

    db = FakeFirestore()
    store = FirestoreKnowledgeStore(KNOWLEDGE, tmp_path, None, client=db)
    base = await store.list("institutions")
    assert base, "the knowledge shipped in the image is read"
    await store.put("plans", "FR/insurer_any", {"key": "FR/insurer_any", "when": datetime(2026, 10, 14)})
    fresh = FirestoreKnowledgeStore(KNOWLEDGE, tmp_path, None, client=db)
    got = await fresh.get("plans", "FR/insurer_any")
    assert got == {"key": "FR/insurer_any", "when": "2026-10-14 00:00:00"}
    assert any(r.get("key") == "FR/insurer_any" for r in await fresh.list("plans"))
    assert "knowledge/plans/records/FR~insurer_any" in db.docs


# ---------------------------------------------------------------------------------------------
# Cloud Storage
# ---------------------------------------------------------------------------------------------

class _Blob:
    def __init__(self, bucket: _Bucket, name: str):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self.bucket.objects[self.name] = (bytes(data), content_type)

    def download_as_bytes(self) -> bytes:
        return self.bucket.objects[self.name][0]

    def exists(self) -> bool:
        return self.name in self.bucket.objects


class _Bucket:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str | None]] = {}

    def blob(self, name: str) -> _Blob:
        return _Blob(self, name)

    def list_blobs(self, prefix: str | None = None) -> list[_Blob]:
        return [_Blob(self, n) for n in sorted(self.objects) if n.startswith(prefix or "")]

    def delete_blobs(self, blobs: list[_Blob], on_error: Any = None) -> None:
        for b in blobs:
            self.objects.pop(b.name, None)


class FakeStorage:
    def __init__(self) -> None:
        self.buckets: dict[str, _Bucket] = {}

    def bucket(self, name: str) -> _Bucket:
        return self.buckets.setdefault(name, _Bucket())


async def test_gcs_blob_store() -> None:
    from afterledger.adapters.gcp.gcs import GcsBlobStore

    client = FakeStorage()
    store = GcsBlobStore("al-demo", REPO_ROOT / "data", client=client)
    ref = await store.put("cases/c1/papers/p1/p1.png", b"\x89PNG", "image/png")
    assert ref == "gs:cases/c1/papers/p1/p1.png"
    assert client.buckets["al-demo"].objects["cases/c1/papers/p1/p1.png"] == (b"\x89PNG", "image/png")
    assert await store.get(ref) == b"\x89PNG"
    assert await store.get("local:cases/c1/papers/p1/p1.png") == b"\x89PNG"   # a snapshot's refs resolve here
    assert await store.exists(ref) and not await store.exists("gs:cases/c1/none.png")
    family = next((REPO_ROOT / "data" / "families").glob("*/family.yaml"))
    rel = family.relative_to(REPO_ROOT / "data").as_posix()
    assert await store.get(f"data:{rel}") == family.read_bytes()             # fixtures come from the image
    assert not await store.exists("gs:../secrets") and not await store.exists("data:../../etc/passwd")
    with pytest.raises(ValueError):
        await store.get("gs:../outside")
    await store.put("cases/c2/x.txt", b"x")
    assert await store.delete_prefix("cases/c1/") == 1
    assert list(client.buckets["al-demo"].objects) == ["cases/c2/x.txt"]
    with pytest.raises(ValueError):
        await store.delete_prefix("")


# ---------------------------------------------------------------------------------------------
# Cloud Translation
# ---------------------------------------------------------------------------------------------

class FakeTranslate:
    def __init__(self, fail: bool = False) -> None:
        self.requests: list[dict] = []
        self.fail = fail

    async def translate_text(self, request: dict) -> Any:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("quota")
        return SimpleNamespace(translations=[SimpleNamespace(translated_text=f"[{request['target_language_code']}] {c}")
                                             for c in request["contents"]])


async def test_cloud_translator_reviewed_first_then_batches() -> None:
    from afterledger.adapters.gcp.translation import CloudTranslator

    api = FakeTranslate()
    tr = CloudTranslator("p", "global", reviewed=GlossaryTranslator(KNOWLEDGE), client=api)
    out = await tr.translate(["Dear Sir or Madam,", "The branch opens at ten.", ""], "hi", "en")
    assert out[0] == "महोदय/महोदया,"                        # reviewed by hand: never sent to the API
    assert out[1] == "[hi] The branch opens at ten."
    assert out[2] == ""
    assert len(api.requests) == 1 and api.requests[0]["contents"] == ["The branch opens at ten."]
    assert api.requests[0]["source_language_code"] == "en" and api.requests[0]["mime_type"] == "text/plain"

    again = await tr.translate(["The branch opens at ten."], "hi-IN", "en")
    assert again == ["[hi] The branch opens at ten."] and len(api.requests) == 1     # cached

    many = [f"Sentence number {i} that nobody reviewed." for i in range(250)]
    out = await tr.translate(many, "ja", "en")
    assert len(api.requests) == 1 + 3 and all(x.startswith("[ja] ") for x in out)    # 100 strings a request

    assert await tr.translate(["Hello there."], "en", "en") == ["Hello there."]
    assert len(api.requests) == 4                           # the same language is never sent


async def test_cloud_translator_outage_keeps_the_original() -> None:
    from afterledger.adapters.gcp.translation import CloudTranslator, _chunks

    tr = CloudTranslator("p", "global", reviewed=GlossaryTranslator(KNOWLEDGE), client=FakeTranslate(fail=True))
    assert await tr.translate(["Nobody reviewed this."], "es", "en") == ["Nobody reviewed this."]
    texts = ["x" * 20_000, "y" * 10_000, "z"]
    assert _chunks([0, 1, 2], texts) == [[0], [1, 2]]       # under 25,000 characters a request


# ---------------------------------------------------------------------------------------------
# Document AI
# ---------------------------------------------------------------------------------------------

def _document() -> Any:
    from google.cloud import documentai as d
    text = "Narmada Bank\nBalance 6,41,870.00\n"

    def line(start: int, end: int, box: tuple[float, float, float, float] | None, conf: float, pixels: bool = False):
        poly = d.BoundingPoly()
        if box and not pixels:
            x0, y0, x1, y1 = box
            poly = d.BoundingPoly(normalized_vertices=[d.NormalizedVertex(x=x0, y=y0), d.NormalizedVertex(x=x1, y=y0),
                                                       d.NormalizedVertex(x=x1, y=y1), d.NormalizedVertex(x=x0, y=y1)])
        elif box:
            x0, y0, x1, y1 = box
            poly = d.BoundingPoly(vertices=[d.Vertex(x=int(x0), y=int(y0)), d.Vertex(x=int(x1), y=int(y1))])
        return d.Document.Page.Line(layout=d.Document.Page.Layout(
            text_anchor=d.Document.TextAnchor(text_segments=[d.Document.TextAnchor.TextSegment(start_index=start,
                                                                                                 end_index=end)]),
            bounding_poly=poly, confidence=conf))

    page = d.Document.Page(dimension=d.Document.Page.Dimension(width=1000, height=2000), lines=[
        line(0, 12, (0.1, 0.05, 0.4, 0.07), 0.98),
        line(13, 33, (100, 300, 600, 340), 0.91, pixels=True),
        line(33, 34, None, 0.5),                            # only a newline: no text, left out
    ])
    return d.Document(text=text, pages=[page])


def test_lines_from_document() -> None:
    from afterledger.adapters.gcp.documentai import lines_from_document

    lines = lines_from_document(_document(), case_id="c", paper_id="pp", page_id="pg1")
    assert [ln.text for ln in lines] == ["Narmada Bank", "Balance 6,41,870.00"]
    assert lines[0].box == pytest.approx([0.1, 0.05, 0.4, 0.07])
    assert lines[1].box == pytest.approx([0.1, 0.15, 0.6, 0.17])   # pixels divided by the page size
    assert lines[0].source == "document_ai" and lines[1].confidence == pytest.approx(0.91)
    assert lines[0].id == "pg1-l01"


async def test_document_ai_ocr_paths() -> None:
    from afterledger.adapters.gcp.documentai import DocumentAiOcr

    class Client:
        def __init__(self, fail: bool = False) -> None:
            self.fail, self.requests = fail, []

        async def process_document(self, request: Any) -> Any:
            self.requests.append(request)
            if self.fail:
                raise RuntimeError("unavailable")
            return SimpleNamespace(document=_document())

    class Fallback:
        def __init__(self) -> None:
            self.calls = 0

        async def read_page(self, **kw: Any) -> list:
            self.calls += 1
            return ["from the PDF text layer"]

    fb = Fallback()
    client = Client()
    ocr = DocumentAiOcr("proj", "us", "proc1", fallback=fb, client=client)
    assert ocr.name == "projects/proj/locations/us/processors/proc1"
    full = "projects/123/locations/eu/processors/abc"
    assert DocumentAiOcr("proj", "us", full, fallback=fb, client=client).name == full
    lines = await ocr.read_page(case_id="c", paper_id="pp", page_id="pg1", image=b"img", mime="image/jpeg")
    assert len(lines) == 2 and client.requests[0].raw_document.mime_type == "image/jpeg"
    got = await ocr.read_page(case_id="c", paper_id="pp", page_id="pg1", image=b"", mime="application/pdf",
                              pdf_page_text=[{"text": "x"}])
    assert got == ["from the PDF text layer"] and fb.calls == 1 and len(client.requests) == 1
    failing = DocumentAiOcr("proj", "us", "proc1", fallback=fb, client=Client(fail=True))
    assert await failing.read_page(case_id="c", paper_id="pp", page_id="pg1", image=b"img", mime="image/png") == []


# ---------------------------------------------------------------------------------------------
# Check grounding
# ---------------------------------------------------------------------------------------------

async def test_check_grounding() -> None:
    from google.cloud import discoveryengine as de

    from afterledger.adapters.gcp.grounding import CheckGrounding

    class Client:
        def __init__(self, score: float, fail: bool = False) -> None:
            self.score, self.fail, self.requests = score, fail, []

        async def check_grounding(self, request: Any) -> Any:
            self.requests.append(request)
            if self.fail:
                raise RuntimeError("permission denied")
            return de.CheckGroundingResponse(support_score=self.score, claims=[de.CheckGroundingResponse.Claim(
                claim_text="The bank must settle within 15 days.", citation_indices=[0],
                grounding_check_required=True)])

    page = "The bank shall settle the claim within 15 days from the date of receipt of all documents."
    good = CheckGrounding("proj", fallback=LexicalGrounding(), client=Client(0.82))
    r = await good.check("The bank must settle within 15 days.", [("https://rbi.org.in/x", page)])
    assert r.supported and r.method == "check_grounding" and r.support_score == pytest.approx(0.82)
    assert r.claims[0]["citations"] == [0]
    req = good.client.requests[0]
    assert req.grounding_config == "projects/proj/locations/global/groundingConfigs/default_grounding_config"
    assert req.facts[0].attributes["source"] == "https://rbi.org.in/x"

    changed = await good.check("The bank must settle within 30 days.", [("https://rbi.org.in/x", page)])
    assert not changed.supported and "30" in changed.claims[-1]["text"]   # a changed figure fails by code

    down = CheckGrounding("proj", fallback=LexicalGrounding(), client=Client(0.9, fail=True))
    r = await down.check("The bank must settle within 15 days.", [("u", page)])
    assert r.method == "lexical"                            # says which check decided


# ---------------------------------------------------------------------------------------------
# The live web: tiers, pages, search
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("url", "tier"), [
    ("https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=1", 1),
    ("https://rbidocs.rbi.org.in/rdocs/notification/PDFs/x.pdf", 1),
    ("https://www.gov.uk/after-a-death", 1),
    ("https://www.courts.go.jp/saiban/syurui/syurui_kazi/", 1),
    ("https://www.ssa.gov/benefits/survivors/", 1),
    ("https://www.service-public.fr/particuliers/vosdroits/F1446", 1),
    ("https://eur-lex.europa.eu/eli/reg/2012/650/oj", 1),
    ("https://www.nsandi.com/get-to-know-us/bereavement", 2),
    ("https://www.reddit.com/r/legaladvice/x", 5),
    ("https://detail.chiebukuro.yahoo.co.jp/qa/question_detail/q1", 5),
    ("https://www.investopedia.com/terms/p/probate.asp", 4),
])
def test_tier_for(url: str, tier: int) -> None:
    from afterledger.adapters.gcp.web import tier_for
    from afterledger.adapters.gcp.wiring import institution_domains

    assert tier_for(url, institution_domains(KNOWLEDGE)) == tier


def test_institution_domains_are_real_only() -> None:
    from afterledger.adapters.gcp.wiring import institution_domains

    domains = institution_domains(KNOWLEDGE)
    assert "rbi.org.in" in domains and "nsandi.com" in domains
    assert not any(d.endswith(".example") for d in domains)


def test_html_to_text() -> None:
    from afterledger.adapters.gcp.web import html_to_text

    html = """<!doctype html><html lang="ja-JP"><head><title> 相続の手続き </title><style>p{}</style></head>
    <body><nav>Menu Home</nav><script>var x=1;</script><h1>相続放棄</h1>
    <p>相続の開始があったことを知った時から<b>3か月以内</b>に家庭裁判所に申述しなければなりません。</p>
    <table><tr><td>手数料</td><td>800円</td></tr></table><footer>© court</footer></body></html>"""
    title, text, lang = html_to_text(html)
    assert title == "相続の手続き" and lang == "ja"
    assert "3か月以内に家庭裁判所" in text and "Menu" not in text and "var x" not in text and "court" not in text
    assert "手数料 | 800円" in text


class _Snapshots:
    def __init__(self, pages: dict[str, PageSnapshot]) -> None:
        self.pages = pages

    async def fetch(self, url: str) -> PageSnapshot | None:
        return self.pages.get(url)


async def test_live_pages() -> None:
    from afterledger.adapters.gcp.web import LivePages

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "www.rbi.org.in":
            return httpx.Response(200, html="<html lang='en'><title>Claims</title><p>Settle within 15 days.</p></html>",
                                  headers={"last-modified": "Tue, 01 Sep 2026 10:00:00 GMT"})
        return httpx.Response(503)

    snaps = _Snapshots({
        "https://www.rbi.org.in/claims": PageSnapshot(url="https://www.rbi.org.in/claims", text="Old words.", tier=1),
        "https://www.fca.org.uk/down": PageSnapshot(url="https://www.fca.org.uk/down", text="Dated words.", tier=1),
        "https://narmada-bank.example/claims": PageSnapshot(url="https://narmada-bank.example/claims",
                                                            text="Invented bank page.", fictional=True),
    })
    pages = LivePages(snaps, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_after=0)

    assert (await pages.fetch("https://www.rbi.org.in/claims")).text == "Old words."     # curated evidence
    live = await pages.fetch_live("https://www.rbi.org.in/claims")
    assert live.text == "Settle within 15 days." and live.tier == 1 and live.lang == "en"
    assert live.updated == "Tue, 01 Sep 2026 10:00:00 GMT" and live.content_hash
    n = len(seen)
    await pages.fetch_live("https://www.rbi.org.in/claims")
    assert len(seen) == n                                   # cached for hours, not fetched again
    assert (await pages.fetch_live("https://www.fca.org.uk/down")).text == "Dated words."  # unreachable: snapshot
    fictional = await pages.fetch_live("https://narmada-bank.example/claims")
    assert fictional.fictional and not any("narmada" in u for u in seen)                  # never on the web
    n = len(seen)
    assert await pages.fetch_live("https://unknown.example.org/x") is None
    assert len(seen) == n + 2                               # a busy server (503) is asked once more
    await pages.fetch_live("https://unknown.example.org/x")
    assert len(seen) == n + 2                               # and not again for a few minutes


async def test_a_quote_research_found_is_checked_on_the_page_not_its_saved_excerpt() -> None:
    """A saved snapshot holds the sentences a person copied (an excerpt); research checks its quotes on the whole page.
    A quote is checked on the snapshot when it is there, and otherwise on the page as it is now; offline there is only
    the snapshot."""
    from afterledger.adapters.gcp.web import LivePages
    from afterledger.knowledge import Knowledge

    url = "https://www.justice.fr/fiche/assurance-vie"
    full = ("Dans les 15 jours suivant la réception de la demande, l'Agira va informer l'ensemble des compagnies "
            "d'assurance du décès de l'assuré.")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=f"<html lang='fr'><p>Il faut interroger l'Agira.</p><p>{full}</p></html>")

    snaps = _Snapshots({url: PageSnapshot(url=url, text="Il faut interroger l'Agira.\nl'Agira va informer", tier=1)})
    live = Knowledge(None, LivePages(snaps, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    assert (await live.page_for_quote(url, "Il faut interroger l'Agira.")).text.startswith("Il faut")   # the snapshot
    page = await live.page_for_quote(url, full)
    assert page is not None and full in page.text                                                     # the page now
    assert (await live.page_for_quote(url, "Not on any page.")).text.startswith("Il faut interroger")  # snapshot shown
    offline = Knowledge(None, snaps)
    assert full not in (await offline.page_for_quote(url, full)).text


async def test_a_page_that_failed_once_is_read_on_the_second_try() -> None:
    """A timeout or a busy server is tried again at once, and a failure is not kept for hours: on 15 October one failed
    read of justice.fr made every quote from that page look missing to the evaluation."""
    from afterledger.adapters.gcp import web
    from afterledger.adapters.gcp.web import LivePages

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, html="<html lang='fr'><p>Le capital est versé dans un délai d'1 mois.</p></html>")

    pages = LivePages(_Snapshots({}), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_after=0)
    page = await pages.fetch_live("https://www.justice.fr/fiche/assurance-vie")
    assert page is not None and "1 mois" in page.text and calls["n"] == 2

    gone = {"n": 0}

    def missing(request: httpx.Request) -> httpx.Response:
        gone["n"] += 1
        return httpx.Response(404)

    pages = LivePages(_Snapshots({}), client=httpx.AsyncClient(transport=httpx.MockTransport(missing)), retry_after=0)
    assert await pages.fetch_live("https://www.justice.fr/gone") is None and gone["n"] == 1     # a 404 is not retried
    url, (at, page) = next(iter(pages._cache.items()))
    pages._cache[url] = (at - web._FAILED_TTL - 1, page)                                        # five minutes later
    await pages.fetch_live("https://www.justice.fr/gone")
    assert gone["n"] == 2


async def test_a_compressed_page_is_read_once_decompressed() -> None:
    """Most official sites send gzip: the page is read decompressed, not decompressed a second time (on 15 October
    every gzip page failed that way and research found official pages "unreachable")."""
    import gzip

    from afterledger.adapters.gcp.web import LivePages

    html = "<html lang='fr'><title>Assurance-vie</title><p>Le capital est versé au bénéficiaire.</p></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=gzip.compress(html.encode("utf-8")),
                              headers={"content-type": "text/html; charset=utf-8", "content-encoding": "gzip"})

    pages = LivePages(_Snapshots({}), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    page = await pages.fetch_live("https://www.service-public.gouv.fr/particuliers/vosdroits/F3053")
    assert page is not None and page.text == "Le capital est versé au bénéficiaire." and page.lang == "fr"


async def test_gemini_search() -> None:
    from google.genai import types

    from afterledger.adapters.gcp.web import GeminiSearch

    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AbC"
    response = types.GenerateContentResponse(candidates=[types.Candidate(grounding_metadata=types.GroundingMetadata(
        grounding_chunks=[types.GroundingChunk(web=types.GroundingChunkWeb(uri=redirect, title="service-public.fr")),
                          types.GroundingChunk(web=types.GroundingChunkWeb(uri="https://www.reddit.com/r/x",
                                                                           title="reddit"))],
        grounding_supports=[types.GroundingSupport(segment=types.Segment(text="Le délai est d'un mois."),
                                                   grounding_chunk_indices=[0])]))])

    class Models:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def generate_content(self, **kw: Any) -> Any:
            self.calls.append(kw)
            return response

    models = Models()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))

    def http(request: httpx.Request) -> httpx.Response:
        if "grounding-api-redirect" in str(request.url):
            return httpx.Response(302, headers={"location": "https://www.service-public.fr/particuliers/vosdroits/F1446"})
        return httpx.Response(200, text="ok")

    search = GeminiSearch("gemini-3.5-flash-lite", client=client,
                          http=httpx.AsyncClient(transport=httpx.MockTransport(http), follow_redirects=True))
    hits = await search.search("délai de versement assurance vie", lang="fr", country="FR")
    assert [h.url for h in hits] == ["https://www.service-public.fr/particuliers/vosdroits/F1446",
                                     "https://www.reddit.com/r/x"]
    assert [h.tier for h in hits] == [1, 5] and hits[0].snippet == "Le délai est d'un mois."
    call = models.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite" and call["config"].tools[0].google_search is not None
    assert "about FR" in call["contents"]


async def test_agent_search_falls_back_to_the_web() -> None:
    from afterledger.adapters.gcp.web import AgentSearch

    class Pager:
        def __init__(self, results: list) -> None:
            self.results = results

        def __aiter__(self):
            async def gen():
                for r in self.results:
                    yield r
            return gen()

    class Client:
        def __init__(self, results: list) -> None:
            self.results = results

        async def search(self, request: Any) -> Pager:
            return Pager(self.results)

    class Web:
        async def search(self, query: str, **kw: Any) -> list:
            return ["from the web"]

    doc = SimpleNamespace(document=SimpleNamespace(derived_struct_data={
        "link": "https://www.cpf.gov.sg/member/growing-your-savings/nominations", "title": "Nominations",
        "snippets": [{"snippet": "Your nominees will receive your CPF savings."}]}))
    found = AgentSearch("p", "global", "store1", fallback=Web(), client=Client([doc]))
    hits = await found.search("CPF nomination")
    assert hits[0].tier == 1 and hits[0].snippet.startswith("Your nominees")
    assert "dataStores/store1/servingConfigs" in found.serving_config
    empty = AgentSearch("p", "global", "store1", fallback=Web(), client=Client([]))
    assert await empty.search("anything") == ["from the web"]


# ---------------------------------------------------------------------------------------------
# Cloud Tasks and BigQuery
# ---------------------------------------------------------------------------------------------

async def test_cloud_tasks_queue() -> None:
    from afterledger.adapters.gcp.tasks import CloudTasksQueue

    class Client:
        def __init__(self) -> None:
            self.created: list[tuple[str, Any]] = []

        async def create_task(self, parent: str, task: Any) -> Any:
            self.created.append((parent, task))
            return SimpleNamespace(name=f"{parent}/tasks/{len(self.created)}")

    ran: list[dict] = []

    async def handler(payload: dict) -> None:
        ran.append(payload)

    client = Client()
    q = CloudTasksQueue("projects/p/locations/asia-south1/queues/work", "https://api.example.run.app/", "tok",
                        client=client)
    q.register("prepare_pack", handler)
    name = await q.enqueue("prepare_pack", {"case_id": "c1", "claim_id": "k1"}, delay_seconds=30)
    assert name.endswith("/tasks/1")
    parent, task = client.created[0]
    assert parent == "projects/p/locations/asia-south1/queues/work"
    assert task.http_request.url == "https://api.example.run.app/internal/tasks/prepare_pack"
    assert task.http_request.headers["X-AfterLedger-Token"] == "tok"
    assert json.loads(task.http_request.body) == {"case_id": "c1", "claim_id": "k1"}
    assert task.dispatch_deadline.total_seconds() == 1800
    assert task.schedule_time.timestamp() > datetime.now(UTC).timestamp() + 20
    with pytest.raises(KeyError):
        await q.enqueue("unknown", {})
    await q.run("prepare_pack", {"case_id": "c1"})
    assert ran == [{"case_id": "c1"}]


async def test_bigquery_trace() -> None:
    from afterledger.adapters.gcp.bigquery import BigQueryTrace

    class Client:
        def __init__(self, fail: bool = False) -> None:
            self.rows: list[dict] = []
            self.fail = fail

        def insert_rows_json(self, table: str, rows: list[dict]) -> list:
            if self.fail:
                raise RuntimeError("not found")
            assert table == "p.afterledger.traces"
            self.rows.extend(rows)
            return []

    client = Client()
    trace = BigQueryTrace("p", "afterledger", client=client)
    await trace.record({"type": "agent", "agent": "reader_a", "case_id": "c1", "ms": 812.4, "model": "gemini"})
    await trace.record({"type": "check", "name": "readback"})
    await trace.flush()
    assert [r["type"] for r in client.rows] == ["agent", "check"]
    assert client.rows[0]["ms"] == 812 and json.loads(client.rows[0]["payload"])["agent"] == "reader_a"
    assert client.rows[1]["ms"] is None
    broken = BigQueryTrace("p", "afterledger", client=Client(fail=True))
    await broken.record({"type": "agent"})
    await broken.flush()                                    # a trace never fails a request
    if trace._task:
        trace._task.cancel()
    if broken._task:
        broken._task.cancel()


# ---------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------

def _stub(monkeypatch: pytest.MonkeyPatch, module: str, *names: str) -> None:
    import importlib
    mod = importlib.import_module(f"afterledger.adapters.gcp.{module}")
    for name in names:
        monkeypatch.setattr(mod, name, type(name, (), {"__init__": lambda self, *a, **kw: setattr(self, "args", (a, kw))}))


def test_wiring_swaps_what_is_configured(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from afterledger.runtime import build_deps

    _stub(monkeypatch, "firestore", "FirestoreCaseStore", "FirestoreKnowledgeStore")
    _stub(monkeypatch, "gcs", "GcsBlobStore")
    _stub(monkeypatch, "bigquery", "BigQueryTrace")
    _stub(monkeypatch, "tasks", "CloudTasksQueue")
    _stub(monkeypatch, "documentai", "DocumentAiOcr")
    _stub(monkeypatch, "translation", "CloudTranslator")
    _stub(monkeypatch, "grounding", "CheckGrounding")
    _stub(monkeypatch, "web", "LivePages", "GeminiSearch", "AgentSearch")
    monkeypatch.setattr("afterledger.agents.factory.ModelProvider.__init__", lambda self, deps: None)

    s = settings.model_copy(update={"mode": "live", "store": "gcp", "llm": "gemini", "project": "proj",
                                    "gcs_bucket": "bucket", "tasks_queue": "projects/proj/locations/r/queues/q",
                                    "api_url": "https://api", "internal_token": "tok", "docai_ocr_processor": "ocr1",
                                    "use_agent_search": True, "search_datastore": "ds",
                                    "model_armor_template": None})       # a laptop's .env may name one
    deps = build_deps(s)
    services = deps.extras["services"]
    assert services == {"store": "firestore", "blobs": "cloud_storage", "trace": "bigquery", "tasks": "cloud_tasks",
                        "ocr": "document_ai", "translation": "cloud_translation", "grounding": "check_grounding",
                        "pages": "live_web", "search": "agent_search", "screen": "patterns", "llm": "gemini",
                        "models": "gemini-3.8-flash, gemini-3.1-pro-preview, gemini-3.5-flash-lite",
                        "voice": "gemini_live"}
    assert type(deps.cases).__name__ == "FirestoreCaseStore" and type(deps.search).__name__ == "AgentSearch"

    _stub(monkeypatch, "modelarmor", "ModelArmorScreen")
    armored = build_deps(s.model_copy(update={"model_armor_template": "projects/proj/locations/us-central1/templates/t"}))
    assert armored.extras["services"]["screen"] == "model_armor"
    assert type(armored.screen).__name__ == "ModelArmorScreen"

    offline_web = build_deps(s.model_copy(update={"use_live_web": False, "use_agent_search": False,
                                                  "use_document_ai": False, "tasks_queue": None}))
    sv = offline_web.extras["services"]
    assert sv["pages"] == "snapshots" and sv["search"] == "snapshots" and sv["ocr"] == "pdf_text_and_sidecars"
    assert sv["tasks"] == "in_process"
    no_voice = build_deps(s.model_copy(update={"use_live_voice": False}))
    assert no_voice.extras["services"]["voice"] == "browser_speech"


def test_wiring_refuses_half_a_configuration(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from afterledger.adapters.gcp.wiring import ConfigError
    from afterledger.runtime import build_deps

    _stub(monkeypatch, "firestore", "FirestoreCaseStore", "FirestoreKnowledgeStore")
    _stub(monkeypatch, "gcs", "GcsBlobStore")
    _stub(monkeypatch, "bigquery", "BigQueryTrace")
    with pytest.raises(ConfigError):
        build_deps(settings.model_copy(update={"store": "gcp", "project": "proj"}))
    with pytest.raises(ConfigError):
        build_deps(settings.model_copy(update={"store": "gcp", "project": "proj", "gcs_bucket": "b",
                                               "tasks_queue": "q"}))


# ---------------------------------------------------------------------------------------------
# Demo bundles into Firestore and Cloud Storage
# ---------------------------------------------------------------------------------------------

async def test_demo_bundle_imports_into_cloud_stores(settings: Any, tmp_path: Path) -> None:
    from afterledger.adapters.gcp.firestore import FirestoreCaseStore
    from afterledger.adapters.gcp.gcs import GcsBlobStore
    from afterledger.seed import snapshot

    bundles = tmp_path / "bundles"
    src = bundles / "verma"
    (src / "case").mkdir(parents=True)
    case = CaseFile(id="demo-verma", title="Rajesh Kumar Verma", demo_key="verma", model_mode="scripted")
    (src / "case" / "case.json").write_text(case.model_dump_json(), encoding="utf-8")
    acts = {f"act_{i}": _activity("demo-verma", i).model_dump(mode="json") for i in range(3)}
    (src / "case" / "activity.json").write_text(json.dumps(acts), encoding="utf-8")
    (src / "blobs" / "papers" / "pp1").mkdir(parents=True)
    (src / "blobs" / "papers" / "pp1" / "p1.png").write_bytes(b"\x89PNG page")

    db, storage = FakeFirestore(), FakeStorage()
    changed: list[str] = []
    deps = SimpleNamespace(
        settings=settings.model_copy(update={"store": "gcp", "demo_bundles": bundles}),
        cases=FirestoreCaseStore(None, client=db), blobs=GcsBlobStore("b", REPO_ROOT / "data", client=storage),
        changed=lambda case_id, *cols: changed.append(case_id))
    storage.bucket("b").objects["cases/demo-verma/papers/old/p1.png"] = (b"stale", None)

    assert snapshot.has_snapshot(deps, "verma") and not snapshot.has_snapshot(deps, "sato")
    assert await snapshot.restore(deps, "verma")
    got = await FirestoreCaseStore(None, client=db).get_case("demo-verma")
    assert got is not None and got.model_mode == "scripted"
    assert len(await deps.cases.list("demo-verma", "activity")) == 3
    assert list(storage.bucket("b").objects) == ["cases/demo-verma/papers/pp1/p1.png"]   # stale file gone
    assert await deps.blobs.get("local:cases/demo-verma/papers/pp1/p1.png") == b"\x89PNG page"
    assert changed == ["demo-verma"]
    assert not snapshot.save(deps, "verma")                 # snapshots are written by the local store only


def test_a_site_without_its_intermediate_certificate_is_completed_from_its_own_certificate() -> None:
    """A government site often sends its certificate without the one that signed it ("unable to get local issuer
    certificate"), and browsers fetch the missing one from the address the certificate names. The fetcher does the
    same: it reads that address from the site's certificate, and only for that failure."""
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID

    from afterledger.adapters.gcp.web import _as_pem, _missing_issuer, issuer_urls

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "www.parliament.example")])
    now = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(
                AuthorityInformationAccessOID.CA_ISSUERS,
                x509.UniformResourceIdentifier("http://cacerts.example.com/IssuingCA.crt"))]), critical=False)
            .sign(key, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    assert issuer_urls(der) == ["http://cacerts.example.com/IssuingCA.crt"]
    assert _as_pem(der).startswith("-----BEGIN CERTIFICATE-----")
    assert _missing_issuer(Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
                                     "local issuer certificate (_ssl.c:1000)"))
    assert not _missing_issuer(Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired"))
