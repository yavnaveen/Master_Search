"""Local folder monitoring and safe, portable result exports."""
import csv
import json
import os
import re
import tempfile
from pathlib import Path, PureWindowsPath
from openpyxl import Workbook
from .indexer import local_folder


def referenced_file(value, workbook_path):
    """Recognize a cell containing a local file path, including unavailable files."""
    text = str(value or '').strip().strip('"')
    if not text or '\n' in text or '\r' in text or '://' in text:
        return None
    windows_path = PureWindowsPath(text)
    if not windows_path.suffix or not re.fullmatch(r'\.[A-Za-z0-9]{1,10}', windows_path.suffix):
        return None
    if not ('\\' in text or '/' in text or windows_path.suffix.lower() in
            {'.pdf', '.xlsx', '.xlsm', '.xls', '.doc', '.docx', '.txt', '.csv', '.png', '.jpg', '.jpeg'}):
        return None
    path = Path(text)
    return path if path.is_absolute() else Path(workbook_path).parent / path


def open_excel_record(path, sheet, rownum):
    """Jump using Excel automation when available; caller handles default-app fallback."""
    if os.name != 'nt':
        return False
    try:
        import win32com.client
        excel = win32com.client.Dispatch('Excel.Application')
        workbook = next((book for book in excel.Workbooks
                         if os.path.normcase(book.FullName) == os.path.normcase(str(path.resolve()))), None)
        if workbook is None:
            workbook = excel.Workbooks.Open(str(path.resolve()))
        excel.Visible = True
        try:
            worksheet = workbook.Worksheets(sheet)
            worksheet.Activate()
            excel.Goto(worksheet.Cells(int(rownum), 1), True)
        except Exception:
            pass  # Hidden sheets may not activate; the workbook is still open.
        return True
    except Exception:
        return False


def folder_snapshot(folders, cancel):
    snapshot = {}
    for folder in folders:
        try:
            root = local_folder(folder)
            def fail(error):
                raise error
            for base, dirs, files in os.walk(root, onerror=fail):
                if cancel.is_set():
                    return None
                dirs[:] = [name for name in dirs if not Path(base, name).is_symlink()
                           and not Path(base, name).is_junction()]
                for name in files:
                    path = Path(base, name)
                    if name.startswith('~$') or path.suffix.lower() not in {'.xlsx', '.xlsm', '.xls'} or path.is_symlink():
                        continue
                    stat = path.stat()
                    snapshot[str(path)] = (stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError):
            # Never interpret an incomplete traversal as deleted workbooks.
            return None
    return snapshot


def export_records(destination, records):
    destination = Path(destination)
    descriptor, temporary = tempfile.mkstemp(prefix='.search-export-', suffix=destination.suffix, dir=destination.parent)
    os.close(descriptor)
    try:
        count = write_records(temporary, records)
        os.replace(temporary, destination)
        return count
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sanitize_sheet_title(name):
    # Invalid characters in Excel worksheet names: \ / ? * : [ ]
    cleaned = re.sub(r'[\\/*?:\[\]]', '_', str(name or '')).strip()
    return cleaned or 'Sheet'


def generate_unique_sheet_name(file_name, sheet_name, existing_names):
    """Generate an Excel sheet name (max 31 characters, unique case-insensitively)."""
    clean_file = sanitize_sheet_title(Path(file_name).stem)
    clean_sheet = sanitize_sheet_title(sheet_name)
    combined = f"{clean_sheet} ({clean_file})"
    if len(combined) <= 31:
        base = combined
    else:
        base = f"{clean_sheet[:17]}_{clean_file[:13]}"

    base = base[:31].strip() or 'Sheet'
    lower_existing = {n.casefold() for n in existing_names}
    if base.casefold() not in lower_existing:
        existing_names.add(base)
        return base

    counter = 1
    while True:
        suffix = f"_{counter}"
        avail = 31 - len(suffix)
        candidate = f"{base[:avail]}{suffix}"
        if candidate.casefold() not in lower_existing:
            existing_names.add(candidate)
            return candidate
        counter += 1


def write_records(destination, records):
    if Path(destination).suffix.lower() == '.xlsx':
        from .search import group_search_results
        groups = group_search_results(records, sort_order='matches')
        book = Workbook()
        existing_names = set()
        if not groups:
            sheet = book.active
            sheet.title = 'Results'
            sheet.append(['Excel Row', 'Notes'])
        else:
            for idx, group in enumerate(groups):
                title = generate_unique_sheet_name(group['file_name'], group['sheet'], existing_names)
                if idx == 0:
                    sheet = book.active
                    sheet.title = title
                else:
                    sheet = book.create_sheet(title=title)
                cols = group['columns']
                has_section = any(r.get('row_section') for r in group['rows'])
                headers = ['Excel Row'] + group['labels'] + (['Section'] if has_section else [])
                sheet.append(headers)
                for r in group['rows']:
                    cells = r.get('cells_dict')
                    if cells is None:
                        try:
                            cells = json.loads(r.get('cells') or '{}')
                        except Exception:
                            cells = {}
                    row_vals = [r['rownum']] + [cells.get(c, '') for c in cols]
                    if has_section:
                        row_vals.append(r.get('row_section') or '')
                    sheet.append(row_vals)
                    for cell in sheet[sheet.max_row]:
                        if isinstance(cell.value, str):
                            cell.data_type = 's'
                sheet.freeze_panes = 'B2'
                sheet.auto_filter.ref = sheet.dimensions
        book.save(destination)
        book.close()
        return len(records)

    columns = sorted({column for row in records for column in json.loads(row['cells'])},
                     key=lambda value: (len(value), value))
    names = {}
    for row in records:
        for column, name in json.loads(row.get('headers') or '{}').items():
            names.setdefault(column, [])
            if name and name not in names[column]:
                names[column].append(name)
    headers = ['File', 'Sheet', 'Excel row'] + [(' / '.join(names[column]) + f' ({column})') if names.get(column) else column for column in columns] + ['Section']
    def values(row):
        cells = json.loads(row['cells'])
        return [row['path'], row['sheet'], row['rownum']] + [cells.get(column, '') for column in columns] + [row.get('row_section') or '']
    with open(destination, 'w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        for row in records:
            writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value
                             for value in values(row)])
    return len(records)
