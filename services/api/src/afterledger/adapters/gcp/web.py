"""The live web for research: search through Gemini with Google Search grounding, an optional Agent Search
data store, and a page fetcher that turns HTML and PDF into text.

Curated knowledge keeps its evidence: a page in data/knowledge/pages is the snapshot that was verified by hand
on the date shown, so `fetch` returns it (and fictional institutions' pages exist only there). Research reads
the live page (`fetch_live`), because a quote is kept only if it is found word for word on the page as it is
today.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import OrderedDict
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from ...domain.checks import normalise
from ...netsafe import UnsafeUrl, allowed, check_url
from ...ports import PageSnapshot, SearchHit

log = logging.getLogger(__name__)

USER_AGENT = "AfterLedger/0.1 (research prototype; reads official pages about estate claims)"
_MAX_BYTES = 8_000_000
_MAX_REDIRECTS = 5
_TTL = 6 * 3600
_FAILED_TTL = 300         # a page that could not be read is tried again after five minutes, not taken as gone for hours
# Pages kept in memory, at most: until 19 October 2026 every page ever read stayed until the server restarted
# (research reads about sixty pages a question, some of them long PDFs)
_CACHE_PAGES = 400
_CACHE_CHARS = 30_000_000
_BUSY = {408, 425, 429, 500, 502, 503, 504}

# Official sources that do not sit under a government domain
_OFFICIAL = {
    "rbi.org.in", "rbidocs.rbi.org.in", "boj.or.jp", "fca.org.uk", "bankofengland.co.uk", "legislation.gov.uk",
    "consumerfinance.gov", "fdic.gov", "justice.fr", "service-public.fr", "legifrance.gouv.fr",
    "banque-france.fr", "bafin.de", "bundesbank.de", "gesetze-im-internet.de", "europa.eu", "eur-lex.europa.eu",
    "cbe.org.eg", "mas.gov.sg", "cpf.gov.sg", "ssa.gov", "irs.gov", "statutes.capitol.texas.gov",
    "indiacode.nic.in", "egazette.gov.in", "laws.e-gov.go.jp", "elaws.e-gov.go.jp", "courts.go.jp", "fsa.go.jp",
    "moj.go.jp", "nta.go.jp", "sebi.gov.in", "irdai.gov.in", "pfrda.org.in", "npci.org.in",
}
_GOV = re.compile(r"(^|\.)(gov|gob|gouv|govt|go|gc|gv|nic|mil)\.[a-z]{2,3}$|\.(gov|mil)$|\.admin\.ch$|\.bund\.de$|\.europa\.eu$")
_FORUM = re.compile(r"(^|\.)(reddit\.com|quora\.com|stackexchange\.com|facebook\.com|x\.com|twitter\.com|"
                    r"tiktok\.com|youtube\.com|medium\.com|blogspot\.com|wordpress\.com|teamblind\.com|"
                    r"chiebukuro\.yahoo\.co\.jp|detail\.chiebukuro\.yahoo\.co\.jp)$")


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def tier_for(url: str, institution_domains: set[str] | frozenset[str] = frozenset()) -> int:
    """1 official (regulator, law, court, government); 2 the institution's own page; 4 a secondary source;
    5 a forum, which is never evidence."""
    host = domain_of(url)
    if host in _OFFICIAL or any(host.endswith("." + d) for d in _OFFICIAL) or _GOV.search(host):
        return 1
    if host in institution_domains or any(host.endswith("." + d) for d in institution_domains):
        return 2
    if _FORUM.search(host):
        return 5
    return 4


# ---- pages ------------------------------------------------------------------------------------

class _Text(HTMLParser):
    """HTML to readable text: scripts, styles and page furniture left out, block elements on their own lines."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template", "button"}
    BLOCK = {"p", "div", "li", "tr", "br", "section", "article", "table", "ul", "ol", "dd", "dt", "blockquote",
             "pre", "h1", "h2", "h3", "h4", "h5", "h6", "main", "aside", "caption", "figcaption", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.lang: str | None = None
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "html":
            self.lang = dict(attrs).get("lang") or self.lang
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in raw.split("\n")]
        out: list[str] = []
        for ln in lines:
            if ln or (out and out[-1]):
                out.append(ln)
        return "\n".join(out).strip()


def html_to_text(html: str) -> tuple[str, str, str | None]:
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # broken markup still gives whatever text was read
        pass
    return re.sub(r"\s+", " ", p.title).strip(), p.text(), (p.lang or "").split("-")[0] or None


