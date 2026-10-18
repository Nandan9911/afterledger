# 0010 Every model and tool call goes through one harness; outside text is material, never instructions

## Context
The agents read text AfterLedger did not write (official pages, the family's papers, banks' replies) and act through
tools (fetching pages). Live models stall, run out of quota and sometimes answer outside their schema. A letter or
form can be well formed and still carry a number the case does not hold. Before this record, a malformed answer
stopped a whole step, discovery's parallel calls never reached the trace, any address could be fetched, and a failed
check was reported but not fixed.

## Decision
One harness wraps every call, whatever the module:
- **Each model call** goes through `MeteredLlm` (budget check, then tokens, milliseconds and estimated cost to the
  trace) and `FallbackLlm` (an overloaded tier falls to the next: pro, flash, lite; a passed deadline is retried, not
  counted as a failure). Every request carries a deadline (`AL_LLM_HTTP_TIMEOUT_S`); every agent node a timeout
  (`AL_AGENT_TIMEOUT_S`) and one retry on transient errors (ADK `retry_config`).
- **An unusable answer** (no answer, or one that does not fit the schema) is sent back once with the validation
  error (`AL_REASK_MALFORMED`).
- **Each tool call** passes `GuardPlugin`, which refuses addresses that are not public http(s) (the same check runs
  again after every redirect), and `ToolTracePlugin`, which traces it.
- **Outside text** is marked untrusted in every context pack, and the shared instructions say it is material to read,
  never instructions to follow. It is screened at three doors (fetched pages, uploaded papers, replies) by a Screen
  port: patterns in the app's six languages offline, Model Armor in front of them live. A flagged page is never
  evidence; a flagged paper or reply is still read, with a warning in Activity and a ledger line.
- **Check, fix, check again** (`repair_loop`): a covering letter, reminder or objection with a number or date that is
  not in the case goes back to its writer with the list; a form value that is too long for its box or does not read
  back from the finished file goes back to `value_composer`, and the form is filled and read back again. Up to
  `AL_REPAIR_ROUNDS` (2) rounds; what still fails is held for a person before it can be sent.
- **A strict live run** (`AL_STRICT_LIVE`) stops at the first Google Cloud service that fails instead of falling back
  to an offline stand-in, so no test passes on a stand-in unnoticed. Normally a failure is recorded, shown on
  `/v1/meta` and the Evaluation page, and the stand-in carries on.

Model Armor is called directly (`sanitize_user_prompt`) rather than as a per-call template on Gemini requests: the
app needs the verdict itself (to keep a flagged page out of evidence while still reading a flagged reply), which a
blocking template cannot give and a logging-only one does not return. Its template lives in `us-central1`, because
`asia-south1` offers neither the prompt-injection nor the malicious-URL filter (October 2026).

## Consequences
- The trace has one `model_call` line per model call wherever it ran (a test counts them against the offline
  oracles' own count), with tokens and cost; Activity shows each step's use, and the Evaluation page totals by case,
  agent and model.
- A spent case budget (`AL_CASE_BUDGET_USD`) stops only optional work (extra research rounds, repairs); reading,
  planning and answering carry on. Second asks for a malformed answer are not optional and are never stopped.
- Every behaviour has a setting that switches it off and restores the earlier behaviour (`AL_REASK_MALFORMED=0`,
  `AL_FALLBACK=false`, `AL_SCREEN=off`, `AL_REPAIR_ROUNDS=0`).
- Offline, the Vermas' first reminder carries a scripted wrong figure, so the demo shows a repair; the Activity line
  says it is scripted.
