# 0006 One API instance with write-through caches, for the prototype

## Context
A case is changed by background work (reading papers, planning, packs) while screens follow it live. Correctness
needs one writer per case at a time and fast reads.

## Decision
The API runs as one instance. Each store keeps a write-through cache in memory (every write reaches Firestore
before it returns) and each case has two asyncio locks: one that serialises writes, held by a family's action for
its short run and by a background job only for each write, and one that keeps one background job at a time on the
case (a job's writes are merged with changes made meanwhile; see architecture.md, "Changes and background work").
The answers to changes, kept for a repeat of the same change, are in memory too. On Cloud Run: maximum one
instance, CPU always allocated so background work and event streams continue between requests, and a one-hour
request timeout for streams and voice. Background work is named tasks with JSON payloads, run in process or
delivered by Cloud Tasks.

## Consequences
- Simple and correct for a prototype and a demonstration.
- To scale out: move case locks to Firestore leases (or Redis), make caches read-through with listeners, keep the
  answers to repeated changes in a shared store, deliver tasks through Cloud Tasks to any instance, and move the
  activity stream to Pub/Sub (see architecture.md).
