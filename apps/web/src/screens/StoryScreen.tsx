'use client';
/**
 * Telling the story: the conversation in the family's own language with English beside it, a microphone
 * and a text box, and "What we know so far" filling in as they talk. It ends with the one-page summary the
 * family confirms (checkpoint C1).
 */
import type { ConversationView, KnownLine, Utterance } from '@afterledger/contracts';
import { Mic, Square } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useRef, useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { useCase } from '@/components/CaseShell';
import { useListening } from '@/components/Voice';
import { Button, ButtonLink, ErrorNote, h1Class, Logo, Skeleton, Spinner } from '@/components/ui';
import { Link } from '@/i18n/navigation';
import { cls, dayMonth, languageName, personName } from '@/lib/format';
import { useAnswer, useConversation, useTurn } from '@/lib/queries';

function AssistantMark() {
  return (
    <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ledger-tint">
      <svg width="20" height="20" viewBox="0 0 30 30">
        <rect x="5" y="13" width="20" height="5.5" rx="1" fill="#FFE066" />
        <rect x="4" y="3" width="22" height="24" rx="3" fill="none" stroke="#1D6B55" strokeWidth="2" />
      </svg>
    </span>
  );
}

function Bubble({ u, conv, locale }: { u: Utterance; conv: ConversationView; locale: string }) {
  const t = useTranslations('story');
  const mine = u.role === 'family';
  const person = conv.people.find((p) => p.id === u.person_id);
  const name = person ? personName(person, locale) : '';
  const showTranslation = !!u.translation && u.lang.split('-')[0] !== locale;
  return (
    <li className={cls('flex items-start gap-3.5', mine && 'justify-end')}>
      {mine ? null : <AssistantMark />}
      <div
        className={cls(
          'max-w-[620px] border border-rule px-[18px] py-3.5',
          mine ? 'rounded-[14px] rounded-se-[4px] bg-[#EAF2EC]' : 'rounded-[14px] rounded-ss-[4px] bg-sheet',
        )}
      >
        {mine && (u.channel === 'voice' || name) ? (
          <div className="mb-1.5 flex items-center gap-2 text-[14px] text-ink-faint">
            {u.channel === 'voice' ? <Mic size={14} aria-hidden="true" /> : null}
            <span>
              {u.channel === 'voice' ? (u.seconds ? t('voiceNote', { name, seconds: u.seconds }) : t('voiceNoteNoTime', { name })) : name}
            </span>
          </div>
        ) : null}
        <p lang={u.lang} className="m-0 text-[19px] leading-[1.5]">
          {u.text}
        </p>
        {showTranslation ? <p className="m-0 mt-1.5 text-[15px] text-ink-faint">{u.translation}</p> : null}
        {!mine && u.why ? <p className="m-0 mt-2.5 text-[14px] font-bold text-ledger">{t('askedBecause', { why: u.why })}</p> : null}
      </div>
    </li>
  );
}

/** "Rajesh Kumar Verma. Died on…" → the name in bold, then the rest. */
function splitLead(text: string, by: '. ' | ', '): [string, string] {
  const i = text.indexOf(by);
  if (i < 0 || i > 60) return ['', text];
  return [text.slice(0, i), text.slice(i + (by === '. ' ? 2 : 0))];
}

