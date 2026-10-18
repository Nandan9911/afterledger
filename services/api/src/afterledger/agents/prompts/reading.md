# Module 3: reading papers

## triage
Say what this paper is.

Input: paper (id, title, pages, origin: upload, photo or seed), lines (the first OCR text lines, each with an id), case; in a live run, the image of the first page.

Return:
- doc_type: one of death_certificate, bank_statement, passbook, fixed_deposit_receipt, insurance_policy, demat_holding_statement, mutual_fund_statement, identity_card, pension_letter, payslip, card_statement, loan_statement, cpf_statement, koseki, heir_certificate, will, letter, handwritten_note; or a short snake_case name for anything else.
- country: the ISO code of the issuing country (from the issuer, address, currency, language and layout).
- languages (BCP-47) and scripts (ISO 15924) printed on it, the main one first.
- issuer: the institution or authority that issued it, exactly as printed, in its own script.
- is_photo: true for a photograph of paper rather than a scan or a digital file. quality: good, fair or poor (blur, glare, folds, cut-off edges).
- title: a short English title for the family's list ("Narmada Bank passbook", "Death certificate").
- known_schema: true when doc_type is one of the types listed above.

## schema_inducer
No stored schema fits this kind of paper. List the fields worth reading on it.

Input: doc_type, lines (OCR text lines), case.

For each field: name (snake_case), label (short English), kind (text, name, date, amount, identifier, choice), required (the paper is of no use without it), tier: A (money moves on it: holders' names, account and policy numbers, amounts, balances), B (it shapes the plan: dates, nominees, relationships, the issuer), C (context).
Prefer the standard names used for other papers when they fit: deceased_name, date_of_death, account_holder, account_number, policy_number, sum_assured, nominee, closing_balance, issue_date, registration_number.
has_transactions: true when the paper has rows of dated payments.

## reader_a
Read the paper from its OCR text lines only. You are one of two independent readers: code compares your reading with one made from the page images, so read exactly what is printed.

Input: schema (id, fields with name, label, kind, required and tier, has_transactions), lines (id, text, page, box as fractions of the page), paper, case.

Return:
- fields: one entry per schema field found: field (the schema name), value exactly as printed (keep the script, digit grouping, currency marks and era dates such as 令和5年3月1日: code normalises them), line_ids (the lines it came from), page, confidence. Leave out a field that is not on the paper; never fill it from elsewhere or by inference.
- rows (when the schema has transactions): every transaction row in order, numbered from 1: date as printed, narration as printed, amount as printed, direction (debit for money out, credit for money in, from the column it is printed in), balance as printed (null if none), page and line_ids. Do not skip, merge or reorder rows; brought-forward and opening-balance lines are not rows.
- notes: anything that made reading uncertain (a cut-off line, a value that could belong to two labels).

## reader_b
Read the paper from the page images only. You are the second independent reader: do not guess what an OCR text layer would say.

Input: schema, pages (id, number, width, height), paper, case, and the page images.

Return the same fields and rows as the first reader would: the same field names, rows numbered from 1 in the same order, values exactly as printed, with page numbers. line_ids stay empty.
Look closely at handwriting and at digits in photographs, where 3 and 8, 1 and 7, 6 and 8, 0 and 9, and 5 and 6 are easily confused; check each amount against the running balance where there is one. Read every script in its own script (Devanagari, Malayalam, Japanese, Arabic).

## reader_c
Two independent readings of one value disagree. Decide the value by looking closely.

Input: field, label, kind, readings (the two readings), line_ids, lines (the OCR text around the value), running_balance (for a statement row: the previous balance and the row), paper, case; in a live run, an enlarged crop of the page around the value, then the whole page.

- Look at the crop. Use any check that applies: a running balance (the previous balance plus a credit, or minus a debit, equals the printed balance), the format of an identifier (PAN, IFSC, a policy number's length), a total, the same value printed twice on the paper.
- value: the value exactly as printed, or null when you cannot tell. Never choose a reading because the other looks unusual.
- reason: one sentence on what you saw ("The last digit is an 8: both loops are closed in the crop").
- confidence: your honest probability that the value is right. The certainty the family sees comes from measured accuracy, not from this number.
- field: copied from the input.
