# Evaluation

The evaluation answers two questions: does the exact part of AfterLedger always give the right answer, and are
the agents better than simple code would be? It has three parts, and the Evaluation page in the app shows all
three with the date of the run and the kind of run that made it.

1. **Checks that must always pass.** Exact comparisons on the six demo families and the shared knowledge. Any
   failure is a bug.
2. **Agents against their baselines.** Each module's agents against plain code doing the same job. The baselines
   are computed in every run; the agents are measured only on cases a live model prepared.
3. **Coverage by country.** How far each country's papers, leads, rules, forms and letters have been worked
   through, from hand-checked (gold) to live only.

## Running it

```bash
python tools/dev.py eval                                  # offline, on the six demo cases; prints the report
cd services/api && uv run afterledger eval --no-seed      # evaluate the cases already in the store
```

Each run writes `.data/eval/latest.json` (under `AL_STATE_DIR`), which the Evaluation page shows; the page's
**Run the evaluation** button runs the same thing in a separate process (`POST /v1/eval/run`). A container image
carries the offline evaluation of the demo cases it was built with, shown until a new run.

## 1. Checks that must always pass

| Check | What is compared | Offline result | Live run |
|---|---|---|---|
| Routes against the hand-checked answer | Every claim's plan, run on the case's facts, against the route worked out by hand in the family's truth file (`expected_route`) | 29 of 29 match | 28 of 28 match; 1 not compared (below) |
| Deadlines and compensation | Calendar, business-day (with the country's holidays) and month clocks, and each delay formula, against hand-worked vectors in `data/eval/clock_vectors.yaml` (8 due dates, 4 compensation amounts) | 12 of 12 exact | 12 of 12 exact |
| Form read-back | Every value read back from every filled form's PDF against what was meant to go there | 258 of 258 values | 258 of 258 values |
| Letter fidelity | Every number and date in every covering letter, reminder and objection must be in the case's facts | 0 unsupported numbers in 14 letters, after 1 repair round | 0 in 14 letters, after 5 repair rounds |
| Quotes on their pages | Every quote in the shared knowledge found word for word on its source page | 222 of 222 found | 320 of 320 found, 98 of them from live research |

A letter whose check finds a number that is not in the case goes back to its writer with the problem, up to two
rounds (`AL_REPAIR_ROUNDS`); the result counts the letters after their repairs, and says how many rounds there were.
In the demo, the Vermas' first reminder is drafted with one wrong figure on purpose, so the check is seen at work.

The routes check uses the claims in each family's file (`data/families/<key>/family.yaml`), which name the route
a person worked out from the regulator's rules. When the stored plan a family's file names is an unverified draft,
a live run researches the institution's own procedure in its place, and that claim is named in the result and not
compared: the hand-worked route is for the draft's steps, and the research check covers the new plan. In the live
run this was the Vermas' family pension, researched from the UP Treasury's pages. The read-back check counts what the
forms module wrote when it filled each form.

The quotes check reads each quote's source page through the same fetcher the app uses. Each quote is checked on its
page's saved snapshot in `data/knowledge/pages`; when the snapshot lacks it (a snapshot can be an excerpt that holds
only the sentences a person copied), a live run reads the page as it is now, which is what research checked its
rules on. A page with no snapshot that cannot be read is reported as such, not as a missing quote. Offline, only the
snapshots exist, as in `afterledger audit-knowledge`.

## 2. Agents against their baselines

