'use client';
/** The buttons under a task: open the right screen, mark a lead, answer a question, or show why. */
import type { CiteView, TaskAction } from '@afterledger/contracts';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { Link } from '@/i18n/navigation';
import { useAnswer, useMarkLead } from '@/lib/queries';
import { useCase } from './CaseShell';
import { buttonClass } from './ui';
import { Why } from './Why';

const LABELS: Record<string, string> = {
  'Review the reminder': 'reviewReminder',
  'Review the letter': 'reviewLetter',
  'Open the pack': 'openPack',
  'Open the plan': 'openPlan',
  'We found it': 'foundIt',
  "We can't find it": 'cantFind',
  'Why?': 'why',
};

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
  const { caseId, viewerId } = useCase();
  const mark = useMarkLead(caseId);
  const answer = useAnswer(caseId);
  const label = (a: TaskAction) => (LABELS[a.label] ? t(LABELS[a.label] as 'why') : a.label);

  return (
    <>
      {actions.map((a, i) => {
        const [kind, ref] = a.target.split(/:(.*)/s);
        const look = primaryFirst && i === 0 && a.kind === 'open' ? 'primary' : 'secondary';
        if (kind === 'why') {
          return <Why key={i} ids={ref.split(',')} cites={cites} />;
        }
        if (a.kind === 'open') {
          const href =
            kind === 'tracker'
              ? `/cases/${caseId}/tracker`
              : kind === 'pack'
                ? `/cases/${caseId}/packs/${ref}`
                : kind === 'plan'
                  ? `/cases/${caseId}/plans/${ref}`
                  : `/cases/${caseId}/today`;
          return (
            <Link key={i} href={href} className={buttonClass(look)}>
              {label(a)}
            </Link>
          );
        }
        if (a.kind === 'mark' && kind === 'lead') {
          return (
            <button
              key={i}
              type="button"
              disabled={mark.isPending}
              className={buttonClass(i === 0 ? 'primary' : 'secondary')}
              onClick={() =>
                mark.mutate(
                  { leadId: ref, mark: (a.value as 'know' | 'check') ?? 'check', by: viewerId },
                  { onSuccess: () => toast.success(t('marked')) },
                )
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
              disabled={answer.isPending}
              className={buttonClass(i === 0 ? 'dark' : 'secondary')}
              onClick={() =>
                answer.mutate({ checkpointId: ref, answer: a.value ?? '', by: viewerId }, { onSuccess: () => toast.success(t('answered')) })
              }
            >
              {a.label}
            </button>
          );
        }
        return null;
      })}
    </>
  );
}
