"""Read-only, bounded product exports generated entirely in memory."""
import csv
import re
from datetime import datetime
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import urlsplit

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SheetImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from PIL import Image, UnidentifiedImageError

from app.services import product_service, upload_service
from app.services.exceptions import ValidationError

MAX_EXPORT_ROWS = 10_000
MAX_TEXT_CHARACTERS = 4_000_000
MAX_PDF_ROWS = 30_000
MIMES = {'csv': 'text/csv', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'pdf': 'application/pdf'}
OPTIONS = {'include_images': False, 'include_description': True, 'include_stock_prices': True, 'include_suppliers': False}
STATUS = {'disponible': 'Disponible', 'bajo_stock': 'Bajo stock', 'agotado': 'Agotado', 'inactivo': 'Inactivo'}
BASE = [('code', 'Código', 22), ('name', 'Nombre', 38), ('category', 'Categoría', 24), ('unit', 'Unidad', 12), ('status', 'Estado', 16), ('updated_at', 'Última actualización', 23)]
STOCK = [('current_stock', 'Stock actual', 14), ('minimum_stock', 'Stock mínimo', 14)]
COST = ('purchase_price', 'Precio de compra', 18)
SALE_PRICE = ('sale_price', 'Precio de venta', 18)


def validate_options(raw):
    if not isinstance(raw, dict) or set(raw) - OPTIONS.keys():
        raise ValidationError('Opciones de exportación inválidas.')
    options = OPTIONS | raw
    if any(type(value) is not bool for value in options.values()):
        raise ValidationError('Las opciones de exportación deben ser booleanas.')
    if options['include_suppliers']:
        raise ValidationError('La exportación de proveedores no está disponible porque el catálogo aún no tiene proveedores asociados.')
    return options


