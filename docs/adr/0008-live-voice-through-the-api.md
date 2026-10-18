# 0008 Live voice through the API, with one grounded tool

## Context
Many family members are more comfortable speaking than typing, in their own language. The Gemini Live API gives
natural spoken conversation, but on Agent Platform it takes OAuth credentials (no browser tokens), and a voice
must not say facts the case does not hold.

## Decision
The API offers a WebSocket (`/v1/cases/{id}/voice`) that opens a Gemini Live session for the browser: 16 kHz PCM
in, 24 kHz PCM out, transcripts both ways. The Live model is told to call `ask_case` for every question about the
case; the tool runs the same grounded answerer as a typed question and saves the question and answer in the
conversation. Offline, the browser's own speech recognition and synthesis are used instead.

## Consequences
- One code path for answers, whether typed or spoken; sources and the grounding check apply to both.
- On Agent Platform the Live model runs in us or eu regions only (`AL_LIVE_LOCATION`), so voice from Asia has the
  latency of that round trip.
