"""PDF label generation utilities for warehouse flows."""

from __future__ import annotations

from io import BytesIO

from loguru import logger

from barcode import get as barcode_get
from barcode.writer import ImageWriter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

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

    barcode_buffer = None
    image_reader = None
    try:
        barcode_buffer = _build_barcode_image(str(product.barcode))
        image_reader = ImageReader(barcode_buffer)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to render barcode image, falling back to text-only labels: %s", exc)

    c = canvas.Canvas(buffer, pagesize=(label_width, label_height))
    for _ in range(quantity):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(10 * mm, label_height - 12 * mm, product.name[:60])
        c.setFont("Helvetica", 9)
        c.drawString(10 * mm, label_height - 18 * mm, f"SKU: {product.sku}")
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
            c.drawString(10 * mm, 18 * mm, f"Штрихкод: {product.barcode}")
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


__all__ = ["build_labels_pdf"]
