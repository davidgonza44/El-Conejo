"""Generic read-only file builders shared by movement and category exports.

CSV/XLSX reuse the hardened product builders (BOM, formula-safe literals,
numeric cells). The PDF is a plain readable report, not a copy of the web UI.
"""
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from app.services.exceptions import ValidationError
from app.services.product_export_service import MIMES, CatalogPDF, csv_file, pdf_text, xlsx_file

MAX_EXPORT_ROWS = 10_000
FORMATS = tuple(MIMES)

__all__ = ['FORMATS', 'MAX_EXPORT_ROWS', 'MIMES', 'csv_file', 'xlsx_file', 'pdf_report', 'validate_format', 'build']


def validate_format(format_name):
    if not isinstance(format_name, str) or format_name not in MIMES:
        raise ValidationError('Formato inválido. Seleccione xlsx, csv o pdf.')
    return format_name


def _pdf_value(value):
    if isinstance(value, datetime):
        return value.strftime('%d/%m/%Y %H:%M')
    if isinstance(value, Decimal):
        return f'{value:,.2f}'
    return ' '.join(pdf_text(value).split())


def pdf_report(title, lines, rows, columns):
    """Landscape A4: brand, title, metadata lines, then one table of real rows."""
    pdf = CatalogPDF(orientation='L', format='A4')
    pdf.core_fonts_encoding = 'cp1252'
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(0, 6, 'Ferretería y Construcciones El Conejo C.A.', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 9, pdf_text(title), new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 9)
    for line in lines:
        pdf.multi_cell(0, 5, pdf_text(line), new_x='LMARGIN', new_y='NEXT')
    pdf.ln(2)
    pdf.set_font('Helvetica', '', 8)
    with pdf.table(col_widths=[width for _, _, width in columns], line_height=4, padding=1,
                   text_align='LEFT', wrapmode='CHAR') as table:
        table.row([label for _, label, _ in columns])
        for data in rows:
            table.row([_pdf_value(data[key]) for key, _, _ in columns])
    return BytesIO(bytes(pdf.output()))


def build(format_name, rows, columns, now, *, sheet, title, lines, stem):
    """Returns (bytes buffer, mimetype, filename) for a validated format."""
    if format_name == 'csv':
        output = csv_file(rows, columns, now)
    elif format_name == 'xlsx':
        output = xlsx_file(rows, columns, now, title=sheet)
    else:
        output = pdf_report(title, lines, rows, columns)
    return output, MIMES[format_name], f'{stem}_{now:%Y-%m-%d}.{format_name}'
