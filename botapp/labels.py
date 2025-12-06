"""Barcode label generation utilities."""

from __future__ import annotations

import tempfile
from pathlib import Path

from io import BytesIO

from barcode import get as barcode_get
from barcode.writer import ImageWriter
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from botapp.products_service import CatalogProduct


async def generate_barcodes_pdf(product: CatalogProduct, quantity: int) -> Path:
    """
    Create a PDF file with ``quantity`` identical barcode labels.

    Each page contains a single label with barcode image and short text
    (name and SKU). The PDF is stored in a temporary file and caller is
    responsible for cleanup after sending the document.
    """

    if quantity <= 0:
        raise ValueError("Quantity must be positive to generate labels")
    if not product.barcode:
        raise ValueError("Barcode is required to generate labels")

    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp_path = Path(temp.name)
    temp.close()

    label_width = 90 * mm
    label_height = 50 * mm

    barcode_buffer = _build_barcode_image(product.barcode)
    image_reader = ImageReader(barcode_buffer)

    c = canvas.Canvas(str(temp_path), pagesize=(label_width, label_height))
    for _ in range(quantity):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(10 * mm, label_height - 12 * mm, product.name[:60])
        c.setFont("Helvetica", 9)
        c.drawString(10 * mm, label_height - 18 * mm, f"SKU: {product.sku}")
        c.drawImage(
            image_reader,
            10 * mm,
            10 * mm,
            width=label_width - 20 * mm,
            height=20 * mm,
            preserveAspectRatio=True,
            mask="auto",
        )
        c.showPage()
    c.save()

    return temp_path


def _build_barcode_image(barcode_value: str):
    writer = ImageWriter()
    barcode_obj = barcode_get("code128", barcode_value, writer=writer)
    buffer = BytesIO()
    barcode_obj.write(buffer, options={"module_width": 0.2, "text_distance": 1.0})
    buffer.seek(0)
    return buffer


__all__ = ["generate_barcodes_pdf"]
