"""Spoken conversation with a case, through the Gemini Live API (live runs only).

    WS /v1/cases/{case_id}/voice?person_id=...&lang=hi

The browser sends binary frames of 16 kHz 16-bit mono PCM from the microphone, and JSON text frames:
{"type": "end"} when the person stops talking, {"type": "text", "text": "..."} for a typed line, and
{"type": "close"}. The service sends binary frames of 24 kHz 16-bit mono PCM (the voice) and JSON text frames:
ready, heard (what the person said, as transcribed), said (what the voice said), answer (a question answered from
the case; its question and answer are saved in the conversation like a typed one), interrupted (stop playing
what is buffered), turn_complete, ending (the session is about to close; open a new one) and error.

Every question about the case goes through the ask_case tool to the same grounded answerer as a typed question,
so the voice never says a fact the case does not hold. Offline, and when live voice is off, the socket is closed
at once with code 4404 and the app uses the browser's own speech instead.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...modules.research import language_name
from .. import actions

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/cases")

UNAVAILABLE = 4404       # live voice is not offered by this run
NO_CASE = 4400


@router.websocket("/{case_id}/voice")
async def voice(ws: WebSocket, case_id: str, person_id: str | None = None, lang: str = "en") -> None:
    deps = ws.app.state.deps
    connect = deps.extras.get("live_connect")
    if connect is None and not deps.settings.live_voice:
        await ws.close(code=UNAVAILABLE, reason="Live voice runs only in a live run with Gemini")
        return
    case = await deps.cases.get_case(case_id)
    from .auth import may_open, signed_in
    if case is None or not await may_open(deps, case, await signed_in(deps, ws)):
        await ws.close(code=NO_CASE, reason="No such case")
        return
    lang = (lang or "en").split("-")[0]
    people = await deps.cases.list(case_id, "people")
    speaker = next((p for p in people if p.id == person_id), None)
    family = ", ".join(f"{p.display_name} ({p.relationship})" if p.relationship else p.display_name
                       for p in people if p.id != case.deceased_id)
    from ...adapters.gcp import live
    instruction = live.instruction_for(deceased=case.title, family=family or "the family",
                                       speaker=speaker.display_name if speaker else None, lang=lang,
                                       language_name=language_name(lang))
    await ws.accept()
    try:
        async with (connect or live.connect)(deps.settings, instruction=instruction, lang=lang) as session:
            await ws.send_json({"type": "ready", "in_rate": live.IN_RATE, "out_rate": live.OUT_RATE})
            up = asyncio.create_task(_from_browser(ws, session))
            down = asyncio.create_task(_from_model(ws, session, deps, case_id, person_id, lang))
            done, pending = await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, WebSocketDisconnect):
                    raise exc
    except WebSocketDisconnect:
        return
    except Exception as exc:
        log.exception("voice session for %s failed", case_id)
        with contextlib.suppress(Exception):
            await ws.send_json({"type": "error", "message": str(exc)[:200] or "The voice session stopped."})
    with contextlib.suppress(Exception):
        await ws.close()


async def _from_browser(ws: WebSocket, session: Any) -> None:
    from google.genai import types
    while True:
        msg = await ws.receive()
        if msg.get("type") == "websocket.disconnect":
            return
        data = msg.get("bytes")
        if data:
            await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))
            continue
        text = msg.get("text")
        if not text:
            continue
        try:
            body = json.loads(text)
        except ValueError:
            continue
        kind = body.get("type")
        if kind == "end":
            await session.send_realtime_input(audio_stream_end=True)
        elif kind == "text" and str(body.get("text", "")).strip():
            await session.send_realtime_input(text=str(body["text"]).strip())
        elif kind == "close":
            return


async def _from_model(ws: WebSocket, session: Any, deps: Any, case_id: str, person_id: str | None,
                      lang: str) -> None:
    while True:                       # receive() yields one turn at a time
        async for msg in session.receive():
            content = getattr(msg, "server_content", None)
            if content is not None:
                await _content(ws, content)
            call = getattr(msg, "tool_call", None)
            if call is not None and call.function_calls:
                await _answer(ws, session, deps, case_id, person_id, lang, call.function_calls)
            if getattr(msg, "go_away", None) is not None:
                await ws.send_json({"type": "ending"})


async def _content(ws: WebSocket, content: Any) -> None:
    turn = getattr(content, "model_turn", None)
    for part in (turn.parts if turn and turn.parts else []):
        blob = getattr(part, "inline_data", None)
        if blob is not None and blob.data and (blob.mime_type or "").startswith("audio/"):
            await ws.send_bytes(blob.data)
    heard = getattr(content, "input_transcription", None)
    if heard is not None and heard.text:
        await ws.send_json({"type": "heard", "text": heard.text, "final": bool(getattr(heard, "finished", False))})
    said = getattr(content, "output_transcription", None)
    if said is not None and said.text:
        await ws.send_json({"type": "said", "text": said.text})
    if getattr(content, "interrupted", False):
        await ws.send_json({"type": "interrupted"})
    if getattr(content, "turn_complete", False):
        await ws.send_json({"type": "turn_complete"})


async def _answer(ws: WebSocket, session: Any, deps: Any, case_id: str, person_id: str | None, lang: str,
                  calls: list[Any]) -> None:
    """Runs ask_case through the grounded answerer and gives the model the answer to say."""
    from google.genai import types
    responses = []
    for fc in calls:
        args = dict(fc.args or {})
        question = str(args.get("question") or "").strip()
        if fc.name != "ask_case" or not question:
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response={
                "status": "error", "retryable": False, "message": "Only ask_case with a question is available."}))
            continue
        try:
            out = await actions.ask(deps, case_id, question=question, person_id=person_id,
                                    lang=(args.get("language") or lang).split("-")[0], channel="voice")
        except Exception as exc:
            log.exception("ask_case failed in a voice session")
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response={
                "status": "error", "retryable": False,
                "message": f"The case could not be checked just now ({str(exc)[:80]}). Say so, and suggest asking again."}))
            continue
        answer = out["answer"]
        await ws.send_json({"type": "answer", "question": out["question"], "answer": answer})
        from ...adapters.gcp.live import speakable
        responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response={
            "status": "ok", "retryable": False,
            "answer": speakable(answer.get("text", ""), answer.get("lang") or lang),
            "answer_en": answer.get("translation") or answer.get("text", ""), "language": answer.get("lang", lang),
            "has_sources": bool(answer.get("sources"))}))
    await session.send_tool_response(function_responses=responses)
