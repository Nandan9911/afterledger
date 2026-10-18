"""Claims checked against their sources with the check grounding API (Agent Search).

The API scores how well the cited facts support a claim, sentence by sentence. Two rules are added by code:
every number in the claim must appear in the facts (a changed figure is the commonest error, and a semantic
score can miss it), and if the API cannot be reached the lexical check decides instead, saying so.
"""
from __future__ import annotations

import logging
from typing import Any

from ...domain.checks import fidelity_violations
from ...ports import GroundingResult

log = logging.getLogger(__name__)

# The API takes up to 200 facts of up to 10,000 half-width characters each, a wide character counting twice, and an
# answer of up to 4,096 tokens. A fact is kept to 9,000 bytes of UTF-8, never fewer than its half-width count: a
# longer page is sent as consecutive pieces (one whole page, not its first 10,000 characters, is what a quote on page
# 40 is checked against), and a page of curly quotes and dashes no longer exceeds the limit and is refused.
FACT_BYTES = 9_000
MAX_FACTS = 200
CLAIM_CHARS = 8_000


def pieces(text: str, limit: int = FACT_BYTES) -> list[str]:
    """A text as consecutive pieces of at most `limit` bytes of UTF-8, each cut at a space when one is near."""
    out: list[str] = []
    rest = text.strip()
    while rest:
        if len(rest.encode("utf-8")) <= limit:
            out.append(rest)
            break
        cut = rest.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
        space = cut.rfind(" ", len(cut) // 2)
        piece = cut[:space] if space > 0 else cut
        out.append(piece)
        rest = rest[len(piece):].strip()
    return out


class CheckGrounding:
    def __init__(self, project: str, fallback: Any, client: Any = None, health: Any = None):
        self.health = health
        from google.cloud import discoveryengine
        self.de = discoveryengine
        if client is None:
            client = discoveryengine.GroundedGenerationServiceAsyncClient()
        self.client = client
        self.config = f"projects/{project}/locations/global/groundingConfigs/default_grounding_config"
        self.fallback = fallback

    async def check(self, claim: str, facts: list[tuple[str, str]], threshold: float = 0.6) -> GroundingResult:
        de = self.de
        sent: list[Any] = []
        origin: list[int] = []                 # the fact each piece came from, for the citations
        for n, (src, text) in enumerate(facts):
            for piece in pieces(text or ""):
                if len(sent) < MAX_FACTS:
                    sent.append(de.GroundingFact(fact_text=piece, attributes={"source": str(src)[:500]}))
                    origin.append(n)
        if not claim.strip() or not sent:      # nothing to check, or nothing to check against: the API refuses both
            return await self.fallback.check(claim, facts, threshold)
        request = de.CheckGroundingRequest(
            grounding_config=self.config, answer_candidate=claim[:CLAIM_CHARS], facts=sent,
            grounding_spec=de.CheckGroundingSpec(citation_threshold=threshold))
        try:
            response = await self.client.check_grounding(request=request)
        except Exception as e:
            log.warning("check grounding failed, using the lexical check: %s", e)
            if self.health is not None:
                self.health.failed("check_grounding", e)    # a strict live run stops here
            return await self.fallback.check(claim, facts, threshold)
        if self.health is not None:
            self.health.succeeded("check_grounding")
        score = float(response.support_score or 0.0)
        numbers = fidelity_violations(claim, " ".join(text for _, text in facts))
        claims = [{"text": c.claim_text,
                   "citations": sorted({origin[i] for i in c.citation_indices if 0 <= i < len(origin)}),
                   "needs_check": bool(c.grounding_check_required)} for c in response.claims]
        if numbers:
            claims.append({"text": "numbers not in the sources: " + ", ".join(sorted(numbers)), "citations": [],
                           "needs_check": True})
        return GroundingResult(support_score=score, supported=score >= threshold and not numbers, claims=claims,
                               method="check_grounding")