# ---- sites that send their certificate without the one that signed it -----------------------------------

def _missing_issuer(e: BaseException) -> bool:
    """A TLS failure because the site left out its intermediate certificate (common on government sites)."""
    text = str(e)
    return "CERTIFICATE_VERIFY_FAILED" in text and ("local issuer" in text or "issuer certificate" in text)


def issuer_urls(der: bytes) -> list[str]:
    """Where a certificate says its issuer's certificate can be fetched (its Authority Information Access)."""
    from cryptography import x509
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID
    cert = x509.load_der_x509_certificate(der)
    try:
        aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]


def _as_pem(data: bytes) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    try:
        cert = x509.load_der_x509_certificate(data)
    except ValueError:
        cert = x509.load_pem_x509_certificate(data)
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


async def context_with_issuer(host: str, port: int = 443, cafile: str | None = None) -> Any:
    """A browser completes such a site's chain by fetching the missing certificate from the address the site's own
    certificate names; this does the same. The fetched certificate is only a link in the chain: the chain must still
    end at a root the bundle trusts, and the host name must still match."""
    import socket
    import ssl

    import certifi

    def leaf() -> bytes:
        probe = ssl.create_default_context()
        probe.check_hostname, probe.verify_mode = False, ssl.CERT_NONE     # only to read the certificate it sends
        with socket.create_connection((host, port), timeout=10) as sock, \
                probe.wrap_socket(sock, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True) or b""

    der = await asyncio.to_thread(leaf)
    pems = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT}) as http:
        for url in issuer_urls(der)[:2]:
            check_url(url)
            r = await http.get(url)
            if r.status_code == 200 and r.content:
                pems.append(_as_pem(r.content))
    if not pems:
        return None
    ctx = ssl.create_default_context(cafile=cafile or certifi.where())
    ctx.load_verify_locations(cadata="".join(pems))
    return ctx


def pdf_to_text(data: bytes) -> tuple[str, str]:
    import pymupdf
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        title = (doc.metadata or {}).get("title") or ""
        return title, "\n".join(page.get_text() for page in doc)


