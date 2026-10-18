# Module 6: the plan, in plain words

## explainer
Explain one claim's plan to the family in plain words.

Input: claim (id, title, amount, currency), plan (key, title, route: the ids of the steps that apply now, questions: answers still needed, steps: id and text), lang (the reader's language), facts (what is already known: name, what it asks, value), claimant (who signs), people (the family: name, relationship, roles), case.

Return:
- summary: one or two sentences in English: what is being claimed (and how much, when known), which route applies and why, in the family's terms ("Because Amit is the nominee and the deposits are under ₹15 lakh, the bank can pay him on a simple claim form."). If plan.questions is not empty, the route depends on answers still to come: say so, and describe what applies whatever the answer.
- steps: one entry per step in plan.route, in order: step_id copied exactly; text: one short sentence naming who does what, with the family's names ("Amit signs the claim form"); who: the person or institution; cites: empty (code attaches each step's own sources).
- summary_local and text_local: the same in lang when lang is not English, natural and respectful as a native speaker writes; empty when lang is English.
- lang: copied from lang.
Use only the plan and the facts given: add no step, document or rule.
