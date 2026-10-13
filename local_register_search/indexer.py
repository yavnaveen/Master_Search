import datetime
import json
import os
from pathlib import Path
from .database import connect, normalize
from .excel_reader import read_rows, Cancelled


def header_rows(records):
    """Detect known register headings in the first 30 rows, preserving unrecognized sheets."""
    from itertools import groupby, islice
    labels = {'s n', 's no', 'sr no', 'serial no', 'date', 'letter no', 'letter number',
              'subject', 'type', 'description', 'drawing number', 'drawing no', 'code',
              'received from organization company', 'received from', 'file path', 'reference no'}
    import re
    for sheet, group in groupby(records, key=lambda record: record[0]):
        group = iter(group)
        prefix = list(islice(group, 30))
        headings = {}
        header_at = -1
        for i, record in enumerate(prefix):
            values = [re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip() for value in record[3].values()]
            if len(values) >= 3 and sum(value in labels for value in values) >= 3:
                header_at, headings = i, record[3]
                break
        for record in prefix[header_at + 1:]:
            if headings and record[3] == headings:
                continue
            yield record, headings
        for record in group:
            if headings and record[3] == headings:
                continue
            yield record, headings


def upgrade_saved_headers(path):
    """Upgrade only extracted SQL rows; never open or change source workbooks."""
    db = connect(path)
    try:
        if db.execute("SELECT value FROM settings WHERE key='register_headers_v2'").fetchone():
            return
        sheets = db.execute('SELECT DISTINCT path,sheet FROM rows').fetchall()
        with db:
            for file, sheet in sheets:
                prefix = db.execute('SELECT rownum,hidden,cells FROM rows WHERE path=? AND sheet=? ORDER BY rownum LIMIT 30',
                                    (file, sheet)).fetchall()
                records = [(sheet, row['rownum'], row['hidden'], json.loads(row['cells'])) for row in prefix]
                detected = next(header_rows(records), None)
                headers = detected[1] if detected else {}
                if not headers:
                    continue
                header = next((row for row in prefix if json.loads(row['cells']) == headers), None)
                db.execute('INSERT OR REPLACE INTO sheet_headers VALUES(?,?,?)', (file, sheet, json.dumps(headers, ensure_ascii=False)))
                if header:
                    db.execute('DELETE FROM rows WHERE path=? AND sheet=? AND rownum<=?', (file, sheet, header['rownum']))
                    db.execute('DELETE FROM rows WHERE path=? AND sheet=? AND cells=?', (file, sheet, header['cells']))
            db.execute("INSERT OR REPLACE INTO settings VALUES('register_headers_v2','1')")
    finally:
        db.close()


def local_folder(path):
    path = Path(os.path.abspath(path))
    if str(path).startswith('\\\\'):
        raise ValueError('Select a local folder, not a network share.')
    if os.name == 'nt':
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise ValueError('Mapped network drives are not supported.')
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Select the actual local folder, not a symlink or junction.')
    path = path.resolve()
    if not path.is_dir():
        raise ValueError('Folder is unavailable.')
    return path


def sync(db_path, folders, cancel, progress=lambda *args: None, rebuild=False):
    db = connect(db_path)
    summary = dict(indexed=0, skipped=0, removed=0, errors=[], cancelled=False)
    try:
        for folder in folders:
            if cancel.is_set():
                raise Cancelled()
            try:
                root = local_folder(folder)
                found = []
                def scan_error(error):
                    raise error
                for base, dirs, names in os.walk(root, onerror=scan_error):
                    if cancel.is_set():
                        raise Cancelled()
                    dirs[:] = [name for name in dirs if not Path(base, name).is_symlink()
                               and not (hasattr(Path(base, name), 'is_junction') and Path(base, name).is_junction())]
                    for name in names:
                        if not name.startswith('~$') and Path(name).suffix.lower() in {'.xlsx', '.xlsm', '.xls'}:
                            candidate = Path(base, name)
                            if not candidate.is_symlink():
                                found.append(str(candidate.resolve()))
            except (OSError, ValueError) as exc:
                summary['errors'].append(f'{folder}: folder scan failed ({type(exc).__name__}); existing index retained.')
                continue
            seen = set(found)
            for i, path in enumerate(found):
                if cancel.is_set():
                    raise Cancelled()
                progress(i + 1, len(found), path)
                try:
                    stat = os.stat(path)
                    old = db.execute('SELECT * FROM files WHERE path=?', (path,)).fetchone()
                    if old and not old['stale'] and not rebuild and old['mtime'] == stat.st_mtime_ns and old['size'] == stat.st_size:
                        summary['skipped'] += 1
                        continue
                    with db:
                        db.execute('DELETE FROM rows WHERE path=?', (path,))
                        db.execute('DELETE FROM sheet_headers WHERE path=?', (path,))
                        saved_sheets = set()
                        for (sheet, row, hidden, cells), headers in header_rows(read_rows(path, cancel)):
                            if sheet not in saved_sheets:
                                db.execute('INSERT OR REPLACE INTO sheet_headers VALUES(?,?,?)',
                                           (path, sheet, json.dumps(headers, ensure_ascii=False)))
                                saved_sheets.add(sheet)
                            text = ' | '.join(cells.values())
                            db.execute('INSERT INTO rows(path,sheet,rownum,hidden,cells,text,normalized) VALUES(?,?,?,?,?,?,?)',
                                       (path, sheet, row, hidden, json.dumps(cells, ensure_ascii=False), text, normalize(text)))
                        after = os.stat(path)
                        if cancel.is_set():
                            raise Cancelled()
                        if (stat.st_mtime_ns, stat.st_size) != (after.st_mtime_ns, after.st_size):
                            raise OSError('Workbook changed during indexing')
                        db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,0,?)', (path, stat.st_mtime_ns, stat.st_size, ''))
                    summary['indexed'] += 1
                except Cancelled:
                    raise
                except Exception as exc:
                    # Parser exception strings may contain cell data: only expose class and a generic remedy.
                    error = f'{type(exc).__name__}: cannot read workbook; check format, permissions, encryption or corruption.'
                    with db:
                        db.execute('INSERT INTO files VALUES(?,?,?,1,?) ON CONFLICT(path) DO UPDATE SET stale=1,error=excluded.error',
                                   (path, 0, 0, error))
                    summary['errors'].append(f'{path}: {error}')
            if cancel.is_set():
                raise Cancelled()
            if not root.is_dir():
                summary['errors'].append(f'{root}: folder became unavailable; deletion cleanup skipped.')
                continue
            with db:
                for record in db.execute('SELECT path FROM files').fetchall():
                    path = record['path']
                    if Path(path).is_relative_to(root) and path not in seen:
                        db.execute('DELETE FROM rows WHERE path=?', (path,))
                        db.execute('DELETE FROM files WHERE path=?', (path,))
                        db.execute('DELETE FROM sheet_headers WHERE path=?', (path,))
                        summary['removed'] += 1
        if not summary['errors']:
            with db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('last_sync', datetime.datetime.now().astimezone().isoformat(timespec='seconds')))
    except Cancelled:
        summary['cancelled'] = True
    finally:
        db.close()
    return summary
