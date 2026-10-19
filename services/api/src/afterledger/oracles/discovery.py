"""Offline answers for the discovery agents."""
from __future__ import annotations

import re
from functools import lru_cache

from ..adapters.llm.models import OracleRequest, ToolCalls
from ..domain.money import format_amount
from . import Ctx, oracle

_CURRENCY = {"IN": "INR", "JP": "JPY", "GB": "GBP", "US": "USD", "SG": "SGD"}


def _day(iso: str) -> str:
    """'2026-03-12' -> '12 March 2026'."""
    from datetime import date
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {d.strftime('%B')} {d.year}"


CADENCE_WORD = {"monthly": "every month", "quarterly": "every quarter", "yearly": "once a year", "once": "once",
                "weekly": "every week", "half_yearly": "twice a year", "irregular": "now and then"}


def _key(text: str) -> str:
    from ..modules.discovery import narration_key
    return narration_key(text)


@lru_cache(maxsize=16)
def _truth_groups_cached(fam_key: str, story_day: str) -> dict[str, str]:
    from datetime import date

    from ..seed.families import load_family
    fam = load_family(fam_key, date.fromisoformat(story_day))
    out: dict[str, str] = {}
    for pid, spec in fam.papers.items():
        if spec.get("ledger"):
            for r in fam.built(pid).rendered.rows:
                g = (r.get("_meta") or {}).get("group")
                if g:
                    out[_key(r["narration"])] = g
    return out


def truth_groups(fam) -> dict[str, str]:
    return _truth_groups_cached(fam.key, fam.story_day.isoformat())


def _patterns(rails: dict | None) -> list[dict]:
    return (rails or {}).get("patterns", [])


