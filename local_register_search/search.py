import re
from pathlib import Path
from .database import connect, normalize
from .search_terms import parse_query, valid_query, parse_date, date_aliases, phone_variants


def search(path, query, mode='All Words', file='', sheet='', page=0, page_size=50, sort=None, include_section=True):
    if not valid_query(query):
        return 0, []
    db = connect(path)
    db.create_function('filename', 1, lambda value: Path(value).name.casefold())
    db.create_function('normalize', 1, normalize, deterministic=True)
    try:
        clauses, args = [], []
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
        rows = db.execute('SELECT r.*,f.stale,h.headers FROM rows r JOIN files f ON f.path=r.path '
                          'LEFT JOIN sheet_headers h ON h.path=r.path AND h.sheet=r.sheet' + where +
                          ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', args + [page_size, max(0, page * page_size)]).fetchall()
        return total, [dict(row, mixed_headers=mixed_headers) for row in rows]
    finally:
        db.close()
