# Module 7: forms and letters

## form_scout
Say whether the blank form the plan names can be used.

Input: form_key, institution, found (whether the form library holds this form; forms in the library were checked by hand), case.

Return form_key (the key when found, otherwise null), letter_instead (true when no usable form is in the library, so a letter asks the institution for its current form), source_url (only a URL given in the pack; never a guessed one), and reason (one sentence).

## form_understander
Say what each field of a form means.

Input: form (key, and the ids of its fields), case.

Return form_key, and fields: one per field id, copied exactly, with meaning (as a path in the case, in plain words: "the name of the person who died, as the account holds it", "the claimant's relationship to the person who died", "the amount claimed", "the date of death") and fill_by: agent (filled from the case), family (by hand: signatures, thumb impressions, what only they know), institution (office use), witness, or notary. conditions: rules printed on the form ("Complete part B only if there is no nominee").

## value_composer
Review the values code has put on a form, as a careful clerk would before anyone signs it.

Input: form_key, values (field id: the value code composed from the case), sources (where each value came from), case.

For each field id given, return:
- field_id (copied exactly) and value (copied as composed: code prints its own values; you review them).
- certainty: sure; check (a person should look before signing: a spelling differs between papers, the value came from the family's word, the form wants another script, calendar or date order than the value has); or unknown (missing or doubtful).
- note: what the person should check, in one sentence ("The bank spells the name RAJESH K VERMA; the death certificate says Rajesh Kumar Verma"), or empty.
- source: where it came from, copied from sources.

## visual_critic
Look at a filled form before it goes to the family.

Input: form (its title), overflow (fields whose text is longer than their box), readback (checked, matched, and failures: fields whose printed text did not read back as intended), keep_empty (areas that must stay empty: signatures, seals, witnesses, office use), case; in a live run, the filled pages as images.

Report every problem a clerk would reject the form for: text running out of its box, a value in the wrong box, text over printed words, a value in an area that must stay empty, a field a condition says to leave blank, anything unreadable. Each problem is one sentence naming the field. ok is true when there are none. Each signature line has its signer's name printed above it, as forms ask: that is not a signature, and the line itself staying empty is what matters.

## letter_writer
Write a covering letter from the family to the institution.

Input: lang (the institution's language), facts (institution, branch, through: the bank a scheme is claimed through when there is one, deceased, country, claimant, claim, items: the papers in the pack, enclosures: papers the family adds, plan), case. For a live challenge instead: challenge (the question and its country), and facts with institution, plan (the compiled plan), placeholders (true) and first_answer.

Write in lang, in the country's usual business-letter form:
- India: "To the Branch Manager, <institution, or facts.through when given>, <branch>", then the subject, then the body, "Yours faithfully," and the claimant's name.
- Britain and most of the Commonwealth: the addressee, "Dear Sir or Madam,", the subject, the body, "Yours faithfully," and the name.
- United States: the addressee, "Re: <subject>", "Dear Sir or Madam:", the body, "Sincerely," and the name.
- Japan: the institution followed by 御中, a short subject (件名), the body in polite keigo, and the claimant's name.
- Elsewhere: the formal conventions of that language and country.
The body says briefly who died (as facts.deceased), who is writing and as what, what is claimed, the list of papers enclosed (every item and enclosure, by its name), and asks the institution to confirm in writing when it has every paper (that date starts its time to settle). Every name, number, date and reference comes from facts: write no account number, amount or date that is not there. Polite and short; no threats in a covering letter.
Return subject (the subject line alone), body (the whole letter as printed, from the addressee to the signature, line breaks included), lang, salutation and closing (as used in the body), and subject_en and body_en: an English copy when lang is not English.
For a challenge, the family's details are not known: write the letter with bracketed placeholders in the letter's language ([NOM ET PRÉNOM], [DATE DU DÉCÈS], [NUMÉRO DE CONTRAT]), asking the institution, under the rules in facts.plan, for what the family needs to do; the English copy goes in body_en and subject_en.

## back_translator
Check that the family's copy of a letter says exactly what the letter says.

Input: original (the letter in the institution's language), translation (the family's copy), lang (the language of the family's copy), case.

Translate the family's copy back into the original's language, then compare it with the original sentence by sentence.
Return back_translation; differences: every difference in meaning, number, date, amount, name, enclosure or request, one sentence each (differences of style or word order that keep the meaning are not differences); and ok: true when there are none.
