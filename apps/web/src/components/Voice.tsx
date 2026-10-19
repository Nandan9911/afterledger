'use client';
/**
 * Voice in the browser: reading aloud with the speech synthesiser, and listening with the browser's speech
 * recognition where it has one (Chrome, Edge, Safari). A live run can use the Gemini Live gateway instead;
 * this keeps voice working in any run.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiUrl } from '@/lib/api';

const BCP47: Record<string, string> = { en: 'en-IN', hi: 'hi-IN', ja: 'ja-JP', es: 'es-US', ml: 'ml-IN', ar: 'ar-EG' };

export function useSpeech() {
  const [speaking, setSpeaking] = useState(false);
  const speak = useCallback((text: string, lang = 'en') => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) return;
    const synth = window.speechSynthesis;
    if (synth.speaking) {
      synth.cancel();
      setSpeaking(false);
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    u.lang = BCP47[lang] ?? lang;
    const voice = synth.getVoices().find((v) => v.lang.toLowerCase().startsWith(u.lang.slice(0, 2).toLowerCase()));
    if (voice) u.voice = voice;
    u.rate = 0.95;
    u.onend = () => setSpeaking(false);
    u.onerror = () => setSpeaking(false);
    setSpeaking(true);
    synth.speak(u);
  }, []);
  useEffect(
    () => () => {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel();
    },
    [],
  );
  return { speak, speaking };
}

type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
};

export function useListening(lang: string, onFinal: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [seconds, setSeconds] = useState(0);
  const rec = useRef<Recognition | null>(null);
  const started = useRef<number>(0);
  const supported =
    typeof window !== 'undefined' &&
    !!(
      (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition ||
      (window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
    );

  const start = useCallback(() => {
    if (!supported) return;
    const Ctor = ((window as unknown as { SpeechRecognition?: new () => Recognition }).SpeechRecognition ||
      (window as unknown as { webkitSpeechRecognition?: new () => Recognition }).webkitSpeechRecognition)!;
    const r = new Ctor();
    r.lang = BCP47[lang] ?? lang;
    r.interimResults = true;
    r.continuous = false;
    r.onresult = (e) => {
      let text = '';
      let final = false;
      for (let i = 0; i < e.results.length; i++) {
        text += e.results[i][0].transcript;
        final = final || e.results[i].isFinal;
      }
      setInterim(text);
      if (final) {
        setSeconds(Math.max(1, Math.round((Date.now() - started.current) / 1000)));
        onFinal(text.trim());
      }
    };
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    rec.current = r;
    started.current = Date.now();
    setInterim('');
    setListening(true);
    r.start();
  }, [lang, onFinal, supported]);

  const stop = useCallback(() => {
    rec.current?.stop();
    setListening(false);
  }, []);

  return { supported, listening, interim, seconds, start, stop };
}

type LiveVoiceState = 'idle' | 'connecting' | 'live';

type LiveParts = {
  ws: WebSocket | null;
  mic: MediaStream | null;
  inCtx: AudioContext | null;
  outCtx: AudioContext | null;
  sources: Set<AudioBufferSourceNode>;
  next: number;
  fresh: boolean;
};

/**
 * A spoken conversation through the API's Gemini Live gateway (a live run only). The microphone is sent as
 * 16 kHz PCM (see public/pcm-recorder.js) and the voice comes back as 24 kHz PCM, played as it arrives; when the
 * person talks over it, the server says so and what is still queued is dropped. Every question about the case
 * goes through the same grounded answerer as a typed one, and lands in the conversation.
 */
