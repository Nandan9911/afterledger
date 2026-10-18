"""The OCR reading path through Document AI's Enterprise Document OCR processor.

Each page is read twice by different methods (this path, and Gemini reading the image), and the two readings
are reconciled by code. A digital PDF already carries its own text lines, which are used as they are. Document
AI does not claim handwriting support for some scripts (Malayalam and Tamil among them); for those lines the
second path is the one that reads them, and the reconciliation says the value was read once.
"""
from __future__ import annotations

import logging
from typing import Any

from ...domain.case import OcrLine

log = logging.getLogger(__name__)


class DocumentAiOcr:
    def __init__(self, project: str, location: str, processor_id: str, fallback: Any, client: Any = None,
                 health: Any = None):
        self.health = health
        from google.cloud import documentai
        self.dai = documentai
        if client is None:
            client = documentai.DocumentProcessorServiceAsyncClient(
                client_options={"api_endpoint": f"{location}-documentai.googleapis.com"})
        self.client = client
        # the processor's id, or its full resource name (as Terraform reports it)
        self.name = processor_id if "/processors/" in processor_id else \
            f"projects/{project}/locations/{location}/processors/{processor_id}"
        self.fallback = fallback

    async def read_page(self, *, case_id: str, paper_id: str, page_id: str, image: bytes, mime: str,
                        pdf_page_text: list[dict] | None = None,
                        sidecar: list[dict] | None = None) -> list[OcrLine]:
        if pdf_page_text:                 # a digital PDF carries its own text: nothing to recognise
            return await self.fallback.read_page(case_id=case_id, paper_id=paper_id, page_id=page_id, image=image,
                                                 mime=mime, pdf_page_text=pdf_page_text, sidecar=None)
        dai = self.dai
        request = dai.ProcessRequest(
            name=self.name, raw_document=dai.RawDocument(content=image, mime_type=mime or "image/png"),
            process_options=dai.ProcessOptions(ocr_config=dai.OcrConfig(enable_native_pdf_parsing=True)))
        try:
            result = await self.client.process_document(request=request)
        except Exception as e:             # the second reading path still reads the page
            log.warning("Document AI could not read %s: %s", page_id, e)
            if self.health is not None:
                self.health.failed("document_ai", e)        # a strict live run stops here
            return []
        if self.health is not None:
            self.health.succeeded("document_ai")
        return lines_from_document(result.document, case_id=case_id, paper_id=paper_id, page_id=page_id)


def lines_from_document(doc: Any, *, case_id: str, paper_id: str, page_id: str) -> list[OcrLine]:
    """Document AI lines as OcrLines: text from the document's text anchors, boxes normalised to 0..1."""
    out: list[OcrLine] = []
    if not doc.pages:
        return out
    page = doc.pages[0]
    width = float(getattr(page.dimension, "width", 0) or 0)
    height = float(getattr(page.dimension, "height", 0) or 0)
    for i, line in enumerate(page.lines):
        text = "".join(doc.text[int(seg.start_index or 0):int(seg.end_index or 0)]
                       for seg in line.layout.text_anchor.text_segments).strip()
        if not text:
            continue
        poly = line.layout.bounding_poly
        if poly.normalized_vertices:
            xs = [v.x for v in poly.normalized_vertices]
            ys = [v.y for v in poly.normalized_vertices]
        elif poly.vertices and width and height:
            xs = [v.x / width for v in poly.vertices]
            ys = [v.y / height for v in poly.vertices]
        else:
            continue
        out.append(OcrLine(id=f"{page_id}-l{i + 1:02d}", case_id=case_id, paper_id=paper_id, page_id=page_id,
                           text=text, box=[min(xs), min(ys), max(xs), max(ys)],
                           confidence=float(line.layout.confidence or 0.9), source="document_ai"))
    return out