class LivePages:
    def __init__(self, snapshots: Any, institution_domains: set[str] | None = None,
                 client: httpx.AsyncClient | None = None, retry_after: float = 2.0):
        self.snapshots = snapshots
        self.institution_domains = frozenset(institution_domains or ())
        # Redirects are followed by hand, so every hop is checked as a public address before it is fetched.
        self.client = client or httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(20.0),
                                                  headers={"User-Agent": USER_AGENT})
        self.retry_after = retry_after
        self._cache: OrderedDict[str, tuple[float, PageSnapshot | None]] = OrderedDict()
        self._completed: dict[str, httpx.AsyncClient | None] = {}   # host -> client that completes its chain

    async def fetch(self, url: str) -> PageSnapshot | None:
        """The verified snapshot when there is one (the evidence a rule was checked against), else the page now."""
        snap = await self.snapshots.fetch(url)
        if snap is not None:
            return snap
        return await self.fetch_live(url)

    async def fetch_live(self, url: str) -> PageSnapshot | None:
        snap = await self.snapshots.fetch(url)
        if snap is not None and snap.fictional:
            return snap                    # an invented institution's page exists only in the demo's snapshots
        hit = self._cache.get(url)
        if hit and time.time() - hit[0] < (_TTL if hit[1] is not None else _FAILED_TTL):
            self._cache.move_to_end(url)
            return hit[1]
        page, busy = await self._get(url)
        if page is None and busy:
            # a timeout, a dropped connection or a busy server usually answers a second time (on 15 October one failed
            # read of justice.fr made all five quotes from it look missing to the evaluation)
            await asyncio.sleep(self.retry_after)
            page, _ = await self._get(url)
        if page is None and snap is not None:
            page = snap                    # unreachable now: the dated snapshot, which says when it was taken
        self._remember(url, page)
        return page

    def _remember(self, url: str, page: PageSnapshot | None) -> None:
        now = time.time()
        self._cache.pop(url, None)
        self._cache[url] = (now, page)
        for key in [k for k, (at, p) in self._cache.items() if now - at >= (_TTL if p is not None else _FAILED_TTL)]:
            del self._cache[key]
        size = sum(len(p.text) for _, p in self._cache.values() if p is not None)
        while len(self._cache) > 1 and (len(self._cache) > _CACHE_PAGES or size > _CACHE_CHARS):
            _, (_, oldest) = self._cache.popitem(last=False)
            size -= len(oldest.text) if oldest is not None else 0

    async def _fetch(self, url: str) -> httpx.Response | None:
        """GET with each hop checked as a public http(s) address, at most five redirects, and a size cap that is
        enforced while reading, not after."""
        for _ in range(_MAX_REDIRECTS + 1):
            check_url(url)
            client = self._completed.get(urlparse(url).hostname or "") or self.client
            try:
                r = await self._open(client, url)
            except httpx.ConnectError as e:
                host = urlparse(url).hostname or ""
                if not _missing_issuer(e) or host in self._completed:
                    raise
                try:
                    ctx = await context_with_issuer(host, urlparse(url).port or 443)
                except Exception as err:
                    log.info("could not complete the certificate chain of %s: %s", host, err)
                    ctx = None
                self._completed[host] = httpx.AsyncClient(
                    verify=ctx, follow_redirects=False, timeout=httpx.Timeout(20.0),
                    headers={"User-Agent": USER_AGENT}) if ctx is not None else None
                if ctx is None:
                    raise
                log.info("%s sent its certificate without its issuer's; fetched the issuer from the address its "
                         "certificate names", host)
                r = await self._open(self._completed[host], url)
            try:
                if r.is_redirect and r.headers.get("location"):
                    url = str(r.url.join(r.headers["location"]))
                    continue
                body = bytearray()
                async for chunk in r.aiter_bytes():         # decompressed as it is read
                    body.extend(chunk)
                    if len(body) > _MAX_BYTES:
                        log.info("not read, larger than %d bytes: %s", _MAX_BYTES, url)
                        return None
                # the body is already decompressed: a copy that kept Content-Encoding would decompress it again
                # and fail (most sites send gzip or brotli)
                headers = [(k, v) for k, v in r.headers.multi_items()
                           if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
                return httpx.Response(r.status_code, headers=headers, content=bytes(body), request=r.request)
            finally:
                await r.aclose()
        log.info("not read, more than %d redirects: %s", _MAX_REDIRECTS, url)
        return None

    @staticmethod
    async def _open(client: httpx.AsyncClient, url: str) -> httpx.Response:
        """Starts a streamed GET (the caller closes it)."""
        return await client.send(client.build_request("GET", url), stream=True)

    async def _get(self, url: str) -> tuple[PageSnapshot | None, bool]:
        """The page, or None and whether trying again soon may help (a timeout, a dropped connection, a busy server)."""
        try:
            r = await self._fetch(url)
        except UnsafeUrl as e:
            log.warning("not fetched, %s: %s", e, url)
            return None, False
        except httpx.TransportError as e:            # timeouts and dropped or refused connections
            log.info("could not fetch %s: %s", url, e)
            return None, True
        except Exception as e:
            log.info("could not fetch %s: %s", url, e)
            return None, False
        if r is None:
            return None, False
        if r.status_code >= 400:
            log.info("could not fetch %s: HTTP %d", url, r.status_code)
            return None, r.status_code in _BUSY
        ctype = r.headers.get("content-type", "").lower()
        try:
            if "pdf" in ctype or urlparse(str(r.url)).path.lower().endswith(".pdf"):
                title, text = await asyncio.to_thread(pdf_to_text, r.content)
                lang = None
            else:
                title, text, lang = html_to_text(r.text)
        except Exception as e:
            log.info("could not read %s: %s", url, e)
            return None, False
        final = str(r.url)
        return PageSnapshot(url=final, title=title, text=text, lang=lang, fetched_at=datetime.now(UTC),
                            content_hash=hashlib.sha256(normalise(text).encode("utf-8")).hexdigest(),
                            status=r.status_code, updated=r.headers.get("last-modified"),
                            tier=tier_for(final, self.institution_domains), publisher=domain_of(final)), False


# ---- search -----------------------------------------------------------------------------------

class GeminiSearch:
    """Google Search through Gemini's grounding tool: the model searches, and the pages it grounded its answer
    on are the results. Grounding links are redirects, so each is followed to the page itself."""

    # A search usually answers in about 5 s. Live on 18 October 2026 one gave no answer for 8 minutes and held up a
    # research run, so a search is given this long, asked once more, and then reported empty.
    timeout_s = 30.0

    def __init__(self, model: str, institution_domains: set[str] | None = None, client: Any = None,
                 http: httpx.AsyncClient | None = None, health: Any = None):
        self.health = health
        if client is None:
            from google import genai
            client = genai.Client()
        self.client = client
        self.model = model
        self.institution_domains = frozenset(institution_domains or ())
        self.http = http or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(10.0),
                                              headers={"User-Agent": USER_AGENT})

    async def search(self, query: str, *, lang: str | None = None, sites: list[str] | None = None,
                     k: int = 8, country: str | None = None) -> list[SearchHit]:
        from google.genai import types
        where = f" The question is about {country}." if country else ""
        only = f" Search only these sites: {', '.join(sites)}." if sites else ""
        prompt = (f"Search the web for official pages (a regulator, the law, a court, a government body, or the "
                  f"institution itself) that answer: {query}.{where}{only} Prefer pages in "
                  f"{lang or 'the language of that country'} and in English. Answer in two sentences.")
        response = None
        for attempt in (1, 2):
            try:
                response = await asyncio.wait_for(self.client.aio.models.generate_content(
                    model=self.model, contents=prompt,
                    config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])),
                    timeout=self.timeout_s)
                break
            except TimeoutError:
                log.warning("search for %r gave no answer in %.0f s (try %d)", query, self.timeout_s, attempt)
            except Exception as e:
                log.warning("search failed for %r: %s", query, e)
                if self.health is not None:
                    self.health.failed("google_search", e)  # a strict live run stops here
                return []
        if response is None:
            if self.health is not None:
                self.health.failed("google_search", f"no answer after {self.timeout_s:.0f} s, twice")
            return []
        if self.health is not None:
            self.health.succeeded("google_search")
        meta = response.candidates[0].grounding_metadata if response.candidates else None
        if meta is None or not meta.grounding_chunks:
            return []
        snippets: dict[int, str] = {}
        for support in meta.grounding_supports or []:
            for idx in support.grounding_chunk_indices or []:
                if support.segment and support.segment.text and idx not in snippets:
                    snippets[idx] = support.segment.text
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for i, chunk in enumerate(meta.grounding_chunks):
            web = chunk.web
            if web is None or not web.uri:
                continue
            url = await self._resolve(web.uri)
            if url in seen or not allowed(url, resolve=False)[0]:
                continue
            seen.add(url)
            hits.append(SearchHit(url=url, title=web.title or domain_of(url), snippet=snippets.get(i, ""), lang=lang,
                                  tier=tier_for(url, self.institution_domains)))
            if len(hits) >= k:
                break
        return hits

    async def _resolve(self, uri: str) -> str:
        if "grounding-api-redirect" not in uri:
            return uri
        try:
            r = await self.http.get(uri)
            return str(r.url)
        except Exception:
            return uri


