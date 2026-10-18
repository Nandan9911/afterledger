# 0009 Demo cases built offline once, imported where the app runs

## Context
The six demo cases take about three minutes to play through their timelines. A cloud deployment should not replay
them through a live model at every start (cost, time, and results that differ from the scripted stories).

## Decision
`afterledger build-demos` seeds the six families offline, writes each case (its records and files) as a bundle,
and runs the offline evaluation on them. The API restores a demo case from its bundle when the case is missing:
by copying folders with the local store, or by writing every record through the store port and uploading every
file at the same path with Firestore and Cloud Storage. The container image builds the bundles; locally,
`python tools/dev.py demos` does.

## Consequences
- The app starts with all six cases ready, and a reset takes seconds.
- Bundled cases say they were prepared with scripted responses. "Watch it from the first paper" replays a family's story with
  whatever model the service runs, so a live run shows live results.
