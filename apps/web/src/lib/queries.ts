'use client';
/**
 * Every read and every action the screens use, as TanStack Query hooks. Everything about one case lives
 * under the key ['case', caseId], so one 'something changed' event from the API refreshes what is open.
 */
import type { AccountIn, AccountPatch, AnswerIn, ChallengeIn, PersonIn, PersonPatch, SignInIn, SignUpIn } from '@afterledger/contracts';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, unwrap, uploadPapers } from './api';
import { useAuth } from './auth';
import { forgetCase } from './mycases';

const c = (id: string) => ['case', id] as const;

// ---- meta and demo ------------------------------------------------------------------------------

export function useMeta() {
  return useQuery({ queryKey: ['meta'], queryFn: () => unwrap(api().GET('/v1/meta')), staleTime: 60_000 });
}

export function useFamilies() {
  return useQuery({
    queryKey: ['families'],
    queryFn: () => unwrap(api().GET('/v1/demo/families')),
    refetchInterval: (q) => (q.state.data?.some((f) => !f.seeded) ? 3000 : false),
  });
}

export function useResetDemo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => unwrap(api().POST('/v1/demo/{key}/reset', { params: { path: { key } } })),
    onSuccess: () => qc.invalidateQueries(),
  });
}

/** The sample cases, and the signed-in account's own (where sign-in is required); without accounts, the cases this
 * browser started or opened (`ids`). */
export function useCases(ids: string[] | null) {
  const who = useAuth((s) => s.user?.id ?? null);
  return useQuery({
    queryKey: ['cases', ids, who],
    queryFn: () => unwrap(api().GET('/v1/cases', { params: { query: { ids: ids?.length ? ids.join(',') : undefined } } })),
    enabled: ids !== null,
  });
}

/** Deletes a case of the family's own, with its papers (a demo case is reset instead). */
export function useDeleteCase() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (caseId: string) => unwrap(api().DELETE('/v1/cases/{case_id}', { params: { path: { case_id: caseId } } })),
    onSuccess: (_out, caseId) => {
      forgetCase(caseId);
      return qc.invalidateQueries({ queryKey: ['cases'] });
    },
  });
}

export function useStartCase() {
  return useMutation({
    mutationFn: (body: { lang: string; starter_name?: string | null }) => unwrap(api().POST('/v1/cases', { body })),
  });
}

// ---- one case -----------------------------------------------------------------------------------

export function useCaseHeader(caseId: string, viewer?: string | null) {
  return useQuery({
    queryKey: [...c(caseId), 'header', viewer ?? null],
    queryFn: () =>
      unwrap(api().GET('/v1/cases/{case_id}', { params: { path: { case_id: caseId }, query: { viewer: viewer ?? undefined } } })),
    placeholderData: keepPreviousData,
  });
}

export function useConversation(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'conversation'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/conversation', { params: { path: { case_id: caseId } } })),
  });
}

export function useTurn(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      text: string;
      person_id?: string | null;
      lang?: string | null;
      channel?: 'voice' | 'text';
      seconds?: number | null;
    }) => unwrap(api().POST('/v1/cases/{case_id}/turns', { params: { path: { case_id: caseId } }, body })),
    onSuccess: (out) => {
      qc.setQueryData([...c(caseId), 'conversation'], out.conversation);
      qc.invalidateQueries({ queryKey: c(caseId) });
    },
  });
}

export function useToday(caseId: string, viewer?: string | null) {
  return useQuery({
    queryKey: [...c(caseId), 'today', viewer ?? null],
    queryFn: () =>
      unwrap(api().GET('/v1/cases/{case_id}/today', { params: { path: { case_id: caseId }, query: { viewer: viewer ?? undefined } } })),
    placeholderData: keepPreviousData,
  });
}

export function useRefreshToday(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (viewerId: string | null) =>
      unwrap(api().POST('/v1/cases/{case_id}/today', { params: { path: { case_id: caseId } }, body: { viewer_id: viewerId } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function usePapers(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'papers'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/papers', { params: { path: { case_id: caseId } } })),
    refetchInterval: (q) => (q.state.data?.reading ? 2500 : false),
  });
}

export function usePaper(caseId: string, paperId: string | null | undefined) {
  return useQuery({
    queryKey: [...c(caseId), 'paper', paperId],
    enabled: !!paperId,
    queryFn: () =>
      unwrap(api().GET('/v1/cases/{case_id}/papers/{paper_id}', { params: { path: { case_id: caseId, paper_id: paperId! } } })),
    placeholderData: keepPreviousData,
  });
}

export function useUpload(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { files: File[]; by?: string | null; together?: boolean }) =>
      uploadPapers(caseId, args.files, { by: args.by, together: args.together }),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useMoney(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'money'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/money', { params: { path: { case_id: caseId } } })),
  });
}

export function useLeads(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'leads'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/leads', { params: { path: { case_id: caseId } } })),
  });
}

