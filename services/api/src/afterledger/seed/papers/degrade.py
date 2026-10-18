"""Turns rendered pages into images: a clean scan, or a phone photo of a paper on a table.

A photo is the page rendered to pixels, rotated a little, lit unevenly, blurred and compressed.
The same affine transform is applied to every line box, so evidence highlights stay exact.
"""
from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass

import pymupdf
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


@dataclass
class PageImage:
    data: bytes
    mime: str
    width: int
    height: int
    boxes: dict[str, list[float]]     # line id -> normalised [x0, y0, x1, y1] on this image


def scan(pdf_bytes: bytes, page_index: int, line_boxes: dict[str, tuple[float, float, float, float]],
         dpi: int = 110) -> PageImage:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    w_pt, h_pt = page.rect.width, page.rect.height
    boxes = {lid: [b[0] / w_pt, b[1] / h_pt, b[2] / w_pt, b[3] / h_pt] for lid, b in line_boxes.items()}
    return PageImage(data=pix.tobytes("png"), mime="image/png", width=pix.width, height=pix.height, boxes=boxes)


def photo(pdf_bytes: bytes, page_index: int, line_boxes: dict[str, tuple[float, float, float, float]],
          *, seed: int, angle: float | None = None, blur: float = 0.6, dpi: int = 120,
          crop: tuple[float, float, float, float] | None = None, paper_tint: str = "#F4EEDC",
          table: str = "#6B5644", quality: int = 72) -> PageImage:
    """A phone photo of (part of) the page. crop is a fraction box of the page to keep."""
    rnd = random.Random(seed)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    w_pt, h_pt = page.rect.width, page.rect.height
    sx, sy = pix.width / w_pt, pix.height / h_pt

    # boxes in pixels
    px = {lid: [b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy] for lid, b in line_boxes.items()}
    if crop:
        cx0, cy0, cx1, cy1 = (int(crop[0] * pix.width), int(crop[1] * pix.height),
                              int(crop[2] * pix.width), int(crop[3] * pix.height))
        img = img.crop((cx0, cy0, cx1, cy1))
        px = {lid: [b[0] - cx0, b[1] - cy0, b[2] - cx0, b[3] - cy0] for lid, b in px.items()
              if b[1] >= cy0 - 2 and b[3] <= cy1 + 2}

    # paper tint (multiply white towards the tint)
    tint = Image.new("RGB", img.size, paper_tint)
    img = Image.blend(img, Image.composite(img, tint, img.convert("L").point(lambda v: 255 if v < 200 else 0)), 0.55)

    # place on a table with a margin, then rotate
    margin = int(0.07 * max(img.size))
    canvas = Image.new("RGB", (img.width + 2 * margin, img.height + 2 * margin), table)
    canvas.paste(img, (margin, margin))
    shadow = ImageDraw.Draw(canvas)
    shadow.rectangle((margin + 6, margin + img.height, margin + img.width + 6, margin + img.height + 6), fill="#3B2E24")
    angle = rnd.uniform(-2.4, 2.4) if angle is None else angle
    rotated = canvas.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=table)

    # transform boxes: translate by margin, rotate around the canvas centre (PIL rotates counter-clockwise)
    cxc, cyc = canvas.width / 2, canvas.height / 2
    a = math.radians(angle)
    cos_a, sin_a = math.cos(a), math.sin(a)

    def rot(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - cxc, y - cyc
        return cxc + dx * cos_a + dy * sin_a, cyc - dx * sin_a + dy * cos_a

    boxes: dict[str, list[float]] = {}
    for lid, (x0, y0, x1, y1) in px.items():
        corners = [rot(x + margin, y + margin) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        boxes[lid] = [max(0.0, min(xs) / rotated.width), max(0.0, min(ys) / rotated.height),
                      min(1.0, max(xs) / rotated.width), min(1.0, max(ys) / rotated.height)]

    # uneven light, blur, a little noise, JPEG
    light = Image.linear_gradient("L").resize(rotated.size).rotate(rnd.uniform(20, 70), expand=False)
    light = light.point(lambda v: int(205 + v * 50 / 255))
    rotated = Image.composite(rotated, ImageEnhance.Brightness(rotated).enhance(0.82), light)
    rotated = rotated.filter(ImageFilter.GaussianBlur(blur))
    noise = Image.effect_noise(rotated.size, 9).convert("RGB")
    rotated = Image.blend(rotated, noise, 0.04)
    buf = io.BytesIO()
    rotated.save(buf, format="JPEG", quality=quality, optimize=True)
    return PageImage(data=buf.getvalue(), mime="image/jpeg", width=rotated.width, height=rotated.height, boxes=boxes)
