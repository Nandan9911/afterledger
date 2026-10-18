"""Identifier checks done by code: checksums and formats. Values are shown masked."""
from __future__ import annotations

import re

from stdnum import bic, iban
from stdnum.exceptions import ValidationError
from stdnum.us import rtn

_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_SORT_CODE = re.compile(r"^\d{2}-?\d{2}-?\d{2}$")
_JP_BANK = re.compile(r"^\d{4}$")
_JP_BRANCH = re.compile(r"^\d{3}$")
_SG_NRIC = re.compile(r"^[STFGM]\d{7}[A-Z]$")


def check(kind: str, value: str) -> tuple[str, str]:
    """Return (result, note) with result in pass, fail, unknown."""
    v = (value or "").strip().upper().replace(" ", "")
    try:
        if kind == "iban":
            iban.validate(v)
            return "pass", "IBAN check digits are valid"
        if kind == "bic":
            bic.validate(v)
            return "pass", "BIC structure is valid"
        if kind == "us_routing":
            rtn.validate(v)
            return "pass", "Routing number checksum is valid"
    except ValidationError as exc:
        return "fail", str(exc) or "checksum failed"
    if kind == "ifsc":
        return ("pass", "IFSC format is valid") if _IFSC.match(v) else ("fail", "IFSC format is not valid")
    if kind == "pan":
        return ("pass", "PAN format is valid") if _PAN.match(v) else ("fail", "PAN format is not valid")
    if kind == "uk_sort_code":
        return ("pass", "Sort code format is valid") if _SORT_CODE.match(v) else ("fail", "Not a sort code")
    if kind == "jp_bank_code":
        return ("pass", "Bank code format is valid") if _JP_BANK.match(v) else ("fail", "Not a bank code")
    if kind == "jp_branch_code":
        return ("pass", "Branch code format is valid") if _JP_BRANCH.match(v) else ("fail", "Not a branch code")
    if kind == "sg_nric":
        return ("pass", "NRIC/FIN format is valid") if _SG_NRIC.match(v) else ("fail", "Not an NRIC/FIN")
    return "unknown", f"No known check for {kind}"


def mask(value: str, keep: int = 4) -> str:
    digits = re.sub(r"\s", "", value or "")
    if len(digits) <= keep:
        return digits
    return "ending " + digits[-keep:]
