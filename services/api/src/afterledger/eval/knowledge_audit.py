"""Audits the shared knowledge shipped in data/knowledge before anything relies on it.

Every assertion's quote must be on its page snapshot word for word; every plan must pass the
static check against the assertion ids that exist; rates, coverage lists, forms and acquire-plans
must point at things that exist. The test suite and `afterledger audit-knowledge` both run it.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..adapters.local.services import FixturePages, _norm_url
from ..adapters.local.stores import _records_from
from ..domain.checks import quote_in_page
from ..domain.plan_language import LEVEL_ORDER, ProcedurePlan
from ..domain.plan_runtime import COMPENSATION_FORMULAS, static_check


@dataclass
class AuditReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = ", ".join(f"{v} {k}" for k, v in self.counts.items())
        return f"{parts}; {len(self.errors)} errors, {len(self.warnings)} warnings"


def _load_folder(folder: Path) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    if not folder.is_dir():
        return out
    for f in sorted(folder.rglob("*")):
        if f.suffix in (".yaml", ".yml", ".json"):
            for rec in _records_from(f).values():
                out.append((f, rec))
    return out


def _load_collection(knowledge_dir: Path, name: str) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for suffix in (".yaml", ".yml", ".json"):
        f = knowledge_dir / f"{name}{suffix}"
        if f.exists():
            out += [(f, r) for r in _records_from(f).values()]
    return out + _load_folder(knowledge_dir / name)


def audit_knowledge(knowledge_dir: Path, *, forms_dir: Path | None = None) -> AuditReport:
    report = AuditReport()
    pages = FixturePages(knowledge_dir)

    # ---- assertions ----------------------------------------------------------------------------
    assertions = _load_folder(knowledge_dir / "assertions")
    ids = Counter(r.get("id") for _, r in assertions)
    report.errors += [f"assertion id '{i}' is used {n} times" for i, n in ids.items() if n > 1]
    known: dict[str, dict] = {}
    for f, r in assertions:
        aid = r.get("id")
        for key in ("id", "text", "quote", "source_url", "jurisdiction", "level"):
            if not r.get(key):
                report.errors.append(f"{f.name}: assertion {aid} has no {key}")
        if r.get("level") and r["level"] not in LEVEL_ORDER:
            report.errors.append(f"{f.name}: assertion {aid} has unknown level {r['level']}")
        page = pages.pages.get(_norm_url(r.get("source_url", "")))
        if page is None:
            report.errors.append(f"assertion {aid}: no page snapshot for {r.get('source_url')}")
        elif not quote_in_page(r.get("quote", ""), page.text):
            report.errors.append(f"assertion {aid}: quote not found on {r.get('source_url')}")
        known[aid] = r
    report.counts["assertions"] = len(known)
    report.counts["pages"] = len(pages.pages)

    # ---- research fixtures (scripted research runs) --------------------------------------------------
    fixtures_dir = knowledge_dir.parent / "eval" / "research_fixtures"
    fixture_ids: dict[str, dict] = {}
    for f in sorted(fixtures_dir.glob("*.yaml")) if fixtures_dir.is_dir() else []:
        fx = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for item in fx.get("assertions", []):
            page = pages.pages.get(_norm_url(item.get("source_url", "")))
            if page is None or not quote_in_page(item.get("quote", ""), page.text):
                report.errors.append(f"research fixture {f.name}: quote of {item.get('id')} not on its page")
            fixture_ids[item["id"]] = item
        for who in ("researcher_a", "researcher_b"):
            for trap in (fx.get(who) or {}).get("traps", []):
                page = pages.pages.get(_norm_url(trap.get("source_url", "")))
                if page is not None and quote_in_page(trap.get("quote", ""), page.text):
                    report.errors.append(f"research fixture {f.name}: trap {trap['id']} is on its page, so it "
                                         "would not be dropped")
        if fx.get("compiled_plan"):
            try:
                cp = ProcedurePlan.model_validate(fx["compiled_plan"])
                report.errors += [f"research fixture {f.name}: {e}"
                                  for e in static_check(cp, set(known) | set(fixture_ids))]
            except (ValidationError, ValueError) as exc:
                report.errors.append(f"research fixture {f.name}: compiled plan does not validate: {exc}")
    report.counts["research_fixtures"] = len(list(fixtures_dir.glob("*.yaml"))) if fixtures_dir.is_dir() else 0

    # ---- plans -----------------------------------------------------------------------------------
    plan_records = _load_collection(knowledge_dir, "plans")
    gold_dir = knowledge_dir.parent / "eval" / "gold_plans"
    gold_records = _load_folder(gold_dir)
    plans: dict[str, ProcedurePlan] = {}
    for f, raw in plan_records + gold_records:
        key = raw.get("key", "?")
        try:
            plan = ProcedurePlan.model_validate(raw)
        except (ValidationError, ValueError) as exc:
            report.errors.append(f"{f.name}: plan {key} does not validate: {exc}")
            continue
        is_gold_file = gold_dir.exists() and f.is_relative_to(gold_dir)
        errs = static_check(plan, set(known) | (set(fixture_ids) if is_gold_file else set()))
        report.errors += [f"plan {key}: {e}" for e in errs]
        for clock in plan.clocks:
            if clock.compensation and clock.compensation not in COMPENSATION_FORMULAS:
                report.errors.append(f"plan {key}: clock {clock.id} uses unknown formula {clock.compensation}")
        is_gold_only = f.is_relative_to(gold_dir) if gold_dir.exists() else False
        if not is_gold_only:
            if key in plans:
                report.errors.append(f"plan key {key} is defined twice")
            plans[key] = plan
    report.counts["plans"] = len(plans)
    report.counts["gold_plans"] = len(gold_records)

    # acquire plans and plan-level checks that need the whole set
    rates = {r.get("id"): r for _, r in _load_collection(knowledge_dir, "rates")}
    form_keys = {r.get("key") or r.get("id") for _, r in _load_collection(knowledge_dir, "forms")}
    for key, plan in plans.items():
        for doc in plan.documents:
            if doc.acquire_plan and doc.acquire_plan not in plans:
                report.errors.append(f"plan {key}: document {doc.id} names missing acquire plan {doc.acquire_plan}")
            if doc.form_key and form_keys and doc.form_key not in form_keys:
                report.errors.append(f"plan {key}: document {doc.id} names missing form {doc.form_key}")
        for clock in plan.clocks:
            base = clock.compensation_params.get("base_rate")
            if base and base not in rates:
                report.errors.append(f"plan {key}: clock {clock.id} uses missing rate {base}")
        for aid in plan.contested:
            if aid not in known:
                report.errors.append(f"plan {key}: contested id {aid} is not an assertion")
        weak = [a for a in plan.all_cites() if a in known and LEVEL_ORDER[known[a]["level"]] < LEVEL_ORDER["sourced"]]
        if weak:
            report.warnings.append(f"plan {key}: cites draft assertions {sorted(weak)}")

    # ---- rates -------------------------------------------------------------------------------------
    for rid, r in rates.items():
        for entry in r.get("history", []):
            cite = entry.get("cites")
            if cite and cite not in known:
                report.errors.append(f"rate {rid}: cites missing assertion {cite}")
    report.counts["rates"] = len(rates)

    # ---- coverage lists ------------------------------------------------------------------------
    for _f, cov in _load_collection(knowledge_dir, "coverage"):
        for item in cov.get("items", []):
            pk = item.get("plan")
            if pk and pk not in plans:
                report.errors.append(f"coverage {cov.get('id')}: item {item.get('id')} names missing plan {pk}")
            for aid in item.get("cites", []):
                if aid not in known:
                    report.errors.append(f"coverage {cov.get('id')}: item {item.get('id')} cites missing {aid}")

    # ---- institutions ----------------------------------------------------------------------------
    insts = _load_collection(knowledge_dir, "institutions")
    inst_ids = Counter(r.get("id") for _, r in insts)
    report.errors += [f"institution id '{i}' is used {n} times" for i, n in inst_ids.items() if n > 1]
    for _, r in insts:
        if r.get("fictional"):
            dom = r.get("domain") or ""
            if dom and not dom.endswith(".example"):
                report.errors.append(f"institution {r['id']}: fictional but domain {dom} is not .example")
            url = r.get("bereavement_url")
            if url and _norm_url(url) not in pages.pages:
                report.errors.append(f"institution {r['id']}: bereavement page {url} has no snapshot")
    for aid, r in known.items():
        inst = r.get("institution_id")
        if inst and inst not in inst_ids:
            report.errors.append(f"assertion {aid}: unknown institution {inst}")
    report.counts["institutions"] = len(inst_ids)

    # ---- forms on disk ---------------------------------------------------------------------------
    if forms_dir is not None and forms_dir.is_dir():
        for _, form in _load_collection(knowledge_dir, "forms"):
            pdf = form.get("pdf")
            if pdf and not (forms_dir / pdf).exists():
                report.errors.append(f"form {form.get('key')}: file {pdf} missing")
    return report


def main() -> int:  # pragma: no cover - thin CLI wrapper
    from ..config import get_settings
    s = get_settings()
    rep = audit_knowledge(s.knowledge_dir, forms_dir=s.data_dir / "forms")
    for e in rep.errors:
        print("ERROR", e)
    for w in rep.warnings:
        print("warn ", w)
    print(rep.summary())
    return 0 if rep.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))
