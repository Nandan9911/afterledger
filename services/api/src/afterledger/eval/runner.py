"""The evaluation: checks that must always pass, and every agent against a simpler baseline.

Checks are exact and run on the six demo families and the shared knowledge: routes against the
hand-checked route in each family's truth file, deadlines and compensation against hand-worked
vectors (data/eval/clock_vectors.yaml), values read back from every filled form, numbers in every
letter, and every quote found word for word on its page.

Baselines are plain code (text-layer rules for reading, the country's payment patterns for discovery,
keyword sentences from the first official page for research), so their scores are real in any run.
Agent scores mean something only when the agents are real models: in an offline run the agents
replay scripted responses, and the report says so instead of printing numbers that measure nothing.

    afterledger eval [--no-seed] [--json report.json]      (or POST /v1/eval/run)
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from ..config import get_settings
from ..domain.checks import normalise, quote_in_page
from ..domain.plan_language import Clock
from ..domain.plan_runtime import compensation, due_date, per_day, route
from ..modules.common import knowledge
from ..modules.discovery import narration_key
from ..modules.reading import norm_value, schema_for
from ..modules.research import gold_plans
from ..oracles.reading import _baseline_reading
from ..seed import snapshot
from ..seed.families import family_keys, load_family
from ..seed.seeder import SeedReport, _Run, demo_case_id, seed_family

COUNTRY_NAMES = {"IN": "India", "JP": "Japan", "GB": "United Kingdom", "US": "United States", "SG": "Singapore",
                 "DE": "Germany", "FR": "France", "EG": "Egypt"}


def _check(cid: str, name: str, ok: bool, result: str, shows: str, detail: list[str] | None = None) -> dict:
    return {"id": cid, "name": name, "ok": ok, "result": result, "shows": shows, "detail": detail or []}


# ---------------------------------------------------------------------------------------------
# the demo cases
# ---------------------------------------------------------------------------------------------

async def _ensure_cases(deps: Any, *, seed: bool) -> dict[str, str]:
    """The six demo cases, from their snapshots when they exist, otherwise seeded now."""
    out = {}
    for key in family_keys():
        case_id = demo_case_id(key)
        exists = await deps.cases.get_case(case_id) is not None
        if seed or not exists:
            if not seed and snapshot.has_snapshot(deps, key) and await snapshot.restore(deps, key):
                out[key] = case_id
                continue
            await seed_family(deps, key, story_day=deps.settings.story_day)
            snapshot.save(deps, key)
        out[key] = case_id
    return out


# ---------------------------------------------------------------------------------------------
# checks that must always pass
# ---------------------------------------------------------------------------------------------

async def check_routes(deps: Any, cases: dict[str, str]) -> dict:
    total = ok = 0
    misses: list[str] = []
    replaced: list[str] = []
    k = knowledge(deps)
    for key, case_id in cases.items():
        fam = load_family(key, deps.settings.story_day)
        run = _Run(deps=deps, fam=fam, case_id=case_id, report=SeedReport(key=key, case_id=case_id,
                                                                            story_day=fam.story_day))
        for tc in fam.get("claims", []):
            expected = tc.get("expected_route_today", tc.get("expected_route"))
            if expected is None:
                continue
            claim_id = await run.claim_id(tc["id"])
            claim = await deps.cases.get(case_id, "claims", claim_id) if claim_id else None
            cp = await deps.cases.get(case_id, "plans", claim.plan_id) if claim and claim.plan_id else None
            stored = await k.plan(tc["plan"]) if cp and tc.get("plan") and cp.plan_key != tc["plan"] else None
            if stored is not None and (stored.unverified or stored.level == "draft"):
                # the stored plan was an unverified draft, so a live run researched the institution's own procedure in
                # its place: the hand-worked route is for the draft's steps, and the research check covers the new plan
                replaced.append(f"{key}/{tc['id']}")
                continue
            total += 1
            plan = await k.plan(cp.plan_key) if cp else None
            got = route(plan, {n: v for n, v in cp.facts.items() if not n.startswith("__")}) if plan and cp else []
            if sorted(got) == sorted(expected):
                ok += 1
            elif claim is None:
                misses.append(f"{key}/{tc['id']}: no claim in the case, expected {expected}")
            else:
                misses.append(f"{key}/{tc['id']}: got {got}, expected {expected}")
    result = f"{ok} of {total} match" + (f"; not compared, as research replaced a draft plan: {', '.join(replaced)}"
                                         if replaced else "")
    return _check("routes", "Routes against the hand-checked answer", total > 0 and ok == total, result,
                  "Every claim's plan, run on the case's facts, takes the route worked out by hand in the family's "
                  "truth file (RBI, SEBI, IRDAI, Japanese, UK, US and Singapore rules)", misses)


def _load_vectors() -> dict:
    f = get_settings().data_dir / "eval" / "clock_vectors.yaml"
    return yaml.safe_load(f.read_text(encoding="utf-8")) if f.exists() else {}


def check_clocks() -> dict:
    vectors = _load_vectors()
    total = ok = 0
    misses: list[str] = []
    for v in vectors.get("due", []):
        total += 1
        clock = Clock(id=v["id"], owner="institution", starts_on="start", length=int(v["length"]), unit=v["unit"],
                      holidays=v.get("holidays"), cites=["vector"])
        got = due_date(clock, date.fromisoformat(str(v["started"])))
        if got == date.fromisoformat(str(v["expect"])):
            ok += 1
        else:
            misses.append(f"{v['id']}: {got} instead of {v['expect']}")
    for v in vectors.get("compensation", []):
        total += 1
        params = {k: Decimal(str(x)) for k, x in (v.get("params") or {}).items()}
        start = date.fromisoformat(str(v["start"])) if v.get("start") else None
        got = compensation(v["formula"], Decimal(str(v["amount"])), date.fromisoformat(str(v["due"])),
                           date.fromisoformat(str(v["paid_on"])), params, start=start)
        good = got == Decimal(str(v["expect"]))
        if v.get("per_day") is not None:
            pd = per_day(v["formula"], Decimal(str(v["amount"])), params)
            good = good and pd == Decimal(str(v["per_day"]))
        if good:
            ok += 1
        else:
            misses.append(f"{v['id']}: {got} instead of {v['expect']}")
    return _check("clocks", "Deadlines and compensation", total > 0 and ok == total, f"{ok} of {total} exact",
                  "Calendar, business-day and month clocks across holidays and month ends, and each delay formula a "
                  "rule states", misses)


async def _repair_rounds(deps: Any, case_id: str, forms: bool) -> int:
    """Rounds of check, fix, check again on a case: forms (filled and read back again) or letters (written again)."""
    n = 0
    for e in await deps.cases.list(case_id, "ledger"):
        if e.check == "repair" and "(round" in e.method and e.method.startswith("the form") == forms:
            n += 1
    return n


async def check_readback(deps: Any, cases: dict[str, str]) -> dict:
    matched = checked = repairs = 0
    misses: list[str] = []
    for key, case_id in cases.items():
        repairs += await _repair_rounds(deps, case_id, forms=True)
        for pack in await deps.cases.list(case_id, "packs"):
            for c in pack.checks:
                if c.name != "readback":
                    continue
                m = re.search(r"(\d+) of (\d+)", c.detail)
                if m:
                    matched += int(m.group(1))
                    checked += int(m.group(2))
                if c.result != "pass":
                    misses.append(f"{key}: {pack.title}: {c.detail}")
    return _check("readback", "Form read-back", checked > 0 and matched == checked and not misses,
                  f"{matched} of {checked} values" + (f", after {repairs} repair round{'s' if repairs != 1 else ''}"
                                                      if repairs else ""),
                  "Every value read back from a filled form matches what was meant to go there, after any value that "
                  "did not fit or read back was composed again", misses)


async def check_letters(deps: Any, cases: dict[str, str]) -> dict:
    """Reminders and objections (checked when drafted) and the covering letter of every pack."""
    letters = fails = repairs = 0
    misses: list[str] = []
    for key, case_id in cases.items():
        repairs += await _repair_rounds(deps, case_id, forms=False)
        for e in await deps.cases.list(case_id, "ledger"):
            if e.check != "fidelity":
                continue
            letters += 1
            if e.result != "pass":
                fails += 1
                misses.append(f"{key}: {e.subject}: {e.detail}")
        for pack in await deps.cases.list(case_id, "packs"):
            for c in pack.checks:
                if c.name == "letter_fidelity":
                    letters += 1
                    if c.result != "pass":
                        fails += 1
                        misses.append(f"{key}: {pack.title}: {c.detail}")
    return _check("letters", "Letter fidelity", letters > 0 and fails == 0,
                  f"{fails} unsupported numbers in {letters} letters"
                  + (f", after {repairs} repair round{'s' if repairs != 1 else ''}" if repairs else ""),
                  "No number or date appears in a letter unless it is in the case; a letter that had one was written "
                  "again and checked again before anyone saw it", misses)


async def check_quotes(deps: Any) -> dict:
    k = knowledge(deps)
    total = found = 0
    misses: list[str] = []
    for a in await k.assertions():
        if not a.quote or not a.source.url:
            continue
        total += 1
        page = await k.page_for_quote(a.source.url, a.quote)
        if page is not None and quote_in_page(a.quote, page.text):
            found += 1
        elif page is None:
            misses.append(f"{a.id}: its page could not be read now ({a.source.url})")
        else:
            misses.append(f"{a.id}: not found on {a.source.url}")
    return _check("quotes", "Quotes on their pages", total > 0 and found == total, f"{found} of {total} found",
                  "Every quote behind a plan step is found word for word on its source page", misses)


# ---------------------------------------------------------------------------------------------
# baselines
# ---------------------------------------------------------------------------------------------

def _same(kind: str, a: str | None, b: str | None, country: str | None) -> bool:
    if a is None or b is None:
        return False
    na, nb = norm_value(kind, a, country), norm_value(kind, b, country)
    if na is not None and nb is not None and na == nb:
        return True
    return normalise(a) == normalise(b)


def right_value(kind: str, got: str | None, truth: dict, country: str | None) -> bool:
    """A value is read right when it is the field as printed or the field's own value, both of which the truth file
    gives ('Our reference: LMP/07714/C' and 'LMP/07714/C'); a name is right with or without the honorific the paper
    prints beside it (様, Mrs, LATE, स्व.). Until 15 October only the printed line counted, so a reader that gave the
    clean value was marked wrong: all nine 'wrong' values in the live run were of that kind."""
    targets = [str(t) for t in (truth.get("as_printed"), truth.get("value")) if t not in (None, "")]
    if any(_same(kind, got, t, country) for t in targets):
        return True
    if got and kind == "name":
        from ..domain.names import strip_honorifics
        bare = normalise(strip_honorifics(got)[0])
        return bool(bare) and any(bare == normalise(strip_honorifics(t)[0]) for t in targets)
    return False


async def reading_baseline(deps: Any, cases: dict[str, str]) -> dict:
    """Text-layer rules (label: value pairs and dated rows) against the truth of every demo paper."""
    pages = right = fields = 0
    scripts: set[str] = set()
    for key, case_id in cases.items():
        fam = load_family(key, deps.settings.story_day)
        lines_by_paper: dict[str, list] = defaultdict(list)
        for ln in await deps.cases.list(case_id, "lines"):
            lines_by_paper[ln.paper_id].append(ln)
        for paper in await deps.cases.list(case_id, "papers"):
            fixture = (paper.fixture_key or "").split("/", 1)[-1]
            if not fixture or fixture not in fam.papers:
                continue
            pages += len(paper.pages)
            scripts |= set(paper.scripts)
            schema = await schema_for(deps, paper.doc_type)
            if not schema:
                continue
            page_no = {p.id: p.number for p in paper.pages}
            pack = [{"id": ln.id, "text": ln.text, "page": page_no.get(ln.page_id, 1), "box": ln.box}
                    for ln in sorted(lines_by_paper.get(paper.id, []), key=lambda x: (page_no.get(x.page_id, 1),
                                                                                     x.box[1], x.box[0]))]
            got = {f["field"]: f["value"] for f in _baseline_reading({"lines": pack, "schema": schema})["fields"]}
            kinds = {f["name"]: f.get("kind", "text") for f in schema.get("fields", [])}
            truth = fam.built(fixture).rendered.fields
            for name, info in truth.items():
                if name not in kinds or info.get("as_printed") is None:
                    continue
                fields += 1
                if right_value(kinds[name], got.get(name), info, paper.country):
                    right += 1
    return {"pages": pages, "scripts": len(scripts), "fields": fields, "right": right}


async def discovery_baseline(deps: Any, cases: dict[str, str]) -> dict:
    """The country's payment patterns, matched against every recurring payment, against the hidden items."""
    from ..oracles.discovery import truth_groups
    hidden = found = false = 0
    for key, case_id in cases.items():
        fam = load_family(key, deps.settings.story_day)
        tg = truth_groups(fam)
        case = await deps.cases.get_case(case_id)
        patterns = []
        for c in case.country_codes or []:
            rails = await deps.knowledge.get("rails", c.split("-")[0])
            patterns += (rails or {}).get("patterns", [])
        lead_groups = {lead.get("group") for lead in fam.get("leads", []) if lead.get("group")}
        hit_groups: set[str] = set()
        for g in await deps.cases.list(case_id, "groups"):
            if not any(re.search(p["rx"], g.label, re.I) for p in patterns):
                continue
            truth_group = tg.get(narration_key(g.label))
            if truth_group in lead_groups:
                hit_groups.add(truth_group)
            else:
                false += 1
        for lead in fam.get("leads", []):
            hidden += 1
            if lead.get("group") in hit_groups:
                found += 1
    return {"hidden": hidden, "found": found, "false": false, "families": len(cases)}


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;。])\s+|\n+", text) if len(s.strip()) > 12]