export function useMarkLead(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { leadId: string; mark: 'know' | 'check' | 'not_relevant'; value?: string; note?: string; by?: string | null }) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/leads/{lead_id}/mark', {
          params: { path: { case_id: caseId, lead_id: args.leadId } },
          body: { mark: args.mark, value: args.value ?? null, note: args.note ?? null, by: args.by ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function usePeople(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'people'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/people', { params: { path: { case_id: caseId } } })),
  });
}

export function useAddPerson(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PersonIn) => unwrap(api().POST('/v1/cases/{case_id}/people', { params: { path: { case_id: caseId } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useUpdatePerson(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { personId: string; body: PersonPatch }) =>
      unwrap(
        api().PATCH('/v1/cases/{case_id}/people/{person_id}', {
          params: { path: { case_id: caseId, person_id: args.personId } },
          body: args.body,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useInvite(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { personId: string; by?: string | null }) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/people/{person_id}/invite', {
          params: { path: { case_id: caseId, person_id: args.personId } },
          body: { by: args.by ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function usePlans(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'plans'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/plans', { params: { path: { case_id: caseId } } })),
  });
}

export function usePlan(caseId: string, planId: string | null | undefined) {
  return useQuery({
    queryKey: [...c(caseId), 'plan', planId],
    enabled: !!planId,
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/plans/{plan_id}', { params: { path: { case_id: caseId, plan_id: planId! } } })),
    placeholderData: keepPreviousData,
  });
}

export function useApprovePlan(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { planId: string; by?: string | null }) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/plans/{plan_id}/approve', {
          params: { path: { case_id: caseId, plan_id: args.planId } },
          body: { by: args.by ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function usePacks(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'packs'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/packs', { params: { path: { case_id: caseId } } })),
  });
}

export function usePack(caseId: string, packId: string | null | undefined) {
  return useQuery({
    queryKey: [...c(caseId), 'pack', packId],
    enabled: !!packId,
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/packs/{pack_id}', { params: { path: { case_id: caseId, pack_id: packId! } } })),
    placeholderData: keepPreviousData,
  });
}

export function useApprovePack(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { packId: string; by?: string | null }) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/packs/{pack_id}/approve', {
          params: { path: { case_id: caseId, pack_id: args.packId } },
          body: { by: args.by ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useTracker(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'tracker'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/tracker', { params: { path: { case_id: caseId } } })),
  });
}

export function useLetterAction(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: {
      corrId: string;
      action: 'approve' | 'sent';
      by?: string | null;
      channel?: 'letter' | 'email' | 'branch' | 'online' | 'phone';
    }) => {
      const path = { case_id: caseId, corr_id: args.corrId };
      if (args.action === 'approve')
        return unwrap(api().POST('/v1/cases/{case_id}/letters/{corr_id}/approve', { params: { path }, body: { by: args.by ?? null } }));
      return unwrap(
        api().POST('/v1/cases/{case_id}/letters/{corr_id}/sent', {
          params: { path },
          body: { by: args.by ?? null, channel: args.channel ?? 'letter', on: null },
        }),
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useDraftLetter(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { claimId: string; kind: 'reminder' | 'objection' | 'grievance'; request?: string; by?: string | null }) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/claims/{claim_id}/letters', {
          params: { path: { case_id: caseId, claim_id: args.claimId } },
          body: { kind: args.kind, request: args.request ?? null, by: args.by ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useRecordReply(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { claimId: string; title: string; text: string; on?: string | null }) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/claims/{claim_id}/replies', {
          params: { path: { case_id: caseId, claim_id: args.claimId } },
          body: { title: args.title, text: args.text, on: args.on ?? null, channel: 'letter' },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useClaimEvent(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: {
      claimId: string;
      event: string;
      on?: string | null;
      amount?: string | null;
      note?: string | null;
      by?: string | null;
    }) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/claims/{claim_id}/events', {
          params: { path: { case_id: caseId, claim_id: args.claimId } },
          body: { event: args.event, on: args.on ?? null, amount: args.amount ?? null, note: args.note ?? null, by: args.by ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useShare(caseId: string) {
  return useMutation({
    mutationFn: (args: { claimId: string; days?: number; by?: string | null }) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/claims/{claim_id}/share', {
          params: { path: { case_id: caseId, claim_id: args.claimId } },
          body: { days: args.days ?? 31, by: args.by ?? null },
        }),
      ),
  });
}

export function useSources(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'sources'],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/sources', { params: { path: { case_id: caseId } } })),
  });
}

export function useActivity(caseId: string, limit = 200) {
  return useQuery({
    queryKey: [...c(caseId), 'activity', limit],
    queryFn: () => unwrap(api().GET('/v1/cases/{case_id}/activity', { params: { path: { case_id: caseId }, query: { limit } } })),
  });
}

export function useCheckpoints(caseId: string) {
  return useQuery({
    queryKey: [...c(caseId), 'checkpoints'],
    queryFn: () =>
      unwrap(api().GET('/v1/cases/{case_id}/checkpoints', { params: { path: { case_id: caseId }, query: { status: 'open' } } })),
  });
}

export function useAnswer(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { checkpointId: string } & AnswerIn) =>
      unwrap(
        api().POST('/v1/cases/{case_id}/checkpoints/{checkpoint_id}/answer', {
          params: { path: { case_id: caseId, checkpoint_id: args.checkpointId } },
          body: { answer: args.answer, value: args.value ?? null, note: args.note ?? null, by: args.by ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: c(caseId) }),
  });
}

export function useAsk(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { question: string; person_id?: string | null; lang?: string | null; channel?: 'voice' | 'text' }) =>
      unwrap(api().POST('/v1/cases/{case_id}/ask', { params: { path: { case_id: caseId } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: [...c(caseId), 'conversation'] }),
  });
}

