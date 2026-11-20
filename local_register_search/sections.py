"""Conservative, bounded-lookahead section detection and intelligent header recognition."""
import re
from collections import deque
from itertools import groupby, islice
from openpyxl.utils.cell import column_index_from_string
from .search_terms import parse_date

FIELD_LABELS = {'company', 'given name', 'surname', 'first name', 'last name',
                'function', 'phone', 'telephone', 'email', 'address', 'name', 'date'}

LOOKUP_KEYWORDS = ('dropdown', 'lookup', 'master list', 'lists', 'config', 'validation')


def is_lookup_sheet(sheet_name):
    if not sheet_name:
        return False
    s = str(sheet_name).strip().casefold()
    return any(kw in s for kw in LOOKUP_KEYWORDS)


def is_merged_title(record):
    cells = record[3]
    style = record[4] if len(record) > 4 else {}
    if style.get('merged') and len(cells) <= 2:
        return True
    return False


def header_score(record):
    cells = record[3]
    if not cells or is_merged_title(record):
        return -1
    score = 0
    for col, val in cells.items():
        s = str(val).strip()
        if not s:
            continue
        if re.fullmatch(r'[+-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)', s):
            continue
        if parse_date(s):
            continue
        if len(s) > 100:
            continue
        score += 1
    return score


def detect_header(prefix_records, sheet_name=''):
    """Scan the first ~10 rows and choose the row with the most non-empty string cells that looks like a header."""
    from .sync_logger import log_debug, log_info
    candidates = [r for r in prefix_records if r[1] <= 10]
    if not candidates:
        return 1, (prefix_records[0][3] if prefix_records else {})
    scored = []
    log_info(f"Candidate rows for header detection in sheet '{sheet_name}' (up to row 10):")
    for r in candidates:
        sc = header_score(r)
        scored.append((sc, -r[1], r))
        log_info(f"   Sheet '{sheet_name}' Row {r[1]}: score={sc}, is_merged={is_merged_title(r)}, cells={r[3]}")
    scored.sort(reverse=True)
    best_score, neg_rownum, best_record = scored[0]
    if best_score <= 0:
        non_merged = [r for r in candidates if not is_merged_title(r)]
        if non_merged:
            best_record = non_merged[0]
    log_info(f"Sheet '{sheet_name}': Chosen header row {best_record[1]} with score {best_score}. Headers: {best_record[3]}")
    return best_record[1], best_record[3]


def is_heading(record, following):
    cells, style = record[3], record[4]
    values = [value.strip() for value in cells.values() if value.strip()]
    if not 1 <= len(values) <= 2 or not all(any(c.isalpha() for c in value) for value in values):
        return False
    if values[0].casefold().rstrip(':') in FIELD_LABELS:
        return False
    text = ' | '.join(values)
    numbered = bool(re.match(r'^[A-Za-z0-9]{1,3}\s*[-.:)]\s+\S', text))
    nearby = [row for row in following if row[1] - record[1] <= 6]
    if len(nearby) < 2:
        return False
    if style['styled'] or style['merged']:
        return numbered or any(len(row[3]) >= 2 for row in nearby) or all(
            next(iter(row[3].values())).strip().casefold().rstrip(':') in FIELD_LABELS for row in nearby[:2])
    return all(column_index_from_string(column) <= 3 for column in cells) and (
        numbered or (len(values) == 1 and len(values[0].split()) >= 2 and len(nearby) >= 3
                     and all(len(row[3]) > len(cells) for row in nearby[:3])))


def contextual_rows(records):
    for sheet, group in groupby(records, key=lambda record: record[0]):
        group = iter(group)
        prefix = []
        for r in group:
            prefix.append(r)
            if r[1] > 10 or len(prefix) >= 15:
                break
        if not prefix:
            continue
        header_rownum, headers = detect_header(prefix, sheet_name=sheet)
        data_prefix = [r for r in prefix if r[1] > header_rownum]

        def row_stream():
            for r in data_prefix:
                yield r
            for r in group:
                if r[1] > header_rownum:
                    yield r

        stream = row_stream()
        buffer = deque(islice(stream, 4))
        section = ''
        while buffer:
            record = buffer.popleft()
            if is_heading(record, list(buffer)):
                section = ' | '.join(value.strip() for value in record[3].values() if value.strip())
            yield record[:4], headers, section
            next_record = next(stream, None)
            if next_record is not None:
                buffer.append(next_record)
