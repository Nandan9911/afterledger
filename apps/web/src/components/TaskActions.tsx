'use client';
/** The buttons under a task: open the right screen, mark a lead, answer a question, or show why. */
import type { CiteView, TaskAction } from '@afterledger/contracts';
import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import { perform } from '@/lib/changes';
import { useAnswer, useMarkLead } from '@/lib/queries';
import { useCase } from './CaseShell';
import { buttonClass } from './ui';
import { Why } from './Why';

const LABELS: Record<string, string> = {
  'Review the reminder': 'reviewReminder',
  'Review the letter': 'reviewLetter',
  'Open the pack': 'openPack',
  'Open the plan': 'openPlan',
  'Open the plans': 'openPlans',
  'Open the leads': 'openLeads',
  'Tell AfterLedger': 'tellUs',
  'Add a paper': 'addPaper',
  'Check the papers': 'checkPapers',
  'We found it': 'foundIt',
  "We can't find it": 'cantFind',
  'Yes, it arrived': 'yesPaid',
  'No, not yet': 'noPaid',
  'Yes, record it': 'yesRecord',
  No: 'no',
  'Why?': 'why',
};

/** Where an 'open' action goes. Anything unknown opens Today rather than a page that does not exist. */
function hrefFor(caseId: string, kind: string, ref: string): string {
  const base = `/cases/${caseId}`;
  switch (kind) {
    case 'tracker':
      return `${base}/tracker`;
    case 'pack':
      return ref ? `${base}/packs/${ref}` : `${base}/packs`;
    case 'plan':
      return ref ? `${base}/plans/${ref}` : `${base}/plans`;
    case 'plans':
    case 'packs':
    case 'leads':
    case 'papers':
    case 'story':
    case 'money':
    case 'people':
    case 'sources':
    case 'ask':
      return `${base}/${kind}`;
    default:
      return `${base}/today`;
  }
}

export function TaskActions({
  actions,
  cites,
  primaryFirst = true,
}: {
  actions: TaskAction[];
  cites: Record<string, CiteView>;
  primaryFirst?: boolean;
}) {
  const t = useTranslations('task');
  const { caseId, viewerId, readOnly } = useCase();
  const mark = useMarkLead(caseId);
  const answer = useAnswer(caseId);
  const label = (a: TaskAction) => (LABELS[a.label] ? t(LABELS[a.label] as 'why') : a.label);

  return (
    <>
      {actions.map((a, i) => {
        const [kind, ref = ''] = a.target.split(/:(.*)/s);
        const look = primaryFirst && i === 0 && a.kind === 'open' ? 'primary' : 'secondary';
        if (kind === 'why') {
          return <Why key={i} ids={ref.split(',')} cites={cites} />;
        }
        if (a.kind === 'open') {
          return (
            <Link key={i} href={hrefFor(caseId, kind, ref)} className={buttonClass(look)}>
              {label(a)}
            </Link>
          );
        }
        if (a.kind === 'mark' && kind === 'lead') {
          return (
            <button
              key={i}
              type="button"
              disabled={mark.isPending || readOnly}
              title={readOnly ? t('sampleOnly') : undefined}
              className={buttonClass(i === 0 ? 'primary' : 'secondary')}
              onClick={() =>
                perform(mark, { leadId: ref, mark: (a.value as 'know' | 'check') ?? 'check', by: viewerId }, { done: () => t('marked') })
              }
            >
              {label(a)}
            </button>
          );
        }
        if (a.kind === 'answer' && kind === 'checkpoint') {
          return (
            <button
              key={i}
              type="button"
              disabled={answer.isPending || readOnly}
              title={readOnly ? t('sampleOnly') : undefined}
              className={buttonClass(i === 0 ? 'dark' : 'secondary')}
              onClick={() =>
                perform(
                  answer,
                  { checkpointId: ref, answer: a.value ?? '', by: viewerId },
                  { done: () => (a.value === 'no' ? t('answeredNo') : t('answered')) },
                )
              }
            >
              {label(a)}
            </button>
          );
        }
        return null;
      })}
    </>
  );
}