def public_image_url(value):
    """Only public web URLs or the application's product media route."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return ''
    if re.fullmatch(r'/media/products/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)', value, re.I):
        return value
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ''
    if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
        return value
    return ''


def thumbnail(url):
    """Never fetch URLs; read only bounded, validated product media files."""
    if not re.fullmatch(r'/media/products/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)', url, re.I):
        return None
    root = (Path(upload_service.uploads_root()) / 'products').resolve()
    try:
        path = (root / url.rsplit('/', 1)[1]).resolve()
        if path.parent != root or not path.is_file() or path.stat().st_size > upload_service.MAX_IMAGE_BYTES:
            return None
        with Image.open(path) as source:
            if source.width * source.height > 16_000_000:
                return None
            source.thumbnail((80, 80))
            output = BytesIO()
            source.convert('RGB').save(output, format='PNG')
        output.seek(0)
        return output
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


def columns_for(options):
    columns = list(BASE)
    if options['include_description']:
        columns.append(('description', 'Descripción', 50))
    if options['include_stock_prices']:
        from app.utils.permissions import response_includes_cost, response_includes_sale_price

        columns.extend(STOCK)
        if response_includes_cost():
            columns.append(COST)
            columns.append(('purchase_price_currency', 'Moneda de compra', 16))
        if response_includes_sale_price():
            columns.append(SALE_PRICE)
            columns.append(('sale_price_currency', 'Moneda de venta', 16))
    if options['include_images']:
        columns.append(('image_url', 'Imagen', 24))
    return columns


def product_values(product):
    return dict(code=product.code, name=product.name, category=product.category.name,
                unit=product.unit, status=STATUS[product.stock_status], updated_at=product.updated_at,
                description=product.description or '', current_stock=product.current_stock,
                minimum_stock=product.minimum_stock, purchase_price=product.purchase_price,
                sale_price=product.sale_price, image_url=public_image_url(product.image_url),
                purchase_price_currency=product.purchase_price_currency or 'Moneda no registrada',
                sale_price_currency=product.sale_price_currency or 'Moneda no registrada')


def safe_csv(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def csv_file(rows, columns, _now):
    output = StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([label for _, label, _ in columns])
    for row in rows:
        writer.writerow([safe_csv(row[key]) for key, _, _ in columns])
    return BytesIO(output.getvalue().encode('utf-8-sig'))


def xlsx_file(rows, columns, _now, title='Productos'):
    from app.services import document_branding

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    # Membrete arriba; la tabla empieza en ``header`` con filtros y encabezado fijo.
    header = document_branding.xlsx_letterhead(sheet, document_branding.company(), title, len(columns))
    fill = PatternFill('solid', fgColor='EAF2FF')
    for index, (_, label, _) in enumerate(columns, 1):
        cell = sheet.cell(header, index, label)
        cell.font = Font(bold=True, color='0B3D91')
        cell.fill = fill
    for index, (key, _, width) in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
        for number, row in enumerate(rows, header + 1):
            _xlsx_cell(sheet, number, index, key, row[key])
    sheet.freeze_panes = f'A{header + 1}'
    last = get_column_letter(len(columns))
    sheet.auto_filter.ref = f'A{header}:{last}{header + max(len(rows), 1)}'
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output


def _xlsx_cell(sheet, number, index, key, value):
    if isinstance(value, str) and (len(value) > 32767 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value)):
        raise ValidationError('Un texto del catálogo excede los límites de XLSX o contiene caracteres no compatibles. Use CSV.')
    cell = sheet.cell(number, index, value)
    if isinstance(value, str):
        cell.data_type = 's'  # Including =,+,-,@: never an XLSX formula.
    cell.alignment = Alignment(vertical='top', wrap_text=True)
    if isinstance(value, Decimal):
        cell.number_format = '0.00'
    if key == 'image_url':
        image = thumbnail(value)
        if image:
            cell.value = None
            sheet.add_image(SheetImage(image), cell.coordinate)
            sheet.row_dimensions[number].height = 64


def pdf_text(value):
    # Same core-font policy as delivery_note_pdf_service: Spanish WinAnsi,
    # unsupported Unicode replaced, no runtime font downloads.
    return str(value if value is not None else '').encode('cp1252', errors='replace').decode('cp1252')


class CatalogPDF(FPDF):
    """PDF de exportación con membrete (logo, empresa y RIF) en cada página."""

    def header(self):
        from app.services import document_branding

        if not hasattr(self, "_brand"):
            self._brand = document_branding.company()
        document_branding.pdf_letterhead(self, self._brand)

    def footer(self):
        self.set_y(-12)
        self.set_font('Helvetica', '', 8)
        self.cell(0, 5, f'Página {self.page_no()}', align='C')


def pdf_file(rows, columns, now):
    detail = any(key == 'description' for key, _, _ in columns)
    detail_rows = sum(
        sum(max(1, (len(line) + 999) // 1000) for line in ('Descripción: ' + row['description']).splitlines())
        for row in rows
    ) if detail else 0
    if len(rows) + detail_rows > MAX_PDF_ROWS:
        raise ValidationError('El PDF contiene demasiadas líneas. Reduzca los filtros o desactive la descripción.')
    pdf = CatalogPDF(orientation='L', format='A4')
    pdf.core_fonts_encoding = 'cp1252'
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 9, 'Catálogo de productos', new_x='LMARGIN', new_y='NEXT')
    pdf.set_font('Helvetica', '', 9)
    pdf.cell(0, 7, f"Exportación: {now:%d/%m/%Y %H:%M} | Productos: {len(rows)}", new_x='LMARGIN', new_y='NEXT')
    table_columns = [c for c in columns if c[0] != 'description']
    pdf.set_font('Helvetica', '', 8)
    with pdf.table(col_widths=[c[2] for c in table_columns], line_height=4, padding=1, text_align='LEFT', wrapmode='CHAR') as table:
        table.row([label for _, label, _ in table_columns])
        for data in rows:
            _pdf_product_row(table, data, table_columns)
            if detail:
                _pdf_description(table, data['description'], len(table_columns))
    return BytesIO(bytes(pdf.output()))


def _pdf_product_row(table, data, columns):
    row = table.row()
    for key, _, _ in columns:
        if key == 'image_url':
            image = thumbnail(data[key])
            if image:
                row.cell(img=image, img_fill_width=False)
            else:
                row.cell('')
        else:
            value = data[key]
            if isinstance(value, datetime):
                value = value.strftime('%d/%m/%Y %H:%M')
            row.cell(' '.join(pdf_text(value).split()))


def _pdf_description(table, description, width):
    for line in pdf_text('Descripción: ' + description).splitlines():
        for start in range(0, max(1, len(line)), 1000):
            table.row().cell(line[start:start + 1000], colspan=width)


def export_products(format_name, raw_options, filters):
    if not isinstance(format_name, str) or format_name not in MIMES:
        raise ValidationError('Formato inválido. Seleccione xlsx, csv o pdf.')
    options = validate_options(raw_options)
    products = product_service.filtered_products_query(**filters).limit(MAX_EXPORT_ROWS + 1).all()
    if len(products) > MAX_EXPORT_ROWS:
        raise ValidationError(f'La exportación supera {MAX_EXPORT_ROWS} productos. Aplique filtros para reducir el catálogo.')
    rows = [product_values(product) for product in products]
    columns = columns_for(options)
    if sum(len(str(row[key] or '')) for row in rows for key, _, _ in columns) > MAX_TEXT_CHARACTERS:
        raise ValidationError('La exportación contiene demasiado texto. Reduzca los filtros o desactive la descripción.')
    now = datetime.now()
    output = {'csv': csv_file, 'xlsx': xlsx_file, 'pdf': pdf_file}[format_name](rows, columns, now)
    return output, MIMES[format_name], f'catalogo_productos_{now:%Y-%m-%d_%H%M}.{format_name}'
