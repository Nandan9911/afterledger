# 0005 Two independent readings per paper; certainty from measured accuracy

## Context
Families' papers are photos and scans in many scripts, with handwriting, era dates and Indian digit grouping. A
single reading, and a model's own confidence, are not good enough when money moves on a digit.

## Decision
Reader A reads the OCR text layer; reader B reads the page image. Code normalises and compares every value; only
disagreements go to reader C, which looks at an enlarged crop and any check that applies (a running balance, an
identifier's format). Certainty labels come from a calibration table of measured accuracy by signals (agreed or
resolved, photo or scan, kind of value): "Sure" at 95% and above, "Please check" from 80%, below that the family is
asked.

## Consequences
- Most values need no person; the few that do become short questions with the crop beside them.
- The starting tables are set by hand, and the Evaluation page says so until `afterledger calibrate --write`
  refits them from cases a live model prepared. A bin changes only once it holds 20 items, to
  (right + 1) / (items + 2), so no label claims certainty from a small sample ([evaluation](../evaluation.md)).
