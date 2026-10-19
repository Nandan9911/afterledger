'use client';
/**
 * People: questions about anyone seen only in the payments (checkpoint C3), every way the papers write the
 * deceased's name and why each is them, the family with their languages and roles, and who signs what in
 * each country. Names are never translated.
 */
import type { IdentityQuestion, PeopleView, Person, SignRow } from '@afterledger/contracts';
import { UserPlus } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useState, type FormEvent } from 'react';
import { toast } from 'sonner';
import { useCase } from '@/components/CaseShell';
import { Avatar, Button, ErrorNote, h1Class, h2Class, Sheet, Skeleton } from '@/components/ui';
import { asDate, cls, countryName, intl, languageName, personName } from '@/lib/format';
import { useAddPerson, useAnswer, useInvite, usePeople } from '@/lib/queries';

function rel(t: ReturnType<typeof useTranslations>, relationship: string | null | undefined, pronoun: string) {
  if (!relationship) return '';
  const key = relationship.toLowerCase();
  const word = t.has(`rel.${key}` as 'rel.wife') ? t(`rel.${key}` as 'rel.wife') : relationship;
  return t('relationOf', { pronoun, rel: word });
}

function QuestionCard({ q, view }: { q: IdentityQuestion; view: PeopleView }) {
  const t = useTranslations('people');
  const locale = useLocale();
  const { caseId, viewerId, readOnly } = useCase();
  const answer = useAnswer(caseId);
  const [other, setOther] = useState(false);
  const [value, setValue] = useState('');
  const cp = q.checkpoint;
  const asked = q.asked_of;
  const local = cp.question_local && cp.language.split('-')[0] !== 'en' ? cp.question_local : null;
  const question = local && cp.language.split('-')[0] === locale ? local : cp.question;
  const pay = (cp.payload ?? {}) as Record<string, string>;
  const done = () => toast.success(t('answered'));
  const label = (v: string, fallback: string) =>
    v === 'yes' && pay.relationship_guess ? t('yesRel', { rel: rel(t, pay.relationship_guess, view.pronoun) }) : fallback;
  return (
    <section
      aria-labelledby={`q-${cp.id}`}
      className="grid items-start gap-5 rounded-[14px] border-[1.5px] border-dashed border-pencil bg-pencil-tint px-6 py-[22px] [grid-template-columns:repeat(auto-fit,minmax(min(360px,100%),1fr))]"
    >
      <div>
        <p className="m-0 text-[14px] font-bold text-pencil">
          {asked ? t('questionFor', { name: personName(asked, locale), language: languageName(cp.language, locale) }) : t('question')}
        </p>
        <h2 id={`q-${cp.id}`} className="m-0 mt-1.5 font-serif text-[26px] font-semibold leading-tight">
          {question}
        </h2>
        {cp.why ? <p className="m-0 mt-2.5">{cp.why}</p> : null}
        {other ? (
          <form
            className="mt-4 flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!value.trim()) return;
              answer.mutate({ checkpointId: cp.id, answer: 'other', value: value.trim(), by: viewerId }, { onSuccess: done });
            }}
          >
            <label className="flex flex-col gap-1 text-[15px] font-bold">
              {t('whoIs', { name: q.person?.name ?? '' })}
              <input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={t('whoIsHint')}
                className="min-h-11 rounded-[10px] border border-rule-strong bg-sheet px-3 text-[17px] font-normal"
              />
            </label>
            <div className="flex gap-2.5">
              <Button type="submit" variant="dark" disabled={!value.trim() || answer.isPending}>
                {t('saveAnswer')}
              </Button>
              <Button variant="secondary" onClick={() => setOther(false)}>
                {t('back')}
              </Button>
            </div>
          </form>
        ) : (
          <div className="mt-4 flex flex-wrap gap-2.5">
            {cp.options.map((o, i) =>
              o.value === 'other' ? (
                <Button key={o.value} variant="secondary" disabled={readOnly} onClick={() => setOther(true)}>
                  {t('someoneElse')}
                </Button>
              ) : (
                <Button
                  key={o.value}
                  variant={i === 0 ? 'dark' : 'secondary'}
                  disabled={answer.isPending || readOnly}
                  onClick={() => answer.mutate({ checkpointId: cp.id, answer: o.value, by: viewerId }, { onSuccess: done })}
                >
                  {o.value === 'yes' ? label('yes', o.label) : o.value === 'no' ? t('no') : o.label}
                </Button>
              ),
            )}
          </div>
        )}
      </div>
      <figure className="m-0 overflow-hidden rounded-[10px] border border-rule bg-sheet">
        {q.rows.map((r) => (
          <div
            key={r.id}
            className="grid grid-cols-[84px_minmax(0,1fr)_auto] gap-2.5 border-b border-rule bg-[#FFF8D6] px-3.5 py-2.5 text-[15px] tabular last:border-b-0"
          >
            <span>
              {new Intl.DateTimeFormat(intl(locale), { day: '2-digit', month: '2-digit', year: '2-digit', timeZone: 'UTC' }).format(
                asDate(r.date)!,
              )}
            </span>
            <span dir="auto" className="truncate font-bold">{r.narration}</span>
            <span className="text-end font-bold">{r.amount.text}</span>
          </div>
        ))}
        {pay.evidence || pay.paper ? (
          <figcaption className="border-t border-rule px-3.5 py-2 text-[14px] text-ink-soft">
            {[pay.paper, pay.evidence].filter(Boolean).join('. ')}
          </figcaption>
        ) : null}
        {local ? (
          <p lang={cp.language} className="m-0 border-t border-rule px-3.5 py-2.5 text-[16px]">
            {local}
          </p>
        ) : null}
      </figure>
    </section>
  );
}

