"""Shared query parsing, index aliases and visible match spans."""
import datetime
import re
from .database import normalize

DATE_FORMATS = ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%d.%m.%Y', '%d %b %Y', '%d %B %Y')
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June',
          'July', 'August', 'September', 'October', 'November', 'December')
DATE_PATTERN = r'(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})'
TOKEN_PATTERN = re.compile(r'"((?:\\.|[^"\\])*)"|(' + DATE_PATTERN + r')(?!\w)|([+()\d][\d+() -]*\d)(?!\S)|(\S+)')


def parse_date(value):
    value = normalize(value).strip()
    # Month names are English and independent of the Windows locale.
    match = re.fullmatch(r'(\d{1,2})\s+([a-z]+)\s+(\d{4})', value)
    if match:
        day, month, year = match.groups()
        for number, name in enumerate(MONTHS, 1):
            if month in (name.casefold(), name[:3].casefold()):
                try:
                    return datetime.date(int(year), number, int(day))
                except ValueError:
                    return None
        return None
    for fmt in DATE_FORMATS[:4]:
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def date_aliases(date):
    month = MONTHS[date.month - 1]
    return [date.isoformat(), date.strftime('%d-%m-%Y'), date.strftime('%d/%m/%Y'),
            date.strftime('%d.%m.%Y'), f'{date.day:02d} {month[:3]} {date.year}',
            f'{date.day:02d} {month} {date.year}']


def phone_digits(term):
    if re.fullmatch(r'[\d+()\s-]+', term):
        digits = re.sub(r'\D', '', term)
        if len(digits) >= 6:
            return digits
    return ''


def phone_variants(term):
    digits = phone_digits(term)
    if not digits:
        return []
    return [digits, digits[2:]] if len(digits) == 12 and digits.startswith('91') else [digits]


def parse_query(query):
    terms = []
    for match in TOKEN_PATTERN.finditer(query.strip()):
        term = next(group for group in match.groups() if group is not None).strip()
        if match.group(1) is not None:
            term = re.sub(r'\\(["\\])', r'\1', term)
        if any(char.isalnum() for char in term):
            if match.group(3) and not phone_digits(term):
                terms.extend(normalize(term).split())
            else:
                terms.append(normalize(term))
    return terms


def valid_query(query):
    terms = parse_query(query)
    return bool(terms) and (len(' '.join(terms)) >= 2 or bool(re.search(r'"[^"\n]*\w[^"\n]*"', query)))


def index_text(cells):
    values = list(cells.values())
    aliases = []
    for value in values:
        date = parse_date(value) or (parse_date(value[:10]) if re.match(r'^\d{4}-\d{2}-\d{2}[ T]', value) else None)
        if date:
            aliases.extend(date_aliases(date))
    text = normalize(' | '.join(values + aliases))
    # Keep columns separated so unrelated numeric fields cannot form a phone.
    digits = ' | '.join(re.sub(r'\D', '', normalize(value)) for value in values)
    return text, digits


def match_spans(value, query):
    normalized, positions = '', []
    for index, char in enumerate(value):
        piece = normalize(char)
        normalized += piece
        positions.extend([index] * len(piece))
    spans = []
    for term in parse_query(query):
        for match in re.finditer(re.escape(term), normalized):
            spans.append((positions[match.start()], positions[match.end() - 1] + 1))
        for digits in phone_variants(term):
            for candidate in re.finditer(r'[+()\d][\d+()\s-]*\d', normalized):
                digit_positions = [i for i in range(candidate.start(), candidate.end()) if normalized[i].isdigit()]
                plain = ''.join(normalized[i] for i in digit_positions)
                for match in re.finditer(re.escape(digits), plain):
                    spans.append((positions[digit_positions[match.start()]], positions[digit_positions[match.end() - 1]] + 1))
        date = parse_date(term)
        if date:
            for candidate in re.finditer(DATE_PATTERN, normalized):
                if parse_date(candidate.group()) == date:
                    spans.append((positions[candidate.start()], positions[candidate.end() - 1] + 1))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged
