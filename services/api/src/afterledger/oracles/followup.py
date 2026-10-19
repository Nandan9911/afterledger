"""Offline answers for the follow-up agents."""
from __future__ import annotations

import re

from ..adapters.llm.models import OracleRequest
from . import Ctx, oracle
from .letters import frame

WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}


@oracle("supervisor")
def supervisor(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    cands = sorted(p.get("candidates", []), key=lambda c: -c.get("priority", 0))
    fam = ctx.family(req)
    truth = (fam.get("today_tasks") or []) if fam else []
    tasks = []
    for c in cands:
        t = next((x for x in truth if x["kind"] == c["kind"] and (
            (x.get("institution_id") and x["institution_id"] == c.get("institution_id")) or
            (x.get("lead_category") and x["lead_category"] == c.get("lead_category")) or
            (x.get("key_prefix") and c["key"].startswith(x["key_prefix"])))), None)
        if truth and t is None:
            continue
        local = (t or {}).get("local") or {}
        tasks.append({"key": c["key"], "kind": c["kind"], "title": (t or {}).get("title", c["title"]),
                      "body": (t or {}).get("body") or c.get("body", ""),
                      "title_local": local.get("title", ""), "body_local": local.get("body", ""),
                      "lang_local": local.get("lang", ""), "owner_person_ids": c.get("owner_person_ids", []),
                      "claim_id": c.get("claim_id"), "due_on": c.get("due_on"), "priority": c.get("priority", 0.5),
                      "cites": c.get("cites", [])})
        if len(tasks) == 3:
            break
    v = p.get("viewer") or {}
    first = (v.get("name_en") or v.get("name") or "").split(" ")[0]
    local = (fam.get("today_greeting_local") or "") if fam else ""
    if isinstance(local, dict):                   # one greeting per family member, by first name
        local = local.get(first) or local.get((v.get("name") or "").split(" ")[0]) or ""
    return {"greeting": greeting_for(called(v.get("name_en") or v.get("name") or ""), tasks, p.get("candidates", [])),
            "greeting_local": local, "tasks": tasks}


def called(name: str) -> str:
    """How a greeting addresses someone: the first name, or the whole name when it starts with an initial
    ("R. P. Shukla")."""
    first = name.split(" ")[0]
    return name if first.endswith(".") or len(first) == 1 else first


def greeting_for(viewer: str, tasks: list[dict], cands: list[dict]) -> str:
    kinds = {c["key"]: c.get("institution_id") for c in cands}
    late = [t for t in tasks if t["key"].startswith("remind:")]
    letters = [t for t in tasks if t["key"].startswith("send:")]
    rest = len(tasks) - len(late) - len(letters)
    parts = []
    if late:
        banks = all((kinds.get(t["key"]) or "").endswith(("_bank", "_bs", "_shinkin", "_savings")) for t in late)
        noun = ("bank is" if banks else "institution is") if len(late) == 1 else ("banks are" if banks else "institutions are")
        parts.append(f"{WORDS.get(len(late), len(late))} {noun} late")
    if letters:
        parts.append(f"{WORDS.get(len(letters), len(letters))} letter{'s' if len(letters) != 1 else ''} "
                     f"{'is' if len(letters) == 1 else 'are'} ready to send")
    if rest:
        parts.append(f"{WORDS.get(rest, rest)} thing{'s' if rest != 1 else ''} need{'' if rest != 1 else 's'} the family")
    # no time of day: the family reads it at any hour
    text = (parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", and " + parts[-1]) if parts \
        else "nothing needs you today"
    text = text[0].lower() + text[1:]
    for w in list(WORDS.values())[:5]:
        text = text.replace(f", and {w} ", f", and {w.lower()} ").replace(f", {w} ", f", {w.lower()} ")
    return f"{viewer}, {text}." if viewer else f"{text[0].upper()}{text[1:]}."


@oracle("correspondence_reader")
def correspondence_reader(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    text = (p.get("title", "") + " " + p.get("text", "")).lower()
    fam = ctx.family(req)
    if fam:
        for r in fam.get("replies", []):
            if r["title"].lower() in text:
                return {k: r.get(k) for k in ("summary", "requests", "outcome", "complete_set_confirmed_on",
                                              "settled_amount", "objection")}
    requests = []
    if "probate" in text or "grant of representation" in text or "succession certificate" in text:
        requests.append("grant of probate" if "probate" in text else "succession certificate")
    objection = None
    for m in p.get("must_not_be_asked", []):
        if any(w in text for w in m.lower().split()[:2]):
            objection = f"The plan's rule says the institution may not ask for: {m}."
    settled = re.search(r"(?:paid|credited|settled)[^0-9₹$£€¥]*?(?:rs\.?|inr|₹|£|s\$|\$|€|¥|usd|gbp|eur|sgd)\s*"
                        r"([\d,]+(?:\.\d{1,2})?)", text) or \
        re.search(r"(?:paid|credited|settled)[^0-9]*([\d,]+\.\d{2})", text)
    return {"summary": p.get("text", "")[:160], "requests": requests, "outcome": "settled" if settled else None,
            "complete_set_confirmed_on": None, "settled_amount": settled.group(1).replace(",", "") if settled else None,
            "objection": objection}


def _slipped(amount: str | None) -> str | None:
    """The amount with its first digit changed: a figure that is not in the case."""
    if not amount:
        return amount
    m = re.search(r"\d", amount)
    if not m:
        return amount
    d = int(m.group())
    return amount[:m.start()] + str(d + 1 if d < 9 else 1) + amount[m.end():]


@oracle("escalation_writer")
def escalation_writer(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    f = p.get("facts") or {}
    kind = p.get("kind", "reminder")
    rules = {r["id"]: r for r in p.get("rules", [])}

    def para(aid_prefix: str) -> str:
        for aid, r in rules.items():
            if aid.startswith(aid_prefix):
                m = re.search(r"para (\d+)", r.get("locator") or "")
                if m:
                    return m.group(1)
        return ""

    holdings = " and ".join(f.get("holdings", []))
    fam = ctx.family(req)
    # The Vermas' first reminder is drafted with one wrong figure, so the demo shows the letter checked, sent back and
    # fixed (a scripted slip, like the readers' planned disagreements); the repair, which lists the problem, is right.
    slip = kind == "reminder" and bool(fam and fam.get("key") == "verma") and not p.get("problems")
    if kind == "reminder":
        p319, p321 = para("in_rbi_319"), para("in_rbi_321")
        body = [f"On {f.get('complete_set_on')} you confirmed that you had received every document for this claim. "
                + (f"Paragraph {p319} of the Reserve Bank of India's Responsible Business Conduct Directions required "
                   if p319 else "The rule required ") +
                f"the claim to be settled within {f.get('clock_length')} calendar days, by {f.get('due_on')}. "
                "It has not been settled."]
        if f.get("owed"):
            body += ["", (f"Under paragraph {p321}, compensation" if p321 else "Compensation") +
                     f" is due for the delay at not less than the Bank Rate plus {f.get('spread')} per cent a year on "
                     f"{f.get('amount')}. At the Bank Rate of {f.get('base_rate')} per cent on {f.get('complete_set_on')}, "
                     f"this is {f.get('per_day')} a day, or {_slipped(f.get('owed')) if slip else f.get('owed')} up to "
                     f"{f.get('today')}."]
        body += ["", "Please settle the claim with this compensation, or tell me the reason for the delay in writing."]
        subject = f"Claim for the deposits of the late {f.get('deceased', '')}, {holdings}"
        return {"subject": f"Reminder: claim for the late {f.get('deceased', '')} is overdue",
                "body": frame(f, subject, body), "lang": "en", "kind": "reminder"}
    if kind == "objection":
        want = set(re.findall(r"\w{4,}", (p.get("request") or "").lower())) | {"grant", "probate", "limit", "threshold",
                                                                              "surety", "succession"}
        ranked = sorted(rules.values(), key=lambda r: -len(want & set(re.findall(r"\w{4,}", r["quote"].lower()))))
        quote = ranked[0]["quote"] if ranked else ""
        body = [f"You have asked for: {p.get('request') or 'an additional document'}. Your own published rule says:",
                f"“{quote}”" if quote else "", "",
                f"The amount of this claim is {f.get('amount')}. Please settle it on the papers already provided, "
                "or tell me in writing why this case is different."]
        what = (p.get("request") or "an additional document").split(";")[0]
        return {"subject": f"Your request for {what}: the late {f.get('deceased', '')}",
                "body": frame(f, f"Claim for the late {f.get('deceased', '')}, {holdings}", body),
                "lang": "en", "kind": "objection"}
    if kind == "chase":
        body = [f"I wrote to you on {f.get('notified_on')} to tell you of the death of {f.get('deceased', '')} and to "
                "ask what you hold for the estate and how to claim it. I have not had a reply.", "",
                "Please reply in writing with what you hold, the amount, and your claim procedure and forms."]
        return {"subject": f"Reminder: the late {f.get('deceased', '')}, my letter of {f.get('notified_on')}",
                "body": frame(f, f"The late {f.get('deceased', '')}, {holdings}" if holdings else
                              f"The late {f.get('deceased', '')}", body), "lang": "en", "kind": "chase"}
    body = ["Please tell me in writing what you still need to settle this claim."]
    return {"subject": f"Claim for the late {f.get('deceased', '')}",
            "body": frame(f, f"Claim for the late {f.get('deceased', '')}", body), "lang": "en", "kind": "request"}


