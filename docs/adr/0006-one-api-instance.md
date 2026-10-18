# 0006 One API instance with write-through caches, for the prototype

## Context
A case is changed by background work (reading papers, planning, packs) while screens follow it live. Correctness
needs one writer per case at a time and fast reads.

## Decision
The API runs as one instance. Each store keeps a write-through cache in memory (every write reaches Firestore
before it returns) and each case has an asyncio lock. On Cloud Run: maximum one instance, CPU always allocated so
background work and event streams continue between requests, and a one-hour request timeout for streams and voice.
Background work is named tasks with JSON payloads, run in process or delivered by Cloud Tasks.

## Consequences
- Simple and correct for a prototype and a demonstration.
- To scale out: move case locks to Firestore leases (or Redis), make caches read-through with listeners, deliver
  tasks through Cloud Tasks to any instance, and move the activity stream to Pub/Sub (see architecture.md).