async def research_baseline(deps: Any) -> dict:
    """Keyword sentences from the first official page each fixture's searches return, against the gold points."""
    folder = get_settings().data_dir / "eval"
    k = knowledge(deps)
    questions = countries = gold_total = gold_hit = sentences_kept = 0
    for f in sorted((folder / "research_fixtures").glob("*.yaml")):
        fx = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        countries += 1
        qs = fx.get("questions", [])
        questions += len(qs)
        gold = None
        for g in sorted((folder / "gold_plans").glob("*.yaml")):
            for raw in (yaml.safe_load(g.read_text(encoding="utf-8")) or {}).get("plans", []):
                if raw.get("key") == fx.get("plan_key"):
                    gold = raw
        if not gold:
            continue
        quotes = []
        for aid in gold.get("compiled_from", []):
            a = await k.assertion(aid)
            raw = None if a else await _fixture_assertion(fx, aid)
            quote = a.quote if a else (raw or {}).get("quote")
            if quote:
                quotes.append(quote)
        gold_total += len(quotes)
        words = {w.lower() for q in qs for query in q.get("queries", []) for w in re.findall(r"\w{5,}", query)}
        page = None
        for q in qs:
            for query in q.get("queries", []):
                hits = await deps.search.search(query, country=f.stem)
                official = [h for h in hits if (getattr(h, "tier", None) or 9) <= 2]
                if official:
                    page = await k.page(official[0].url)
                    break
            if page:
                break
        if page is None:
            continue
        kept = [s for s in _sentences(page.text) if any(w in s.lower() for w in words)]
        sentences_kept += len(kept)
        gold_hit += sum(1 for q in quotes if any(quote_in_page(q, s) for s in kept))
    return {"questions": questions, "countries": countries, "gold": gold_total, "hit": gold_hit,
            "kept": sentences_kept}


