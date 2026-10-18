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
        request = de.CheckGroundingRequest(
            grounding_config=self.config, answer_candidate=claim[:16_000],
            facts=[de.GroundingFact(fact_text=text[:10_000], attributes={"source": str(src)[:500]})
                   for src, text in facts[:200] if text],
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
        claims = [{"text": c.claim_text, "citations": list(c.citation_indices),
                   "needs_check": bool(c.grounding_check_required)} for c in response.claims]
        if numbers:
            claims.append({"text": "numbers not in the sources: " + ", ".join(sorted(numbers)), "citations": [],
                           "needs_check": True})
        return GroundingResult(support_score=score, supported=score >= threshold and not numbers, claims=claims,
                               method="check_grounding")
