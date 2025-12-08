"""PDF label generation utilities for warehouse flows."""

from __future__ import annotations

import os
from io import BytesIO

from loguru import logger

from barcode import get as barcode_get
from barcode.writer import ImageWriter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from botapp.products_service import CatalogProduct


async def build_labels_pdf(product: CatalogProduct, quantity: int) -> bytes:
    """Build a PDF with repeated barcode labels for the given product.

    Returns the PDF content as bytes to allow in-memory sending via Telegram.
    """

    if quantity <= 0:
        raise ValueError("Quantity must be positive to generate labels")
    if not product.barcode:
        raise ValueError("Barcode is required to generate labels")

    buffer = BytesIO()

    label_width = 90 * mm
    label_height = 50 * mm
    font_name = _get_primary_font()

    barcode_buffer = None
    image_reader = None
    try:
        barcode_buffer = _build_barcode_image(str(product.barcode))
        image_reader = ImageReader(barcode_buffer)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to render barcode image, falling back to text-only labels: {}",
            exc,
        )

    c = canvas.Canvas(buffer, pagesize=(label_width, label_height))
    for _ in range(quantity):
        _safe_draw_string(
            c,
            10 * mm,
            label_height - 12 * mm,
            product.name[:60],
            font_name,
            10,
            bold=True,
        )
        _safe_draw_string(
            c,
            10 * mm,
            label_height - 18 * mm,
            f"SKU: {product.sku}",
            font_name,
            9,
        )
        if image_reader:
            c.drawImage(
                image_reader,
                10 * mm,
                10 * mm,
                width=label_width - 20 * mm,
                height=20 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
        else:
            _safe_draw_string(
                c,
                10 * mm,
                18 * mm,
                f"Штрихкод: {product.barcode}",
                font_name,
                9,
            )
        c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.read()


def _build_barcode_image(barcode_value: str):
    writer = ImageWriter()
    barcode_obj = barcode_get("code128", barcode_value, writer=writer)
    buffer = BytesIO()
    barcode_obj.write(buffer, options={"module_width": 0.2, "text_distance": 1.0})
    buffer.seek(0)
    return buffer


def _safe_draw_string(
    pdf_canvas: canvas.Canvas,
    x: float,
    y: float,
    text: str,
    font_name: str | None,
    font_size: int,
    *,
    bold: bool = False,
):
    """Draw text on the canvas, tolerating Unicode issues.

    Tries to use a Unicode-capable font when available; falls back to sanitizing
    unsupported characters to keep PDF generation stable.
    """

    safe_font = font_name or "Helvetica"
    try:
        pdf_canvas.setFont(safe_font + ("-Bold" if bold and not font_name else ""), font_size)
        pdf_canvas.drawString(x, y, text or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falling back to ASCII-only labels: {}", exc)
        sanitized = (text or "").encode("latin-1", "ignore").decode("latin-1")
        fallback_font = "Helvetica-Bold" if bold else "Helvetica"
        pdf_canvas.setFont(fallback_font, font_size)
        pdf_canvas.drawString(x, y, sanitized)


def _get_primary_font() -> str | None:
    """Register a Unicode-capable font if available on the system."""

    candidates = [
        ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ("LiberationSans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ]

    for font_name, path in candidates:
        if not os.path.exists(path):
            continue
        try:
            try:
                pdfmetrics.getFont(font_name)
            except KeyError:
                pdfmetrics.registerFont(TTFont(font_name, path))
            return font_name
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not register font %s at %s: %s", font_name, path, exc)

    return None


__all__ = ["build_labels_pdf"]
