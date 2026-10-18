# 0007 The family's language and the institution's language, never mixed

## Context
A family in Lucknow speaks Hindi to a bank that works in English; a son in Abu Dhabi asks about a bank in Cairo in
Arabic; a Japanese bank needs its own forms in Japanese with era dates.

## Decision
- The interface's words are in message catalogs (six languages, checked for missing keys and ICU syntax).
- The words the server writes are written in English and translated for the page's language through the
  translation port: a reviewed table first (hand-checked, used offline), then Cloud Translation live.
- Letters and forms are in the institution's language and format; the family's copy of a letter is translated,
  then translated back and compared by an agent, and every number is checked in both directions by code.
- Names, amounts, dates and quotes are never translated. A quote keeps its source's words, with a translation beside it.

## Consequences
- Offline, a sentence with no reviewed translation stays in English rather than being guessed.
- Each family member sees their own greeting and tasks in their own language.
