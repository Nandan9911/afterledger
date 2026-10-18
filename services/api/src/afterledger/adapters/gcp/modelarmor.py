"""Model Armor screens outside text (a fetched page, an uploaded paper, an institution's reply) for prompt
injection, jailbreak attempts and malicious links before any agent reads it.

The template (AL_MODEL_ARMOR_TEMPLATE, projects/P/locations/L/templates/T) turns on the prompt-injection and
malicious-URI filters with multi-language detection; its region must offer both (us-central1 does; asia-south1
offers neither, as of October 2026). The offline pattern screen runs too, so a phrase either one catches is flagged.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...ports import ScreenResult

log = logging.getLogger(__name__)

_CHUNK = 20_000          # characters per request; longer text is screened in pieces
_MAX_CHUNKS = 6          # the first 120,000 characters, more than any page or letter AfterLedger reads


class ModelArmorScreen:
    engine = "model_armor"

    def __init__(self, template: str, *, fallback: Any, health: Any = None, client: Any = None):
        from google.api_core.client_options import ClientOptions
        from google.cloud import modelarmor_v1
        self.m = modelarmor_v1
        self.template = template
        location = template.split("/locations/")[1].split("/")[0]
        self.client = client or modelarmor_v1.ModelArmorClient(
            transport="rest", client_options=ClientOptions(api_endpoint=f"modelarmor.{location}.rep.googleapis.com"))
        self.fallback = fallback
        self.health = health

    async def screen(self, text: str, *, kind: str, source: str = "") -> ScreenResult:
        patterns = await self.fallback.screen(text, kind=kind, source=source)
        chunks = [text[i:i + _CHUNK] for i in range(0, min(len(text or ""), _CHUNK * _MAX_CHUNKS), _CHUNK)]
        # Model Armor refuses blank content (400 SANITIZATION_CONTENT_EMPTY), and there is nothing in it to screen:
        # a photo's empty text layer stopped a strict live run on 15 October
        chunks = [c for c in chunks if c.strip()]
        reasons: list[str] = []
        skipped = False
        try:
            for chunk in chunks:
                found, why, skip = await asyncio.to_thread(self._one, chunk)
                skipped = skipped or skip
                if found:
                    reasons.extend(r for r in why if r not in reasons)
        except Exception as e:
            log.warning("Model Armor could not screen %s %s: %s", kind, source, e)
            if self.health is not None:
                self.health.failed("model_armor", e)      # a strict live run stops here
            return patterns
        if self.health is not None:
            self.health.succeeded("model_armor")
        reasons += [f"pattern:{r}" for r in patterns.reasons if f"pattern:{r}" not in reasons]
        return ScreenResult(flagged=bool(reasons), reasons=reasons, engine="model_armor", skipped=skipped)

    def _one(self, chunk: str) -> tuple[bool, list[str], bool]:
        m = self.m
        r = self.client.sanitize_user_prompt(request=m.SanitizeUserPromptRequest(
            name=self.template, user_prompt_data=m.DataItem(text=chunk)))
        res = r.sanitization_result
        found = res.filter_match_state == m.FilterMatchState.MATCH_FOUND
        why: list[str] = []
        skipped = False
        for key, fr in res.filter_results.items():
            for attr in ("pi_and_jailbreak_filter_result", "malicious_uri_filter_result"):
                sub = getattr(fr, attr, None)
                if not sub:
                    continue
                if getattr(sub, "match_state", None) == m.FilterMatchState.MATCH_FOUND:
                    why.append(key)
                state = getattr(sub, "execution_state", None)
                if state is not None and state != m.FilterExecutionState.EXECUTION_SUCCESS:
                    skipped = True
        return found, why or (["model_armor"] if found else []), skipped
