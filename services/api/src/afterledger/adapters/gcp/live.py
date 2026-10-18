"""Voice through the Gemini Live API.

One Live session per spoken conversation: the browser's microphone audio goes in (16 kHz, 16-bit PCM) and the
model's voice comes back (24 kHz, 16-bit PCM), with transcripts of both. The model has one tool, ask_case, and
is told to use it for every question about the case, so whatever the voice says about the family's money comes
from the case's own grounded answer (the same answerer, sources and grounding check as a typed question).

On Agent Platform the Live model runs only in us and eu regions (AL_LIVE_LOCATION, default us-central1); on the
Gemini Developer API (an API key) there is no region to choose.
"""
from __future__ import annotations

import os
import re
from contextlib import AbstractAsyncContextManager
from typing import Any

from google.genai import types

IN_RATE = 16_000
OUT_RATE = 24_000

BCP47 = {"en": "en-IN", "hi": "hi-IN", "ja": "ja-JP", "es": "es-US", "ml": "ml-IN", "ar": "ar-EG", "de": "de-DE",
         "fr": "fr-FR", "ta": "ta-IN", "te": "te-IN", "bn": "bn-IN", "mr": "mr-IN", "ko": "ko-KR"}

ASK_CASE = types.FunctionDeclaration(
    name="ask_case",
    description="Answers a question about this family's case (claims, papers, money found, deadlines, who must sign, "
                "what to do next) from the case and its sources. Call it for every question about the case, then "
                "say its answer.",
    parameters=types.Schema(type=types.Type.OBJECT, properties={
        "question": types.Schema(type=types.Type.STRING, description="The question, in the person's own words"),
        "language": types.Schema(type=types.Type.STRING, description="BCP-47 code of the language they speak")},
        required=["question"]),
    behavior=types.Behavior.BLOCKING)


def _enterprise() -> bool:
    for name in ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI"):
        if name in os.environ:
            return os.environ[name].strip().lower() in ("1", "true", "yes")
    return False


def live_config(*, instruction: str, lang: str, voice: str = "Kore") -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=instruction,
        tools=[types.Tool(function_declarations=[ASK_CASE])],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(
            language_code=BCP47.get(lang.split("-")[0], lang),
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()))


def connect(settings: Any, *, instruction: str, lang: str) -> AbstractAsyncContextManager[Any]:
    """An open Live session (an async context manager), on Agent Platform or the Developer API."""
    from google import genai
    if _enterprise():
        client = genai.Client(vertexai=True, project=settings.project, location=settings.live_location)
    else:
        client = genai.Client()
    return client.aio.live.connect(model=settings.model_live, config=live_config(instruction=instruction, lang=lang))


def instruction_for(*, deceased: str, family: str, speaker: str | None, lang: str, language_name: str) -> str:
    who = f" You are speaking with {speaker}." if speaker else ""
    return (
        f"You are the voice of AfterLedger, helping a family settle what {deceased} left behind ({family}).{who}\n"
        "Speak warmly, slowly and briefly, as a kind person would to someone grieving. One or two sentences at a "
        "time, then let them speak.\n"
        "For every question about the case (money, claims, papers, deadlines, who must sign, what to do next), call "
        "ask_case with the question in the person's own words, then say its answer faithfully: keep every name, "
        "amount and date exactly, and add no fact of your own. If ask_case says something is not known, say so "
        "plainly. Never answer a question about the case from your own knowledge, and never give legal or tax "
        "advice.\n"
        "For greetings or thanks, reply briefly without calling the tool.\n"
        f"RESPOND IN {language_name.upper()}. YOU MUST RESPOND UNMISTAKABLY IN {language_name.upper()}, unless the "
        "person switches language; then follow them.")


_YEN = re.compile(r"(¥\s?|JPY\s?)?(\d{1,3}(?:,\d{3})+|\d{5,})(\s?円)?")


def _man(n: int) -> str:
    oku, rest = divmod(n, 100_000_000)
    man, ones = divmod(rest, 10_000)
    return (f"{oku}億" if oku else "") + (f"{man}万" if man else "") + (str(ones) if ones else "")


def speakable(text: str, lang: str) -> str:
    """The answer as the voice should read it. In Japanese a yen amount is written in 万 and 億 (3,480,000円 as
    348万円): on 15 October Gemini Live read 3,480,000円 as 三百万四千八百円, 3,004,800 yen."""
    if lang.split("-")[0] != "ja":
        return text

    def one(m: re.Match) -> str:
        if not (m.group(1) or m.group(3)):
            return m.group(0)                 # a number that is not yen: an account number, a year
        return _man(int(m.group(2).replace(",", ""))) + "円"
    return _YEN.sub(one, text)

