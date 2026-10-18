# 0004 Rules are assertions with verbatim quotes, compiled into typed plans

## Context
Claim procedures differ by country, institution, amount, nominee, will and dispute. A plan that a model writes as
prose cannot be checked, compared or run for each family.

## Decision
A rule is an assertion: one plain sentence, the verbatim quote that supports it, the page (with tier, title and
the date it was checked) and a verification level. Plans are small typed programs compiled from assertions:
variables, documents, clocks and steps with conditions, each citing assertions. Code runs a plan on a case's facts
to get the route, the papers, the clocks and the questions only the family can answer. Every compiled plan must
pass a static check, and a plan for a situation with a hand-checked gold plan must route identically on every
combination of its variables.

## Consequences
- Every step a family sees links to the words it rests on, and the date they were checked.
- Research results join the knowledge base with their level and time to live; a better-verified plan is never
  overwritten by a weaker one.
- When no official source is found, the plan says it is unverified and starts by asking the institution in writing.
