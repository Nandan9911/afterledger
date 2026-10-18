"""The voice WebSocket, against a fake Gemini Live session built from the SDK's own message types: audio and
transcripts flow both ways, and a question goes through ask_case to the grounded answerer and is saved."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.genai import types

from afterledger.app.main import create_app


class FakeLive:
    """Plays one spoken turn: hears a question, calls ask_case, waits for the answer, speaks it."""

    def __init__(self) -> None:
        self.audio_in = 0
        self.ended = asyncio.Event()
        self.tool_answers: list[Any] = []
        self.answered = asyncio.Event()
        self.texts: list[str] = []

    async def send_realtime_input(self, *, audio: Any = None, audio_stream_end: bool | None = None,
                                  text: str | None = None) -> None:
        if audio is not None:
            assert audio.mime_type == "audio/pcm;rate=16000"
            self.audio_in += len(audio.data)
        if audio_stream_end:
            self.ended.set()
        if text:
            self.texts.append(text)

    async def send_tool_response(self, *, function_responses: Any) -> None:
        self.tool_answers.extend(function_responses)
        self.answered.set()

    async def receive(self):
        await self.ended.wait()
        self.ended.clear()
        yield types.LiveServerMessage(server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text="Which papers are still missing?", finished=True)))
        yield types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[types.FunctionCall(
            id="call-1", name="ask_case", args={"question": "Which papers are still missing?", "language": "en"})]))
        await self.answered.wait()
        yield types.LiveServerMessage(server_content=types.LiveServerContent(
            model_turn=types.Content(role="model", parts=[types.Part(inline_data=types.Blob(
                data=b"\x01\x00" * 480, mime_type="audio/pcm;rate=24000"))]),
            output_transcription=types.Transcription(text="Here is what the case says.")))
        yield types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))


@pytest.fixture
def fake() -> FakeLive:
    return FakeLive()


@pytest.fixture
def client(deps: Any, fake: FakeLive):
    @contextlib.asynccontextmanager
    async def connect(settings: Any, *, instruction: str, lang: str):
        assert "ask_case" in instruction and "RESPOND IN" in instruction
        yield fake

    deps.extras["live_connect"] = connect
    with TestClient(create_app(deps, prepare_demo=False)) as c:
        c.deps = deps
        yield c


def _new_case(c: TestClient) -> str:
    r = c.post("/v1/cases", json={"lang": "en", "starter_name": "Emma Smith"})
    assert r.status_code in (200, 201), r.text
    return r.json()["case"]["id"]


def test_a_spoken_question_is_answered_from_the_case(client: TestClient, fake: FakeLive) -> None:
    case_id = _new_case(client)
    with client.websocket_connect(f"/v1/cases/{case_id}/voice?lang=en") as ws:
        ready = ws.receive_json()
        assert ready == {"type": "ready", "in_rate": 16000, "out_rate": 24000}
        ws.send_bytes(b"\x00\x00" * 640)                 # 40 ms of silence
        ws.send_json({"type": "end"})
        got: list[Any] = []
        while True:
            msg = ws.receive()
            if msg.get("bytes"):
                got.append(("audio", len(msg["bytes"])))
                continue
            import json
            body = json.loads(msg["text"])
            got.append((body["type"], body))
            if body["type"] == "turn_complete":
                break
        ws.send_json({"type": "close"})
    kinds = [k for k, _ in got]
    assert kinds == ["heard", "answer", "audio", "said", "turn_complete"]
    answer = dict(got)["answer"]
    assert answer["question"]["text"] == "Which papers are still missing?" and answer["question"]["channel"] == "voice"
    assert answer["answer"]["role"] == "assistant"
    assert fake.audio_in == 1280
    resp = fake.tool_answers[0]
    assert resp.id == "call-1" and resp.response["status"] == "ok" and resp.response["answer"]
    saved = client.get(f"/v1/cases/{case_id}/conversation").json()["utterances"]
    assert any(u["text"] == "Which papers are still missing?" and u["channel"] == "voice" for u in saved)


def test_closed_when_live_voice_is_off(deps: Any) -> None:
    from starlette.websockets import WebSocketDisconnect
    with TestClient(create_app(deps, prepare_demo=False)) as c:
        case_id = _new_case(c)
        with pytest.raises(WebSocketDisconnect) as info, c.websocket_connect(f"/v1/cases/{case_id}/voice"):
            pass
        assert info.value.code == 4404
        assert c.get("/v1/meta").json()["live_gateway"] is None


def test_unknown_case(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as info, client.websocket_connect("/v1/cases/nope/voice"):
        pass
    assert info.value.code == 4400


def test_a_yen_amount_is_given_to_the_voice_as_it_is_read() -> None:
    """Gemini Live read 3,480,000円 as 三百万四千八百円 (3,004,800 yen) on 15 October."""
    from afterledger.adapters.gcp.live import speakable
    text = "東都銀行での3,480,000円の払戻手続き。口座番号 12345678、2026年、¥120,000,000 と 12,345,678円。"
    assert speakable(text, "ja") == "東都銀行での348万円の払戻手続き。口座番号 12345678、2026年、1億2000万円 と 1234万5678円。"
    assert speakable("EUR 48,215.60 and 3,480,000 yen", "en") == "EUR 48,215.60 and 3,480,000 yen"
