"""Adding papers to a case: uploads (PDFs and photos) and the demo families' synthetic papers.

Every page gets an image (what the evidence viewer shows) and OCR lines with boxes normalised to
that image. Uploaded PDFs give their own text lines; photos go to Document AI in live mode. The
demo papers carry a sidecar of the lines they were rendered with, which plays Document AI's part
offline.
"""
from __future__ import annotations

import asyncio
import io
import re
from typing import Any

import pymupdf
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from ..domain.case import OcrLine, PageRef, Paper
from ..domain.ids import new_id

PDF_DPI = 110
PAGES_AT_ONCE = 4                   # an upload's pages stored and read side by side
SHOWN = {"JPEG", "PNG", "WEBP"}     # what every browser shows and every reader takes

register_heif_opener()              # an iPhone's photos are HEIC


def viewable(data: bytes, mime: str) -> tuple[bytes, str, Image.Image]:
    """A photo as a page image: a HEIC photo (or any other format a browser cannot show) becomes a JPEG, turned
    upright; the original file is kept as it came."""
    img = Image.open(io.BytesIO(data))
    if img.format in SHOWN:
        return data, mime, img
    buf = io.BytesIO()
    ImageOps.exif_transpose(img).convert("RGB").save(buf, "JPEG", quality=90)
    data = buf.getvalue()
    return data, "image/jpeg", Image.open(io.BytesIO(data))


def _pdf_lines(page: Any) -> list[dict]:
    """Text lines of a digital PDF page with boxes normalised to the page."""
    w, h = page.rect.width, page.rect.height
    out = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            out.append({"text": text, "box": [x0 / w, y0 / h, x1 / w, y1 / h]})
    return out


async def add_upload(deps: Any, case_id: str, files: list[tuple[str, bytes, str]], *, uploaded_by: str | None,
                     title: str | None = None) -> Paper:
    """files: (name, bytes, mime). Several images make one multi-page paper; a PDF is one paper."""
    paper_id = new_id("pp")
    pages: list[PageRef] = []
    lines: list[OcrLine] = []
    origin = "upload"
    first_ref = None
    # each page drawn here, then stored and read (OCR) side by side, a few at a time (until 19 October 2026 one
    # after another: three pages kept the family waiting 7.4 s)
    todo: list[dict] = []
    for name, data, mime in files:
        safe = re.sub(r"[^\w.\-]+", "_", name).strip("._") or "upload"   # safe on every file system
        ref = await deps.blobs.put(f"cases/{case_id}/papers/{paper_id}/original/{safe}", data, mime)
        first_ref = first_ref or ref
        if mime == "application/pdf" or name.lower().endswith(".pdf"):
            doc = pymupdf.open(stream=data, filetype="pdf")
            for page in doc:
                pix = page.get_pixmap(dpi=PDF_DPI, alpha=False)
                n = len(todo) + 1
                todo.append({"n": n, "image": pix.tobytes("png"), "mime": "image/png", "ext": ".png",
                             "width": pix.width, "height": pix.height, "text": _pdf_lines(page)})
        else:
            origin = "photo"
            data, mime, img = viewable(data, mime)
            todo.append({"n": len(todo) + 1, "image": data, "mime": mime, "ext": _ext(mime), "width": img.width,
                         "height": img.height, "text": None})
    gate = asyncio.Semaphore(PAGES_AT_ONCE)

    async def store_and_read(pg: dict) -> tuple[PageRef, list[OcrLine]]:
        async with gate:
            page_id = f"{paper_id}-p{pg['n']}"
            img_ref = await deps.blobs.put(f"cases/{case_id}/papers/{paper_id}/p{pg['n']}{pg['ext']}", pg["image"],
                                           pg["mime"])
            extra = {"pdf_page_text": pg["text"]} if pg["text"] is not None else {}
            found = await deps.ocr.read_page(case_id=case_id, paper_id=paper_id, page_id=page_id, image=pg["image"],
                                             mime=pg["mime"], **extra)
            return PageRef(id=page_id, number=pg["n"], image=img_ref, width=pg["width"], height=pg["height"]), found

    for ref_, found in await asyncio.gather(*(store_and_read(pg) for pg in todo)):
        pages.append(ref_)
        lines.extend(found)
    paper = Paper(id=paper_id, case_id=case_id, title=title or files[0][0].rsplit(".", 1)[0],
                  file=first_ref, pages=pages, origin=origin, status="queued", stage="Waiting to be read",
                  uploaded_by=uploaded_by, uploaded_at=deps.now(), synthetic=False)
    await deps.cases.put(case_id, "papers", paper)
    await deps.cases.put_many(case_id, "lines", lines)
    return paper


def _ext(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}.get(mime, ".bin")


async def add_demo_paper(deps: Any, case_id: str, family: Any, paper_key: str, *, uploaded_by: str | None) -> Paper:
    """Renders one of a demo family's papers and adds it as if the family had uploaded it."""
    spec = family.paper(paper_key)
    built = family.built(paper_key)
    paper_id = f"pp_{paper_key}"
    pages: list[PageRef] = []
    lines: list[OcrLine] = []
    if spec.get("origin") == "photo":
        first_ref = None
        for i, im in enumerate(built.images, start=1):
            ref = await deps.blobs.put(f"cases/{case_id}/papers/{paper_id}/p{i}{_ext(im.mime)}", im.data, im.mime)
            first_ref = first_ref or ref
            pages.append(PageRef(id=f"{paper_id}-p{i}", number=i, image=ref, width=im.width, height=im.height))
        original = first_ref
    else:
        original = await deps.blobs.put(f"cases/{case_id}/papers/{paper_id}/original/{paper_key}.pdf",
                                        built.rendered.pdf, "application/pdf")
        for i, im in enumerate(built.images, start=1):
            ref = await deps.blobs.put(f"cases/{case_id}/papers/{paper_id}/p{i}.png", im.data, im.mime)
            pages.append(PageRef(id=f"{paper_id}-p{i}", number=i, image=ref, width=im.width, height=im.height))
    confidence = 0.93 if spec.get("origin") == "photo" else 0.99
    for i, im in enumerate(built.images, start=1):
        page_id = f"{paper_id}-p{i}"
        sidecar = [{"id": f"{paper_id}-{ln.id}", "text": ln.text, "box": im.boxes[ln.id], "confidence": confidence,
                    "ocr_visible": ln.ocr_visible}
                   for ln in built.rendered.lines if ln.page == i and ln.id in im.boxes]
        # live: Document AI reads the page image; offline: the sidecar plays the part of its output
        lines.extend(await deps.ocr.read_page(case_id=case_id, paper_id=paper_id, page_id=page_id, image=im.data,
                                              mime=im.mime, sidecar=sidecar))
    paper = Paper(id=paper_id, case_id=case_id, title=spec["title"], title_en=spec.get("title_en"), file=original, pages=pages,
                  origin="photo" if spec.get("origin") == "photo" else "upload", status="queued",
                  stage="Waiting to be read", uploaded_by=uploaded_by, uploaded_at=deps.now(), synthetic=True,
                  fixture_key=f"{family.key}/{paper_key}")
    await deps.cases.put(case_id, "papers", paper)
    await deps.cases.put_many(case_id, "lines", lines)
    return paper
