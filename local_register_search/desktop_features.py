"""Local folder monitoring and safe, portable result exports."""
import csv
import json
import os
import tempfile
from pathlib import Path
from openpyxl import Workbook
from .indexer import local_folder


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


def write_records(destination, records):
    columns = sorted({column for row in records for column in json.loads(row['cells'])},
                     key=lambda value: (len(value), value))
    names = {}
    for row in records:
        for column, name in json.loads(row.get('headers') or '{}').items():
            names.setdefault(column, [])
            if name and name not in names[column]:
                names[column].append(name)
    headers = ['File', 'Sheet', 'Excel row'] + [(' / '.join(names[column]) + f' ({column})') if names.get(column) else column for column in columns]
    def values(row):
        cells = json.loads(row['cells'])
        return [row['path'], row['sheet'], row['rownum']] + [cells.get(column, '') for column in columns]
    if Path(destination).suffix.lower() == '.xlsx':
        book = Workbook()
        sheet = book.active
        sheet.title = 'Search results'
        sheet.append(headers)
        for row in records:
            sheet.append(values(row))
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = 's'  # Preserve literal text, including leading '='.
        sheet.freeze_panes = 'D2'
        sheet.auto_filter.ref = sheet.dimensions
        book.save(destination)
        book.close()
    else:
        with open(destination, 'w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            for row in records:
                writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value
                                 for value in values(row)])
    return len(records)
