"""Offline answers for the forms and letters agents."""
from __future__ import annotations

import re

from ..adapters.llm.models import OracleRequest
from . import Ctx, oracle
from .letters import frame

_JA = re.compile(r"[぀-ヿ一-鿿]")


def ja_name(name: str) -> str:
    """The Japanese name of a paper whose English name carries it in brackets, for a letter in Japanese:
    'Seal registration certificates of every heir (相続人全員の印鑑証明書)' -> '相続人全員の印鑑証明書', and
    an alternative '(or ...)' becomes （または…）. A name with no Japanese is kept as it is."""
    main, _, alt = name.partition(" (or ")
    alt = alt[:-1] if alt.endswith(")") else alt

    def one(x: str) -> str:
        ja = [s for s in re.findall(r"\(([^()]*)\)", x) if _JA.search(s)]
        return ja[-1] if ja and not _JA.search(re.sub(r"\([^()]*\)", "", x)) else x

    return one(main.strip()) + (f"（または{one(alt.strip())}）" if alt else "")


@oracle("form_scout")
def form_scout(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    found = p.get("found")
    return {"form_key": p.get("form_key") if found else None, "source_url": None, "letter_instead": not found,
            "reason": "Found in the form library." if found else "No form in the library; a letter asks for it."}


@oracle("form_understander")
def form_understander(ctx: Ctx, req: OracleRequest) -> dict:
    form = ctx.payload(req).get("form") or {}
    return {"form_key": form.get("key", ""), "fields": [{"field_id": f, "meaning": f.replace("_", " "), "fill_by": "agent"}
                                                       for f in form.get("fields", [])], "conditions": []}


_SHORT = [(r"\bRoad\b", "Rd"), (r"\bStreet\b", "St"), (r"\bAvenue\b", "Ave"), (r"\bApartments?\b", "Apts"),
          (r"\bBuilding\b", "Bldg"), (r"\bDistrict\b", "Dist."), (r"\bNagar\b", "Ngr"), (r"\bColony\b", "Col."),
          (r"\bNear\b", "Nr"), (r"\bOpposite\b", "Opp."), (r"\bSector\b", "Sec."), (r"\bFloor\b", "Fl.")]


def shorter(text: str, holds: int | None) -> str:
    """A value too long for its box, in standard abbreviations; then, if still too long, without its least
    specific trailing part (a country after the postcode, say). Numbers are never touched."""
    out = text
    for pat, rep in _SHORT:
        out = re.sub(pat, rep, out)
    out = re.sub(r"\s+", " ", out).strip()
    while holds and len(out) > holds and "," in out:
        head, _, tail = out.rpartition(",")
        if re.search(r"\d", tail):
            break
        out = head.strip()
    return out


@oracle("value_composer")
def value_composer(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    values = p.get("values") or {}
    sources = p.get("sources") or {}
    fix = p.get("fix_fields") or {}
    out = []
    for fid, v in values.items():
        if v in (None, "", False, True):
            continue
        if fid in fix and fix[fid].get("issue") == "too long for its box":
            out.append({"field_id": fid, "value": shorter(str(v), fix[fid].get("box_holds_characters")), "source": "",
                        "certainty": "sure", "note": "Shortened to fit its box, with standard abbreviations."})
            continue
        note, certainty = "", "sure"
        if fid == "deceased_name" and "account" in sources.get("deceased.name", "").lower() + \
                sources.get("deceased.name", "").lower():
            note = "Spelled as the institution's own records spell it."
        out.append({"field_id": fid, "value": str(v), "source": "", "certainty": certainty, "note": note})
    return {"values": out}


@oracle("visual_critic")
def visual_critic(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    over = p.get("overflow") or []
    rb = p.get("readback") or {}
    problems = [f"Text in {f} is longer than its box." for f in over]
    problems += [f"{f} did not read back as intended." for f in rb.get("failures", [])]
    return {"ok": not problems, "problems": problems}


@oracle("letter_writer")
def letter_writer(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    f = p.get("facts") or {}
    lang = p.get("lang", "en")
    if p.get("challenge"):
        from .research import fixture_for
        fx = fixture_for(p["challenge"]) or {}
        lt = fx.get("letter")
        if lt:
            return {"subject": lt["subject"], "body": lt["body"], "lang": lt.get("lang", lang),
                    "body_en": lt.get("translation", ""), "subject_en": lt.get("subject_en", "")}
        body = ("Dear Sir or Madam,\n\nMy [relationship], [FULL NAME], died on [DATE]. Please tell me in writing what you "
                "need from the family to deal with [his/her] [account or policy] with you.\n\nYours faithfully,")
        return {"subject": "After a death: what do you need from the family?", "body": body, "lang": "en"}
    fam = ctx.family(req)
    if fam:
        for c in fam.get("claims", []):
            cov = c.get("cover_letter")
            if cov and cov.get("match", "") in (f.get("claim") or ""):
                return {"subject": cov["subject"], "body": cov["body"], "lang": cov.get("lang", lang),
                        "salutation": "", "closing": ""}
    items = [i for i in f.get("items", []) if i != "Covering letter"]
    encl = f.get("enclosures", [])
    if lang == "ja":
        body = (f"{f.get('institution', '')} 御中\n\n故 {f.get('deceased', '')} の相続手続きについて、"
                f"必要書類を同封いたします。\n\n" + "\n".join(f"・{ja_name(x)}" for x in items + encl) +
                "\n\n書類がそろいましたら、ご連絡くださいますようお願い申し上げます。\n\n" + (f.get("claimant") or ""))
        return {"subject": "相続手続き書類の送付について", "body": body, "lang": "ja"}
    subject = f"Claim for the {(f.get('claim') or '').split(': ', 1)[-1]} of the late {f.get('deceased', '')}"
    lines = ["I enclose the papers for this claim:"] + [f"- {x}" for x in items + encl]
    lines += ["", "Please confirm in writing when you have every paper, so that the time for settling the claim can be "
              "counted from that day."]
    return {"subject": f"Claim for the late {f.get('deceased', '')}", "body": frame(f, subject, lines), "lang": "en"}


@oracle("back_translator")
def back_translator(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    same = p.get("original") == p.get("translation")
    return {"back_translation": p.get("original", ""), "differences": [] if not same else
            ["No reviewed translation offline; the copy is the original."], "ok": True}
