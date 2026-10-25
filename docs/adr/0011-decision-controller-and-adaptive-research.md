# 0011 The decision controller chooses the next move; research runs in rounds until nothing is open

## Context
Fixed rules decide too much: under them every value that is not sure becomes a question for the family, every open
lead a question, and research runs once, by two researchers over every question, whatever is found. A family's
effort and a research budget are better spent where they change the outcome: the controller in
`domain/decision.py` weighs measured probabilities, stakes and costs to act, ask, research, wait or hand to a
professional, and the adjudicator's list of points to research again says where more research is needed.

## Decision
With `AL_DECISIONS=controller` (the default):
- **Readings.** A value that is not sure is asked about only when the answer is worth the family's effort. A tier B
  value above its tier's bar (80%) is used as read, and Activity says so; a tier A value (it decides where money goes)
  that is not sure always goes to a person.
- **Leads.** The family hears of every lead; a lead whose institution's reply to a claim is already due waits for that
  reply, which will settle it.
- **Rules.** A claim moves money, so its plan is tier A: a stored plan resting on two verified sources and fresh rules
  is used as stored; one resting on a single source, or with a rule past its check date, is researched again before
  use (standard depth, or deep when it cites few rules). Offline there is nothing new to find, so the decision is
  traced and the stored plan used.

Every decision goes to the trace with its scores. `AL_DECISIONS=rules` uses the fixed rules instead.

With `AL_RESEARCH_MODE=adaptive` (the default), research runs in rounds. Each question group gets its own pair of
researchers (parallel workers, at most `AL_RESEARCH_PARALLEL` at once); after the citation check and the
adjudicator, what is still open (the researchers' gaps and the adjudicator's re-research list) is planned as the next
round's questions. It stops when nothing is open, after `AL_RESEARCH_MAX_ROUNDS` rounds, when the same questions stay
open two rounds running, or when the case's budget for model work is spent; the plan is then compiled from what was
found, and anything still open stays labelled as open. The depth comes from the controller: quick is one researcher,
one round; standard is both researchers, one round; deep is the full loop. Compiler check errors go back to the
compiler (up to three attempts), not to another research round: they are about the plan's structure, which another
search does not fix.

## Consequences
- Fewer questions reach the family; each one that does has a reason in Activity.
- Research costs more where it is needed and less where it is not. Offline fixtures show both stop rules: France
  finds a missing step in round 2 and still matches its gold plan; Egypt stops after round 2 with no official page
  and a plan that starts by asking the bank in writing.
- `AL_RESEARCH_MODE=fixed` runs a single pass by two researchers instead.
