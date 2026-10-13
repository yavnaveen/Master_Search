# Search update

## Section-aware chips

Restart the app, then click Sync files when the status says:
`Search index update ho raha hai. Sync files dobara dabayein`.
Old rows have a NULL row_section until their source workbook is re-read;
an indexed row with no detected heading stores an empty string instead.
Unchanged files with old rows are re-indexed on an ordinary sync. Ensure their
folders are still configured. Unavailable or unreadable workbooks retain the
old records and migration reminder. Source workbooks are only read.

Settings > Include section heading in search is ON by default. Add `Company`
as one chip and `B - Client Data` as another, then search in All Words. For the
example with heading row 18 and Company row 20, only row 20 should match.
Replace the second chip with `C - Archive Responsible`: only Company row 31
under heading row 29 should match. Turn the option OFF: those paired queries
have no result unless both phrases occur in the actual data row itself.

The Section column and Record details show the inherited heading. Export includes
Section and exports the current search only. File, Sheet, Row and empty `-`
placeholders are not highlighted. Real cells and Section use the chip colours.
Conflicting workbook header schemas produce generic Column A, Column B, etc.
throughout the search, including across pages; Record details uses each row's
own workbook headings.

Detection is conservative and heuristic, not a guarantee for every workbook:
- A candidate has only one or two nonempty textual cells. Numeric value rows
  and common field labels such as Company, Given Name, Function and Phone are
  excluded.
- Bold/fill or a horizontal merged range supplies formatting evidence, but
  the candidate must also have at least two nearby following populated rows.
  Numbered headings, richer detail rows or recognized field labels support it.
- An unformatted candidate must be in the first three columns and have a
  section prefix like `B -`, or be a multiword title followed by at least three
  richer detail rows.
- At most three following populated rows are inspected, within six Excel rows.
  The section resets on a new heading or worksheet; rows before any heading
  have no section. Header row 1 supplies labels and is excluded from results.

Automated coverage includes the exact row 20/31 example, bold field-label
rejection, merged/fill headings, worksheet isolation, migration of unchanged
files, mixed headers, the settings toggle and a source-workbook SHA256 check.

## Keyword chips

Type `Administration - Bank Guarantees`, then press Enter to create a chip.
Type `CATEGORIES (Structured)`, then press Enter to create the second chip.
Press Search, or press Enter again with the input empty. Each chip is one
contiguous phrase, including its spaces and punctuation. Quotes are unnecessary
in the chip input. All Words requires both phrases in the same row; Any word
matches either phrase. A draft in the input is also included when Search is
pressed. Up to eight keywords are accepted.

Check that each phrase has its chip's highlight colour in results and details,
including when a phrase wraps across table lines. Empty cells show `-` but never
highlight or contribute a search match. Entering just `-`, `/`, `,` or `|` creates
no chip and produces no matches. Actual cell data remains unchanged.

Click a chip's X to remove it and search again. Backspace in an empty input
removes the last chip. Double-click a chip to edit, then Enter to commit the
change. The bar's right-hand X clears all keywords and results. With no keywords,
the message is `Type a keyword and press Enter.` A single uncommitted character
does not search; a committed single-character chip can search.

Check Previous/Next retain the keyword query and Export results includes all
matching pages only. The result heading lists the keywords used for the search.
The older examples below describe raw query parsing; in the chip input, enter
each desired keyword separately and press Enter rather than typing an entire
AND query as one chip.

The existing desktop layout, folder selection and sync workflow are retained.
The match selector now offers All Words (AND) and Any word (OR). Double quotes
keep a phrase together. Unquoted formatted phone numbers and dates are recognized
as single terms too. A phrase remains a literal substring, so "Adit 1" also
matches "Adit 10"; adding 90.3 narrows it to rows containing both terms.

## Existing index

Startup adds missing row_text and row_digits columns using ALTER TABLE. When
old rows have no row_text, the app displays:

    Search index update ho raha hai, Sync files dabayein

Click Sync files: unchanged workbooks containing old rows are re-read too.
If an old workbook is outside the configured folders, add its folder before
syncing. Settings > Rebuild index also re-reads all configured workbooks.
Workbook errors retain the previous records and keep the update reminder.
Excel row 1 supplies column labels only; data keeps its original Excel row number.
An empty first row does not cause the next data row to become a header.

## Manual checks

Create a workbook with row 1: Name, Amount, Phone, Date, Notes. Add:

| Name | Amount | Phone | Date (real Excel date cell) | Notes |
| --- | --- | --- | --- | --- |
| Rishikesh | 2973.6 | 98765-43210 | 13 October 2026 | 100% A_B |
| Adit 1 | 90.3 | +91 98765 43210 | 2 January 2025 | Example |
| Adit 10 | 80.3 | 9876543210 | 29 February 2024 | Other |

1. Launch using Setup_and_Run.bat, add the workbook folder, click Sync files.
   The result table must be empty both on startup and after sync.
2. In All Words, search `rishikesh 2973.6`: first row only.
3. Search `"Adit 1" 90.3`: second row only; both terms highlighted.
4. Search `9876543210`, `98765-43210`, `(98765) 43210`, or `+91 98765 43210`:
   all three phone rows match, with the corresponding digits highlighted.
   Indian 12-digit numbers starting with 91 also search the local 10-digit form.
5. Search `13-10-2026`, `13/10/2026`, `2026-10-13`, `13.10.2026`, or
   `13 Oct 2026`: the first row matches and its displayed date is highlighted.
6. In Any word, search `rishikesh 90.3`: first and second rows. In All Words:
   no results. Searching `100%` or `A_B` must treat symbols literally.
7. Clear the query or type one character: results clear immediately and the app
   says "Type a name, phone number, date or phrase to search."
8. Add 105 rows containing `bulk`, sync, then search `bulk`: pages contain
   50, 50 and 5 rows. Check Previous/Next and the count above the table.
9. Export results: all 105 matching rows export, including other pages; unrelated
   rows and workbook headers do not export. Try file/sheet filters too.

Automated checks, from the project directory in PowerShell:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests create temporary workbooks/databases and exercise the Qt window offscreen;
they do not modify your real Excel files or your installed search database.

## Large datasets

This implementation uses parameterized LIKE with escaped %, _ and backslash,
over normalized row_text and per-column digit strings in row_digits. Digits are
separated between columns to prevent a phone match spanning unrelated fields.
Date aliases are in row_text only, not the digits index. Ordinary word-prefix
FTS filtering is removed because it loses valid middle-of-word matches.

A normal B-tree index on row_text cannot accelerate leading-wildcard
`LIKE '%keyword%'`, so adding that index would waste storage for this workload.
For large registers, FTS5 with the trigram tokenizer is the better next option:
it supports substring candidate searches. Keep the current escaped LIKE checks
as the final authority, including for phone/date alternatives. Terms shorter
than three characters require a scan, and FTS5's LIKE acceleration is unavailable
with an ESCAPE clause. A separate trigram MATCH candidate filter can avoid that
restriction; it must index both row_text and row_digits and maintain insert,
update and delete triggers. Benchmark against representative workbooks before
enabling it; this update does not claim a speedup on large data. The previous
word-based row_fts table is retained for database compatibility but not queried.

References:
- https://www.sqlite.org/optoverview.html#the_like_optimization
- https://www.sqlite.org/fts5.html#the_trigram_tokenizer