@oracle("rail_profiler")
def rail_profiler(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    prof = p.get("profile") or {}
    return {"country": p.get("country", ""), "conventions": prof.get("conventions", [])}


@oracle("payee_screener")
def payee_screener(ctx: Ctx, req: OracleRequest) -> dict:
    """Offline: the rails' patterns and the words that name a policy, fund, loan or pension decide."""
    from ..modules.discovery import _TELLING
    p = ctx.payload(req)
    items = []
    for g in p.get("groups", []):
        label = str(g.get("label", ""))
        up = label.upper()
        kind = "cash" if ("ATM" in up or "CASH" in up) else "unknown"
        cp = None
        for pat in _patterns(p.get("rails")):
            if re.search(pat["rx"], up, re.I):
                kind, cp = pat["category"], pat.get("institution_id")
                break
        worth = bool(_TELLING.search(label)) or (kind not in ("cash", "unknown") and kind != "merchant")
        items.append({"group_id": g["id"], "counterparty": cp, "kind": kind, "worth": worth})
    return {"items": items}


@oracle("narration_interpreter")
def narration_interpreter(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    g = p.get("group") or {}
    fam = ctx.family(req)
    if fam:
        tg = truth_groups(fam).get(_key(g.get("label", "")))
        info = (fam.get("groups") or {}).get(tg) if tg else None
        if info:
            return {"group_id": g["id"], "counterparty": info.get("counterparty"),
                    "counterparty_kind": info.get("kind"), "interpretation": info["interpretation"],
                    "alternatives": info.get("alternatives", [])}
    label = g.get("label", "")
    up = label.upper()
    for pat in _patterns(p.get("rails")):
        if re.search(pat["rx"], up, re.I):
            return {"group_id": g["id"], "counterparty": pat.get("institution_id"), "counterparty_kind": pat["category"],
                    "interpretation": f"{pat['title']}: {g.get('count')} payment{'' if g.get('count') == 1 else 's'} of "
                                      f"{format_amount(g.get('amount', 0), g.get('currency', 'INR'))}, "
                                      f"{CADENCE_WORD.get(g.get('cadence'), '')}.", "alternatives": []}
    m = re.match(r"(?:UPI|NEFT|IMPS)[/ ]+([A-Z][A-Z .]+?)[/ ]", up + "/")
    if m:
        return {"group_id": g["id"], "counterparty": m.group(1).title().strip(), "counterparty_kind": "person",
                "interpretation": f"Money sent to {m.group(1).title().strip()} {CADENCE_WORD.get(g.get('cadence'), '')}.",
                "alternatives": ["Support for a family member", "Repayment of a private loan"]}
    if "ATM" in up or "CASH" in up:
        return {"group_id": g["id"], "counterparty": None, "counterparty_kind": "cash",
                "interpretation": "Cash taken from or paid into the account.", "alternatives": []}
    return {"group_id": g["id"], "counterparty": None, "counterparty_kind": None,
            "interpretation": "An ordinary payment; nothing it points to.", "alternatives": []}


def _group_ids_for(fam, groups: list[dict], truth_group: str) -> list[str]:
    tg = truth_groups(fam)
    return [g["id"] for g in groups if tg.get(_key(g.get("label", ""))) == truth_group]


@oracle("hypothesis_generator")
def hypothesis_generator(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    groups = p.get("groups", [])
    fam = ctx.family(req)
    if fam:
        out = []
        for lead in fam.get("leads", []):
            if lead.get("kind") == "debt":
                continue            # the liability scout finds debts
            gids = _group_ids_for(fam, groups, lead["group"]) if lead.get("group") else []
            pids = [f"pp_{x}" for x in lead.get("papers", [])]
            papers_present = {pp["id"] for pp in p.get("papers", [])}
            pids = [x for x in pids if x in papers_present]
            if not gids and not pids:
                continue            # the evidence has not arrived yet
            out.append({"key": lead["key"], "kind": lead["kind"], "category": lead["category"],
                        "holding_kind": lead.get("holding_kind"), "title_local": lead.get("title_local", ""),
                        "title": lead.get("title", lead["key"]), "group_ids": gids, "paper_ids": pids,
                        "institution_id": lead.get("institution_id"), "reason": lead.get("reason", "")})
        return {"hypotheses": out}
    out = []
    # baseline: the interpreter has already matched each group against the country's rail patterns
    for g in groups:
        kind = g.get("counterparty_kind")
        if kind and kind not in ("person", "cash", None) and kind not in ("loan", "credit_card"):
            out.append({"key": f"{kind}_{g['id'][-6:]}", "kind": "entitlement" if kind in ("pension", "life_cover",
                        "social_security", "employer") else "asset", "category": kind,
                        "title": (g.get("interpretation") or kind).split(":")[0], "group_ids": [g["id"]],
                        "institution_id": g.get("counterparty") if str(g.get("counterparty", "")).startswith(("in_", "gb_", "us_", "sg_", "jp_")) else None,
                        "reason": g.get("interpretation", "")})
    return {"hypotheses": out}


@oracle("investigator")
def investigator(ctx: Ctx, req: OracleRequest) -> dict | ToolCalls:
    p = ctx.payload(req)
    h = p.get("hypothesis") or {}
    if not req.tool_results and h.get("group_ids"):
        return ToolCalls([("get_rows", {"group_id": h["group_ids"][0]})])
    fam = ctx.family(req)
    if fam:
        lead = fam.lead(h["key"]) or {}
        cites = lead.get("cites", [])
        searched = [r for r in req.tool_results if r["name"] == "search_knowledge"]
        if lead.get("kind") == "entitlement" and cites and not searched:
            # whether the family is owed it turns on a rule: the investigator looks the rules up first
            words = dict.fromkeys(part for c in cites for part in c.split("_")[1:] if not part.isdigit())
            return ToolCalls([("search_knowledge", {"query": " ".join(words)})])
        if searched:                          # cite the rules the search returned (all of the lead's, when it found none)
            found = {a["id"] for a in (searched[-1]["response"] or {}).get("assertions", [])}
            cites = [c for c in cites if c in found] or cites
        return {"key": h["key"], "sentence": lead.get("sentence", h.get("title", "")),
                "sentence_local": lead.get("sentence_local", ""),
                "investigator_note": lead.get("investigator_note", ""), "how_to_confirm": lead.get("how_to_confirm", ""),
                "next_step_note": lead.get("next_step_note", ""), "signals": lead.get("signals", {}),
                "cites": cites, "value": lead.get("value")}
    rows = (req.tool_results[0]["response"].get("rows", []) if req.tool_results else [])
    n = len(rows)
    cur = next((_CURRENCY[c] for c in (p.get("case") or {}).get("countries") or [] if c in _CURRENCY), None)
    amount = (format_amount(rows[0]["amount"], cur) if cur else rows[0]["amount"]) if rows else "?"
    when = ", ".join(_day(r["date"]) for r in rows[:3])
    payments = f"{n} payment{'s' if n != 1 else ''}"
    return {"key": h["key"], "sentence": f"{payments} on the statement point{'s' if n == 1 else ''} to it.",
            "investigator_note": f"{payments} of {amount}, on {when}.",
            "how_to_confirm": "Ask the institution whether he held this, quoting the payments.",
            "next_step_note": "", "signals": {"cadence": "monthly" if n >= 3 else "irregular", "product_match": "partial"},
            "cites": [], "value": None}


@oracle("devils_advocate")
def devils_advocate(ctx: Ctx, req: OracleRequest) -> dict:
    inv = ctx.payload(req).get("investigation") or {}
    fam = ctx.family(req)
    if fam:
        lead = fam.lead(inv.get("key", "")) or {}
        return {"key": inv.get("key", ""), "critic_note": lead.get("critic_note", ""),
                "alternatives": lead.get("alternatives", []), "downgrade": bool(lead.get("downgrade", False))}
    return {"key": inv.get("key", ""), "critic_note": "The payments could belong to an arrangement that has already "
            "ended, or be in another person's name.", "alternatives": [], "downgrade": True}


@oracle("liability_scout")
def liability_scout(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    groups = p.get("groups", [])
    fam = ctx.family(req)
    debts = []
    if fam:
        for lead in fam.get("leads", []):
            if lead.get("kind") != "debt":
                continue
            gids = _group_ids_for(fam, groups, lead["group"]) if lead.get("group") else []
            if not gids and lead.get("group"):
                continue
            debts.append({"key": lead["key"], "title": lead.get("title", lead["key"]), "sentence": lead.get("sentence", ""),
                          "category": lead.get("category", "loan"), "holding_kind": lead.get("holding_kind"),
                          "title_local": lead.get("title_local", ""), "sentence_local": lead.get("sentence_local", ""),
                          "group_ids": gids, "institution_id": lead.get("institution_id"),
                          "monthly": lead.get("monthly"), "next_due": lead.get("next_due"), "cites": lead.get("cites", [])})
        return {"debts": debts}
    for g in groups:
        if g.get("direction") == "debit" and re.search(r"\bEMI\b|LOAN", (g.get("label") or "").upper()):
            debts.append({"key": f"loan_{g['id'][-6:]}", "title": "A loan with monthly instalments",
                          "sentence": f"{g.get('label')} is taken {CADENCE_WORD.get(g.get('cadence'), '')}.",
                          "group_ids": [g["id"]], "monthly": str(g.get("amount_typical")), "cites": []})
    return {"debts": debts}


@oracle("coverage_auditor")
def coverage_auditor(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam = ctx.family(req)
    truth = (fam.get("coverage") or {}) if fam else {}
    held = set(p.get("holdings", [])) | set(p.get("leads", []))
    items = []
    for cov in (p.get("coverage") or {}).values():
        for it in cov:
            t = truth.get(it["id"])
            if t:
                items.append({"id": it["id"], "label": it["label"], "status": t["status"], "detail": t.get("detail", ""),
                              "ask": t.get("ask")})
                continue
            found = held & set(it.get("categories", []))
            items.append({"id": it["id"], "label": it["label"], "status": "found" if found else "not_seen",
                          "detail": "" if found else "No paper mentions one.", "ask": None if found else it.get("ask")})
    return {"items": items}
