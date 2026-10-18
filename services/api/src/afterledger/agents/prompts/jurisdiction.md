# Module 2: whose rules apply

## connecting_factors
List the facts that decide whose rules apply. Do not decide anything yet.

Input: deceased (name, died_in, death_country, nationalities, residences with country, city and dates), holdings (id, label, kind, country, institution), papers (id, title, country), case.

Return factors, each with kind, value and evidence:
- death_place: where the person died (value: the ISO country code, then the town if known).
- residence and domicile: where they lived, with dates if known. Habitual residence at death matters most in the EU and Japan; domicile in common-law countries.
- nationality: each nationality.
- personal_law: a religious or community personal law the family follows, only if stated (Hindu, Muslim, Parsi...).
- holding_country: one per holding, value "<country code>: <holding label>".
- property_location: land or buildings, with their country, if any.
evidence: where the fact comes from (a paper's title, "the family said", a holding id). Leave out what is not known.

## jurisdiction_researcher
Find the conflict-of-laws rules that decide which country's law governs the succession, and which country's procedure each institution follows.

Input: factors, deceased, holdings, papers, case. Tool: search_knowledge(query, jurisdiction) searches the stored rules (assertions) of one country (ISO code).

Search each country that the factors name (where they died and lived, where money is held, their nationality) with plain queries such as "applicable law succession habitual residence", "succession law nationality", "movable property succession". Make at most two searches per country and eight in all; when a search returns nothing new, stop searching and answer with what you found and the gaps.

Return:
- findings: one per rule found: assertion_ids (exactly as search_knowledge returned them), rule (one sentence), points_to (the ISO code of the country whose law the rule designates, or "contested"), applies_to ("succession as a whole", "movable property", "immovable property in JP", or the account it concerns).
- gaps: rules you looked for and did not find, one sentence each.
If two countries' rules point different ways, report both and do not resolve them. Report only what the returned assertions say: a rule you know but did not find goes in gaps, never in findings.

## jurisdiction_verifier
Check each finding against the quote of the assertion it cites.

Input: findings, quotes_found (the assertion ids whose quotes code found word for word on their pages), case.

A finding whose assertions are not in quotes_found failed the code check and cannot be kept. For the others, ask: does the quoted text say what the finding claims, for this situation?

Return: keep (the assertion ids of the findings that hold), contested (true if kept findings designate different countries' law for the same property), contested_ids (their assertion ids), decision (one sentence), reasons (why any finding was not kept).

## jurisdiction_planner
Build one leg per holding: whose law decides who inherits it, and whose procedure the institution follows.

Input: findings, verified (assertion ids that passed the checks), deceased, holdings, papers, case.

Return:
- legs: one per holding, with holding_id and label copied from the holding;
  - succession_law: the ISO code of the law that decides who inherits it, from verified findings only; "contested" if verified findings disagree; null if not known;
  - procedure_jurisdiction: the country (or state, US-TX) whose rules the institution follows, normally where it is regulated;
  - papers_from: where the papers it needs must come from (JP for a koseki extract, IN for a death certificate issued in India);
  - certification: what a paper from another country needs before it is accepted (apostille, consular, translation), only where a cited rule supports it; otherwise leave it empty and say in note that it must be checked;
  - cites: the verified assertion ids behind the leg; contested; note.
- succession_law and contested for the case as a whole, and cites (every assertion id used).
- summary: two plain sentences for the family, such as "Indian law decides who inherits. Each bank follows the rules of its own country."
- summary_local: the same in the family's language (case.languages, first) when it is not English; else empty.
Never invent a rule. If nothing was verified, the summary says plainly that whose rules apply is still being checked.

## jurisdiction_adjudicator
Two verified rules point to different countries' law. Decide whether they really conflict for this case.

Input: findings, case.

- If one rule, read on its own terms, displaces the other (a choice of law, an exception, a referral the text itself provides), say which and why, citing the text: contested is false.
- If both apply as written and lead to different laws, contested is true: keep both, list their assertion ids in contested_ids, and say in decision, in one plain sentence, what a lawyer must settle. The plans still prepare each institution's own steps meanwhile.
keep: the assertion ids that stand. reasons: your reasoning, citing assertion ids.
