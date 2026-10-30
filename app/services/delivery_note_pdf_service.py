"""Generación in-memory de PDF interno de notas de entrega (fpdf2).

Reutiliza delivery_note_service.get_note_or_404: no duplica la consulta ni
altera stock, movimientos ni el estado de la nota.

El PDF es un documento INTERNO de entrega. No es factura, comprobante fiscal
ni documento tributario. No se inventan RIF, IVA ni datos de contacto de la
empresa que no existan en la configuración del proyecto.

Unicode: se usan las fuentes core Helvetica de PDF (WinAnsi/cp1252), que
incluyen á é í ó ú ñ Ñ de forma legal, sin añadir archivos TTF al repositorio.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

from app.models.delivery_note import STATUS_CANCELLED, STATUS_ISSUED, DeliveryNote
from app.services import delivery_note_service, settings_service

DOCUMENT_TITLE = "NOTA DE ENTREGA"
INTERNAL_DISCLAIMER = (
    "Documento interno de entrega. No constituye factura ni comprobante fiscal."
)
GENERATED_BY = (
    "Generado por el sistema de Ferretería y Construcciones El Conejo C.A."
)

_STATUS_LABELS = {
    STATUS_ISSUED: "Emitida",
    STATUS_CANCELLED: "Cancelada",
}

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9\-]+")


def _company_name() -> str:
    """Nombre de la empresa configurado en Configuración > General (con valor por defecto)."""
    return settings_service.get(settings_service.COMPANY_NAME)


def _to_pdf(value: object) -> str:
    """Convierte texto a WinAnsi/cp1252 para fuentes core de PDF."""
    if value is None:
        return ""
    return str(value).encode("cp1252", errors="replace").decode("cp1252")


def _format_money(value) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = amount < 0
    amount = abs(amount)
    whole, frac = f"{amount:.2f}".split(".")
    groups: list[str] = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    formatted = ".".join(reversed(groups)) + "," + frac
    return f"-{formatted}" if negative else formatted


def _format_quantity(value) -> str:
    quantity = Decimal(str(value))
    if quantity == quantity.to_integral_value():
        return f"{int(quantity)}"
    return _format_money(quantity)


def _format_datetime(value: datetime | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.strftime("%d/%m/%Y %H:%M")


def safe_pdf_filename(note_number: str | None) -> str:
    """Nombre de descarga fijo y sanitizado; nunca acepta rutas del usuario."""
    cleaned = _FILENAME_SAFE_RE.sub("", note_number or "")
    if not cleaned:
        cleaned = "sin-numero"
    return f"nota-entrega-{cleaned}.pdf"


class DeliveryNotePDF(FPDF):
    """PDF A4 con pie fijo y salto de página automático."""

    def __init__(self, cancelled: bool = False) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._cancelled = cancelled
        self.core_fonts_encoding = "cp1252"
        self.set_margins(15, 16, 15)
        self.set_auto_page_break(auto=True, margin=28)
        self.set_title(_to_pdf(DOCUMENT_TITLE))
        self.set_author(_to_pdf(_company_name()))
        self.set_creator(_to_pdf(GENERATED_BY))

    def footer(self) -> None:
        self.set_y(-24)
        self.set_draw_color(180, 180, 180)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(2)
        self.set_text_color(90, 90, 90)
        self.set_font("Helvetica", "I", 8)
        self.cell(
            self.epw,
            4,
            _to_pdf(INTERNAL_DISCLAIMER),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
            align="C",
        )
        self.cell(
            self.epw,
            4,
            _to_pdf(GENERATED_BY),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
            align="C",
        )
        extra = "NOTA CANCELADA — " if self._cancelled else ""
        self.cell(
            self.epw,
            4,
            _to_pdf(f"{extra}Página {self.page_no()}"),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
            align="C",
        )
        self.set_text_color(0, 0, 0)


def _draw_letterhead(pdf: DeliveryNotePDF, note: dict) -> None:
    band_h = 18
    y = pdf.get_y()
    pdf.set_fill_color(25, 55, 95)
    pdf.rect(pdf.l_margin, y, pdf.epw, band_h, style="F")
    pdf.set_xy(pdf.l_margin, y + 1.5)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(
        pdf.epw,
        7,
        _to_pdf(_company_name()),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="C",
    )
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(
        pdf.epw,
        8,
        _to_pdf(DOCUMENT_TITLE),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="C",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(
        pdf.epw,
        5,
        _to_pdf(INTERNAL_DISCLAIMER),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="C",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    status_label = _STATUS_LABELS.get(note["status"], note["status"] or "")
    pdf.set_font("Helvetica", "", 10)
    meta_left = pdf.epw / 2
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(28, 5, _to_pdf("Número:"), new_x=XPos.END, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(meta_left - 28, 5, _to_pdf(note["note_number"]), new_x=XPos.END, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(38, 5, _to_pdf("Fecha de emisión:"), new_x=XPos.END, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(
        0,
        5,
        _to_pdf(_format_datetime(note["created_at"])),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(28, 5, _to_pdf("Estado:"), new_x=XPos.END, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "B" if note["status"] == STATUS_CANCELLED else "", 10)
    pdf.cell(0, 5, _to_pdf(status_label), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    if note["status"] == STATUS_CANCELLED:
        pdf.set_fill_color(176, 32, 32)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(
            pdf.epw,
            9,
            _to_pdf("NOTA CANCELADA"),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
            align="C",
            fill=True,
        )
        pdf.set_text_color(60, 60, 60)
        pdf.set_font("Helvetica", "", 9)
        if note["cancelled_at"] is not None:
            pdf.cell(
                pdf.epw,
                5,
                _to_pdf(f"Fecha de cancelación: {_format_datetime(note['cancelled_at'])}"),
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        canceller = note["cancelled_by"]
        if canceller:
            pdf.cell(
                pdf.epw,
                5,
                _to_pdf(f"Cancelada por: {canceller}"),
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)


def _draw_client_block(pdf: DeliveryNotePDF, note: dict) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(238, 241, 245)
    pdf.cell(
        pdf.epw,
        7,
        "Datos del cliente",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        fill=True,
    )
    pdf.ln(1)

    def _row(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(32, 5, _to_pdf(label), new_x=XPos.END, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(pdf.epw - 32, 5, _to_pdf(value))

    _row("Nombre:", note["customer_name"] or "No registrado")
    if note["customer_document"]:
        _row("Documento:", note["customer_document"])
    if note["customer_phone"]:
        _row("Teléfono:", note["customer_phone"])
    if note["customer_address"]:
        _row("Dirección:", note["customer_address"])
    pdf.ln(2)


def _draw_items_table(pdf: DeliveryNotePDF, note: dict) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(238, 241, 245)
    pdf.cell(
        pdf.epw,
        7,
        "Productos",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        fill=True,
    )
    pdf.ln(1)

    headings_style = FontFace(
        emphasis="BOLD",
        color=(255, 255, 255),
        fill_color=(25, 55, 95),
    )
    pdf.set_font("Helvetica", size=8)
    items = sorted(note["items"], key=lambda item: item["id"] or 0)
    include_prices = note["total_amount"] is not None
    with pdf.table(
        width=pdf.epw,
        col_widths=(22, 78, 22, 32, 26) if include_prices else (28, 122, 30),
        text_align=("LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT") if include_prices else ("LEFT", "LEFT", "RIGHT"),
        headings_style=headings_style,
        first_row_as_headings=True,
        repeat_headings=1,
        line_height=6.5,
        padding=1.6,
        borders_layout="ALL",
        cell_fill_color=(245, 247, 250),
        cell_fill_mode="ROWS",
        wrapmode="WORD",
    ) as table:
        header = table.row()
        header.cell(_to_pdf("Código"))
        header.cell(_to_pdf("Producto"))
        header.cell(_to_pdf("Cantidad"))
        if include_prices:
            header.cell(_to_pdf("Precio unitario"))
            header.cell(_to_pdf("Subtotal"))
        for item in items:
            row = table.row()
            row.cell(_to_pdf(item["product_code"]))
            row.cell(_to_pdf(item["product_name"]))
            row.cell(_format_quantity(item["quantity"]))
            if include_prices:
                row.cell(_format_money(item["unit_price"]))
                row.cell(_format_money(item["line_total"]))

    if not include_prices:
        return

    pdf.ln(3)
    pdf.set_fill_color(25, 55, 95)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 11)
    total_label = f"Total general: {_format_money(note['total_amount'])}"
    pdf.cell(
        pdf.epw,
        9,
        _to_pdf(total_label),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="R",
        fill=True,
    )
    pdf.set_text_color(0, 0, 0)


def render_delivery_note_pdf(note: DeliveryNote) -> bytes:
    """Genera el PDF en memoria a partir de una nota ya cargada."""
    # La misma proyección autorizada que las APIs, antes de dibujar o generar bytes.
    payload = note.to_dict()
    pdf = DeliveryNotePDF(cancelled=payload["status"] == STATUS_CANCELLED)
    pdf.add_page()
    _draw_letterhead(pdf, payload)
    _draw_client_block(pdf, payload)
    _draw_items_table(pdf, payload)
    # output() sin nombre devuelve bytearray; nunca se escribe a disco.
    return bytes(pdf.output())


def build_pdf_response(note_id: int) -> tuple[bytes, str]:
    """Obtiene la nota existente y devuelve (bytes del PDF, nombre seguro)."""
    note = delivery_note_service.get_note_or_404(note_id)
    pdf_bytes = render_delivery_note_pdf(note)
    return pdf_bytes, safe_pdf_filename(note.note_number)