async def _fixture_assertion(fx: dict, aid: str) -> dict | None:
    """An assertion the fixture's researchers find (not yet in the shared knowledge)."""
    items = fx.get("assertions") or []
    if isinstance(items, dict):
        items = [{"id": k, **v} for k, v in items.items()]
    return next((i for i in items if isinstance(i, dict) and i.get("id") == aid), None)


def _pct(a: int, b: int) -> str:
    return f"{round(100 * a / b)}%" if b else "n/a"


# ---------------------------------------------------------------------------------------------
# the agents, measured on what a live model prepared
# ---------------------------------------------------------------------------------------------

async def live_cases(deps: Any, cases: dict[str, str]) -> dict[str, str]:
    """The demo cases a live model prepared (a live replay, or cassettes from one). Cases prepared with scripted
    responses are left out: their answers come from the truth files, so they measure nothing."""
    out = {}
    for key, case_id in cases.items():
        case = await deps.cases.get_case(case_id)
        if case is not None and case.model_mode in ("gemini", "replay"):
            out[key] = case_id
    return out


async def reading_scores(deps: Any, cases: dict[str, str]) -> dict:
    """Every value the readers settled, against the truth of the paper it was read from."""
    right = fields = asked = wrong = papers = typed = 0
    for key, case_id in cases.items():
        fam = load_family(key, deps.settings.story_day)
        values = await deps.cases.list(case_id, "values")
        for paper in await deps.cases.list(case_id, "papers"):
            fixture = (paper.fixture_key or "").split("/", 1)[-1]
            if not fixture or fixture not in fam.papers:
                continue
            true_type = fam.papers[fixture].get("doc_type")
            papers += 1
            typed += int(paper.doc_type == true_type)
            schema = await schema_for(deps, true_type) or {}
            kinds = {f["name"]: f.get("kind", "text") for f in schema.get("fields", [])}
            got = {v.field: v for v in values if v.paper_id == paper.id}
            for name, info in fam.built(fixture).rendered.fields.items():
                if name not in kinds or info.get("as_printed") is None:
                    continue
                fields += 1
                v = got.get(name)
                if v is None or v.value is None:
                    asked += int(v is not None and v.status == "disagree")
                    continue
                if right_value(kinds[name], v.value, info, paper.country):
                    right += 1
                else:
                    wrong += 1
    return {"fields": fields, "right": right, "asked": asked, "wrong": wrong, "papers": papers, "typed": typed}


