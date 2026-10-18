"""Letters as printable PDFs, in any script: each paragraph is shaped by PyMuPDF's HTML box (HarfBuzz
with built-in Noto fonts), flowing onto new pages as needed. Right-to-left languages are set right to left."""
from __future__ import annotations

import html

import pymupdf

RTL = {"ar", "he", "fa", "ur"}
PAGE = (595.0, 842.0)
MARGIN = 62.0


def letter_pdf(text: str, *, lang: str = "en", title: str | None = None, footer: str | None = None) -> bytes:
    doc = pymupdf.open()
    lang = (lang or "en").split("-")[0]
    rtl = lang in RTL
    css = ("* {font-family: sans-serif; font-size: 11px; color: #14202B; margin: 0; padding: 0; line-height: 1.5;}"
           " div {text-align: left;} .t {font-weight: bold;}")   # MuPDF reads 'left' as the start side
    small = "* {font-family: sans-serif; font-size: 7.5px; color: #5F6E78; margin: 0; padding: 0;}"
    dir_attr = ' dir="rtl"' if rtl else ""
    left, right, top, bottom = MARGIN, PAGE[0] - MARGIN, MARGIN, PAGE[1] - MARGIN - 18

    def new_page():
        page = doc.new_page(width=PAGE[0], height=PAGE[1])
        if footer:
            page.insert_htmlbox(pymupdf.Rect(left, PAGE[1] - MARGIN, right, PAGE[1] - MARGIN + 24),
                                f"<div>{html.escape(footer)}</div>", css=small)
        return page

    page = new_page()
    y = top
    blocks = []
    if title:
        blocks.append(f'<div class="t" lang="{lang}"{dir_attr}>{html.escape(title)}</div>')
    for para in text.replace("\r\n", "\n").split("\n\n"):
        body = "<br/>".join(html.escape(line) for line in para.split("\n")) or "&nbsp;"
        blocks.append(f'<div lang="{lang}"{dir_attr}>{body}</div>')
    for block in blocks:
        spare, _ = page.insert_htmlbox(pymupdf.Rect(left, y, right, bottom), block, css=css, scale_low=1)
        if spare < 0:                      # does not fit on what is left of this page
            page = new_page()
            y = top
            spare, _ = page.insert_htmlbox(pymupdf.Rect(left, y, right, bottom), block, css=css, scale_low=0.5)
        y = bottom - max(spare, 0) + 9
    out = doc.tobytes(deflate=True, garbage=4)
    doc.close()
    return out
