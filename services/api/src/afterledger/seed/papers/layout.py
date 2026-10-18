"""A small layout engine for synthetic papers.

Every piece of text is placed in its own rectangle with PyMuPDF's insert_htmlbox, which shapes
Devanagari, Malayalam, Japanese, Arabic and Latin text with built-in Noto fonts. Because we place
each rectangle ourselves, we know exactly where every line sits; that becomes the paper's
sidecar of OCR lines and the evidence boxes the app highlights. (PyMuPDF's own text extraction
garbles complex scripts, so it is never used to recover these boxes.)

A page is built from blocks: titles, key-value grids, tables, paragraphs, stamps and signatures.
Tables flow onto new pages, repeating their header.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

import pymupdf

A4 = (595.0, 842.0)
WATERMARK = "SAMPLE DOCUMENT · NOT A REAL RECORD"


@dataclass
class Line:
    """One printed line, as an OCR engine would return it."""
    id: str
    text: str
    page: int                         # 1-based
    box: tuple[float, float, float, float]   # points on the page
    field: str | None = None          # schema field this line carries, if any
    row: int | None = None            # table row index (0-based, data rows only)
    col: str | None = None            # table column key
    ocr_visible: bool = True
    lang: str | None = None


@dataclass
class Style:
    font: str = "sans-serif"
    size: float = 9.0
    color: str = "#1b1b1b"
    accent: str = "#1D4E89"
    muted: str = "#555555"
    rule: str = "#9aa4ad"
    margin: float = 42.0
    line_gap: float = 3.0
    lang: str = "en"
    rtl: bool = False


@dataclass
class Doc:
    style: Style
    size: tuple[float, float] = A4
    pdf: Any = None
    pages: list[Any] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    y: float = 0.0
    counter: int = 0
    watermark: bool = True
    footer: str | None = None
    header_repeat: list[dict] | None = None

    def __post_init__(self) -> None:
        self.pdf = pymupdf.open()
        self.new_page()

    # ---- pages ----------------------------------------------------------------------------
    # PyMuPDF orphans Page objects when pages are added, so only the newest page is kept and
    # earlier pages are reloaded by number when needed.
    @property
    def page(self) -> Any:
        return self._current

    @property
    def page_no(self) -> int:
        return len(self.pages)

    def new_page(self) -> None:
        w, h = self.size
        self._current = self.pdf.new_page(width=w, height=h)
        self.pages.append(len(self.pages))
        self.y = self.style.margin

    def space_left(self) -> float:
        return self.size[1] - self.style.margin - 28 - self.y

    def ensure(self, height: float) -> bool:
        """Start a new page when the next block does not fit. Returns True if it broke."""
        if self.space_left() < height:
            self.new_page()
            return True
        return False

    # ---- primitive text --------------------------------------------------------------------
    def put(self, rect: tuple[float, float, float, float], text: str, *, size: float | None = None,
            bold: bool = False, color: str | None = None, align: str = "left", font: str | None = None,
            field_name: str | None = None, row: int | None = None, col: str | None = None,
            lang: str | None = None, record: bool = True, italic: bool = False,
            letter_spacing: float = 0.0, ocr_visible: bool = True) -> Line | None:
        st = self.style
        x0, y0, x1, y1 = rect
        lang = lang or st.lang
        rtl = st.rtl if lang == st.lang else lang in ("ar", "he", "fa", "ur")
        css = (f"* {{font-family: {font or st.font}; font-size: {size or st.size}px; color: {color or st.color};"
               f" margin: 0; padding: 0; line-height: 1.15; letter-spacing: {letter_spacing}px;}}"
               f" div {{text-align: {('right' if rtl and align == 'left' else align)};"
               f" font-weight: {'bold' if bold else 'normal'}; font-style: {'italic' if italic else 'normal'};}}")
        dir_attr = ' dir="rtl"' if rtl else ""
        text = str(text)
        body = html.escape(text).replace("\n", "<br/>")
        self.page.insert_htmlbox(pymupdf.Rect(x0, y0, x1, y1 + 4), f'<div lang="{lang}"{dir_attr}>{body}</div>', css=css)
        if not record or not text.strip():
            return None
        self.counter += 1
        line = Line(id=f"l{self.counter:04d}", text=text, page=self.page_no, box=(x0, y0, x1, y1),
                    field=field_name, row=row, col=col, ocr_visible=ocr_visible, lang=lang)
        self.lines.append(line)
        return line

    def text_height(self, size: float | None = None, lines: int = 1) -> float:
        s = size or self.style.size
        return lines * s * 1.25

    def rule(self, y: float | None = None, x0: float | None = None, x1: float | None = None,
             width: float = 0.6, color: str | None = None) -> None:
        st = self.style
        y = self.y if y is None else y
        x0 = st.margin if x0 is None else x0
        x1 = self.size[0] - st.margin if x1 is None else x1
        self.page.draw_line((x0, y), (x1, y), color=_rgb(color or st.rule), width=width)

    def box(self, rect: tuple[float, float, float, float], *, color: str | None = None, width: float = 0.6,
            fill: str | None = None, dashes: str | None = None) -> None:
        self.page.draw_rect(pymupdf.Rect(*rect), color=_rgb(color or self.style.rule), width=width,
                            fill=_rgb(fill) if fill else None, dashes=dashes)

    # ---- blocks ------------------------------------------------------------------------------
    def title(self, text: str, *, size: float = 15, align: str = "left", color: str | None = None,
              field_name: str | None = None, lang: str | None = None, gap: float = 4) -> Line | None:
        h = self.text_height(size)
        self.ensure(h)
        w = self.size[0]
        line = self.put((self.style.margin, self.y, w - self.style.margin, self.y + h), text, size=size,
                        bold=True, align=align, color=color or self.style.accent, field_name=field_name, lang=lang)
        self.y += h + gap
        return line

    def text(self, text: str, *, size: float | None = None, bold: bool = False, align: str = "left",
             color: str | None = None, field_name: str | None = None, lang: str | None = None,
             gap: float | None = None, indent: float = 0.0, italic: bool = False,
             width: float | None = None) -> Line | None:
        """One line of text (wrap long text yourself with paragraph())."""
        h = self.text_height(size)
        self.ensure(h)
        x0 = self.style.margin + indent
        x1 = x0 + width if width else self.size[0] - self.style.margin
        line = self.put((x0, self.y, x1, self.y + h), text, size=size, bold=bold, align=align, color=color,
                        field_name=field_name, lang=lang, italic=italic)
        self.y += h + (self.style.line_gap if gap is None else gap)
        return line

    def paragraph(self, text: str, *, size: float | None = None, width_chars: int = 95,
                  lang: str | None = None, gap: float = 6, field_name: str | None = None,
                  indent: float = 0.0, color: str | None = None) -> list[Line]:
        out = []
        for ln in _wrap(text, width_chars):
            line = self.text(ln, size=size, lang=lang, gap=1, field_name=field_name, indent=indent, color=color)
            if line:
                out.append(line)
        self.y += gap
        return out

    def kv(self, items: list[tuple[str, str, str | None]], *, cols: int = 2, label_w: float = 110,
           size: float | None = None, gap: float = 6, lang: str | None = None,
           value_bold: bool = True, label_color: str | None = None) -> dict[str, Line]:
        """A grid of 'label: value' pairs. items: (label, value, field name)."""
        st = self.style
        w = self.size[0] - 2 * st.margin
        col_w = w / cols
        h = self.text_height(size) + 3
        found: dict[str, Line] = {}
        for i in range(0, len(items), cols):
            self.ensure(h)
            for j, (label, value, fname) in enumerate(items[i:i + cols]):
                x = st.margin + j * col_w
                self.put((x, self.y, x + label_w - 4, self.y + h), label, size=size, color=label_color or st.muted,
                         lang=lang)
                line = self.put((x + label_w, self.y, x + col_w - 6, self.y + h), value, size=size,
                                bold=value_bold, field_name=fname, lang=lang)
                if fname and line:
                    found[fname] = line
            self.y += h
        self.y += gap
        return found

    def table(self, columns: list[dict], rows: list[dict], *, size: float | None = None, row_h: float | None = None,
              header_fill: str = "#E8EEF3", zebra: str | None = "#F7F9FB", gap: float = 8,
              lang: str | None = None, before_rows: list[dict] | None = None,
              repeat_header: bool = True, grid: bool = True,
              page_header: Any = None) -> list[dict[str, Line]]:
        """columns: [{key, label, width (fraction), align}]; rows: [{key: text, '_field': {...}}].
        Returns per data row the Line of each cell."""
        st = self.style
        size = size or st.size
        row_h = row_h or self.text_height(size) + 5
        x0 = st.margin
        total_w = self.size[0] - 2 * st.margin
        xs = [x0]
        for c in columns:
            xs.append(xs[-1] + total_w * c["width"])

        def header() -> None:
            self.ensure(row_h * 2)
            if header_fill:
                self.box((x0, self.y, xs[-1], self.y + row_h), color=st.rule, fill=header_fill, width=0.4)
            for c, xa, xb in zip(columns, xs, xs[1:], strict=False):
                self.put((xa + 3, self.y + 2.5, xb - 3, self.y + row_h - 1), c["label"], size=size - 0.5, bold=True,
                         align=c.get("align", "left"), lang=lang)
            self.y += row_h

        header()
        out: list[dict[str, Line]] = []
        for idx, r in enumerate(rows):
            if self.space_left() < row_h:
                self.new_page()
                if page_header:
                    page_header(self)
                if repeat_header:
                    header()
            if zebra and idx % 2 == 1:
                self.box((x0, self.y, xs[-1], self.y + row_h), color=zebra, fill=zebra, width=0)
            cells: dict[str, Line] = {}
            fields = r.get("_fields", {})
            for c, xa, xb in zip(columns, xs, xs[1:], strict=False):
                val = r.get(c["key"], "")
                if val is None or val == "":
                    continue
                line = self.put((xa + 3, self.y + 2.5, xb - 3, self.y + row_h - 1), str(val), size=size,
                                align=c.get("align", "left"), row=r.get("_row", idx), col=c["key"],
                                field_name=fields.get(c["key"]), lang=lang, bold=bool(r.get("_bold")))
                if line:
                    cells[c["key"]] = line
            if grid:
                self.rule(self.y + row_h, x0, xs[-1], width=0.3)
            self.y += row_h
            out.append(cells)
        self.y += gap
        return out

    def stamp(self, text: str, rect: tuple[float, float, float, float], *, color: str = "#8B1E3F",
              size: float = 9, record: bool = True, field_name: str | None = None, lang: str | None = None,
              round_: bool = False) -> Line | None:
        self.box(rect, color=color, width=1.2)
        if round_:
            x0, y0, x1, y1 = rect
            self.page.draw_oval(pymupdf.Rect(x0 + 3, y0 + 3, x1 - 3, y1 - 3), color=_rgb(color), width=0.8)
        x0, y0, x1, y1 = rect
        return self.put((x0 + 4, y0 + (y1 - y0) / 2 - size * 0.7, x1 - 4, y1 - 2), text, size=size, bold=True,
                        color=color, align="center", record=record, field_name=field_name, lang=lang)

    def signature(self, label: str, *, name: str | None = None, x: float | None = None, width: float = 170,
                  lang: str | None = None, signed: bool = True) -> None:
        st = self.style
        x = st.margin if x is None else x
        self.ensure(40)
        y = self.y + 22
        if signed and name:
            self.put((x + 6, y - 18, x + width, y - 2), name, size=13, italic=True, color="#20307A",
                     font="serif", record=False)
        self.page.draw_line((x, y), (x + width, y), color=_rgb(st.rule), width=0.6)
        self.put((x, y + 2, x + width, y + 14), label, size=7.5, color=st.muted, lang=lang)
        self.y = y + 18

    def gap(self, h: float) -> None:
        self.y += h

    # ---- finishing ------------------------------------------------------------------------------
    def finish(self) -> bytes:
        w, h = self.size
        total = len(self.pages)
        for i in range(1, total + 1):
            page = self.pdf[i - 1]
            if self.watermark:
                css = "* {font-family: sans-serif; font-size: 7px; color: #B03A2E; letter-spacing: 0.5px;}"
                page.insert_htmlbox(pymupdf.Rect(0, h - 18, w, h - 6),
                                    f'<div style="text-align:center">{html.escape(WATERMARK)}</div>', css=css)
            if self.footer:
                css = "* {font-family: sans-serif; font-size: 7px; color: #666666;}"
                text = self.footer.format(page=i, pages=total)
                page.insert_htmlbox(pymupdf.Rect(self.style.margin, h - 30, w - self.style.margin, h - 19),
                                    f'<div style="text-align:right">{html.escape(text)}</div>', css=css)
        return self.pdf.tobytes(deflate=True, garbage=4)   # 4 merges the copies of each embedded font


def _rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap; CJK text (no spaces) wraps by characters."""
    out: list[str] = []
    for para in text.split("\n"):
        if " " not in para.strip() and len(para) > width:
            out.extend(para[i:i + width] for i in range(0, len(para), width))
            continue
        cur = ""
        for word in para.split(" "):
            if len(cur) + len(word) + 1 > width and cur:
                out.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        out.append(cur)
    return out