async def discovery_scores(deps: Any, cases: dict[str, str]) -> dict:
    """The leads the agents raised, against the hidden items in each family's truth file. An item a paper already
    put on the money map (the policy the family uploaded) was not hidden on that run: discovery rightly does not
    propose it again, so it is counted apart (on_paper), neither found nor missed."""
    from ..modules.planning import LEAD_KINDS
    from ..oracles.discovery import truth_groups
    hidden = found = false = on_paper = 0
    for key, case_id in cases.items():
        fam = load_family(key, deps.settings.story_day)
        tg = truth_groups(fam)
        groups = {g.id: tg.get(narration_key(g.label)) for g in await deps.cases.list(case_id, "groups")}
        truth = fam.get("leads", [])
        truth_groups_hit: set[str] = set()
        categories_hit: set[str] = set()
        for lead in await deps.cases.list(case_id, "leads"):
            if lead.category.startswith("__"):
                continue
            mapped = {groups.get(g) for g in lead.group_ids} - {None}
            matches = [t for t in truth if (t.get("group") and t["group"] in mapped) or
                       (not t.get("group") and t.get("category") == lead.category)]
            if matches:
                truth_groups_hit |= {t["group"] for t in matches if t.get("group")}
                categories_hit |= {t["category"] for t in matches if not t.get("group")}
            else:
                false += 1
        papers = {(h.institution_id, h.kind) for h in await deps.cases.list(case_id, "holdings") if h.paper_ids}
        for t in truth:
            hit = (t.get("group") in truth_groups_hit) if t.get("group") else (t.get("category") in categories_hit)
            if not hit and (t.get("institution_id"), t.get("holding_kind") or LEAD_KINDS.get(t.get("category"))) in papers:
                on_paper += 1
                continue
            hidden += 1
            found += int(hit)
    return {"hidden": hidden, "found": found, "false": false, "on_paper": on_paper}


