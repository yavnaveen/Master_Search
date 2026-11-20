import json
import os
import re
from pathlib import Path
from openpyxl.utils.cell import column_index_from_string
from .database import connect, normalize
from .search_terms import parse_query, valid_query, parse_date, date_aliases, phone_variants
from .sections import is_lookup_sheet


def in_folder(file_path, folder_path):
    if not file_path or not folder_path:
        return False
    try:
        return Path(file_path).resolve().is_relative_to(Path(folder_path).resolve())
    except Exception:
        p = os.path.normcase(os.path.abspath(file_path))
        f = os.path.normcase(os.path.abspath(folder_path))
        if not f.endswith(os.sep):
            f += os.sep
        return p.startswith(f)


def search(path, query, mode='All Words', file='', sheet='', page=0, page_size=-1, sort=None, include_section=True, folder='', folders=None):
    if not valid_query(query):
        return 0, []
    db = connect(path)
    db.create_function('filename', 1, lambda value: Path(value).name.casefold())
    db.create_function('normalize', 1, normalize, deterministic=True)
    db.create_function('in_folder', 2, in_folder, deterministic=True)
    db.create_function('is_lookup_sheet', 1, lambda s: 1 if is_lookup_sheet(s) else 0, deterministic=True)
    try:
        clauses, args = [], []
        include_lookup_row = db.execute("SELECT value FROM settings WHERE key='include_lookup_files'").fetchone()
        include_lookup_files = set(json.loads(include_lookup_row[0])) if include_lookup_row and include_lookup_row[0] else set()
        keyword_clauses = []
        def pattern(value):
            return '%' + value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        for term in parse_query(query):
            date = parse_date(term)
            alternatives = date_aliases(date) if date else [term]
            parts = []
            for value in alternatives:
                text_column = "(r.row_text || ' | ' || normalize(COALESCE(r.row_section,'')))" if include_section else 'r.row_text'
                parts.append(text_column + " LIKE ? ESCAPE '\\'")
                args.append(pattern(value.casefold()))
            for digits in (phone_variants(term) if not date else []):
                parts.append("r.row_digits LIKE ? ESCAPE '\\'")
                args.append(pattern(digits))
            keyword_clauses.append('(' + ' OR '.join(parts) + ')')
        clauses.append('(' + (' OR ' if mode.casefold() == 'any word' else ' AND ').join(keyword_clauses) + ')')
        clauses.append('r.rownum>1')
        if file:
            clauses.append('r.path=?')
            args.append(file)
            if not sheet and file not in include_lookup_files:
                clauses.append('is_lookup_sheet(r.sheet)=0')
        elif folder:
            clauses.append('in_folder(r.path, ?)')
            args.append(folder)
            if not sheet:
                if include_lookup_files:
                    placeholders = ','.join(['?' for _ in include_lookup_files])
                    clauses.append(f'(is_lookup_sheet(r.sheet)=0 OR r.path IN ({placeholders}))')
                    args.extend(list(include_lookup_files))
                else:
                    clauses.append('is_lookup_sheet(r.sheet)=0')
        elif folders:
            clauses.append('(' + ' OR '.join(['in_folder(r.path, ?)' for _ in folders]) + ')')
            args.extend(folders)
            if not sheet:
                if include_lookup_files:
                    placeholders = ','.join(['?' for _ in include_lookup_files])
                    clauses.append(f'(is_lookup_sheet(r.sheet)=0 OR r.path IN ({placeholders}))')
                    args.extend(list(include_lookup_files))
                else:
                    clauses.append('is_lookup_sheet(r.sheet)=0')
        else:
            if not sheet:
                if include_lookup_files:
                    placeholders = ','.join(['?' for _ in include_lookup_files])
                    clauses.append(f'(is_lookup_sheet(r.sheet)=0 OR r.path IN ({placeholders}))')
                    args.extend(list(include_lookup_files))
                else:
                    clauses.append('is_lookup_sheet(r.sheet)=0')
        if sheet:
            clauses.append('r.sheet=?')
            args.append(sheet)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        total = db.execute('SELECT count(*) FROM rows r' + where, args).fetchone()[0]
        schemas = db.execute('SELECT DISTINCT h.headers FROM rows r LEFT JOIN sheet_headers h '
                             'ON h.path=r.path AND h.sheet=r.sheet' + where + ' LIMIT 2', args).fetchall()
        mixed_headers = len(schemas) > 1
        order = 'r.path,r.sheet,r.rownum'
        if sort:
            key, descending = sort
            expression = {'file': 'filename(r.path)', 'sheet': 'r.sheet COLLATE NOCASE', 'row': 'r.rownum', 'section': 'r.row_section COLLATE NOCASE'}.get(key)
            if expression is None and re.fullmatch('[A-Z]+', key):
                expression = "json_extract(r.cells, '$." + key + "')"
            if expression:
                order = expression + (' DESC' if descending else ' ASC') + ',r.path,r.sheet,r.rownum'
        if page_size > 0:
            rows = db.execute('SELECT r.*,f.stale,h.headers FROM rows r JOIN files f ON f.path=r.path '
                              'LEFT JOIN sheet_headers h ON h.path=r.path AND h.sheet=r.sheet' + where +
                              ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', args + [page_size, max(0, page * page_size)]).fetchall()
        else:
            rows = db.execute('SELECT r.*,f.stale,h.headers FROM rows r JOIN files f ON f.path=r.path '
                              'LEFT JOIN sheet_headers h ON h.path=r.path AND h.sheet=r.sheet' + where +
                              ' ORDER BY ' + order, args).fetchall()
        result_rows = []
        for row in rows:
            d = dict(row, mixed_headers=mixed_headers)
            p = Path(d['path'])
            d['file_id'] = str(p)
            d['file_name'] = p.name
            d['file_path'] = str(p)
            d['folder'] = p.parent.name
            d['folder_path'] = str(p.parent)
            d['worksheet'] = d['sheet']
            d['excel_row'] = d['rownum']
            try:
                headers_dict = json.loads(d.get('headers') or '{}')
            except Exception:
                headers_dict = {}
            try:
                cells_dict = json.loads(d.get('cells') or '{}')
            except Exception:
                cells_dict = {}
            d['headers_dict'] = headers_dict
            d['header_list'] = [headers_dict[c] for c in sorted(headers_dict.keys(), key=column_index_from_string) if headers_dict.get(c)]
            d['cells_dict'] = cells_dict
            result_rows.append(d)
        return total, result_rows
    finally:
        db.close()


def group_search_results(rows, sort_order='matches'):
    """
    Groups matched search rows by (file_path, worksheet).
    sort_order: 'matches' (most matches first, default) or 'alphabetical' (by file_name then worksheet).
    Returns a list of group dictionaries.
    """
    from collections import OrderedDict
    groups_map = OrderedDict()

    for r in rows:
        file_path = r.get('file_path') or r.get('path')
        sheet = r.get('worksheet') or r.get('sheet')
        key = (file_path, sheet)
        if key not in groups_map:
            p = Path(file_path)
            headers_dict = None
            if 'headers' in r:
                try:
                    headers_dict = json.loads(r.get('headers') or '{}')
                except Exception:
                    pass
            if headers_dict is None:
                headers_dict = r.get('headers_dict') or {}
            header_list = [headers_dict[c] for c in sorted(headers_dict.keys(), key=column_index_from_string) if headers_dict.get(c)]
            groups_map[key] = {
                'file_id': r.get('file_id') or str(p),
                'file_name': r.get('file_name') or p.name,
                'file_path': str(p),
                'folder': r.get('folder') or p.parent.name,
                'folder_path': r.get('folder_path') or str(p.parent),
                'worksheet': sheet,
                'sheet': sheet,
                'headers': headers_dict,
                'header_list': header_list,
                'rows': [],
            }
        groups_map[key]['rows'].append(r)

    group_list = list(groups_map.values())

    # Determine unique columns and labels for THAT specific worksheet group
    for group in group_list:
        headers_dict = group['headers']
        cols = set(headers_dict.keys())
        for r in group['rows']:
            cells = None
            if 'cells' in r:
                try:
                    cells = json.loads(r.get('cells') or '{}')
                except Exception:
                    pass
            if cells is None:
                cells = r.get('cells_dict') or {}
            cols.update(cells.keys())

        sorted_cols = sorted(cols, key=column_index_from_string)
        labels = []
        unnamed_cols = set()
        for col in sorted_cols:
            hdr = headers_dict.get(col, '').strip()
            if hdr:
                labels.append(hdr)
            else:
                labels.append(f'Col {col}')
                unnamed_cols.add(col)
        group['columns'] = sorted_cols
        group['labels'] = labels
        group['unnamed_cols'] = unnamed_cols
        group['match_count'] = len(group['rows'])

    if sort_order == 'alphabetical':
        group_list.sort(key=lambda g: (g['file_name'].casefold(), g['sheet'].casefold()))
    else:  # default: 'matches' (most matches first)
        group_list.sort(key=lambda g: (-g['match_count'], g['file_name'].casefold(), g['sheet'].casefold()))

    return group_list
