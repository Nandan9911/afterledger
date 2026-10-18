"""Country names from their ISO codes, in any language Babel knows (the code itself when it does not)."""
from __future__ import annotations


def country_name(code: str, lang: str = "en") -> str:
    try:
        from babel import Locale
        return Locale.parse(lang).territories.get(code.upper(), code)
    except Exception:  # an unknown language code
        return code