export function useLiveVoice({
  path,
  lang,
  personId,
  onAnswer,
  onEnded,
}: {
  path: string | null;
  lang: string;
  personId: string | null;
  onAnswer?: () => void;
  onEnded?: (error: string | null) => void;
}) {
  const [state, setState] = useState<LiveVoiceState>('idle');
  const [heard, setHeard] = useState('');
  const [said, setSaid] = useState('');
  const [speaking, setSpeaking] = useState(false);
  const parts = useRef<LiveParts>({ ws: null, mic: null, inCtx: null, outCtx: null, sources: new Set(), next: 0, fresh: true });
  const handlers = useRef({ onAnswer, onEnded });
  useEffect(() => {
    handlers.current = { onAnswer, onEnded };
  }, [onAnswer, onEnded]);

  const silence = useCallback(() => {
    const p = parts.current;
    for (const s of p.sources) {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    p.sources.clear();
    p.next = 0;
    setSpeaking(false);
  }, []);

  const teardown = useCallback(() => {
    const p = parts.current;
    silence();
    if (p.ws && p.ws.readyState <= WebSocket.OPEN) {
      try {
        if (p.ws.readyState === WebSocket.OPEN) p.ws.send(JSON.stringify({ type: 'close' }));
        p.ws.close();
      } catch {
        /* closing anyway */
      }
    }
    p.mic?.getTracks().forEach((t) => t.stop());
    void p.inCtx?.close().catch(() => undefined);
    void p.outCtx?.close().catch(() => undefined);
    parts.current = { ws: null, mic: null, inCtx: null, outCtx: null, sources: new Set(), next: 0, fresh: true };
    setState('idle');
  }, [silence]);

  const stop = useCallback(() => {
    const wasOpen = !!parts.current.ws;
    teardown();
    if (wasOpen) handlers.current.onEnded?.(null);
  }, [teardown]);

  const play = useCallback((buf: ArrayBuffer) => {
    const p = parts.current;
    const ctx = p.outCtx;
    if (!ctx || buf.byteLength < 2) return;
    const pcm = new Int16Array(buf, 0, Math.floor(buf.byteLength / 2));
    const audio = ctx.createBuffer(1, pcm.length, 24000);
    const ch = audio.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
    const src = ctx.createBufferSource();
    src.buffer = audio;
    src.connect(ctx.destination);
    const at = Math.max(ctx.currentTime + 0.02, p.next);
    src.start(at);
    p.next = at + audio.duration;
    p.sources.add(src);
    setSpeaking(true);
    src.onended = () => {
      p.sources.delete(src);
      if (!p.sources.size) setSpeaking(false);
    };
  }, []);

  const start = useCallback(async () => {
    if (!path || parts.current.ws) return;
    setState('connecting');
    setHeard('');
    setSaid('');
    const fail = (message: string) => {
      teardown();
      handlers.current.onEnded?.(message);
    };
    try {
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const inCtx = new AudioContext();
      const outCtx = new AudioContext({ sampleRate: 24000 });
      await inCtx.audioWorklet.addModule('/pcm-recorder.js');
      const query = new URLSearchParams({ lang, ...(personId ? { person_id: personId } : {}) });
      const url = apiUrl(path).replace(/^http/, 'ws');
      const ws = new WebSocket(`${url}${url.includes('?') ? '&' : '?'}${query}`);
      ws.binaryType = 'arraybuffer';
      parts.current = { ...parts.current, ws, mic, inCtx, outCtx };
      const recorder = new AudioWorkletNode(inCtx, 'pcm-recorder');
      recorder.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(e.data);
      };
      const mute = inCtx.createGain();
      mute.gain.value = 0; // the recorder must be pulled by the graph, but nothing should be heard
      inCtx.createMediaStreamSource(mic).connect(recorder);
      recorder.connect(mute).connect(inCtx.destination);
      ws.onmessage = (e: MessageEvent<ArrayBuffer | string>) => {
        if (typeof e.data !== 'string') {
          play(e.data);
          return;
        }
        let msg: { type: string; text?: string; message?: string };
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        const p = parts.current;
        if (msg.type === 'ready') setState('live');
        else if (msg.type === 'heard' && msg.text) {
          if (p.fresh) {
            setSaid('');
            setHeard('');
            p.fresh = false;
          }
          setHeard((h) => (h + msg.text).trimStart());
        } else if (msg.type === 'said' && msg.text) setSaid((s) => (s + msg.text).trimStart());
        else if (msg.type === 'answer') handlers.current.onAnswer?.();
        else if (msg.type === 'interrupted') silence();
        else if (msg.type === 'turn_complete') p.fresh = true;
        else if (msg.type === 'ending') stop();
        else if (msg.type === 'error') fail(msg.message ?? 'error');
      };
      ws.onclose = (e) => {
        if (parts.current.ws === ws) fail(e.code === 4404 ? 'unavailable' : e.reason || `closed (${e.code})`);
      };
      ws.onerror = () => undefined; // onclose follows with the reason
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err));
    }
  }, [path, lang, personId, play, silence, stop, teardown]);

  useEffect(() => teardown, [teardown]);

  return { available: !!path, state, heard, said, speaking, start, stop };
}
