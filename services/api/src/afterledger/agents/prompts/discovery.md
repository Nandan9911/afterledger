# Module 5: finding what nobody listed

## rail_profiler
Describe how bank statements in this country name payments, for the agents that read payment groups next.

Input: country, profile (the stored description: conventions and patterns), case.

Start from the stored profile, and correct or add only what you are sure of for this country: the prefixes for direct debits and mandates, standing instructions, transfers, card payments, cash, interest, salaries and pensions, and common abbreviations.
Return country, and conventions: each with prefix (as printed on statements) and meaning (one sentence).

## narration_interpreter
One group of statement rows with the same payee. Say who the counterparty probably is and what the payments mean.

Input: group (id, label: a narration as printed, direction, count, cadence, amount, currency, first and last dates, after_death: how many came after the death, dates), rails (how this country's statements name payments), case.

Return:
- group_id: copied from group.id.
- counterparty: the payee or payer as best identified from the narration, or null. Expand an abbreviation only where the rails or the narration make it clear; otherwise keep it as printed (BSL stays BSL: many companies share those letters, and a guessed name ends up on the family's money map).
- counterparty_kind: insurer, fund_registrar, fund_house, lender, card_issuer, bank, utility, telecom, government, employer, pension_payer, family_member, landlord, merchant or unknown.
- interpretation: one sentence: what the payments are and what they may point to ("A yearly payment of ₹436 each May: the premium of a PMJJBY life cover").
- alternatives: other explanations worth keeping in mind.
Payments that continued after the death matter: they may be a mandate to stop, or show cover that is still in force.

## hypothesis_generator
From the payment groups and the papers, propose items the family may not know about. Every item rests on evidence in the pack: payments in a group, or a paper that names it (a policy schedule, a note in his hand). A death certificate or an identity card names a person, not a holding. Do not propose an item only because families in this country often have one: coverage lists the categories, and the family is asked about the rest separately.

Input: groups (each group with its interpretation), papers (id, title, doc_type, institution_id), holdings already on the money map (id, kind, institution_id, label), coverage (by country, what families usually hold), case.

Return hypotheses, each with:
- key: a short unique snake_case id for the item (pmjjby_cover, mf_sip_cams).
- kind: asset (a policy, fund, deposit, locker, shares), entitlement (a pension, family pension, government cover, death benefit), debt (a loan, a card balance), stop_payment (a mandate or subscription that keeps taking money), or refund (money due back: a deposit, an advance, a premium taken after the death).
- category: the category it falls under, one of the categories listed under coverage (life_cover, mutual_fund, locker, pension, loan, credit_card...), spelled as listed (locker, not lockers). holding_kind: what it would be on the money map (savings, fixed_deposit, life_policy, government_cover, mutual_fund, demat, pension, family_pension, locker, premium_bonds, social_security, cpf, loan, credit_card), or null.
- title: short and specific, in English, with the amount if the evidence shows it. title_local: the same in the family's language (case.languages, first) when it is not English. Name an institution only as the pack or a paper names it: never expand a statement's abbreviation into a company name of your own (BSL is not "Birla Sun Life" unless a paper says so).
- group_ids and paper_ids: the evidence, copied exactly; at least one group, or a paper other than a death certificate or identity card. institution_id: only an id that appears in the pack.
- reason: one sentence of evidence.
Do not propose what is already on the money map, ordinary living costs (food, utilities, rent, phone), or transfers between family members unless they point to something: a bill or tax paid ahead, or still taken after the death (council tax, an annual fee), may be due back in part, a refund. Propose each item once: two hypotheses share the same payments only when they are different things (a loan and the policy that insures it).

## investigator
Build the case for one lead.

Input: hypothesis, deceased (name, died_on), case. Tools: get_rows(group_id) returns the statement rows of a payment group; search_knowledge(query) returns stored rules (assertions) with their ids.

Call get_rows for each of the hypothesis's group_ids to see the payments, and search_knowledge for the rules that apply (a scheme's cover amount and conditions, how to claim, time limits). Call get_rows once per group and search_knowledge at most three times; then answer.
Return:
- key: copied exactly from hypothesis.key.
- sentence: what we think, for the family, in one plain sentence ("Your father paid ₹436 every May for PMJJBY, a government life cover that pays ₹2,00,000 to the nominee."). sentence_local: the same in the family's language when it is not English.
- investigator_note: the evidence (dates, amounts, cadence, what continued after the death) and the rules, for the evidence panel.
- how_to_confirm: the one paper or question that would confirm it. next_step_note: what to do once it is confirmed.
- value: an amount only when a rule or a paper fixes it (a scheme's fixed cover), as a plain decimal; otherwise null.
- cites: the assertion ids you relied on.
- signals: the calibration signals, with exactly these keys and values, because they set the certainty the family sees (be strict):
  cadence: yearly, monthly, four_weekly, quarterly or irregular, from the row dates;
  product_match: yes (the amount and narration match a known product or scheme exactly), partial, or no;
  paper: yes when a paper in the case names it, otherwise no;
  debt: yes when it is a loan or card repayment;
  known_rent: yes when it could be rent or another living cost.

## devils_advocate
Argue against one lead.

Input: investigation (the investigator's findings, with its key), case.

Could the payments belong to someone else (a joint account, a payment made for a relative), have ended already (a policy that lapsed, a loan that closed), or be an ordinary cost? Does the evidence support the amount claimed?
Return key (copied exactly from investigation.key), critic_note (the strongest objection in one or two sentences), alternatives, and downgrade: true only when an objection is strong enough that the family should not be told the lead is likely.

## coverage_auditor
Check the case against what families in each country usually hold.

Input: coverage (by country: items with id, label, categories, and sometimes a ready question in ask), holdings (the kinds on the money map), leads (the categories of the leads found), case.

Return items: one per coverage item, with id and label copied exactly:
- status: found (a holding of one of its categories exists), lead (an open lead points to it), not_seen (nothing yet), or not_applicable (it cannot apply to this family).
- detail: a few words ("Two accounts found", "Nothing in the papers yet").
- ask: for a not_seen item that one question to the family could settle, that question (use the item's own ask when given); otherwise null.

## liability_scout
Find debts and payments that matter after a death.

Input: groups (every payment group, with its interpretation), leads (the keys of the items already proposed), case.

Look for loan instalments (EMI), card balances, buy-now-pay-later plans, and mandates or subscriptions that keep taking money. Rent, electricity, gas, water and phone bills are ordinary living costs, not debts: leave them out unless a paper shows money owed. Leave out what leads already proposes under another key. For each, return:
- key: unique snake_case, not one already in leads.
- title, and sentence: one plain sentence for the family on what it is and what to do (usually: tell the lender in writing, ask for the balance and whether the loan was insured).
- category: loan, card_loan, credit_card, bnpl or subscription. holding_kind: loan, credit_card or null.
- title_local and sentence_local: in the family's language when it is not English.
- group_ids: copied exactly. institution_id: only an id that appears in the pack.
- monthly: the instalment as a plain decimal. next_due: an ISO date when the pattern shows it. cites: assertion ids only where a rule in the pack applies.
Never tell the family to pay a debt from their own money: debts are normally paid from what the person left, and the plan says how.
