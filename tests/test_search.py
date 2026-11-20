import datetime
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from local_register_search.database import connect, needs_search_update
from local_register_search.indexer import sync
from local_register_search.search import search
from local_register_search.search_terms import parse_query, match_spans
from local_register_search.desktop_features import export_records


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / 'index.sqlite3'
        book = Workbook()
        sheet = book.active
        sheet.title = 'Register'
        sheet.append(['Name', 'Amount', 'Phone', 'Date', 'Notes'])
        sheet.append(['Rishikesh', 2973.6, '98765-43210', datetime.date(2026, 10, 13), '100% A_B'])
        sheet.append(['Adit 1', 90.3, '+91 98765 43210', datetime.date(2025, 1, 2), 'prefix needle suffix'])
        sheet.append(['Adit 10', 80.3, '9876543210', datetime.date(2024, 2, 29), '1000 AXB'])
        for i in range(105):
            sheet.append([f'Person {i}', i, '', '', 'bulk record'])
        sheet = book.create_sheet('Other')
        sheet.append(['Name', 'Amount'])
        sheet.append(['Other', 12])
        book.save(self.root / 'register.xlsx')
        book.close()
        result = sync(self.db_path, [str(self.root)], threading.Event())
        self.assertEqual(result['indexed'], 1, result)

    def test_queries_and_modes(self):
        self.assertEqual(parse_query('"Adit 1" 90.3'), ['adit 1', '90.3'])
        self.assertEqual(parse_query('13 Oct 2026'), ['13 oct 2026'])
        self.assertEqual(parse_query('12 34'), ['12', '34'])
        self.assertEqual(search(self.db_path, 'rishikesh 2973.6')[0], 1)
        self.assertEqual(search(self.db_path, '"Adit 1" 90.3')[0], 1)
        self.assertEqual(search(self.db_path, 'rishikesh 90.3', 'Any word')[0], 2)
        self.assertEqual(search(self.db_path, 'rishikesh 90.3')[0], 0)
        self.assertEqual(search(self.db_path, 'ishik')[0], 1)
        self.assertEqual(search(self.db_path, '100%')[0], 1)
        self.assertEqual(search(self.db_path, 'A_B')[0], 1)
        for query in ('', ' ', 'a', '""', '-', '/', ',', '|'):
            self.assertEqual(search(self.db_path, query), (0, []))

    def test_phones_dates_headers_filters(self):
        for phone in ('9876543210', '98765-43210', '+91 98765 43210', '(98765) 43210'):
            self.assertEqual(search(self.db_path, phone)[0], 3, phone)
        for date in ('13-10-2026', '13/10/2026', '2026-10-13', '13.10.2026', '13 Oct 2026', '13 October 2026'):
            self.assertEqual(search(self.db_path, date)[0], 1, date)
        self.assertEqual(search(self.db_path, 'Name')[0], 0)
        row = search(self.db_path, 'rishikesh')[1][0]
        self.assertEqual(row['rownum'], 2)
        self.assertEqual(json.loads(row['headers'])['A'], 'Name')
        self.assertEqual(search(self.db_path, 'other', sheet='Register')[0], 0)
        self.assertEqual(search(self.db_path, 'other', sheet='Other')[0], 1)

    def test_pagination_export_and_sync(self):
        pages = [search(self.db_path, 'bulk', page=i, page_size=50) for i in range(3)]
        self.assertEqual([len(rows) for total, rows in pages], [50, 50, 5])
        self.assertTrue(all(total == 105 for total, rows in pages))
        ids = [row['id'] for _, rows in pages for row in rows]
        self.assertEqual(len(set(ids)), 105)
        matching = search(self.db_path, 'bulk', page_size=-1)[1]
        self.assertEqual(export_records(self.root / 'results.csv', matching), 105)
        self.assertEqual(sync(self.db_path, [str(self.root)], threading.Event())['skipped'], 1)

    def test_migration_forces_unchanged_file_resync(self):
        db = connect(self.db_path)
        with db:
            db.execute("UPDATE rows SET row_text='',row_digits=''")
        self.assertTrue(needs_search_update(db))
        db.close()
        self.assertEqual(sync(self.db_path, [str(self.root)], threading.Event())['indexed'], 1)
        db = connect(self.db_path)
        self.assertFalse(needs_search_update(db))
        db.close()
        legacy = self.root / 'legacy.sqlite3'
        db = sqlite3.connect(legacy)
        db.execute('CREATE TABLE rows(id INTEGER PRIMARY KEY,path TEXT,sheet TEXT,rownum INTEGER,hidden INTEGER,cells TEXT,text TEXT,normalized TEXT)')
        db.execute("INSERT INTO rows VALUES(1,'old','Sheet',2,0,'{}','old','old')")
        db.commit()
        db.close()
        db = connect(legacy)
        self.assertTrue(needs_search_update(db))
        self.assertTrue({'row_text', 'row_digits'} <= {row['name'] for row in db.execute('PRAGMA table_info(rows)')})
        db.close()

    def test_highlight_spans(self):
        for value, query in [('Rishikesh 2973.6', 'rishikesh 2973.6'), ('Adit 1 / 90.3', '"Adit 1" 90.3')]:
            self.assertEqual(len(match_spans(value, query)), 2)
        self.assertEqual(match_spans('98765-43210', '9876543210'), [(0, 11)])
        self.assertEqual(match_spans('9876543210', '+91 98765 43210'), [(0, 10)])
        self.assertEqual(match_spans('13 Oct 2026', '13/10/2026'), [(0, 11)])
        self.assertEqual(match_spans('Straße', 'STRASSE'), [(0, 6)])

    def test_search_requires_synced_backend_records(self):
        from PySide6.QtWidgets import QApplication
        from local_register_search.ui import Window
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        app = QApplication.instance() or QApplication([])
        profile = self.root / 'empty-profile'
        profile.mkdir()
        with patch('local_register_search.ui.data_dir', return_value=profile):
            window = Window()
        self.addCleanup(window.close)
        self.assertEqual(window.empty_title.text(), 'No searchable data')
        self.assertIn('add a folder', window.empty_message.text())
        self.assertIn('color:#b42318', window.empty_message.styleSheet())
        self.assertEqual(window.result_count.text(), '')
        self.assertEqual(window.count.text(), '')
        window.set_dark_mode(True)
        self.assertIn('color:#ffb4ab', window.empty_message.styleSheet())
        window.set_dark_mode(False)
        window.query.setText('rishikesh')
        window.indexed = 100  # A stale UI count must not permit an empty backend search.
        with patch.object(window, 'launch') as launch:
            window.submit_search()
            launch.assert_not_called()
        self.assertEqual(window.table.rowCount(), 0)
        self.assertNotIn('No matching', window.result_count.text())
        self.assertEqual(window.result_count.text(), '')
        self.assertEqual(window.count.text(), '')
        self.assertFalse(window.export_button.isEnabled())
        self.assertTrue(all(not button.isEnabled() for button in window.page_buttons))
        window.folders = [str(self.root)]
        window.update_empty_state()
        self.assertIn('Click Sync files', window.empty_message.text())
        window.scanning = True
        window.update_empty_state()
        self.assertIn('Files are syncing', window.empty_message.text())
        window.scanning = False
        sync(window.path, [str(self.root)], threading.Event())
        window.refresh_filters()
        with patch.object(window, 'launch') as launch:
            window.submit_search()
            launch.assert_called_once()
        self.assertNotIn('color:#b42318', window.result_count.styleSheet())
        window.search_busy = False
        window.search_done(window.generation, (0, []))
        self.assertIn('No matching rows', window.result_count.text())

    def test_window_empty_state_paging_sync_and_migration(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        from local_register_search.ui import Window
        app = QApplication.instance() or QApplication([])
        with patch('local_register_search.ui.data_dir', return_value=self.root):
            window = Window()
        self.addCleanup(window.close)
        window.path = self.db_path
        window.refresh_filters()
        self.assertEqual(window.table.rowCount(), 0)
        self.assertEqual(window.empty_message.text(), 'Type a keyword and press Enter.')

        self.assertEqual(window.page_size.count(), 1)
        self.assertEqual(window.page_size.currentData(), 50)

        def settle():
            for _ in range(400):
                app.processEvents()
                if not window.search_busy and not window.timer.isActive() and not window.jobs:
                    return
                QTest.qWait(10)
            self.fail('Search worker did not finish')

        window.query.setText('bulk')
        window.submit_search()
        settle()
        self.assertEqual(window.table.rowCount(), 10)
        self.assertEqual(window.result_count.text(), '105 matches in 1 file')
        # With all-rows fetch (no global page limit), page navigation has no effect;
        # the first group card always shows the first BATCH_SIZE=10 rows.
        window.change_page(1)
        settle()
        self.assertEqual(window.result_count.text(), '105 matches in 1 file')
        window.change_page(1)
        settle()
        self.assertEqual(window.table.rowCount(), 10)
        self.assertFalse(window.page_buttons[1].isEnabled())
        window.query.setText('a')
        self.assertEqual(window.table.rowCount(), 0)
        self.assertFalse(window.export_button.isEnabled())
        window.query.setText('rishikesh')
        window.submit_search()
        settle()
        window.sync_done(dict(indexed=1, skipped=0, removed=0, errors=[], cancelled=False))
        settle()
        self.assertEqual(window.table.rowCount(), 0)
        self.assertEqual(window.query.text(), '')
        db = connect(self.db_path)
        with db:
            db.execute("UPDATE rows SET row_text=''")
        db.close()
        window.refresh_filters()
        self.assertEqual(window.status.text(), 'Search index update ho raha hai. Sync files dobara dabayein')
        self.assertEqual(window.empty_message.text(), 'Type a keyword and press Enter.')


    def test_searchable_dropdowns_scope_sort_and_geometry(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication
        from local_register_search.ui import Window
        from local_register_search.filter_dropdown import natural_key
        book = Workbook()
        book.remove(book.active)
        names = [f'TP-{i}' for i in range(43, 8, -1)] + ['Sheet10', 'Sheet2', 'Sheet1', 'A very long worksheet name']
        for name in names:
            sheet = book.create_sheet(name)
            sheet.append(['Name', 'Value'])
            sheet.append(['Example', 1])
        source = self.root / 'many-sheets.xlsx'
        book.save(source)
        book.close()
        sync(self.db_path, [str(self.root)], threading.Event())
        app = QApplication.instance() or QApplication([])
        with patch('local_register_search.ui.data_dir', return_value=self.root):
            window = Window()
        self.addCleanup(window.close)
        window.show()
        app.processEvents()
        sheets = window.sheet_filter
        self.assertEqual(sheets.itemText(0), 'All worksheets')
        actual = [sheets.itemText(i) for i in range(1, sheets.count())]
        self.assertEqual(actual, sorted(set(actual), key=natural_key))
        self.assertLess(actual.index('TP-9'), actual.index('TP-10'))
        self.assertLess(actual.index('Sheet2'), actual.index('Sheet10'))
        window.file_filter.setCurrentIndex(window.file_filter.findData(str(source.resolve())))
        window.timer.stop()
        self.assertEqual({sheets.itemText(i) for i in range(1, sheets.count())}, set(names))
        sheets.showPopup()
        app.processEvents()
        self.addCleanup(sheets.hidePopup)
        self.assertTrue(sheets.list_view.verticalScrollBar().maximum() > 0)
        self.assertLessEqual(sheets.list_view.viewport().height(), 12 * max(32, sheets.fontMetrics().height() + 16))
        screen = QGuiApplication.screenAt(sheets.popup.pos()) or QGuiApplication.primaryScreen()
        self.assertTrue(screen.availableGeometry().contains(sheets.popup.geometry()))
        sheets.filter_input.setText('tp-3')
        filtered = [sheets.list_view.item(i).text() for i in range(sheets.list_view.count())]
        self.assertEqual(filtered[0], 'All worksheets')
        self.assertTrue(all('tp-3' in name.casefold() for name in filtered[1:]))
        sheets.filter_input.setText('does-not-exist')
        self.assertEqual(sheets.list_view.item(1).text(), 'No sheets found')
        self.assertFalse(sheets.list_view.item(1).flags() & Qt.ItemFlag.ItemIsEnabled)
        sheets.hidePopup()
        window.file_filter.setCurrentIndex(0)
        window.timer.stop()
        self.assertGreater(sheets.count(), len(names) + 1)
        for combo, message in [(window.file_filter, 'No files found'), (window.folder_filter, 'No folders found')]:
            combo.showPopup()
            combo.filter_input.setText('not-found-xyz')
            self.assertEqual(combo.list_view.item(1).text(), message)
            combo.hidePopup()

    def test_section_context_exact_rows_and_mixed_headers(self):
        import hashlib
        from openpyxl.styles import Font, PatternFill
        from local_register_search.chip_input import quoted
        book = Workbook()
        sheet = book.active
        sheet.title = 'Sections'
        sheet.append(['Field', 'Value', 'Notes'])
        sheet.cell(18, 1, 'B - Client Data').font = Font(bold=True)
        for row, label in [(19, 'Given Name'), (20, 'Company'), (21, 'Function'), (22, 'Phone')]:
            sheet.cell(row, 1, label).font = Font(bold=True)
            sheet.cell(row, 2, 'Client detail')
        sheet.merge_cells('A29:C29')
        sheet.cell(29, 1, 'C - Archive Responsible')
        sheet.cell(29, 1).fill = PatternFill('solid', fgColor='FFFF00')
        for row, label in [(30, 'Given Name'), (31, 'Company'), (32, 'Function'), (33, 'Phone')]:
            sheet.cell(row, 1, label)
            sheet.cell(row, 2, 'Archive detail')
        other = book.create_sheet('Plain')
        other.append(['Different field', 'Different value'])
        other.append(['Company', 'Other detail'])
        for row in range(3, 6):
            other.append(['Function', 'Plain detail'])
        source = self.root / 'sections.xlsx'
        book.save(source)
        book.close()
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        result = sync(self.db_path, [str(self.root)], threading.Event())
        self.assertEqual(result['indexed'], 1, result)
        for section, expected_row in [('B - Client Data', 20), ('C - Archive Responsible', 31)]:
            query = quoted('Company') + ' ' + quoted(section)
            total, records = search(self.db_path, query)
            self.assertEqual(total, 1)
            self.assertEqual(records[0]['rownum'], expected_row)
            self.assertEqual(records[0]['row_section'], section)
            self.assertEqual(search(self.db_path, query, include_section=False)[0], 0)
        self.assertEqual(search(self.db_path, '"Company"', sheet='Plain')[1][0]['row_section'], '')
        db = connect(self.db_path)
        with db:
            db.execute('UPDATE rows SET row_section=NULL WHERE path=?', (str(source.resolve()),))
        self.assertTrue(needs_search_update(db))
        db.close()
        self.assertEqual(sync(self.db_path, [str(self.root)], threading.Event())['indexed'], 1)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        mixed = search(self.db_path, '"Company"')[1]
        self.assertEqual(len(mixed), 3)
        self.assertTrue(all(row['mixed_headers'] for row in mixed))
        # Generic headings must remain consistent even on a one-row page.
        self.assertTrue(search(self.db_path, '"Company"', page_size=1)[1][0]['mixed_headers'])
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtWidgets import QApplication, QCheckBox
        from local_register_search.ui import Window
        app = QApplication.instance() or QApplication([])
        with patch('local_register_search.ui.data_dir', return_value=self.root):
            window = Window()
        self.addCleanup(window.close)
        window.search_done(window.generation, (3, mixed))
        self.assertEqual(window.table.horizontalHeaderItem(1).text(), 'Field')
        self.assertEqual(window.table.horizontalHeaderItem(window.table.columnCount() - 1).text(), 'Section')
        client_index = next(i for i, row in enumerate(mixed) if row['row_section'] == 'B - Client Data')
        self.assertEqual(window.row_fields(mixed[client_index])[0][1], 'Field')
        window.select_record_data(mixed[client_index])
        self.assertIn('Section:', window.detail.toPlainText())
        self.assertIn('B - Client Data', window.detail.toPlainText())
        option = next(control for control in window.findChildren(QCheckBox) if control.text() == 'Include section heading in search')
        self.assertTrue(option.isChecked())
        option.setChecked(False)
        window.timer.stop()
        self.assertFalse(window.include_section)
        records = search(self.db_path, '"Company" "B - Client Data"')[1]
        self.assertEqual(export_records(self.root / 'section-results.csv', records), 1)
        self.assertIn('B - Client Data', (self.root / 'section-results.csv').read_text(encoding='utf-8-sig'))

    def test_phrase_chips_colors_and_empty_cells(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from local_register_search.ui import Window
        from local_register_search.chip_input import quoted
        app = QApplication.instance() or QApplication([])
        with patch('local_register_search.ui.data_dir', return_value=self.root):
            window = Window()
        self.addCleanup(window.close)
        bar = window.query
        phrases = ['Administration - Bank Guarantees', 'CATEGORIES (Structured)']
        for phrase in phrases:
            bar.editor.setText(phrase)
            QTest.keyClick(bar.editor, Qt.Key.Key_Return)
        self.assertEqual([term for term, color in bar.chips], phrases)
        self.assertEqual(parse_query(bar.text()), [phrase.casefold() for phrase in phrases])
        self.assertEqual(bar.editor.placeholderText(), 'Add another keyword...')
        row = search(self.db_path, 'rishikesh')[1][0]
        row['cells'] = json.dumps({'A': phrases[0], 'B': phrases[1], 'C': ''})
        row['headers'] = json.dumps({'A': 'Department', 'B': 'Category', 'C': 'Empty'})
        db = connect(self.db_path)
        from local_register_search.search_terms import index_text
        text, digits = index_text(json.loads(row['cells']))
        with db:
            db.execute('UPDATE rows SET cells=?,row_text=?,row_digits=? WHERE id=?', (row['cells'], text, digits, row['id']))
        db.close()
        self.assertEqual(search(self.db_path, bar.text())[0], 1)
        window.search_done(window.generation, (1, [row]))
        self.assertIn(bar.chips[0][1], window.highlight(phrases[0]))
        self.assertIn(bar.chips[1][1], window.highlight(phrases[1]))
        self.assertEqual(window.highlight('-'), '-')
        self.assertEqual(window.table.item(0, 3).data(Qt.ItemDataRole.UserRole), 'empty-cell')
        self.assertEqual(window.result_count.text(), '1 match in 1 file')
        bar.edit_chip(0)
        bar.editor.setText('Administration - Bank Guarantees updated')
        bar.enter()
        self.assertEqual(bar.chips[0][0], 'Administration - Bank Guarantees updated')
        bar.load_query(' '.join(quoted(term) for term in phrases))
        self.assertEqual([term for term, _ in bar.chips], phrases)
        # Avoid starting workers while testing removal mechanics.
        bar.searchRequested.disconnect(window.submit_search)
        QTest.keyClick(bar.editor, Qt.Key.Key_Backspace)
        self.assertEqual(len(bar.chips), 1)
        bar.clear()
        self.assertEqual(window.table.rowCount(), 0)
        bar.editor.setText('-')
        bar.enter()
        self.assertEqual(bar.chips, [])
        for i in range(9):
            bar.editor.setText('keyword ' + str(i))
            bar.enter()
        self.assertEqual(len(bar.chips), 8)
        bar.clear()
        bar.editor.setText('a')
        self.assertEqual(bar.text(), '')
        bar.enter()
        self.assertEqual(parse_query(bar.text()), ['a'])

    def test_referenced_file_links_only_for_file_cells(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtWidgets import QApplication
        from local_register_search.ui import Window
        from local_register_search.desktop_features import referenced_file
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        app = QApplication.instance() or QApplication([])
        pdf = self.root / 'drawing.pdf'
        pdf.write_bytes(b'%PDF-1.4')
        self.assertIsNone(referenced_file('N/A', self.root / 'register.xlsx'))
        self.assertIsNone(referenced_file('Company Pvt. Ltd', self.root / 'register.xlsx'))
        self.assertEqual(referenced_file('drawing.pdf', self.root / 'register.xlsx'), pdf)
        with patch('local_register_search.ui.data_dir', return_value=self.root):
            window = Window()
        self.addCleanup(window.close)
        row = search(self.db_path, 'rishikesh')[1][0]
        row['cells'] = json.dumps({'A': 'N/A', 'B': str(pdf), 'C': 'Company Pvt. Ltd'})
        window.search_done(window.generation, (1, [row]))
        self.assertNotIn('Open file', window.detail.toPlainText())
        self.assertFalse(window.open_file_button.isHidden())
        window.select_record(window.table.model().index(0, 0))
        window.show()
        app.processEvents()
        self.assertEqual({button.height() for button in window.record_buttons}, {28})
        primary, *secondary = window.record_buttons
        self.assertLessEqual(max(button.width() for button in secondary) - min(button.width() for button in secondary), 1)
        self.assertGreater(primary.width(), secondary[0].width())
        self.assertLess(primary.y(), window.open_file_button.y())
        self.assertLess(window.open_file_button.y(), window.detail.y())
        self.assertEqual(window.detail_file_paths, {'B': pdf})
        with patch('local_register_search.ui.QDesktopServices.openUrl', return_value=True) as opened:
            window.open_file_button.click()
            opened.assert_called_once_with(QUrl.fromLocalFile(str(pdf)))
        with patch('local_register_search.ui.QDesktopServices.openUrl') as opened:
            window.open_referenced_file(QUrl('record-file:A'))
            opened.assert_not_called()
        row['cells'] = json.dumps({'A': 'N/A'})
        window.search_done(window.generation, (1, [row]))
        self.assertTrue(window.open_file_button.isHidden())
        row['cells'] = json.dumps({'A': 'needle', 'B': 'NOTE: ' + 'long note text ' * 30})
        row['headers'] = json.dumps({'A': 'Name', 'B': 'Note'})
        window.search_done(window.generation, (1, [row]))
        window.field_filter.setText('needle')
        self.assertIn('needle', window.detail.toPlainText())
        self.assertNotIn('long note text', window.detail.toPlainText())
        self.assertEqual(window.field_count.text(), '1 of 2 fields')
        window.field_filter.clear()
        self.assertIn('Show more', window.detail.toPlainText())
        window.detail_link_clicked(QUrl('expand-note:B'))
        self.assertIn('Show less', window.detail.toPlainText())
        self.assertIn('long note text ' * 30, window.detail.toPlainText())
        header_note = 'NOTE: If a hyperlink is not opening, please change the default application from Chrome to a specific PDF viewer. ' * 4
        row['headers'] = json.dumps({'A': header_note})
        row['cells'] = json.dumps({'A': '3999'})
        window.search_done(window.generation, (1, [row]))
        rendered = window.detail.toHtml()
        self.assertIn('colspan="2"', rendered)
        self.assertIn('Column A', window.detail.toPlainText())
        self.assertIn('3999', window.detail.toPlainText())
        self.assertIn('Show more', window.detail.toPlainText())
        window.detail_link_clicked(QUrl('expand-note:A:header'))
        self.assertIn(header_note.strip(), window.detail.toPlainText())

    def test_table_readability_and_saved_widths(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFontDatabase, QFontMetrics
        from PySide6.QtWidgets import QApplication
        from local_register_search.ui import Window
        app = QApplication.instance() or QApplication([])
        font_file = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'segoeui.ttf'
        if font_file.exists():
            QFontDatabase.addApplicationFont(str(font_file))
        with patch('local_register_search.ui.data_dir', return_value=self.root):
            window = Window()
        self.addCleanup(window.close)
        window.show()
        long_text = 'Learning and Understanding ' * 15
        row = search(self.db_path, 'rishikesh')[1][0]
        row['cells'] = json.dumps({'A': long_text, 'B': '2973.6', 'C': '', 'D': 'short'})
        row['headers'] = json.dumps({'A': 'KPI', 'B': 'Weightage', 'C': '', 'D': 'Target', 'E': 'Empty'})
        row['path'] = str(self.root / 'PMS draft KPI Sheet with a very long file name.xlsx')
        window.search_done(window.generation, (1, [row]))
        app.processEvents()
        self.assertEqual(window.table.horizontalHeaderItem(1).text(), 'KPI')
        self.assertEqual(window.table.horizontalHeaderItem(3).text(), 'Col C')
        self.assertEqual(window.table.item(0, 3).text(), '-')
        self.assertEqual(window.table.item(0, 5).text(), '-')
        self.assertTrue(window.table.item(0, 2).textAlignment() & Qt.AlignmentFlag.AlignRight)
        self.assertTrue(window.table.item(0, 1).textAlignment() & Qt.AlignmentFlag.AlignLeft)
        font = window.table.font()
        lines = window.match_delegate.visible_lines(long_text, font, 110, 1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[-1].endswith('…'))
        self.assertGreaterEqual(window.table.rowHeight(0), 18)
        self.assertLessEqual(window.table.rowHeight(0), 22)
        self.assertEqual(window.table.item(0, 1).toolTip(), long_text)
        open_button = window.group_cards[0].open_file_btn
        with patch.object(window, 'open_path') as open_path:
            open_button.click()
            open_path.assert_called_once_with(False, row)
        existing_row = dict(row, path=str(self.root / 'register.xlsx'))
        with patch('local_register_search.desktop_features.open_excel_record', return_value=False) as jump, \
             patch('local_register_search.ui.QDesktopServices.openUrl', return_value=True) as fallback:
            window.open_path(False, existing_row)
            jump.assert_called_once_with(Path(existing_row['path']), existing_row['sheet'], existing_row['rownum'])
            fallback.assert_called_once()
        window.select_record_data(row)
        self.assertIn(long_text, window.detail.toPlainText())

    def test_dark_theme_page_backgrounds_and_button_colors(self):
        from PySide6.QtWidgets import QApplication, QLabel
        from local_register_search.ui import Window
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        app = QApplication.instance() or QApplication([])
        with patch('local_register_search.ui.data_dir', return_value=self.root):
            window = Window()
        self.addCleanup(window.close)
        window.show()
        for dark, expected in [(True, '#111e2a'), (False, '#f8fafb'), (True, '#111e2a')]:
            window.set_dark_mode(dark)
            for index in (1, 2, 3):
                window.show_tab(index)
                app.processEvents()
                content = window.pages.widget(index).widget()
                image = content.grab().toImage()
                self.assertEqual(image.pixelColor(image.width() - 3, image.height() - 3).name(), expected)
            online = next(label for label in window.findChildren(QLabel) if 'Offline' in label.text())
            self.assertIn('#74db99', online.styleSheet())
        window.clear_all_button.setEnabled(True)
        app.processEvents()
        image = window.clear_all_button.grab().toImage()
        self.assertEqual(image.pixelColor(5, image.height() // 2).name(), '#462d35')
        danger = next(label for label in window.findChildren(QLabel) if label.text() == 'Danger zone')
        self.assertIn('#ffb4ab', danger.styleSheet())

    def test_top_navigation_drawer_responsive_and_confirmations(self):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtTest import QTest
        from local_register_search.ui import Window
        app = QApplication.instance() or QApplication([])
        font_file = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'segoeui.ttf'
        if font_file.exists():
            QFontDatabase.addApplicationFont(str(font_file))
        with patch('local_register_search.ui.data_dir', return_value=self.root):
            window = Window()
        self.addCleanup(window.close)
        window.show()
        app.processEvents()
        self.assertIsNone(window.findChild(type(window.centralWidget()), 'sidebar'))
        self.assertEqual(window.pages.count(), 4)
        self.assertTrue(window.detail_drawer.isHidden())
        self.assertEqual(window.table.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        window.folders = [str(self.root)]
        window.save_folders()
        window.folder_filter.setCurrentIndex(1)
        self.assertEqual(window.folder_list.currentRow(), 0)
        window.nav_buttons[1].click()
        self.assertEqual(window.pages.currentIndex(), 1)
        self.assertTrue(window.nav_buttons[1].isChecked())
        self.assertFalse(window.nav_buttons[0].isChecked())
        window.show_tab(0)
        window.query.setText('rishikesh')
        window.timer.stop()
        window.search_done(window.generation, search(self.db_path, 'rishikesh'))
        self.assertTrue(window.detail_drawer.isHidden())
        window.select_record(window.table.model().index(0, 0))
        app.processEvents()
        self.assertFalse(window.detail_drawer.isHidden())
        self.assertIn('Name', window.detail.toPlainText())
        window.drawer_close.click()
        self.assertTrue(window.detail_drawer.isHidden())
        window.resize(640, 600)
        app.processEvents()
        top = window.findChild(type(window.centralWidget()), 'topbar')
        self.assertGreaterEqual(top.height(), 40)
        for control in window.nav_buttons:
            self.assertLessEqual(control.mapTo(top, control.rect().bottomRight()).x(), top.width())
        self.assertLessEqual(window.query.geometry().right(), window.query.parentWidget().width())
        with patch('local_register_search.ui.QMessageBox.question', return_value=QMessageBox.StandardButton.No) as confirm:
            window.delete_folder_data()
            window.delete_index()
            self.assertEqual(confirm.call_count, 2)
        self.assertEqual(search(self.db_path, 'rishikesh')[0], 1)
        window.scanning = True
        window.update_progress(120, 500, 'register.xlsx')
        QTest.qWait(180)
        self.assertFalse(window.sync_panel.isHidden())
        self.assertEqual(window.progress_label.text(), '120/500 files')
        window.scanning = False
        window.resize(1440, 900)
        QTest.qWait(700)
        app.processEvents()
        if os.environ.get('SEARCH_UI_PREVIEW'):
            window.grab().save(os.environ['SEARCH_UI_PREVIEW'])

    def test_ktlr_layout_header_detection_and_folder_scoping(self):
        from openpyxl.styles import Alignment
        ktlr_dir = self.root / 'KTLR'
        ktlr_dir.mkdir()
        wb = Workbook()
        ws = wb.active
        ws.title = 'Outgoing'
        ws.merge_cells('A1:I1')
        ws['A1'] = 'Project Name : Kharghar Turbhe Tunnel Road (KTLR)'
        headers = ['Sr No', 'Date', 'Letter Number', 'Type', 'Communication Mode', 'From', 'To', 'Subject', 'Attachments']
        for col_idx, name in enumerate(headers, 1):
            ws.cell(row=3, column=col_idx, value=name)
        # Column J has no header name in row 3
        row4 = [1, '15-10-2026', 'KTLR/2026/001', 'Letter', 'Email', 'Lombardi', 'Client', 'Monthly Report\nSubmission', 'Report.pdf', 12]
        for col_idx, val in enumerate(row4, 1):
            cell = ws.cell(row=4, column=col_idx, value=val)
            if '\n' in str(val):
                cell.alignment = Alignment(wrap_text=True)
        row5 = [2, '16-10-2026', 'KTLR/2026/002', 'Letter', 'Courier', 'Contractor', 'Lombardi', 'Invoice Verification', 'Invoice.pdf', 5]
        for col_idx, val in enumerate(row5, 1):
            ws.cell(row=5, column=col_idx, value=val)
        ktlr_file = ktlr_dir / 'KTLR_Project.xlsx'
        wb.save(ktlr_file)
        wb.close()

        ircon_dir = self.root / 'IRCON'
        ircon_dir.mkdir()
        wb_ircon = Workbook()
        ws_ircon = wb_ircon.active
        ws_ircon.title = 'Outgoing'
        ws_ircon.append(['Sr No', 'Date', 'Letter No', 'Sender', 'Recipient', 'Subject'])
        ws_ircon.append([1, '01-09-2026', 'IRCON/OUT/101', 'Lombardi', 'IRCON', 'Tunnel Design Submission'])
        ircon_file = ircon_dir / 'Correspondence register_Project.xlsm'
        wb_ircon.save(ircon_file)
        wb_ircon.close()

        db_path = self.root / 'ktlr_test.sqlite3'
        result = sync(db_path, [str(ktlr_dir), str(ircon_dir)], threading.Event())
        self.assertEqual(result['indexed'], 2, result)
        self.assertEqual(result['errors'], [])

        # 1. Header auto-detection: row 3 is detected as the header, row 1 merged title is ignored
        db = connect(db_path)
        hdr_row = db.execute("SELECT headers FROM sheet_headers WHERE path=?", (str(ktlr_file.resolve()),)).fetchone()
        self.assertIsNotNone(hdr_row)
        ktlr_headers = json.loads(hdr_row[0])
        self.assertEqual(ktlr_headers['A'], 'Sr No')
        self.assertEqual(ktlr_headers['B'], 'Date')
        self.assertEqual(ktlr_headers['C'], 'Letter Number')
        self.assertEqual(ktlr_headers['H'], 'Subject')
        self.assertNotIn('Project Name', ktlr_headers.get('A', ''))

        # Row 3 itself is NOT inserted as a data row
        row3_in_data = db.execute("SELECT 1 FROM rows WHERE path=? AND rownum=3", (str(ktlr_file.resolve()),)).fetchone()
        self.assertIsNone(row3_in_data)
        db.close()

        # 2. Folder-scoped search: searching KTLR only returns KTLR records
        total_ktlr, rows_ktlr = search(db_path, 'Lombardi', folder=str(ktlr_dir))
        self.assertEqual(total_ktlr, 2)
        for r in rows_ktlr:
            self.assertEqual(r['path'], str(ktlr_file.resolve()))
            self.assertFalse(r['mixed_headers'])
            headers_dict = json.loads(r['headers'])
            self.assertEqual(headers_dict['A'], 'Sr No')
            self.assertEqual(headers_dict['C'], 'Letter Number')

        # Searching IRCON only returns IRCON records
        total_ircon, rows_ircon = search(db_path, 'Lombardi', folder=str(ircon_dir))
        self.assertEqual(total_ircon, 1)
        self.assertEqual(rows_ircon[0]['path'], str(ircon_file.resolve()))

        # Searching with folders=[ktlr_dir] excludes IRCON
        total_folders, rows_folders = search(db_path, 'Lombardi', folders=[str(ktlr_dir)])
        self.assertEqual(total_folders, 2)
        self.assertTrue(all(r['path'] == str(ktlr_file.resolve()) for r in rows_folders))

        # 3. Date, multi-line, and header exclusion
        self.assertEqual(search(db_path, '15-10-2026', folder=str(ktlr_dir))[0], 1)
        self.assertEqual(search(db_path, '15/10/2026', folder=str(ktlr_dir))[0], 1)
        self.assertEqual(search(db_path, '"Monthly Report"', folder=str(ktlr_dir))[0], 1)
        # Header string alone does not match data rows
        self.assertEqual(search(db_path, '"Communication Mode"', folder=str(ktlr_dir))[0], 0)

    def test_ktlr_style_workbook_sync_pipeline(self):
        from openpyxl.styles import Alignment
        ktlr_folder = self.root / 'KTLR_Pipeline_Test'
        ktlr_folder.mkdir()
        wb = Workbook()
        ws = wb.active
        ws.title = 'Email Register'
        # Row 1: Merged title cell in A1:J1
        ws.merge_cells('A1:J1')
        ws['A1'] = 'Project Name : Kharghar Turbhe Tunnel Road (KTLR)'
        # Row 2: Blank
        # Row 3: Headers (Sr No to Attachments, Column J blank)
        headers = ['Sr No', 'Date', 'Letter Number', 'Type', 'Communication Mode', 'From', 'To', 'Subject', 'Attachments']
        for col_idx, name in enumerate(headers, 1):
            ws.cell(row=3, column=col_idx, value=name)
        # Row 4+: Data with wrapped text and DD-MM-YYYY dates
        row4 = [1, '15-10-2026', 'KTLR/2026/001', 'Letter', 'Email', 'Lombardi', 'Client', 'Monthly Progress Report\nSubmission', 'Report.pdf', 12]
        for col_idx, val in enumerate(row4, 1):
            c = ws.cell(row=4, column=col_idx, value=val)
            if '\n' in str(val):
                c.alignment = Alignment(wrap_text=True)
        row5 = [2, '20-10-2026', 'KTLR/2026/002', 'Notice', 'Speed Post', 'Authority', 'Lombardi', 'Site Inspection Notes\nSection B', 'Notice.pdf', 3]
        for col_idx, val in enumerate(row5, 1):
            c = ws.cell(row=5, column=col_idx, value=val)
            if '\n' in str(val):
                c.alignment = Alignment(wrap_text=True)
        xlsx_file = ktlr_folder / 'KTLR_Project.xlsx'
        wb.save(xlsx_file)
        wb.close()

        # Run full sync pipeline against temp folder
        db_path = self.root / 'ktlr_pipeline.sqlite3'
        result = sync(db_path, [str(ktlr_folder)], cancel=threading.Event(), rebuild=True)

        # Assert files_indexed == 1 and rows_indexed > 0
        files_indexed = result.get('files_indexed', result.get('indexed'))
        rows_indexed = result.get('rows_indexed', 0)
        self.assertEqual(files_indexed, 1)
        self.assertGreater(rows_indexed, 0)
        self.assertEqual(rows_indexed, 2)
        self.assertEqual(len(result['errors']), 0)

    def test_grouped_search_results_and_multi_sheet_export(self):
        from local_register_search.search import group_search_results
        from local_register_search.desktop_features import export_records
        from openpyxl import load_workbook

        test_dir = self.root / 'grouping_test_dir'
        test_dir.mkdir()

        # File 1: Invoices
        wb1 = Workbook()
        ws1 = wb1.active
        ws1.title = 'Invoices'
        ws1.append(['Invoice No', 'Vendor', 'Amount', 'Description'])
        ws1.append(['INV-101', 'Lombardi India', 45000, 'Tunnel Design Consulting'])
        wb1.save(test_dir / 'finance_register.xlsx')
        wb1.close()

        # File 2: Letters
        wb2 = Workbook()
        ws2 = wb2.active
        ws2.title = 'Letters'
        ws2.append(['Sr No', 'Letter Number', 'Sender', 'Recipient', 'Subject'])
        ws2.append([1, 'LTR-001', 'Client', 'Lombardi', 'Site Notice'])
        ws2.append([2, 'LTR-002', 'Lombardi', 'Contractor', 'Design Review'])
        wb2.save(test_dir / 'correspondence_letters.xlsx')
        wb2.close()

        # File 3: Personnel
        wb3 = Workbook()
        ws3 = wb3.active
        ws3.title = 'Personnel'
        ws3.append(['Emp ID', 'Full Name', 'Designation', 'Project'])
        ws3.append(['E-100', 'Lombardi Resident Engineer', 'Lead', 'KTLR'])
        wb3.save(test_dir / 'hr_master.xlsx')
        wb3.close()

        db_path = self.root / 'group_test.sqlite3'
        sync_result = sync(db_path, [str(test_dir)], threading.Event(), rebuild=True)
        self.assertEqual(sync_result['indexed'], 3)

        # 1. Search for 'Lombardi'
        total, rows = search(db_path, 'Lombardi')
        self.assertEqual(total, 4)

        # 2. Check row dictionary attributes (Requirement 1)
        for r in rows:
            self.assertIn('file_id', r)
            self.assertIn('file_name', r)
            self.assertIn('file_path', r)
            self.assertIn('folder', r)
            self.assertIn('worksheet', r)
            self.assertIn('excel_row', r)
            self.assertIn('header_list', r)
            self.assertIn('cells_dict', r)
            self.assertEqual(r['file_name'], Path(r['path']).name)
            self.assertEqual(r['worksheet'], r['sheet'])
            self.assertEqual(r['excel_row'], r['rownum'])

        # 3. Test grouping logic (Requirement 2 & 9)
        # Default sort: most matches first
        groups = group_search_results(rows, sort_order='matches')
        self.assertEqual(len(groups), 3)

        # Most matches first: Letters has 2 matches, Invoices and Personnel have 1 match each
        self.assertEqual(groups[0]['file_name'], 'correspondence_letters.xlsx')
        self.assertEqual(groups[0]['sheet'], 'Letters')
        self.assertEqual(groups[0]['match_count'], 2)
        self.assertEqual(groups[0]['labels'], ['Sr No', 'Letter Number', 'Sender', 'Recipient', 'Subject'])

        # Group 1 & 2
        names_and_sheets = {(g['file_name'], g['sheet']) for g in groups[1:]}
        self.assertEqual(names_and_sheets, {('finance_register.xlsx', 'Invoices'), ('hr_master.xlsx', 'Personnel')})

        inv_group = next(g for g in groups if g['file_name'] == 'finance_register.xlsx')
        self.assertEqual(inv_group['match_count'], 1)
        self.assertEqual(inv_group['labels'], ['Invoice No', 'Vendor', 'Amount', 'Description'])

        hr_group = next(g for g in groups if g['file_name'] == 'hr_master.xlsx')
        self.assertEqual(hr_group['match_count'], 1)
        self.assertEqual(hr_group['labels'], ['Emp ID', 'Full Name', 'Designation', 'Project'])

        # Test alphabetical sort
        alpha_groups = group_search_results(rows, sort_order='alphabetical')
        self.assertEqual([g['file_name'] for g in alpha_groups],
                         ['correspondence_letters.xlsx', 'finance_register.xlsx', 'hr_master.xlsx'])

        # 4. Test grouped multi-sheet Excel export (Requirement 7)
        export_file = self.root / 'grouped_results.xlsx'
        exported_count = export_records(export_file, rows)
        self.assertEqual(exported_count, 4)

        wb_exported = load_workbook(export_file)
        self.assertEqual(len(wb_exported.sheetnames), 3)
        for sname in wb_exported.sheetnames:
            self.assertLessEqual(len(sname), 31)

        # Verify Letters sheet in export
        letters_sheet = next(wb_exported[s] for s in wb_exported.sheetnames if 'Letters' in s)
        self.assertEqual(letters_sheet.cell(1, 1).value, 'Excel Row')
        self.assertEqual(letters_sheet.cell(1, 2).value, 'Sr No')
        self.assertEqual(letters_sheet.cell(1, 3).value, 'Letter Number')
        self.assertEqual(letters_sheet.max_row, 3)  # header + 2 data rows

        # Verify Invoices sheet in export
        invoices_sheet = next(wb_exported[s] for s in wb_exported.sheetnames if 'Invoices' in s)
        self.assertEqual(invoices_sheet.cell(1, 1).value, 'Excel Row')
        self.assertEqual(invoices_sheet.cell(1, 2).value, 'Invoice No')
        self.assertEqual(invoices_sheet.cell(1, 3).value, 'Vendor')
        self.assertEqual(invoices_sheet.max_row, 2)  # header + 1 data row

        wb_exported.close()

    def test_ktlr_search_yes_excludes_lookup_sheet(self):
        """Requirement 7: searching 'yes' on KTLR workbook returns 20 matches in ONE group (Email Register) and none from Dropdown_Master."""
        from local_register_search.search import group_search_results
        ktlr_dir = self.root / 'KTLR_Test'
        ktlr_dir.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws_email = wb.active
        ws_email.title = 'Email Register'
        # Row 1 merged title
        ws_email.merge_cells('A1:J1')
        ws_email.cell(1, 1, 'Project Name : Kharghar Turbhe Tunnel Road (KTLR)')
        # Row 2 blank
        # Row 3 real header
        headers = ['Sr No', 'Date', 'Letter Number', 'Type', 'Communication Mode', 'From', 'To', 'Subject', 'Attachments', '']
        for col_idx, h in enumerate(headers, 1):
            ws_email.cell(3, col_idx, h)
        # Data rows: 41 data rows, exactly 20 with 'yes'
        for row_num in range(1, 42):
            has_yes = 'yes' if row_num <= 20 else 'no'
            row_data = [row_num, f'{row_num:02d}-05-2024', f'LTR-{row_num:03d}', has_yes, 'Email', 'Client', 'Lombardi', f'Subject {row_num}', 'Doc.pdf', f'Page {row_num}']
            for col_idx, val in enumerate(row_data, 1):
                ws_email.cell(3 + row_num, col_idx, val)

        # Dropdown_Master sheet (lookup sheet) containing 'yes'
        ws_dropdown = wb.create_sheet('Dropdown_Master')
        ws_dropdown.cell(1, 1, 'Valid Modes')
        ws_dropdown.cell(2, 1, 'yes')
        ws_dropdown.cell(3, 1, 'no')

        ktlr_file = ktlr_dir / 'KTLR_Project.xlsx'
        wb.save(ktlr_file)
        wb.close()

        # Run sync
        ktlr_db = self.root / 'ktlr_yes_test.sqlite3'
        sync_result = sync(ktlr_db, [str(ktlr_dir)], threading.Event(), rebuild=True)
        self.assertEqual(sync_result['indexed'], 1)

        # Verify sync status shows Dropdown_Master as excluded lookup sheet
        excluded_statuses = [s for s in sync_result['file_status'] if s.get('status') == 'excluded']
        self.assertTrue(any('Dropdown_Master' in s['path'] and 'excluded: lookup sheet' in s['reason'] for s in excluded_statuses))

        # Search for 'yes'
        total, rows = search(ktlr_db, 'yes')
        self.assertEqual(total, 20)

        # Group search results: should return exactly 1 group (Email Register)
        groups = group_search_results(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['sheet'], 'Email Register')
        self.assertEqual(groups[0]['match_count'], 20)
        self.assertEqual(groups[0]['file_name'], 'KTLR_Project.xlsx')
        # Check that unnamed column J is labeled 'Col J' with is_unnamed
        self.assertIn('Col J', groups[0]['labels'])
        self.assertIn('J', groups[0]['unnamed_cols'])
        # None from Dropdown_Master
        self.assertFalse(any(g['sheet'] == 'Dropdown_Master' for g in groups))
        self.assertFalse(any(r['sheet'] == 'Dropdown_Master' for r in rows))


    def test_multi_folder_global_pagination_bug_fix(self):
        """Two files in two registered folders; keyword with 260+20 matches; no folder → 2 groups, total 280; folder B → 1 group, 20."""
        from local_register_search.search import group_search_results

        folder_a = self.root / 'folder_a'
        folder_b = self.root / 'folder_b'
        folder_a.mkdir(exist_ok=True)
        folder_b.mkdir(exist_ok=True)

        # File A: 260 data rows, every row has 'yes'
        wb_a = Workbook()
        ws_a = wb_a.active
        ws_a.title = 'Outgoing'
        ws_a.append(['Sr No', 'Date', 'Subject', 'Status'])
        for i in range(260):
            ws_a.append([i + 1, '01-01-2024', f'Letter {i + 1}', 'yes'])
        wb_a.save(folder_a / 'Correspondence_Register.xlsx')
        wb_a.close()

        # File B: 20 data rows, every row has 'yes'
        wb_b = Workbook()
        ws_b = wb_b.active
        ws_b.title = 'Email Register'
        ws_b.append(['Sr No', 'Subject', 'Status'])
        for i in range(20):
            ws_b.append([i + 1, f'Email {i + 1}', 'yes'])
        wb_b.save(folder_b / 'KTLR_Project.xlsx')
        wb_b.close()

        db_path = self.root / 'multi_folder_test.sqlite3'
        sync_result = sync(db_path, [str(folder_a), str(folder_b)], threading.Event(), rebuild=True)
        self.assertEqual(sync_result['indexed'], 2, sync_result)

        # No folder filter → both groups, total 280
        total, rows = search(db_path, 'yes', folders=[str(folder_a), str(folder_b)])
        self.assertEqual(total, 280, f'Expected 280 total, got {total}')
        groups = group_search_results(rows)
        self.assertEqual(len(groups), 2, f'Expected 2 groups, got {len(groups)}: {[g["file_name"] for g in groups]}')
        counts = {g['file_name']: g['match_count'] for g in groups}
        self.assertEqual(counts.get('Correspondence_Register.xlsx'), 260)
        self.assertEqual(counts.get('KTLR_Project.xlsx'), 20)

        # Folder B selected → only 1 group, 20 matches
        total_b, rows_b = search(db_path, 'yes', folder=str(folder_b), folders=[str(folder_a), str(folder_b)])
        self.assertEqual(total_b, 20, f'Expected 20 total for folder B, got {total_b}')
        groups_b = group_search_results(rows_b)
        self.assertEqual(len(groups_b), 1, f'Expected 1 group for folder B, got {len(groups_b)}')
        self.assertEqual(groups_b[0]['match_count'], 20)
        self.assertEqual(groups_b[0]['file_name'], 'KTLR_Project.xlsx')


if __name__ == '__main__':
    unittest.main()
