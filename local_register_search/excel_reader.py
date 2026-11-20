import datetime
import os
import re
from zipfile import ZipFile
from xml.etree.ElementTree import iterparse
from pathlib import Path
import openpyxl
import xlrd
from openpyxl.utils import get_column_letter
from .sync_logger import log_info, log_debug, log_warning


def safe_open_rb(path):
    """Open a file in binary mode with full Windows sharing rights (READ | WRITE | DELETE)."""
    if os.name == 'nt':
        import ctypes
        import msvcrt
        kernel32 = ctypes.windll.kernel32
        GENERIC_READ = 0x80000000
        FILE_SHARE_ALL = 1 | 2 | 4  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        OPEN_EXISTING = 3
        FILE_ATTRIBUTE_NORMAL = 0x80

        handle = kernel32.CreateFileW(
            str(path),
            GENERIC_READ,
            FILE_SHARE_ALL,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None
        )
        if handle != -1 and handle != 0xFFFFFFFFFFFFFFFF:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            return open(fd, 'rb', closefd=True)
    return open(path, 'rb')


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


def merged_rows(path, worksheet_path, cancel):
    rows = set()
    from openpyxl.utils.cell import range_boundaries
    target = worksheet_path.replace('\\', '/').lstrip('/')
    try:
        with safe_open_rb(path) as f_zip:
            with ZipFile(f_zip) as archive:
                namelist = archive.namelist()
                if target not in namelist:
                    candidates = [n for n in namelist if n.endswith(target.split('/')[-1])]
                    if candidates:
                        target = candidates[0]
                    else:
                        return rows
                with archive.open(target) as stream:
                    for event, element in iterparse(stream, events=('end',)):
                        if cancel.is_set():
                            raise Cancelled()
                        if element.tag.endswith('}mergeCell'):
                            try:
                                left, top, right, bottom = range_boundaries(element.attrib['ref'])
                                if right > left:
                                    rows.add(top)
                            except ValueError as ve:
                                log_debug(f"Invalid mergeCell ref '{element.attrib.get('ref')}': {ve}")
                        element.clear()
    except Cancelled:
        raise
    except Exception as exc:
        log_debug(f"Could not inspect merged cells in {worksheet_path}: {exc}")
    return rows


def read_rows(path, cancel, with_context=False):
    """Yield actual row references; never evaluate formulas or save a workbook."""
    if Path(path).suffix.lower() == '.xls':
        try:
            with safe_open_rb(path) as f_xls:
                content = f_xls.read()
            book = xlrd.open_workbook(file_contents=content, on_demand=True, formatting_info=True)
        except Exception as exc:
            log_warning(f"safe_open_rb on '{path}' failed: {exc}; falling back to file path")
            book = xlrd.open_workbook(str(path), on_demand=True, formatting_info=True)
        try:
            sheet_names = book.sheet_names()
            log_info(f"Worksheets found in '{path}': {sheet_names}")
            for sheet_name in sheet_names:
                sheet = book.sheet_by_name(sheet_name)
                for r in range(sheet.nrows):
                    if cancel.is_set():
                        raise Cancelled()
                    cells = {}
                    styled = False
                    for c in range(sheet.ncols):
                        cell = sheet.cell(r, c)
                        value = cell.value
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            value = xlrd.xldate_as_datetime(value, book.datemode)
                        fmt = book.format_map.get(book.xf_list[cell.xf_index].format_key)
                        text = display(value, fmt.format_str if fmt else '')
                        if text:
                            cells[get_column_letter(c + 1)] = text
                            xf = book.xf_list[cell.xf_index]
                            styled |= bool(book.font_list[xf.font_index].bold or xf.background.fill_pattern)
                    if cells:
                        merged = any(top == r and right - left > 1 for top, bottom, left, right in sheet.merged_cells) if with_context else False
                        record = (sheet.name, r + 1, bool(sheet.visibility), cells)
                        yield record + ({'styled': styled, 'merged': merged},) if with_context else record
                book.unload_sheet(sheet.name)
        finally:
            book.release_resources()
        return

    f_cached = None
    f_formulas = None
    cached = None
    formulas = None
    try:
        f_cached = safe_open_rb(path)
        f_formulas = safe_open_rb(path)
        cached = openpyxl.load_workbook(f_cached, read_only=True, data_only=True, keep_links=False)
        formulas = openpyxl.load_workbook(f_formulas, read_only=True, data_only=False, keep_links=False)
        sheet_names = cached.sheetnames
        log_info(f"Worksheets found in '{path}': {sheet_names}")
        for sheet in cached:
            raw = formulas[sheet.title]
            merges = merged_rows(path, raw._worksheet_path, cancel) if with_context else set()
            sheet.reset_dimensions()
            raw.reset_dimensions()
            for rownum, (values, originals) in enumerate(zip(sheet.iter_rows(), raw.iter_rows()), 1):
                if cancel.is_set():
                    raise Cancelled()
                cells = {}
                styled = False
                for col, (cell, original) in enumerate(zip(values, originals), 1):
                    text = display(cell.value, getattr(original, 'number_format', ''))
                    if original.data_type == 'f' and cell.value is None:
                        text = '[Formula: cached value unavailable] ' + str(original.value)
                    if text:
                        cells[get_column_letter(col)] = text
                        styled |= bool((original.font and original.font.bold) or (original.fill and original.fill.patternType))
                if cells:
                    record = (sheet.title, rownum, sheet.sheet_state != 'visible', cells)
                    yield record + ({'styled': styled, 'merged': rownum in merges},) if with_context else record
    finally:
        if cached is not None:
            cached.close()
        if formulas is not None:
            formulas.close()
        if f_cached is not None:
            f_cached.close()
        if f_formulas is not None:
            f_formulas.close()
