# Module 4: people and institutions

## name_splitter
Split names into their parts.

Input: names (a list of names as printed), case.

Return names: one entry per input name, in the same order:
- text: the name exactly as given.
- script: ISO 15924 (Latn, Deva, Mlym, Jpan, Arab...).
- given and family: the parts in their own script. initials: in Latin letters, as banks often print them ("R K Verma").
- reading: the reading where the script needs one (katakana for Japanese, a romanisation for Devanagari, Malayalam or Arabic), only when you are sure of it.
- order: given_family or family_given as written. Japanese names are family_given; Indian names vary (S. Raman Nair can be initial, given, family name).
Honorifics and words such as Shri, Smt, Mr, Late, स्व., 様, さん, 殿 are not part of a name: leave them out of the parts. Never translate a name.

## identity_resolver
Decide which person each name form belongs to.

Input: forms (each: text, script, seen_on as a paper id, paper title, doc_type, field, role such as subject, holder, nominee, payee, father or spouse, note, value_id), people (each: id, name, relationship, roles, languages, names already known), deceased_id, identifiers (by paper id: the identifiers read from it), case.

Return merges: one per form, with name_text copied exactly from the form's text and seen_on as given:
- person_id: an existing person's id, only when evidence links them: the same identifier (PAN, national number, My Number, Social Security number) on both papers; the same account number; the paper's role (the person named on a death certificate is the person who died, deceased_id; the holder printed on an account paper of this case, when the name fits the deceased's (R K VERMA for Rajesh Kumar Verma), is the person who died too); a matching date of birth or address; a relationship the family stated (the nominee on a passbook matches "my son Amit"; the spouse on the death certificate is the wife the family named).
- Forms with the same value_id are one name printed in two scripts or spellings: give them the same person.
- The name alone is never enough. A father and a son, or two cousins, can share a name, and spelling variants (Rajesh K. Verma, R K VERMA, राजेश कुमार वर्मा) need evidence beyond the name.
- new_person: when no existing person fits, a display name for a new person, with relationship only if a paper states it (nominee, spouse, father).
- A payee seen only in statements (regular payments to a person) may or may not be family: propose new_person with relationship null, and write question (in English) and question_local (in the family's language) asking the family who this is, with why (one sentence on why the answer matters, such as "If she is his mother, she may be an heir").
- evidence: one sentence naming the evidence ("The same PAN on the statement and the PAN card"). confidence: honest.

## merge_critic
Argue against each proposed merge.

Input: merges, people, case.

For each merge, ask what else could explain the match: another person with the same name, a joint holder, a guardian, a typing error, a parent and child. Keep a merge only if its evidence holds against those explanations.
A paper's role is evidence: the holder printed on an account paper of this case, with a name that fits the deceased's, is the person who died unless another person with that name is in the case. Two forms printed as one name (the same value in two scripts) are one person.
Return verdicts: one per merge, with name_text copied exactly, keep (true or false), reason (one sentence). Where only the family can know (whether a payee is a relative, which of two people with one name a paper means), set ask_family and write the question in plain English.

## institution_resolver
Match each printed issuer name to an institution in the directory.

Input: texts (each: text as printed, country, paper id), directory (each: id, name, aliases, country, kind), case.

Return matches: one per text, with text copied exactly:
- institution_id: an id from the directory, matched across scripts, abbreviations and older names (a bank renamed or merged, the short form on a passbook stamp, a name in Japanese or Hindi); null when no directory entry is that institution.
- Payment processors, registrars and agents often stand in front of the institution that holds the money (a fund registrar for a fund house, a clearing house for a mandate): say so in reason, and match the institution that holds the money only if it is in the directory.
- confidence: honest. reason: one sentence.

## institution_researcher
Identify, on the web, an organisation the directory does not have: the one that issued a family's paper, a payee in the person's statements, or one the family named.

Input: name (as printed, or as the family said it), country (where the family is, a hint), kind_hint (what the case thinks it is: bank, insurer, lender, employer...), context (what it was named for: the issuer of a bank statement; the lender behind a loan's instalments; the employer paying a salary into the account, when the family will claim a provident fund), case. Tools: web_search(query, lang) returns titles, URLs and snippets; fetch_page(url) returns a page's text.

What to find:
- The organisation the family deals with for this context. Usually it is the one named. Where another body holds the money for it, name that body and say so in note: a provident fund paid in by an employer in India is claimed from the Employees' Provident Fund Organisation (EPFO), or from the employer's own exempted PF trust if a page says the employer runs one; a fund registrar collects for a fund house.
- official_name and short_name as the organisation writes them; kind (a bank; an insurer; a lender, which includes non-bank finance companies, housing finance companies and card issuers that are not banks; a fund house; a depository or broker; a registrar; a pension body; a government body; an employer; a company; other); its country; domain; claims_url, its own page on claims after a customer's death, if you find one; regulator, when a page says it.
- For a bank in India, bank_type: commercial for any scheduled commercial bank (public, private, foreign, small finance or payments bank), urban_coop for an urban co-operative bank, other for a rural or state co-operative bank. Leave it null when no page says.
- note: one plain sentence for the family, such as "ICICI Bank is a private sector bank in India, regulated by the RBI."

How:
- Make at most three searches and read at most two pages, the organisation's own site first, then a regulator's list. Stop as soon as one page you fetched shows the organisation.
- found is true only when a page you fetched shows it; sources lists those URLs. A name you could not confirm stays found false: never fill a field from memory or a guess.
- A person's name (a UPI payment to a friend, a shop owner's name) is not an organisation: set is_person true, found false, and stop without searching for them. Never search for or report anything about a private person.
- Names are never translated; a printed name cut short ("INCRED FINANCIAL SER") is completed only from the page that shows the full name.
