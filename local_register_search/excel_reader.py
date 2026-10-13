import datetime
import re
from pathlib import Path
import openpyxl
import xlrd
from openpyxl.utils import get_column_letter


class Cancelled(Exception):
    pass


def display(value, fmt=''):
    if value is None:
        return ''
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat(sep=' ') if isinstance(value, datetime.datetime) else value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if re.fullmatch('0+', fmt) and float(value).is_integer():
            return str(int(value)).zfill(len(fmt))
    return str(value)


def read_rows(path, cancel):
    """Yield actual row references; never evaluate formulas or save a workbook."""
    if Path(path).suffix.lower() == '.xls':
        book = xlrd.open_workbook(path, on_demand=True, formatting_info=True)
        try:
            for sheet_name in book.sheet_names():
                sheet = book.sheet_by_name(sheet_name)
                for r in range(sheet.nrows):
                    if cancel.is_set():
                        raise Cancelled()
                    cells = {}
                    for c in range(sheet.ncols):
                        cell = sheet.cell(r, c)
                        value = cell.value
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            value = xlrd.xldate_as_datetime(value, book.datemode)
                        fmt = book.format_map.get(book.xf_list[cell.xf_index].format_key)
                        text = display(value, fmt.format_str if fmt else '')
                        if text:
                            cells[get_column_letter(c + 1)] = text
                    if cells:
                        yield sheet.name, r + 1, bool(sheet.visibility), cells
                book.unload_sheet(sheet.name)
        finally:
            book.release_resources()
        return
    cached = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
    formulas = None
    try:
        formulas = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
        for sheet in cached:
            raw = formulas[sheet.title]
            sheet.reset_dimensions()
            raw.reset_dimensions()
            for rownum, (values, originals) in enumerate(zip(sheet.iter_rows(), raw.iter_rows()), 1):
                if cancel.is_set():
                    raise Cancelled()
                cells = {}
                for col, (cell, original) in enumerate(zip(values, originals), 1):
                    text = display(cell.value, getattr(original, 'number_format', ''))
                    if original.data_type == 'f' and cell.value is None:
                        text = '[Formula: cached value unavailable] ' + str(original.value)
                    if text:
                        cells[get_column_letter(col)] = text
                if cells:
                    yield sheet.title, rownum, sheet.sheet_state != 'visible', cells
    finally:
        cached.close()
        if formulas:
            formulas.close()
