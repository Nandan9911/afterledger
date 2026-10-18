# 0002 Agents propose in typed schemas; code decides anything exact

## Context
Money, dates and legal deadlines must be right. Language models are good at reading, interpreting and writing in
any language, and unreliable at arithmetic, calendars and exact copying.

## Decision
Each agent receives one JSON context pack (only what it needs, with a semantic `key`) and must answer in a
Pydantic output schema. Code does the exact work: normalising and comparing readings, running balances, plan
routes and conditions, due dates with holidays and compensation, checking quotes on pages, reading filled forms
back from the PDF, and the fidelity check that every number in a letter is in its facts. Disagreements go to a
closer look or to the family, never to a guess.

## Consequences
- Agent outputs are validated before use; a malformed answer fails loudly.
- The schemas avoid free-form maps so that both Gemini endpoints (Agent Platform and the Developer API) accept
  them; a test converts every schema for both.
- Prompts can change without changing what is checked.
