"""Membrete de los documentos generados: logo, nombre y RIF de la empresa.

Los datos salen de Configuración > General (nombre, RIF y logo subido); si no hay logo propio
se usa el logo oficial. El RIF solo aparece cuando está configurado: nunca se inventa.
PDF y Excel comparten este membrete; el CSV se conserva como dato puro para poder reimportarlo.
"""
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import current_app
from PIL import Image

from app.services import company_logo_service, settings_service
from app.services.inventory_service import COMMERCIAL_ZONE

BLUE = (21, 112, 239)
INK = (16, 24, 50)
MUTED = (101, 113, 141)
RULE = (216, 226, 240)


def company() -> dict:
    """Nombre, RIF (puede ser vacío) y logo como imagen RGB lista para PDF/Excel (o None)."""
    return {"name": settings_service.get(settings_service.COMPANY_NAME),
            "tax_id": settings_service.get(settings_service.COMPANY_TAX_ID),
            "logo": _logo_image()}


def _logo_image():
    path = company_logo_service.get_company_logo_path()
    if path is None:
        path = Path(current_app.static_folder) / "img" / "logo_oficial_el_conejo.png"
    if not path.is_file():
        return None
    with Image.open(path) as source:
        image = source.convert("RGBA")
    # El PDF no admite transparencia en todas las variantes: se aplana sobre blanco.
    flat = Image.new("RGB", image.size, "white")
    flat.paste(image, mask=image.getchannel("A"))
    flat.thumbnail((360, 360))
    return flat


def _text(value) -> str:
    return str(value or "").encode("cp1252", errors="replace").decode("cp1252")


def pdf_letterhead(pdf, brand: dict, *, subtitle: str = "Sistema de Inventario") -> None:
    """Dibuja el membrete en la parte superior de la página actual y deja el cursor debajo."""
    top, left = pdf.t_margin, pdf.l_margin
    right = pdf.w - pdf.r_margin
    text_x = left
    if brand["logo"] is not None:
        pdf.image(brand["logo"], x=left, y=top, h=15)
        text_x = left + 15 * brand["logo"].width / brand["logo"].height + 4
    pdf.set_xy(text_x, top + 1)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(right - text_x - 60, 6, _text(brand["name"]))
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.cell(60, 6, _text(f"Generado: {datetime.now(COMMERCIAL_ZONE):%d/%m/%Y %H:%M}"), align="R")
    pdf.set_xy(text_x, top + 7.5)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(right - text_x, 5, _text(f"RIF: {brand['tax_id']}" if brand["tax_id"] else subtitle))
    if brand["tax_id"]:
        pdf.set_xy(text_x, top + 12)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(right - text_x, 4, _text(subtitle))
    pdf.set_draw_color(*BLUE)
    pdf.set_line_width(0.6)
    pdf.line(left, top + 18, right, top + 18)
    pdf.set_line_width(0.2)
    pdf.set_text_color(*INK)
    pdf.set_xy(left, top + 22)


def xlsx_letterhead(sheet, brand: dict, title: str, width: int) -> int:
    """Bloque superior de la hoja (logo, empresa, RIF, título y fecha). Devuelve la fila del encabezado."""
    from openpyxl.drawing.image import Image as SheetImage
    from openpyxl.styles import Font

    if brand["logo"] is not None:
        buffer = BytesIO()
        logo = brand["logo"].copy()
        logo.thumbnail((64, 64))
        logo.save(buffer, "PNG")
        buffer.seek(0)
        sheet.add_image(SheetImage(buffer), "A1")
    text_column = 2 if brand["logo"] is not None and width > 1 else 1
    lines = [(brand["name"], Font(bold=True, size=14, color="101832")),
             (f"RIF: {brand['tax_id']}" if brand["tax_id"] else "Sistema de Inventario", Font(size=10, color="65718D")),
             (title, Font(bold=True, size=12, color="1570EF")),
             (f"Generado: {datetime.now(COMMERCIAL_ZONE):%d/%m/%Y %H:%M}", Font(size=9, color="65718D"))]
    for row, (value, font) in enumerate(lines, 1):
        cell = sheet.cell(row, text_column, value)
        cell.font = font
        cell.data_type = "s"
    return len(lines) + 2