function Known({ conv }: { conv: ConversationView }) {
  const t = useTranslations('story');
  const groups: Array<[string, KnownLine[]]> = [];
  for (const k of conv.known) {
    const g = groups.find(([label]) => label === k.label);
    if (g) g[1].push(k);
    else groups.push([k.label, [k]]);
  }
  const label = (l: string) => (t.has(`labels.${l}` as 'labels.Family') ? t(`labels.${l}` as 'labels.Family') : l);
  if (!groups.length) return <p className="m-0 border-t-2 border-ink pt-3.5 text-ink-soft">{t('empty')}</p>;
  return (
    <>
      {groups.map(([l, lines], gi) => (
        <section key={l} className={cls('pt-3.5', gi === 0 ? 'border-t-2 border-ink' : 'border-t border-rule')}>
          <h3 className="m-0 text-[15px] font-bold text-ink-faint">{label(l)}</h3>
          {l === 'Who died' ? (
            lines.map((k, i) => {
              const [name, rest] = splitLead(k.text, '. ');
              return (
                <div key={i}>
                  {name ? <p className="m-0 mt-1.5 text-[20px] font-bold">{name}</p> : null}
                  <p className="m-0 mt-0.5 text-ink-soft">{rest}</p>
                </div>
              );
            })
          ) : lines.length > 1 || l === 'Family' ? (
            <ul className="m-0 mt-2 flex list-none flex-col gap-2 p-0">
              {lines.map((k, i) => {
                const [name, rest] = splitLead(k.text, ', ');
                return (
                  <li key={i}>
                    {name ? <strong>{name}</strong> : null}
                    {rest}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="m-0 mt-1.5">{lines[0].text}</p>
          )}
        </section>
      ))}
    </>
  );
}

function Summary({ conv }: { conv: ConversationView }) {
  const t = useTranslations('story');
  const locale = useLocale();
  const { caseId, viewerId, readOnly } = useCase();
  const answer = useAnswer(caseId);
  const [changing, setChanging] = useState(false);
  const [note, setNote] = useState('');
  const cp = conv.summary;
  // A summary sent back with a change is set aside while the change is heard; it comes back rebuilt.
  if (!cp || (cp.status !== 'open' && cp.answer === 'change')) return null;
  if (cp.status !== 'open') {
    const who = conv.people.find((p) => p.id === cp.answered_by);
    return (
      <div className="flex flex-col gap-3 rounded-xl border-[1.5px] border-ledger bg-ledger-tint px-4 py-3.5">
        <p className="m-0 font-bold text-ledger-deep">
          {who && cp.answered_at
            ? t('confirmedBy', { name: personName(who, locale), date: dayMonth(cp.answered_at, locale) })
            : t('confirmedPlain')}
        </p>
        <ButtonLink href={`/cases/${caseId}/papers`} variant="primary" className="self-start">
          {t('addPapers')}
        </ButtonLink>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2.5">
      <p className="m-0 text-[18px] font-bold">{t('isRight')}</p>
      {changing ? (
        <form
          className="flex flex-col gap-2.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (!note.trim()) return;
            answer.mutate(
              { checkpointId: cp.id, answer: 'change', note: note.trim(), by: viewerId },
              { onSuccess: () => (setChanging(false), setNote(''), toast.success(t('changeSent'))) },
            );
          }}
        >
          <label className="flex flex-col gap-1.5 text-[15px] font-bold">
            {t('changeLabel')}
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              className="rounded-[10px] border border-rule-strong bg-sheet p-3 text-[17px] font-normal"
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <Button type="submit" disabled={answer.isPending || !note.trim()}>
              {t('sendChange')}
            </Button>
            <Button variant="secondary" disabled={answer.isPending} onClick={() => setChanging(false)}>
              {t('keepIt')}
            </Button>
            {answer.isPending ? <Spinner label={t('thinking')} /> : null}
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-3">
          <Button
            size="lg"
            disabled={answer.isPending || readOnly}
            onClick={() =>
              answer.mutate({ checkpointId: cp.id, answer: 'yes', by: viewerId }, { onSuccess: () => toast.success(t('confirmed')) })
            }
          >
            {t('yesRight')}
          </Button>
          <Button size="lg" variant="secondary" disabled={readOnly} onClick={() => setChanging(true)}>
            {t('change')}
          </Button>
        </div>
      )}
    </div>
  );
}

function Composer({
  onSend,
  pending,
  lang,
}: {
  onSend: (text: string, channel: 'voice' | 'text', seconds?: number) => void;
  pending: boolean;
  lang: string;
}) {
  const t = useTranslations('story');
  const ta = useTranslations('ask');
  const [text, setText] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const started = useRef(0);
  const voice = useListening(lang, (final) => {
    if (final) onSend(final, 'voice', Math.max(1, Math.round((Date.now() - started.current) / 1000)));
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!text.trim() || pending) return;
    onSend(text.trim(), 'text');
    setText('');
  }
  return (
    <form onSubmit={submit} className="mt-2 flex max-w-[760px] items-center gap-3 rounded-2xl border border-rule-strong bg-sheet p-2.5">
      <button
        type="button"
        aria-label={voice.listening ? t('stopListening') : t('tapToSpeak')}
        aria-pressed={voice.listening}
        onClick={() => {
          if (!voice.supported) {
            toast(ta('voiceUnavailable'));
            input.current?.focus();
            return;
          }
          if (voice.listening) voice.stop();
          else {
            started.current = Date.now();
            voice.start();
          }
        }}
        className={cls(
          'grid h-14 w-14 shrink-0 place-items-center rounded-full text-white',
          voice.listening ? 'animate-pulse-soft bg-debit' : 'bg-ledger hover:bg-ledger-deep',
        )}
      >
        {voice.listening ? <Square size={20} aria-hidden="true" /> : <Mic size={24} aria-hidden="true" />}
      </button>
      <label className="flex flex-1">
        <span className="sr-only">{t('typeLabel')}</span>
        <input
          ref={input}
          value={voice.listening ? voice.interim : text}
          onChange={(e) => setText(e.target.value)}
          placeholder={voice.listening ? t('listening') : t('typeHere')}
          className="min-h-12 w-full bg-transparent text-[18px] text-ink outline-none placeholder:text-ink-faint"
        />
      </label>
      <Button type="submit" variant="dark" disabled={pending || !text.trim()}>
        {t('send')}
      </Button>
    </form>
  );
}

export function StoryScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('story');
  const tc = useTranslations('common');
  const locale = useLocale();
  const { header, viewerId, readOnly } = useCase();
  const q = useConversation(caseId);
  const turn = useTurn(caseId);
  const end = useRef<HTMLDivElement>(null);
  const [pendingText, setPendingText] = useState<string | null>(null);
  const conv = q.data;
  const bare = header?.case.stage === 'intake';

  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;

  const spoken = conv?.languages?.[0]?.split('-')[0] ?? header?.case.languages?.[0]?.split('-')[0] ?? locale;
  const speaking =
    spoken && spoken !== 'en'
      ? t('speakingIn', { language: languageName(spoken, locale) })
      : t('speakingInPlain', { language: languageName(spoken, locale) });

  function send(text: string, channel: 'voice' | 'text', seconds?: number) {
    setPendingText(text);
    turn.mutate(
      { text, person_id: viewerId, lang: spoken || locale, channel, seconds },
      {
        onSettled: () => setPendingText(null),
        onSuccess: () => setTimeout(() => end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }), 80),
        onError: (e) => toast.error(tc('error', { message: e.message })),
      },
    );
  }

  const conversation = (
    <div className="flex min-w-0 flex-col gap-5">
      <h1 className={cls(h1Class, 'sm:text-[34px]')}>{t('title')}</h1>
      <p className="m-0 max-w-[40em] text-ink-soft">{t('lede')}</p>
      {conv ? (
        <ol aria-label={t('conversation')} className="m-0 mt-2 flex max-w-[760px] list-none flex-col gap-[18px] p-0">
          {conv.utterances.map((u) => (
            <Bubble key={u.id} u={u} conv={conv} locale={locale} />
          ))}
          {pendingText ? (
            <li className="flex justify-end">
              <div className="max-w-[620px] rounded-[14px] rounded-se-[4px] border border-rule bg-[#EAF2EC] px-[18px] py-3.5 opacity-70">
                <p className="m-0 text-[19px]">{pendingText}</p>
              </div>
            </li>
          ) : null}
          {turn.isPending ? (
            <li className="flex items-center gap-3.5">
              <AssistantMark />
              <Spinner label={t('thinking')} />
            </li>
          ) : null}
        </ol>
      ) : (
        <div className="flex max-w-[760px] flex-col gap-4">
          <Skeleton className="h-20 w-3/4" />
          <Skeleton className="ms-auto h-24 w-2/3" />
          <Skeleton className="h-16 w-3/5" />
        </div>
      )}
      <div ref={end} />
      {readOnly ? null : !conv?.summary || conv.summary.status === 'open' || conv.summary.answer === 'change' ? (
        <Composer onSend={send} pending={turn.isPending} lang={spoken || locale} />
      ) : null}
    </div>
  );

  const known = (
    <div className="flex flex-col gap-[22px]">
      <div>
        <h2 className="m-0 font-serif text-[26px] font-semibold">{t('known')}</h2>
        <p className="m-0 mt-1.5 text-[15px] text-ink-faint">{t('knownLede')}</p>
      </div>
      {conv ? <Known conv={conv} /> : <Skeleton className="h-60" />}
      {conv?.urgent.length ? (
        <section className="rounded-xl border-[1.5px] border-debit bg-debit-tint px-4 py-3.5">
          <h3 className="m-0 text-[16px] font-bold text-debit">{t('urgent')}</h3>
          {conv.urgent.map((u, i) => (
            <p key={i} className="m-0 mt-1.5">
              {u}
            </p>
          ))}
        </section>
      ) : null}
      {conv ? (
        <div className="mt-auto">
          <Summary conv={conv} />
        </div>
      ) : null}
    </div>
  );

  if (bare) {
    // A case still being told has nothing else to show yet, so it gets the whole page, as designed.
    return (
      <div className="flex min-h-screen flex-col bg-paper">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-rule bg-sheet px-4 py-3.5 sm:px-8">
          <div className="flex items-center gap-3">
            <Link href="/" className="flex items-center gap-3 text-ink no-underline" aria-label="AfterLedger">
              <Logo size={26} />
              <span className="font-serif text-[20px] font-semibold">AfterLedger</span>
            </Link>
            <span className="border-s border-rule ps-3 text-[16px] text-ink-faint">{t('starting')}</span>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[15px] text-ink-soft">{speaking}</span>
            <Link
              href="/"
              className={cls(
                'inline-flex min-h-11 items-center rounded-[10px] border border-rule-strong bg-sheet px-4 text-[15px] font-bold text-ink no-underline hover:text-ink',
              )}
            >
              {t('pause')}
            </Link>
          </div>
        </header>
        <div id="main" className="flex flex-1 flex-wrap">
          <main className="min-w-0 flex-[999_1_560px] px-4 py-8 sm:px-10">{conversation}</main>
          <aside
            aria-label={t('known')}
            className="flex min-w-0 max-w-full flex-[1_1_420px] flex-col border-s border-rule bg-sheet px-6 pb-10 pt-8 sm:px-8 lg:max-w-[520px]"
          >
            {known}
          </aside>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 text-[15px] text-ink-soft">{speaking}</p>
      <div className="grid items-start gap-8 xl:grid-cols-[minmax(0,1fr)_420px]">
        {conversation}
        <aside aria-label={t('known')} className="flex flex-col rounded-[14px] border border-rule bg-sheet px-6 py-7 xl:sticky xl:top-6">
          {known}
        </aside>
      </div>
    </div>
  );
}