function NamesTable({ view }: { view: PeopleView }) {
  const t = useTranslations('people');
  const locale = useLocale();
  const d = view.deceased;
  if (!d || !view.names.length) return null;
  return (
    <Sheet className="overflow-x-auto px-[22px] py-5" aria-labelledby="names" tabIndex={0}>
      <h2 id="names" className={h2Class}>
        {t('namesTitle', { name: personName(d, locale), pronoun: view.pronoun })}
      </h2>
      <table className="mt-3 w-full border-collapse text-[15.5px]">
        <thead>
          <tr className="text-start text-ink-faint">
            <th scope="col" className="border-b-[1.5px] border-rule-strong py-2 pe-2 text-start font-semibold">
              {t('writtenAs')}
            </th>
            <th scope="col" className="border-b-[1.5px] border-rule-strong p-2 text-start font-semibold">
              {t('on')}
            </th>
            <th scope="col" className="border-b-[1.5px] border-rule-strong py-2 ps-2 text-start font-semibold">
              {t('whyItIs', { pronoun: view.pronoun })}
            </th>
          </tr>
        </thead>
        <tbody>
          {view.names.map((n, i) => (
            <tr key={i}>
              <td lang={n.form.lang ?? undefined} className="border-b border-rule py-2.5 pe-2 font-bold">
                {n.form.text}
                {n.form.reading ? <span className="block text-[14px] font-normal text-ink-faint">{n.form.reading}</span> : null}
              </td>
              <td className="border-b border-rule p-2.5">{n.seen_on_title}</td>
              <td className="border-b border-rule py-2.5 ps-2 text-ink-soft">{n.why || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Sheet>
  );
}

function PersonLine({ p, pronoun }: { p: Person; pronoun: string }) {
  const t = useTranslations('people');
  const locale = useLocale();
  const { caseId, viewerId, readOnly } = useCase();
  const invite = useInvite(caseId);
  const unknown = p.certainty === 'unknown' || !p.relationship;
  const place = [p.city, p.country ? countryName(p.country, locale) : null].filter((x, i, a) => x && a.indexOf(x) === i).join(', ');
  const langs = p.languages.map((l) => languageName(l, locale)).filter(Boolean);
  const speech = langs.length ? `${langs.join(t('and'))}${p.channel === 'voice' ? `, ${t('byVoice')}` : ''}` : '';
  const roles = p.roles
    .filter((r) => r !== 'deceased')
    .map((r) => (t.has(`roles.${r}` as 'roles.heir') ? t(`roles.${r}` as 'roles.heir') : r));
  const parts = [place, speech, ...roles].filter(Boolean);
  const name = p.name_en && p.name_en !== p.display_name ? `${p.display_name} (${p.name_en})` : p.display_name;
  return (
    <li className="grid grid-cols-[40px_minmax(0,1fr)_auto] items-start gap-3 border-t border-rule py-3">
      {unknown ? (
        <span
          aria-hidden="true"
          className="grid h-10 w-10 place-items-center rounded-full border-[1.5px] border-dotted border-blank font-bold text-blank"
        >
          ?
        </span>
      ) : (
        <Avatar person={{ initials: p.initials, color: p.color, name: p.display_name }} size={40} />
      )}
      <div className="min-w-0">
        <p className="m-0 font-bold">
          {name}
          {!unknown && p.relationship ? `, ${rel(t, p.relationship, pronoun)}` : ''}
        </p>
        <p className="m-0 mt-0.5 text-[15px] text-ink-soft">
          {parts.length ? `${parts.join('. ')}.` : ''} {unknown ? t('notKnownYet') : ''}
        </p>
      </div>
      <div className="text-end">
        {p.access === 'owner' ? <span className="text-[13px] font-bold text-ledger">{t('owner')}</span> : null}
        {p.access === 'member' ? <span className="text-[13px] font-bold text-ledger">{t('member')}</span> : null}
        {p.access === 'invited' ? <span className="text-[13px] font-bold text-pencil">{t('invited')}</span> : null}
        {p.access === 'none' && !unknown ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={invite.isPending || readOnly}
            onClick={() =>
              invite.mutate(
                { personId: p.id, by: viewerId },
                { onSuccess: () => toast.success(t('inviteDone', { name: personName(p, locale) })) },
              )
            }
          >
            {t('inviteOne')}
          </Button>
        ) : null}
      </div>
    </li>
  );
}

const SPOUSE = new Set(['wife', 'husband', 'spouse', 'partner']);
const CHILD = new Set(['son', 'daughter', 'child', 'stepson', 'stepdaughter']);
const PARENT = new Set(['father', 'mother', 'parent']);

const NODE_W = 124;
const NODE_H = 92;
const GAP_X = 40;
const ROW_GAP = 36;
const AVATAR_Y = 18; // the middle of a node's avatar, where a marriage line meets it

type Placed = { p: Person; note: string; x: number; y: number }; // x: the node's centre

/** The family drawn as an heir chart is drawn: the person who died, a spouse beside (a double line), parents above
 * the person who died, children below the couple. A picture for sighted readers only: the list under it says the
 * same to a screen reader. */
function FamilyTree({ view }: { view: PeopleView }) {
  const t = useTranslations('people');
  const d = view.deceased;
  const of = (set: Set<string>) => view.family.filter((p) => p.certainty !== 'unknown' && set.has((p.relationship ?? '').toLowerCase()));
  const spouses = of(SPOUSE);
  const children = of(CHILD);
  const parents = of(PARENT);
  if (!d || (!spouses.length && !children.length && !parents.length)) return null;
  const note = (p: Person) => rel(t, p.relationship, view.pronoun);
  const step = NODE_W + GAP_X;
  const coupleY = parents.length ? NODE_H + ROW_GAP : 0;
  const childY = coupleY + NODE_H + ROW_GAP;
  const nodes: Placed[] = [{ p: d, note: t('died'), x: 0, y: coupleY }];
  const lines: string[] = [];
  const marriage = (x1: number, x2: number, y: number) => {
    lines.push(`M${x1} ${y - 2.5}H${x2}`, `M${x1} ${y + 2.5}H${x2}`);
  };
  spouses.forEach((p, i) => {
    const x = (i + 1) * step;
    nodes.push({ p, note: note(p), x, y: coupleY });
    marriage(x - step + NODE_W / 2 - 22, x - NODE_W / 2 + 22, coupleY + AVATAR_Y);
  });
  if (parents.length) {
    const xs = parents.map((_, i) => (i - (parents.length - 1) / 2) * step);
    parents.forEach((p, i) => nodes.push({ p, note: note(p), x: xs[i], y: 0 }));
    for (let i = 1; i < xs.length; i++) marriage(xs[i - 1] + NODE_W / 2 - 22, xs[i] - NODE_W / 2 + 22, AVATAR_Y);
    const from = parents.length > 1 ? AVATAR_Y + 2.5 : NODE_H - 6;
    lines.push(`M0 ${from}V${coupleY}`);
  }
  if (children.length) {
    const mid = spouses.length ? step / 2 : 0; // from the marriage line, or from the person who died
    const spacing = NODE_W + 12;
    const xs = children.map((_, i) => mid + (i - (children.length - 1) / 2) * spacing);
    const bar = childY - ROW_GAP / 2;
    lines.push(`M${mid} ${spouses.length ? coupleY + AVATAR_Y + 2.5 : coupleY + NODE_H - 6}V${bar}`);
    if (xs.length > 1) lines.push(`M${xs[0]} ${bar}H${xs[xs.length - 1]}`);
    children.forEach((p, i) => {
      nodes.push({ p, note: note(p), x: xs[i], y: childY });
      lines.push(`M${xs[i]} ${bar}V${childY}`);
    });
  }
  const minX = Math.min(...nodes.map((n) => n.x)) - NODE_W / 2;
  const width = Math.max(...nodes.map((n) => n.x)) + NODE_W / 2 - minX;
  const height = Math.max(...nodes.map((n) => n.y)) + NODE_H;
  return (
    <figure aria-hidden="true" className="m-0 mt-3 overflow-x-auto pb-1">
      <div className="relative mx-auto" style={{ width, height }}>
        <svg
          className="absolute inset-0 text-rule-strong"
          width={width}
          height={height}
          viewBox={`${minX} 0 ${width} ${height}`}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
        >
          {lines.map((dd, i) => (
            <path key={i} d={dd} />
          ))}
        </svg>
        {nodes.map((n) => (
          <div
            key={n.p.id}
            className="absolute flex flex-col items-center gap-1 text-center"
            style={{ left: n.x - minX - NODE_W / 2, top: n.y, width: NODE_W }}
          >
            <Avatar person={{ initials: n.p.initials, color: n.p.color, name: n.p.display_name }} size={36} />
            <span className="break-words text-[14px] font-bold leading-tight">{n.p.display_name}</span>
            {n.note ? <span className="text-[12.5px] leading-tight text-ink-soft">{n.note}</span> : null}
          </div>
        ))}
      </div>
    </figure>
  );
}

function AddPerson({ onDone }: { onDone: () => void }) {
  const t = useTranslations('people');
  const tc = useTranslations('common');
  const { caseId, viewerId } = useCase();
  const add = useAddPerson(caseId);
  const [name, setName] = useState('');
  const [relationship, setRelationship] = useState('');
  const [city, setCity] = useState('');
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    add.mutate(
      {
        display_name: name.trim(),
        relationship: relationship.trim() || null,
        city: city.trim() || null,
        roles: [],
        languages: [],
        by: viewerId,
      },
      {
        onSuccess: () => {
          toast.success(t('addDone', { name: name.trim() }));
          onDone();
        },
      },
    );
  }
  const field = 'min-h-11 rounded-[10px] border border-rule-strong bg-sheet px-3 text-[17px] font-normal';
  return (
    <form
      onSubmit={submit}
      className="grid gap-3 rounded-[14px] border border-rule bg-sheet p-5 [grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr))]"
    >
      <label className="flex flex-col gap-1 text-[15px] font-bold">
        {t('name')}
        <input required value={name} onChange={(e) => setName(e.target.value)} className={field} />
      </label>
      <label className="flex flex-col gap-1 text-[15px] font-bold">
        {t('relationship')}
        <input value={relationship} onChange={(e) => setRelationship(e.target.value)} placeholder={t('whoIsHint')} className={field} />
      </label>
      <label className="flex flex-col gap-1 text-[15px] font-bold">
        {t('city')}
        <input value={city} onChange={(e) => setCity(e.target.value)} className={field} />
      </label>
      <div className="flex items-end gap-2.5">
        <Button type="submit" disabled={add.isPending || !name.trim()}>
          {t('add')}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          {tc('cancel')}
        </Button>
      </div>
    </form>
  );
}

function SignTables({ view }: { view: PeopleView }) {
  const t = useTranslations('people');
  const locale = useLocale();
  const byCountry = new Map<string, SignRow[]>();
  for (const r of view.signing) {
    const k = r.country ?? '';
    byCountry.set(k, [...(byCountry.get(k) ?? []), r]);
  }
  const fam = view.family;
  return (
    <>
      {[...byCountry.entries()].map(([country, rows]) => {
        const ids = fam
          .filter((p) => rows.some((r) => r.cells.some((c) => c.person_id === p.id && (c.signs || c.text !== 'Nothing to sign'))))
          .map((p) => p.id);
        const cols = fam.filter((p) => ids.includes(p.id) || rows.some((r) => r.cells.some((c) => c.person_id === p.id && c.signs)));
        const shown = cols.length ? cols : fam;
        return (
          <Sheet key={country} className="overflow-x-auto px-[22px] py-5" aria-labelledby={`signs-${country}`} tabIndex={0}>
            <h2 id={`signs-${country}`} className={h2Class}>
              {country && byCountry.size > 1 ? t('signsIn', { country: countryName(country, locale) }) : t('signsTitle')}
            </h2>
            <table className="mt-3 w-full min-w-[640px] border-collapse text-[15.5px]">
              <thead>
                <tr className="text-ink-faint">
                  <th scope="col" className="border-b-[1.5px] border-rule-strong py-2 pe-2 text-start font-semibold">
                    {t('claim')}
                  </th>
                  {shown.map((p) => (
                    <th key={p.id} scope="col" className="border-b-[1.5px] border-rule-strong p-2 text-start font-semibold">
                      {personName(p, locale)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.claim_id}>
                    <th scope="row" className="border-b border-rule py-2.5 pe-2 text-start font-normal">
                      {r.title}
                    </th>
                    {shown.map((p) => {
                      const c = r.cells.find((x) => x.person_id === p.id);
                      const depends = c && !c.signs && c.text.startsWith('Depends');
                      return (
                        <td key={p.id} className={cls('border-b border-rule p-2.5', c?.signs ? 'font-bold' : 'text-ink-faint')}>
                          {depends ? (
                            <span className="rounded-full border-[1.5px] border-dotted border-blank px-2 py-0.5 text-[14px] font-bold text-blank">
                              {t('depends')}
                            </span>
                          ) : c ? (
                            c.text
                          ) : (
                            t('nothingToSign')
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </Sheet>
        );
      })}
      {view.pending.length ? <p className="m-0 text-[15px] text-ink-soft">{t('pending', { claims: view.pending.join('; ') })}</p> : null}
    </>
  );
}

export function PeopleScreen({ caseId }: { caseId: string }) {
  const t = useTranslations('people');
  const { readOnly } = useCase();
  const q = usePeople(caseId);
  const [adding, setAdding] = useState(false);
  const view = q.data;
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className={h1Class}>{t('title')}</h1>
          <p className="m-0 mt-2 max-w-[46em] text-ink-soft">{t('lede')}</p>
        </div>
        <Button variant="secondary" disabled={readOnly} onClick={() => setAdding((v) => !v)} aria-expanded={adding}>
          <UserPlus size={18} aria-hidden="true" />
          {t('add')}
        </Button>
      </header>
      {adding ? <AddPerson onDone={() => setAdding(false)} /> : null}
      {!view ? (
        <Skeleton className="h-96" />
      ) : (
        <>
          {view.questions.map((qq) => (
            <QuestionCard key={qq.checkpoint.id} q={qq} view={view} />
          ))}
          <div className="grid items-start gap-6 [grid-template-columns:repeat(auto-fit,minmax(min(460px,100%),1fr))]">
            <NamesTable view={view} />
            <Sheet className="px-[22px] py-5" aria-labelledby="fam">
              <h2 id="fam" className={h2Class}>
                {t('family')}
              </h2>
              <FamilyTree view={view} />
              <ul className="m-0 mt-3 list-none p-0">
                {view.family.map((p) => (
                  <PersonLine key={p.id} p={p} pronoun={view.pronoun} />
                ))}
              </ul>
            </Sheet>
          </div>
          <SignTables view={view} />
        </>
      )}
    </div>
  );
}
