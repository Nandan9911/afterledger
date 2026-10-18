# 0003 Scripted responses for every agent offline, labelled as such

## Context
The flow has 44 agents. Building, testing and demonstrating it offline needs every agent to answer, repeatably.

## Decision
Each agent has an oracle (`oracles/`) that derives its answer from the demo family's truth file and the
knowledge fixtures, using the same context pack a live model gets. A ScriptedLlm adapter runs the oracles inside
ADK, so the workflows, tools and schemas are exercised exactly as live. A live run can record cassettes, which a
replay run plays back.

## Consequences
- Offline results are deterministic and the six families reach their expected state on every run.
- Offline results say nothing about agent quality. Every case records the kind of run that prepared it
  (scripted, gemini, replay), the app shows it, and the evaluation reports agent scores only for live runs.