async def conversation_scores(deps: Any, cases: dict[str, str]) -> dict:
    answers = grounded = 0
    for case_id in cases.values():
        for u in await deps.cases.list(case_id, "utterances"):
            if u.role == "assistant" and u.grounded is not None:
                answers += 1
                grounded += int(bool(u.grounded and u.sources))
    return {"answers": answers, "grounded": grounded}


async def research_scores(deps: Any) -> dict:
    """Live research runs (challenges, and research for a case) against the gold plans where one exists."""
    runs = [r for r in await knowledge(deps).store.list("challenges") if r.get("model") in ("gemini", "replay")]
    with_gold = [r for r in runs if r.get("gold")]
    rounds = [int(r.get("rounds") or 0) for r in runs if r.get("rounds")]
    return {"runs": len(runs), "with_gold": len(with_gold),
            "matched": sum(1 for r in with_gold if (r.get("gold") or {}).get("match")),
            "verified": sum(1 for r in runs if (r.get("plan") or {}).get("level") in ("dual_verified", "sourced")),
            "rounds": rounds}


# ---------------------------------------------------------------------------------------------
# coverage by country
# ---------------------------------------------------------------------------------------------

LEVEL_WORD = {"gold": "gold", "outcome_confirmed": "verified", "adjudicated": "verified", "dual_verified": "verified",
              "sourced": "one_source", "draft": "live_only"}
LEVEL_RANK = {"gold": 4, "verified": 3, "one_source": 2, "contested": 1, "live_only": 0}