| Module | Test set | Measure | Baseline (plain code) | Baseline, offline | Live run |
|---|---|---|---|---|---|
| Reading papers | 61 pages of the families' papers, in 4 scripts | Fields read exactly right | Text-layer rules: a label, then its value | 79 of 175 fields (45%) | Baseline 53 of 175 (30%) on Document AI's lines. Agents 167 of 169 (99%), none wrong, 2 left unread; paper type right 32 of 33 |
| Finding what nobody listed | 21 hidden items in 6 families | Items found, and false leads raised | The country's payment patterns, matched against each recurring payment | 13 of 21 found, 3 false | Baseline the same. Agents 17 of 20 found, 11 false leads; 1 more was already on a paper |
| Researching rules | 4 questions in 2 countries (France, Egypt) | Points matching the gold plan, with quotes found | Sentences containing a search word, from the first official page found | 7 of 8 gold points, in 7 sentences kept | Baseline 0 to 2 of 8 over three runs (the first page a live search returns varies). Agents: 1 of 3 France runs matched the gold plan; 4 of 5 runs found official sources; 15 rounds in 5 runs, at most 3 |
| Conversation | The questions in the families' timelines, and five typed questions | Answers grounded in the case's sources | none | | 7 of 7 answers grounded |

The test set is the six demo families: their papers (`papers.yaml`) are generated from what every field says,
and each family's file (`family.yaml`) says which payments point to hidden holdings. Research is measured against hand-checked
gold plans (`data/eval/gold_plans`) using the questions in `data/eval/research_fixtures`.

A value is read right when it is the field as printed or the field's own value, both of which the truth file gives
(`Our reference: LMP/07714/C` and `LMP/07714/C`), and a name is right with or without the honorific printed beside
it (様, Mrs, LATE, स्व.). The baseline is scored the same way.