// ---- knowledge, challenge, desk, evaluation ---------------------------------------------------------

export function useAssertion(id: string | null | undefined) {
  return useQuery({
    queryKey: ['assertion', id],
    enabled: !!id,
    queryFn: () => unwrap(api().GET('/v1/knowledge/assertions/{assertion_id}', { params: { path: { assertion_id: id! } } })),
    staleTime: 10 * 60_000,
  });
}

export function usePresets() {
  return useQuery({ queryKey: ['presets'], queryFn: () => unwrap(api().GET('/v1/challenges/presets')), staleTime: 10 * 60_000 });
}

export function useStartChallenge() {
  return useMutation({
    mutationFn: (body: ChallengeIn) => unwrap(api().POST('/v1/challenges', { body })),
  });
}

/** The questions the signed-in account asked, newest first, running ones included (the public site lists nobody
 * else's). Refreshed every few seconds while one is still researching. */
export function useMyQuestions() {
  const who = useAuth((s) => s.user?.id ?? null);
  return useQuery({
    queryKey: ['challenges', who],
    queryFn: () => unwrap(api().GET('/v1/challenges')),
    enabled: who !== null,
    refetchInterval: (q) => (q.state.data?.some((c) => c.status === 'running') ? 5000 : false),
  });
}

export function useChallenge(runId: string, live: boolean) {
  return useQuery({
    queryKey: ['challenge', runId],
    queryFn: () => unwrap(api().GET('/v1/challenges/{run_id}', { params: { path: { run_id: runId } } })),
    refetchInterval: live ? 1500 : false,
  });
}

export function useSaveChallenge() {
  return useMutation({
    mutationFn: (runId: string) => unwrap(api().POST('/v1/challenges/{run_id}/case', { params: { path: { run_id: runId } } })),
  });
}

export function useDesk(token: string) {
  return useQuery({
    queryKey: ['desk', token],
    queryFn: () => unwrap(api().GET('/v1/desk/{token}', { params: { path: { token } } })),
    retry: false,
  });
}

export function useEvaluation() {
  return useQuery({
    queryKey: ['eval'],
    queryFn: () => unwrap(api().GET('/v1/eval')),
    refetchInterval: (q) => ((q.state.data as { running?: boolean } | undefined)?.running ? 3000 : false),
  });
}

export function useUsage() {
  return useQuery({ queryKey: ['eval', 'usage'], queryFn: () => unwrap(api().GET('/v1/eval/usage')), staleTime: 15_000 });
}

export function useRunEvaluation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api().POST('/v1/eval/run')),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['eval'] }),
  });
}

// ---- accounts -----------------------------------------------------------------------------------

/** Signing in or up keeps the account and its token in this browser, and drops anything cached for someone else. */
export function useSignIn() {
  const qc = useQueryClient();
  const signIn = useAuth((s) => s.signIn);
  return useMutation({
    mutationFn: (body: SignInIn) => unwrap(api().POST('/v1/auth/signin', { body })),
    onSuccess: (out) => {
      signIn(out.token, out.user);
      qc.clear();
    },
  });
}

export function useSignUp() {
  const qc = useQueryClient();
  const signIn = useAuth((s) => s.signIn);
  return useMutation({
    mutationFn: (body: SignUpIn) => unwrap(api().POST('/v1/auth/signup', { body })),
    onSuccess: (out) => {
      signIn(out.token, out.user);
      qc.clear();
    },
  });
}

export function useSignOut() {
  const qc = useQueryClient();
  const signOut = useAuth((s) => s.signOut);
  return () => {
    signOut();
    qc.clear();
  };
}

export function useAccounts(enabled: boolean) {
  return useQuery({ queryKey: ['accounts'], queryFn: () => unwrap(api().GET('/v1/admin/users')), enabled });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AccountIn) => unwrap(api().POST('/v1/admin/users', { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useEditAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AccountPatch }) =>
      unwrap(api().PATCH('/v1/admin/users/{user_id}', { params: { path: { user_id: id } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => unwrap(api().DELETE('/v1/admin/users/{user_id}', { params: { path: { user_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}