async def coverage(deps: Any, cases: dict[str, str]) -> list[dict]:
    k = knowledge(deps)
    levels: dict[str, list[str]] = defaultdict(list)
    for plan in await k.plans():
        if plan.level == "draft":
            continue                      # a plan that starts by asking the institution claims nothing yet
        levels[plan.jurisdiction.split("-")[0]].append("contested" if plan.contested else LEVEL_WORD.get(plan.level, "live_only"))
    rules: dict[str, str] = {}
    rules_detail: dict[str, str] = {}
    words = {"gold": "gold", "verified": "verified", "one_source": "one source", "contested": "contested"}
    for c, found in levels.items():
        counts = {w: found.count(w) for w in ("gold", "verified", "one_source", "contested") if found.count(w)}
        # the level most of the country's plans have; the detail line names every level, the weakest included
        rules[c] = max(counts, key=lambda w: (counts[w], -LEVEL_RANK[w]))
        rules_detail[c] = (f"{len(found)} plan{'s' if len(found) != 1 else ''}: "
                           + ", ".join(f"{n} {words[w]}" for w, n in counts.items()))
    forms = {(f.get("key") or f.get("id") or "").split("/")[0] for f in await k.forms()}
    papers: set[str] = set()
    leads: set[str] = set()
    letters: set[str] = set()
    contested: set[str] = set()
    for key, case_id in cases.items():
        fam = load_family(key, deps.settings.story_day)
        for p in await deps.cases.list(case_id, "papers"):
            if p.country:
                papers.add(p.country.split("-")[0])
        case = await deps.cases.get_case(case_id)
        for lead in fam.get("leads", []):
            inst = await k.institution(lead["institution_id"]) if lead.get("institution_id") else None
            country = inst.country if inst else (case.country_codes or [""])[0]
            if country:
                leads.add(country.split("-")[0])
        for corr in await deps.cases.list(case_id, "correspondence"):
            if corr.direction == "out" and corr.letter_text:
                claim = await deps.cases.get(case_id, "claims", corr.claim_id) if corr.claim_id else None
                if claim and claim.country:
                    letters.add(claim.country.split("-")[0])
        for pk in await deps.cases.list(case_id, "packs"):
            claim = await deps.cases.get(case_id, "claims", pk.claim_id)
            if claim and claim.country and any(i.kind == "letter" for i in pk.items):
                letters.add(claim.country.split("-")[0])
        if case.jurisdiction and case.jurisdiction.contested:
            contested |= {c.split("-")[0] for c in case.country_codes}
    # Countries only in the research test set: a hand-checked gold plan counts; a run that found no official
    # page (its plan starts by asking the institution) leaves the rules to a live run.
    folder = get_settings().data_dir / "eval" / "research_fixtures"
    researched = {f.stem for f in folder.glob("*.yaml")} if folder.is_dir() else set()
    gold_countries = {g.jurisdiction.split("-")[0] for g in gold_plans()}
    for c in researched - set(rules):
        if c in gold_countries:
            rules[c], rules_detail[c] = "gold", "1 plan: 1 gold, in the research test set"
        else:
            rules[c] = "live_only"
            rules_detail[c] = ("Researched in the test set: no official page describes the procedure, so the plan "
                               "starts by asking the institution in writing")
    order = ["IN", "JP", "GB", "US", "SG", "DE", "FR", "EG"]
    out = []
    for c in order:
        rule = rules.get(c, "live_only")
        if c in contested and c == "DE":
            rule = "contested"
        out.append({"country": c, "name": COUNTRY_NAMES.get(c, c),
                    "papers": "verified" if c in papers else "live_only",
                    "leads": "verified" if c in leads else "live_only",
                    "rules": rule, "rules_detail": rules_detail.get(c, ""),
                    "forms": "verified" if c in forms else "live_only",
                    "letters": "verified" if c in letters else "live_only"})
    return out


# ---------------------------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------------------------