A compiled plan matches a gold plan when it sends the family the same way in every test case (every combination of
the gold plan's questions, and each number either side of every threshold). A model names its steps its own way, in
the family's language, so each compiled step is first paired with the gold step it stands for: the same kind of step
by the same person, taken in mostly the same cases or naming the same body (AGIRA, Ciclade). A step that waits on a
question the gold plan does not ask counts as on the route. Where the sources leave the day itself open ("less than
10 years", "more than 10 years"), the gold plan says so (`unclear_at_threshold`) and the threshold day is not
tested. A plan that does not match is shown on the challenge page with the number of cases it agreed on, and is not
stored.

### Why the agent column is empty offline

Offline, every agent answers from a scripted response derived from the family's truth file (ADR 0003). Scoring
those answers against the same truth file would print numbers that measure nothing, so the report shows **After a
live run** instead, and the page says why. Every case records the kind of run that prepared it (`scripted`,
`gemini` or `replay`); only `gemini` and `replay` cases are measured.

A replay plays back the responses recorded in a live run. If a call has no recorded response, the replay answers
it from the scripted oracle, so that case is labelled `scripted` again, says so in its activity feed, and is left
out of the measurement.

### Measuring the agents

1. Start a live run ([runbooks/live-run.md](runbooks/live-run.md)).
2. Prepare the demo cases with the live model: **Watch it from the first paper** (in a demo case's menu, or
   **Watch** on the home page) replays a family's story from its first paper, or `uv run afterledger seed`
   rebuilds all six (add `--snapshot` to keep them for fast resets).
3. Run the evaluation on those cases: `uv run afterledger eval --no-seed`, or the button on the Evaluation page.
   - Reading: fields right, wrong, and sent to the family as a question; paper types right.
   - Discovery: hidden items found, and false leads. An item a paper the family uploaded already put on the money
     map is counted apart: discovery rightly does not propose it again.
   - Research: live challenge runs that matched a gold plan, and runs that found official sources.
   - Conversation: answers grounded in the case's sources.
4. Refit the certainty tables from the same cases (below).

## Certainty labels and calibration

A read value's label (Sure, Please check, or a question for the family) and a lead's label (sure, likely,
possible, unlikely) come from `data/knowledge/calibration.json`, never from a model's own confidence (ADR 0005).
Each module's table maps signals to a measured probability:

- reading: whether the two readings matched, were resolved by a closer look, came from one path only or still
  disagree; whether the paper is a photo; the kind of value; whether a running balance checked it;
- discovery: the payment's cadence, whether it matches a product, whether a paper or a family note supports it;
- people: what links a name to a person (an identifier, an account number, the same paper and role, the name
  only, or the family).

The starting tables are set by hand and the Evaluation page says so (**Certainty labels: starting values set by
hand**). After the demo cases have been prepared by a live model:

```bash
cd services/api
uv run afterledger calibrate            # what a refit would change, bin by bin
uv run afterledger calibrate --write    # write data/knowledge/calibration.json; restart the API to use it
```

Each read value is compared with the truth of the paper it was read from, and each lead with the family's hidden
items. An item falls into the most specific bin its signals match (the same lookup the app makes); items that
match no bin refit the module's base rate. A bin's value changes only when it holds at least 20 items, and then to
`(right + 1) / (items + 2)`, so no bin claims certainty from a small sample; smaller bins keep their starting
value and the report says how many items they had. Values a family confirmed are not counted (a person read
them, not the agents). The people table keeps its starting values: six families have too few names to measure
it. After a write, the page shows the date and the number of live cases the tables were measured on.

Six families are a small test set: on the reading table they fill two or three bins, and the discovery table
needs more families before any bin reaches 20 leads. The refit is there so that every label the app shows is
traceable to a measurement or plainly marked as a starting value.

The tables in `calibration.json` are fitted on the six cases of the live run. Two reading bins held enough items:
values both readings matched on a paper with a text layer (107 of 107 right, measured at 0.991 against a starting
value of 0.995) and on a photo (37 of 37, 0.974 against 0.985). Both are above the 0.95 needed for **Sure**. Three
other reading bins held 8 to 10 items each, all right, and keep their starting values, and the rest held none; the
discovery table had 19 leads across all its bins and keeps its own. Each bin's `n` in `calibration.json` says how many items it had, and the Evaluation page says
which values were measured.

## 3. Coverage by country

Each country gets a level for its papers, leads, rules, forms and letters.

| Level | For rules | For papers, leads, forms and letters |
|---|---|---|
| gold | A plan checked by hand against the regulator's own pages | |
| verified | Two researchers found each rule separately (or an adjudicator settled them, or a claim's outcome confirmed them), with every quote on its page | Shown working on the demo families (below) |
| one source | One researcher found it; its quotes are on their pages | |
| contested | Two countries' laws disagree; both are shown and nothing is decided by a model | |
| live only | Researched when a family needs it, in a live run | Not in the test set yet |

- **Papers**: a demo family has papers from the country, read in the test run.
- **Leads**: a hidden item in a demo family is held at an institution in the country.
- **Rules**: the level most of the country's plans have; the detail line lists every level, the weakest
  included. A country only in the research test set counts as gold when it has a hand-checked gold plan (France);
  when no official page describes the procedure (Egypt), its rules are left to a live run.
- **Forms**: the form library has the country's forms (each rendered and read back by the knowledge audit).
- **Letters**: a covering letter, reminder or objection was written for a claim in the country and checked.

## 4. Model use

Every model call is metered (`MeteredLlm`): tokens in, out and thinking, time, and an estimated cost from
`data/knowledge/prices.yaml` (a route on a non-global endpoint at its markup). The Evaluation page's **Model use**
section shows the totals per case (kept with each case's activity, so they survive a restart) and, since the
service started, per agent and per model (`GET /v1/eval/usage`). Offline, scripted answers use no tokens, and the
section says so. A replayed call uses none either; its trace line says `replayed`.

`AL_CASE_BUDGET_USD` sets what a case may spend on optional model work. At four fifths of it, the case's activity
says so; at the limit, extra research rounds and repairs stop and say why, while reading papers and answering
questions carry on. Research into a country's rules that any case can use (how a kind of claim is made there) is
not charged to the case that started it; each such run stops at `AL_RESEARCH_BUDGET_USD` ($2.50 unless set).

The live evaluation runs (Google Cloud project `afterledger`) made 5,492 answered model calls, priced by
the meter at $42.29: Gemini 3.6 Flash 2,861 calls ($18.70), Gemini 3.8 Flash 1,977 on the global and `us`
endpoints ($15.92), Gemini 3.1 Pro 319 ($7.34) and Gemini 3.5 Flash-Lite 335 ($0.32). Gemini 3.8 Flash was slow
during the run (a median of 6.4 seconds on the global endpoint and 8.9 on `us`, with calls of several minutes), so the
harness asked its peers, as [decision record 0012](adr/0012-routes-hedging-and-resumable-recordings.md) describes,
and Gemini 3.6 Flash answered more than half the calls. The meter counts the answer that was used: the 524 requests that lost such a race were cancelled
and are not in the total, nor are Google Search grounding, Document AI, Cloud Translation, check grounding or Model
Armor. Google's invoice is the record.

## 5. The live run

AfterLedger ran end to end on real Gemini on 15 October 2026, in the Google Cloud project `afterledger`, with every
Google Cloud service live and strict (`AL_STRICT_LIVE=true`: a failing service stops the run): Firestore, Cloud Storage,
BigQuery, Document AI, Cloud Translation, check grounding, Google Search grounding and Model Armor, and Gemini Live
for a spoken question in English and in Japanese. Each family's story was played from its first paper, both
challenge countries were researched, and five typed questions were asked of the Tanakas' case. The results above
are the evaluation of the six cases that run prepared.

| Family | Countries | Time | Model calls | Model cost |
|---|---|---|---|---|
| Tanaka | Japan, Germany | 15 min | 128 | $0.77 |
| Clarke | United Kingdom | 15 min | 118 | $0.84 |
| Morales | United States | 21 min | 200 | $1.22 |
| Nair | Singapore, India | 23 min | 197 | $1.24 |
| Sato | Japan | 23 min | 131 | $1.17 |
| Verma | India | 43 min | 338 | $1.95 |
| France and Egypt challenges | | 31 min | 296 | $3.21 |
| Five typed questions (Tanaka) | | 2 min | 27 | $0.08 |

The France challenge takes about 18 minutes live and Egypt about 12; a family's story takes 15 to 45 minutes, the
largest part of it reading papers from their images.

Real Gemini and the live web raise problems that scripted answers cannot. The kinds that matter most, and how
AfterLedger meets each (every one has a test):

- **Answers that leave things out.** Gemini may omit what a schema leaves optional; in the live run the investigator
  left out its calibration signals in 99 of 185 answers, which would leave those leads at the base rate. Signals and
  cites are required fields.
- **Model judgements that code makes or checks.** A will the family never mentioned, a contested succession law
  stated as settled, a lead guessed from a death certificate alone: code keeps only what the case supports, and
  writes the words for a contested law itself.
- **The same thing found twice.** Every paper runs discovery again, and Gemini can name the same holding under a new
  key, or one person in two scripts. Leads and people are matched to what was found before.
- **Reading.** A reader can copy `2,915.40` as `2.915.40`, put withdrawals in the deposit column, or read a photo's
  balances one row off: the running balance settles direction and value, and a third reader checks once a paper.
- **The harness.** A slow endpoint, a `500` from Gemini 3.1 Pro, a dropped connection, a tool loop of more than a
  hundred calls, and a retried step that answers after a failed first try: each is retried, hedged or stopped.
- **The evaluation itself.** A read value is compared with both its printed line and its own value, a quote is
  checked on its live page when the saved snapshot is an excerpt, a researched plan is not compared with the route
  of the draft it replaced, and a page that cannot be read is tried a second time, and again after five minutes,
  rather than kept as missing.

Limits of this run:

- The six families are both the test set and the set the certainty tables were fitted on. The numbers show the
  system working on them, not how it does on families it has not seen.
- One run each, and answers vary from run to run, so these numbers are a sample, not a rate. Discovery missed three
  hidden items, among them the Clarkes' council tax refund, which none of four live runs found; Sato's Minato policy
  was already on a paper, so it is counted apart.
- Discovery raised 11 false leads to the baseline's 3: the agents look further and are wrong more often. Each
  carries its certainty label, and nothing goes to an institution until the family approves it.
- Research matched the France gold plan in 1 of 3 live runs; the plans from the other two were not stored. Egypt has
  no gold plan to compare with.
