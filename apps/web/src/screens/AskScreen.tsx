'use client';
/**
 * Ask: the companion. Ask anything about the case by voice or text in your own language; answers come only
 * from the case and its sources, each with "See the source". A question the case cannot answer becomes a
 * research request rather than a guess.
 */
import type { CiteView, ConversationView, Utterance } from '@afterledger/contracts';
import { Keyboard, Mic, Send } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useSearchParams } from 'next/navigation';
import { Fragment, useEffect, useRef, useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { useCase } from '@/components/CaseShell';
import { useListening, useLiveVoice, useSpeech } from '@/components/Voice';
import { Why } from '@/components/Why';
import { Button, ErrorNote, Skeleton, Spinner } from '@/components/ui';
import { localeNames, type Locale } from '@/i18n/routing';
import { perform } from '@/lib/changes';
import { caseTitle, cls, personName } from '@/lib/format';
import { useAsk, useConversation, useMeta } from '@/lib/queries';

function isQa(u: Utterance) {
  return (u.role === 'family' && u.intent === 'question') || (u.role === 'assistant' && u.grounded !== null && u.grounded !== undefined);
}

function SourceChips({ u, cites, today }: { u: Utterance; cites: Record<string, CiteView>; today: string | undefined }) {
  const t = useTranslations('ask');
  const list = u.sources.map((s) => cites[s]).filter(Boolean);
  if (!list.length) {
    return u.grounded ? <span className="rounded-full bg-chip px-2.5 py-0.5 text-[13px] text-ink-soft">{t('noSource')}</span> : null;
  }
  const official = list.some((c) => c.tier <= 2);
  const checkedToday = list.some((c) => c.checked_on && today && c.checked_on === today);
  return (
    <div className="mt-2.5 flex flex-wrap gap-1.5 text-[13px]">
      <Why
        ids={u.sources}
        cites={cites}
        title={t('seeSource')}
        className="rounded-full border-[1.5px] border-ledger bg-ledger-tint px-2.5 py-0.5 text-[13px] text-ledger no-underline"
      >
        {t('seeSource')}
      </Why>
      {official ? <span className="rounded-full bg-chip px-2.5 py-0.5 text-ink-soft">{t('official')}</span> : null}
      {checkedToday ? <span className="rounded-full bg-chip px-2.5 py-0.5 text-ink-soft">{t('checkedToday')}</span> : null}
      {u.grounded === false ? (
        // the grounding check did not find the answer in the sources it cites
        <span className="rounded-full border border-dashed border-pencil bg-pencil-tint px-2.5 py-0.5 font-bold text-pencil">
          {t('unsupported')}
        </span>
      ) : null}
    </div>
  );
}

function Bubble({ u, conv, today, locale }: { u: Utterance; conv: ConversationView; today: string | undefined; locale: string }) {
  const t = useTranslations('ask');
  const mine = u.role === 'family';
  const person = conv.people.find((p) => p.id === u.person_id);
  const name = person ? personName(person, locale) : '';
  const translation = u.translation && u.lang.split('-')[0] !== locale ? u.translation : null;
  const paras = u.text.split(/\n+/).filter(Boolean);
  return (
    <li
      className={cls(
        'max-w-[min(560px,88%)] border border-rule px-3.5 py-3',
        mine ? 'self-end rounded-[14px] rounded-se-[4px] bg-[#EAF2EC]' : 'rounded-[14px] rounded-ss-[4px] bg-sheet',
      )}
    >
      {mine && name ? (
        <p className="m-0 mb-0.5 text-[13px] text-ink-faint">{u.channel === 'voice' ? t('viaVoice', { name }) : name}</p>
      ) : null}
      {paras.map((p, i) => (
        <p key={i} lang={u.lang} className={cls('m-0', i > 0 && 'mt-2')}>
          {p}
        </p>
      ))}
      {translation ? <p className="m-0 mt-1.5 text-[14px] text-ink-faint">{translation}</p> : null}
      {!mine ? <SourceChips u={u} cites={conv.cites} today={today} /> : null}
    </li>
  );
}

export function AskScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('ask');
  const locale = useLocale();
  const params = useSearchParams();
  const { header, viewerId } = useCase();
  const conv = useConversation(caseId);
  const ask = useAsk(caseId);
  const speech = useSpeech();
  const [text, setText] = useState(params.get('q') ?? '');
  const [typing, setTyping] = useState(!!params.get('q'));
  const [pending, setPending] = useState<string | null>(null);
  const [followUps, setFollowUps] = useState<string[]>([]);
  const [research, setResearch] = useState(false);
  // answers on a shared sample case are not saved (it stays as it is for everyone): they are shown here only
  const [unsaved, setUnsaved] = useState<Array<{ question: Utterance; answer: Utterance; cites: Record<string, CiteView> }>>([]);
  const end = useRef<HTMLDivElement>(null);
  const voice = useListening(locale, (final) => {
    if (final) send(final, 'voice');
  });
  // In a live run the API offers a Gemini Live voice: a real spoken conversation, answered from the case.
  const meta = useMeta();
  const gateway = meta.data?.live_gateway;
  const live = useLiveVoice({
    path: gateway ? gateway.replace('{case_id}', encodeURIComponent(caseId)) : null,
    lang: locale,
    personId: viewerId,
    onAnswer: () => void conv.refetch(),
    onEnded: (error) => {
      if (error) toast(error === 'unavailable' ? t('voiceUnavailable') : t('liveFailed', { message: error }));
    },
  });
  const talking = voice.listening || live.state !== 'idle';

  function startSpeaking() {
    if (live.available) void live.start();
    else if (voice.supported) voice.start();
    else {
      toast(t('voiceUnavailable'));
      setTyping(true);
    }
  }

  function stopSpeaking() {
    if (live.state !== 'idle') live.stop();
    else voice.stop();
  }

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [conv.data?.utterances.length, unsaved.length]);

  function send(question: string, channel: 'voice' | 'text') {
    const q = question.trim();
    if (!q || ask.isPending) return;
    setPending(q);
    setFollowUps([]);
    setResearch(false);
    perform(
      ask,
      { question: q, person_id: viewerId, lang: locale, channel },
      {
        done: (out) => {
          setFollowUps(out.follow_ups ?? []);
          setResearch(!!out.research_started);
          if (out.kept === false) {
            const cites = Object.fromEntries((out.sources ?? []).map((c) => [c.id, c]));
            setUnsaved((list) => [...list, { question: out.question, answer: out.answer, cites }]);
          }
          if (channel === 'voice') speech.speak(out.answer.text, out.answer.lang.split('-')[0]);
        },
        after: () => setPending(null),
      },
    );
    setText('');
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    send(text, 'text');
  }

  if (conv.isError) return <ErrorNote error={conv.error} retry={() => conv.refetch()} />;
  const qa = (conv.data?.utterances ?? []).filter(isQa);
  const caseName = caseTitle(header?.case.title);
  return (
    <div className="mx-auto flex min-h-[calc(100vh-180px)] w-full max-w-[760px] flex-col gap-4">
      <header className="flex items-end justify-between gap-3 border-b border-rule pb-3">
        <div>
          {caseName ? <p className="m-0 text-[14px] text-ink-faint">{t('caseOf', { name: caseName })}</p> : null}
          <h1 className="m-0 font-serif text-[26px] font-semibold sm:text-[30px]">{t('title')}</h1>
        </div>
        <span className="rounded-full border-[1.5px] border-ledger px-2.5 py-1 text-[14px] font-bold text-ledger">
          {localeNames[locale as Locale] ?? locale}
        </span>
      </header>

      {!conv.data ? (
        <Skeleton className="h-64" />
      ) : (
        <ol aria-label={t('conversation')} className="m-0 flex flex-1 list-none flex-col gap-3 p-0">
          {!qa.length && !pending && !unsaved.length ? (
            <>
              <li className="rounded-[14px] border border-dashed border-rule-strong bg-sheet px-4 py-3 text-ink-soft">{t('empty')}</li>
              {/* a first question to start from, for a family that does not know what to ask */}
              <li className="flex flex-wrap gap-2">
                {(['example1', 'example2', 'example3'] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => send(t(k), 'text')}
                    className="rounded-full border border-rule-strong bg-sheet px-3 py-1.5 text-[15px] hover:border-ink"
                  >
                    {t(k)}
                  </button>
                ))}
              </li>
            </>
          ) : null}
          {qa.map((u) => (
            <Bubble key={u.id} u={u} conv={conv.data} today={header?.today} locale={locale} />
          ))}
          {unsaved.map((x, i) => (
            <Fragment key={`unsaved-${i}`}>
              <Bubble u={x.question} conv={conv.data} today={header?.today} locale={locale} />
              <Bubble
                u={x.answer}
                conv={{ ...conv.data, cites: { ...conv.data.cites, ...x.cites } }}
                today={header?.today}
                locale={locale}
              />
            </Fragment>
          ))}
          {unsaved.length ? <li className="text-[14px] text-ink-faint">{t('notSaved')}</li> : null}
          {pending ? (
            <>
              <li className="max-w-[min(560px,88%)] self-end rounded-[14px] rounded-se-[4px] border border-rule bg-[#EAF2EC] px-3.5 py-3 opacity-75">
                {pending}
              </li>
              <li>
                <Spinner label={t('thinking')} />
              </li>
            </>
          ) : null}
          {research ? (
            <li className="rounded-[14px] border-[1.5px] border-dotted border-blank bg-blank-tint px-4 py-3 text-[15px]">
              {t('research')}
            </li>
          ) : null}
          {followUps.length ? (
            <li className="flex flex-wrap gap-2">
              {followUps.map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => send(f, 'text')}
                  className="rounded-full border border-rule-strong bg-sheet px-3 py-1.5 text-[15px] hover:border-ink"
                >
                  {f}
                </button>
              ))}
            </li>
          ) : null}
        </ol>
      )}
      {/* room for the composer, so the last answer never ends up behind it */}
      <div ref={end} className="h-6 scroll-mb-40 lg:scroll-mb-28" />

      <section
        aria-label={talking ? t('listening') : t('speak')}
        className="sticky bottom-[78px] flex flex-col items-center gap-3 rounded-[20px] bg-ink px-4 pb-4 pt-[18px] text-white lg:bottom-4"
      >
        {talking ? (
          <>
            <div aria-hidden="true" className="flex h-11 items-center gap-1.5">
              {[22, 36, 44, 30, 40, 24, 34].map((h, i) => (
                <span
                  key={i}
                  className="w-1.5 animate-pulse-soft rounded-full bg-hl"
                  style={{ height: h, animationDelay: `${i * 120}ms` }}
                />
              ))}
            </div>
            <p className="m-0 text-[18px] font-bold" role="status">
              {live.state === 'connecting' ? t('liveConnecting') : live.state === 'live' ? t('liveTalking') : t('listening')}
            </p>
            {live.state === 'live' && !live.heard && !live.said ? (
              <p className="m-0 text-center text-[15px] text-night-soft">{t('liveHint')}</p>
            ) : null}
            {voice.interim || live.heard ? (
              <p lang={locale} className="m-0 text-center text-[15px] text-night-soft">
                “{voice.interim || live.heard}”
              </p>
            ) : null}
            {live.said ? (
              <p className="m-0 max-w-[36em] text-center text-[16px]" aria-live="polite">
                {live.said}
              </p>
            ) : null}
            <div className="flex w-full gap-2.5">
              <button type="button" onClick={stopSpeaking} className="min-h-12 flex-1 rounded-xl bg-white font-bold text-ink">
                {t('stop')}
              </button>
              <button
                type="button"
                onClick={() => (stopSpeaking(), setTyping(true))}
                className="min-h-12 flex-1 rounded-xl border border-[#5B6A75] font-bold text-white"
              >
                {t('type')}
              </button>
            </div>
          </>
        ) : typing ? (
          <form onSubmit={submit} className="flex w-full items-center gap-2">
            <label className="flex-1">
              <span className="sr-only">{t('placeholder')}</span>
              <input
                autoFocus
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={t('placeholder')}
                className="min-h-12 w-full rounded-xl bg-white px-3.5 text-[17px] text-ink outline-none"
              />
            </label>
            <Button type="submit" variant="highlight" aria-label={t('send')} disabled={!text.trim() || ask.isPending} className="min-h-12">
              <Send size={18} aria-hidden="true" />
            </Button>
            <button
              type="button"
              aria-label={t('speak')}
              onClick={() => (live.available || voice.supported ? (setTyping(false), startSpeaking()) : toast(t('voiceUnavailable')))}
              className="grid h-12 w-12 place-items-center rounded-xl border border-[#5B6A75]"
            >
              <Mic size={20} aria-hidden="true" />
            </button>
          </form>
        ) : (
          <div className="flex w-full items-center gap-2.5">
            <button
              type="button"
              onClick={startSpeaking}
              className="flex min-h-14 flex-1 items-center justify-center gap-2.5 rounded-xl bg-hl text-[18px] font-bold text-ink"
            >
              <Mic size={22} aria-hidden="true" />
              {t('speak')}
            </button>
            <button
              type="button"
              onClick={() => setTyping(true)}
              className="flex min-h-14 items-center gap-2 rounded-xl border border-[#5B6A75] px-4 font-bold"
            >
              <Keyboard size={20} aria-hidden="true" />
              {t('type')}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