async def run_all(deps: Any, *, seed: bool = False) -> dict:
    started = time.time()
    cases = await _ensure_cases(deps, seed=seed)
    checks = [await check_routes(deps, cases), check_clocks(), await check_readback(deps, cases),
              await check_letters(deps, cases), await check_quotes(deps)]
    rd = await reading_baseline(deps, cases)
    dc = await discovery_baseline(deps, cases)
    rs = await research_baseline(deps)
    asks = sum(1 for key in cases for ev in (load_family(key, deps.settings.story_day).get("timeline") or [])
               if isinstance(ev, dict) and ev.get("do") == "ask")
    measured = await live_cases(deps, cases)
    live = bool(measured)
    after = "After a live run"
    sr = await reading_scores(deps, measured) if measured else None
    sd = await discovery_scores(deps, measured) if measured else None
    sc = await conversation_scores(deps, measured) if measured else None
    srs = await research_scores(deps)
    on = f" on {len(measured)} live case{'s' if len(measured) != 1 else ''}" if measured else ""
    agents = [
        {"id": "reading", "module": "Reading papers", "test_set": f"{rd['pages']} pages in {rd['scripts']} scripts",
         "measure": "Fields read exactly right", "baseline": f"{_pct(rd['right'], rd['fields'])} "
                                                             f"({rd['right']} of {rd['fields']})",
         "baseline_how": "Text-layer rules: a label, then its value",
         "agent": f"{_pct(sr['right'], sr['fields'])} ({sr['right']} of {sr['fields']}){on}; {sr['asked']} sent to the "
                  f"family, {sr['wrong']} wrong; paper type right {sr['typed']} of {sr['papers']}"
         if sr and sr["fields"] else after},
        {"id": "discovery", "module": "Finding what nobody listed",
         "test_set": f"{dc['hidden']} hidden items, {dc['families']} families",
         "measure": "Items found, and false leads raised",
         "baseline": f"{dc['found']} of {dc['hidden']} found, {dc['false']} false",
         "baseline_how": "The country's payment patterns, matched against each recurring payment",
         "agent": f"{sd['found']} of {sd['hidden']} found, {sd['false']} false{on}"
                  + (f"; {sd['on_paper']} more already on a paper" if sd.get("on_paper") else "")
         if sd and sd["hidden"] else after},
        {"id": "research", "module": "Researching rules", "test_set": f"{rs['questions']} questions, "
                                                                     f"{rs['countries']} countries",
         "measure": "Points matching the gold answers, with quotes found",
         "baseline": f"{rs['hit']} of {rs['gold']} gold points, in {rs['kept']} sentences kept"
         if rs["gold"] else "Not applicable",
         "baseline_how": "Sentences with a search word, from the first official page",
         "agent": (f"{srs['matched']} of {srs['with_gold']} live runs matched the gold plan; "
                   f"{srs['verified']} of {srs['runs']} found official sources"
                   + (f"; {sum(srs['rounds'])} rounds in {len(srs['rounds'])} runs, at most {max(srs['rounds'])}"
                      if srs["rounds"] else ""))
         if srs["runs"] else after},
        {"id": "conversation", "module": "Conversation", "test_set": f"{asks} questions in the families' scripts",
         "measure": "Tasks finished, questions asked, answers grounded", "baseline": "Not applicable",
         "baseline_how": "", "agent": f"{sc['grounded']} of {sc['answers']} answers grounded in sources{on}"
         if sc and sc["answers"] else after},
    ]
    report = {"ran_at": datetime.now(UTC).isoformat(timespec="seconds"), "mode": deps.models.label,
              "story_day": deps.settings.story_day.isoformat() if deps.settings.story_day else None,
              "seconds": round(time.time() - started, 1), "checks": checks, "agents": agents,
              "coverage": await coverage(deps, cases),
              "live_cases": sorted(measured),
              "note": "" if live else "No demo case here was prepared by a live model, so only the checks and the "
                                      "baselines are measured. Watch a family's story with Gemini (or run "
                                      "`afterledger seed` live), then run the evaluation again to measure the agents."}
    return report


def print_report(report: dict) -> None:
    print(f"AfterLedger evaluation, {report.get('mode')} run at {report.get('ran_at')} ({report.get('seconds')} s)")
    print()
    print("Checks that must always pass")
    for c in report.get("checks", []):
        print(f"  [{'pass' if c['ok'] else 'FAIL'}] {c['name']}: {c['result']}")
        for d in c.get("detail", [])[:8]:
            print(f"         - {d}")
    print()
    print("Agents against their baselines")
    for a in report.get("agents", []):
        print(f"  {a['module']}: {a['test_set']}; {a['measure']}. Baseline: {a['baseline']}. Agent: {a['agent']}.")
    print()
    print("Coverage by country")
    for c in report.get("coverage", []):
        print(f"  {c['name']:16s} papers {c['papers']:10s} leads {c['leads']:10s} rules {c['rules']:10s} "
              f"forms {c['forms']:10s} letters {c['letters']}")
    if report.get("note"):
        print()
        print(report["note"])


def write_report(report: dict, path: Path) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
