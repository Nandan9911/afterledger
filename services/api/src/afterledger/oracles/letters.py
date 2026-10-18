"""The frame of an offline letter, in the custom of the country it goes to."""
from __future__ import annotations


def frame(f: dict, subject: str, body: list[str]) -> str:
    """India addresses the branch manager and puts the subject under the address; Britain and most of the
    Commonwealth put the subject under 'Dear Sir or Madam'; America puts a 'Re:' line above it and closes with
    'Sincerely'. A scheme claimed through a bank account goes to that bank's branch (``through``)."""
    where = (f.get("through") or f.get("institution") or "") + (f", {f['branch']}" if f.get("branch") else "")
    country = (f.get("country") or "IN").split("-")[0]
    if country == "IN":
        head, close = [f"To the Branch Manager, {where}", "", subject], "Yours faithfully,"
    elif country == "US":
        head, close = [where, "", f"Re: {subject}", "", "Dear Sir or Madam,"], "Sincerely,"
    else:
        head, close = [where, "", "Dear Sir or Madam,", "", subject], "Yours faithfully,"
    return "\n".join([*head, "", *body, "", close, f.get("claimant") or ""])
