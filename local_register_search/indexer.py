import datetime
import json
import os
import traceback
from pathlib import Path
from .database import connect, normalize
from .excel_reader import read_rows, Cancelled
from .search_terms import index_text
from .sections import contextual_rows, is_lookup_sheet
from .sync_logger import log_info, log_warning, log_error, log_debug


def header_rows(records):
    """Use auto-detected header row as headings, retaining original data row numbers."""
    from itertools import groupby
    from .sections import detect_header
    for sheet, group in groupby(records, key=lambda record: record[0]):
        group_list = list(group)
        header_rownum, headers = detect_header(group_list, sheet_name=sheet)
        for record in group_list:
            if record[1] <= header_rownum:
                continue
            yield record, headers


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
    try:
        path = path.resolve()
    except Exception:
        pass
    if not path.is_dir():
        raise ValueError('Folder is unavailable.')
    return path


def sync(db_path, folders, cancel, progress=lambda *args: None, rebuild=False):
    db = connect(db_path)
    summary = dict(indexed=0, skipped=0, removed=0, errors=[], cancelled=False, file_status=[], rows_indexed=0, files_indexed=0)
    log_info(f"=== Sync started (folders={folders}, rebuild={rebuild}) ===")
    try:
        for folder in folders:
            if cancel.is_set():
                raise Cancelled()
            log_info(f"Scanning folder: '{folder}'")
            try:
                root = local_folder(folder)
                log_info(f"Resolved root folder: '{root}' (exists={root.exists()})")
                found = []
                def scan_error(error):
                    err_msg = f"{getattr(error, 'filename', folder)}: scan warning ({type(error).__name__}: {error})"
                    log_warning(err_msg)
                    summary['errors'].append(err_msg)

                for base, dirs, names in os.walk(root, onerror=scan_error):
                    if cancel.is_set():
                        raise Cancelled()
                    log_debug(f"Walking directory '{base}': {len(dirs)} subdirs, {len(names)} files")
                    # Prune recursive symlink loops
                    dirs[:] = [name for name in dirs if not Path(base, name).is_symlink()]
                    for name in names:
                        if name.startswith('~$'):
                            log_debug(f"Ignoring temporary lock file: {name}")
                            continue
                        p = Path(base, name)
                        suffix = p.suffix.lower()
                        if suffix in {'.xlsx', '.xlsm', '.xls'}:
                            try:
                                candidate = p.resolve()
                                if candidate.is_file():
                                    sz = candidate.stat().st_size
                                    log_info(f"Discovered Excel file: '{candidate}' ({sz} bytes)")
                                    found.append(str(candidate))
                                else:
                                    msg = f"{p}: skipped (file not accessible)"
                                    log_warning(msg)
                                    summary['errors'].append(msg)
                                    summary['file_status'].append({'path': str(p), 'status': 'skipped', 'rows': 0, 'reason': 'File not accessible'})
                            except Exception as exc:
                                msg = f"{p}: skipped ({type(exc).__name__}: {exc})"
                                log_error(msg, exc_info=True)
                                summary['errors'].append(msg)
                                summary['file_status'].append({'path': str(p), 'status': 'failed', 'rows': 0, 'reason': f'{type(exc).__name__}: {exc}'})
                        else:
                            log_debug(f"Ignoring non-Excel file: '{name}' (suffix '{suffix}')")
            except (OSError, ValueError) as exc:
                msg = f"{folder}: folder scan failed ({type(exc).__name__}: {exc}); existing index retained."
                log_error(msg, exc_info=True)
                summary['errors'].append(msg)
                summary['file_status'].append({'path': str(folder), 'status': 'failed', 'rows': 0, 'reason': f'{type(exc).__name__}: {exc}'})
                continue

            seen = set(found)
            log_info(f"Found {len(found)} Excel files in '{folder}': {found}")
            include_lookup_row = db.execute("SELECT value FROM settings WHERE key='include_lookup_files'").fetchone()
            include_lookup_files = set(json.loads(include_lookup_row[0])) if include_lookup_row and include_lookup_row[0] else set()
            for i, path in enumerate(found):
                if cancel.is_set():
                    raise Cancelled()
                progress(i + 1, len(found), path)
                log_info(f"[{i+1}/{len(found)}] Processing file: '{path}'")
                try:
                    stat = os.stat(path)
                    old = db.execute('SELECT * FROM files WHERE path=?', (path,)).fetchone()
                    outdated = db.execute("SELECT 1 FROM rows WHERE path=? AND (row_text IS NULL OR row_text='' OR row_section IS NULL) LIMIT 1", (path,)).fetchone()
                    if old and not old['stale'] and not rebuild and not outdated and old['mtime'] == stat.st_mtime_ns and old['size'] == stat.st_size:
                        log_info(f"Decision: SKIP '{path}'. Reason: unchanged (mtime={stat.st_mtime_ns}, size={stat.st_size})")
                        summary['skipped'] += 1
                        summary['file_status'].append({
                            'path': path, 'status': 'skipped', 'rows': 0,
                            'reason': 'Unchanged (matched cache mtime/size)'
                        })
                        continue

                    log_info(f"Decision: ACCEPT '{path}'. Reason: new or modified (rebuild={rebuild})")
                    include_lookups = path in include_lookup_files
                    rows_written = 0
                    data_rows_read = 0
                    sheets_indexed = []
                    excluded_lookup_sheets = set()
                    with db:
                        db.execute('DELETE FROM rows WHERE path=?', (path,))
                        db.execute('DELETE FROM sheet_headers WHERE path=?', (path,))
                        saved_sheets = set()

                        def filtered_row_stream(p=path):
                            for r in read_rows(p, cancel, with_context=True):
                                sheet_name = r[0]
                                if not include_lookups and is_lookup_sheet(sheet_name):
                                    if sheet_name not in excluded_lookup_sheets:
                                        excluded_lookup_sheets.add(sheet_name)
                                        log_info(f"Skipping lookup sheet '{sheet_name}' in '{p}' (excluded: lookup sheet)")
                                    continue
                                yield r

                        for (sheet, row, hidden, cells), headers, section in contextual_rows(filtered_row_stream()):
                            data_rows_read += 1
                            if sheet not in saved_sheets:
                                log_info(f"Saving headers for sheet '{sheet}': {headers}")
                                db.execute('INSERT OR REPLACE INTO sheet_headers VALUES(?,?,?)',
                                           (path, sheet, json.dumps(headers, ensure_ascii=False)))
                                saved_sheets.add(sheet)
                                sheets_indexed.append(sheet)
                            text = ' | '.join(cells.values())
                            row_text, row_digits = index_text(cells)
                            db.execute('INSERT INTO rows(path,sheet,rownum,hidden,cells,text,normalized,row_text,row_digits,row_section) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                       (path, sheet, row, hidden, json.dumps(cells, ensure_ascii=False), text, normalize(text), row_text, row_digits, section))
                            rows_written += 1

                        after = os.stat(path)
                        if cancel.is_set():
                            raise Cancelled()
                        if stat.st_size != after.st_size or abs(stat.st_mtime_ns - after.st_mtime_ns) > 2_000_000_000:
                            raise OSError('Workbook changed during indexing')
                        db.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,0,?)', (path, stat.st_mtime_ns, stat.st_size, ''))

                    summary['indexed'] += 1
                    summary['files_indexed'] = summary['indexed']
                    summary['rows_indexed'] += rows_written
                    sheet_info = ', '.join(f"'{s}'" for s in sheets_indexed) if sheets_indexed else 'no sheets'
                    detail = f"{rows_written} rows indexed across {sheet_info} (read {data_rows_read} data rows, wrote {rows_written} to DB)"
                    if excluded_lookup_sheets:
                        detail += f" [excluded: lookup sheet: {', '.join(sorted(excluded_lookup_sheets))}]"
                    log_info(f"Successfully indexed '{path}': {detail}")
                    summary['file_status'].append({
                        'path': path, 'status': 'indexed', 'rows': rows_written,
                        'reason': detail
                    })
                    for excl_sheet in sorted(excluded_lookup_sheets):
                        summary['file_status'].append({
                            'path': f"{Path(path).name} › {excl_sheet}",
                            'status': 'excluded',
                            'rows': 0,
                            'reason': 'excluded: lookup sheet'
                        })
                except Cancelled:
                    raise
                except Exception as exc:
                    msg = str(exc).strip()
                    error = f'{type(exc).__name__}: {msg}' if msg else f'{type(exc).__name__}: cannot read workbook'
                    log_error(f"Failed indexing '{path}': {error}\n{traceback.format_exc()}")
                    with db:
                        db.execute('INSERT INTO files VALUES(?,?,?,1,?) ON CONFLICT(path) DO UPDATE SET stale=1,error=excluded.error',
                                   (path, 0, 0, error))
                    summary['errors'].append(f'{path}: {error}')
                    summary['file_status'].append({
                        'path': path, 'status': 'failed', 'rows': 0,
                        'reason': error
                    })

            if cancel.is_set():
                raise Cancelled()
            if not root.is_dir():
                msg = f"{root}: folder became unavailable; deletion cleanup skipped."
                log_warning(msg)
                summary['errors'].append(msg)
                continue
            with db:
                for record in db.execute('SELECT path FROM files').fetchall():
                    path = record['path']
                    try:
                        is_rel = Path(path).resolve().is_relative_to(root)
                    except Exception:
                        is_rel = False
                    if is_rel and path not in seen:
                        log_info(f"Removing deleted file from index: '{path}'")
                        db.execute('DELETE FROM rows WHERE path=?', (path,))
                        db.execute('DELETE FROM files WHERE path=?', (path,))
                        db.execute('DELETE FROM sheet_headers WHERE path=?', (path,))
                        summary['removed'] += 1

        if folders:
            configured_roots = []
            for f in folders:
                try:
                    configured_roots.append(local_folder(f))
                except Exception:
                    pass
            if configured_roots:
                with db:
                    for record in db.execute('SELECT path FROM files').fetchall():
                        p_str = record['path']
                        try:
                            in_any = any(Path(p_str).resolve().is_relative_to(cr) for cr in configured_roots)
                        except Exception:
                            in_any = False
                        if not in_any:
                            log_info(f"Removing orphaned file (not in configured folders): '{p_str}'")
                            db.execute('DELETE FROM rows WHERE path=?', (p_str,))
                            db.execute('DELETE FROM files WHERE path=?', (p_str,))
                            db.execute('DELETE FROM sheet_headers WHERE path=?', (p_str,))
                            summary['removed'] += 1

        if not summary['cancelled']:
            with db:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('last_sync', datetime.datetime.now().astimezone().isoformat(timespec='seconds')))
            log_info(f"Updated last_sync in settings.")
    except Cancelled:
        summary['cancelled'] = True
        log_warning("Sync was cancelled by user.")
    finally:
        db.close()
    log_info(f"=== Sync completed: {summary['indexed']} indexed, {summary['skipped']} skipped, {summary['removed']} removed, {len(summary['errors'])} errors ===")
    return summary