class AgentSearch:
    """An Agent Search (formerly Vertex AI Search) data store of curated official sites, searched first; the open web
    fills in when it returns nothing."""

    def __init__(self, project: str, location: str, data_store: str, fallback: Any,
                 institution_domains: set[str] | None = None, client: Any = None, health: Any = None):
        from google.cloud import discoveryengine
        self.health = health
        self.de = discoveryengine
        if client is None:
            client = discoveryengine.SearchServiceAsyncClient()
        self.client = client
        self.serving_config = (f"projects/{project}/locations/{location}/collections/default_collection/dataStores/"
                               f"{data_store}/servingConfigs/default_serving_config")
        self.fallback = fallback
        self.institution_domains = frozenset(institution_domains or ())

    async def search(self, query: str, *, lang: str | None = None, sites: list[str] | None = None,
                     k: int = 8, country: str | None = None) -> list[SearchHit]:
        de = self.de
        try:
            pager = await self.client.search(request=de.SearchRequest(serving_config=self.serving_config,
                                                                      query=query, page_size=k))
            hits: list[SearchHit] = []
            async for result in pager:
                data = dict(result.document.derived_struct_data or {})
                url = str(data.get("link") or "")
                if not url:
                    continue
                snippet = ""
                for s in data.get("snippets", []) or []:
                    snippet = str(dict(s).get("snippet", "")) if s else ""
                    break
                hits.append(SearchHit(url=url, title=str(data.get("title", "")), snippet=snippet, lang=lang,
                                      tier=tier_for(url, self.institution_domains)))
                if len(hits) >= k:
                    break
        except Exception as e:
            log.warning("Agent Search failed for %r: %s", query, e)
            if self.health is not None:
                self.health.failed("agent_search", e)       # a strict live run stops here
            hits = []
        return hits or await self.fallback.search(query, lang=lang, sites=sites, k=k, country=country)
