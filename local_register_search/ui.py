import html
import json
import os
import re
import threading
import sys
import datetime
from pathlib import Path
from openpyxl.utils.cell import column_index_from_string, get_column_letter
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QTextBrowser,
    QLabel, QListWidget, QFileDialog, QMessageBox, QSplitter, QProgressBar)
from .database import connect, data_dir
from .indexer import sync, local_folder, upgrade_saved_headers
from .search import search


class Worker(QThread):
    done = Signal(object)
    progress = Signal(int, int, str)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            self.done.emit(self.operation(self))
        except Exception as exc:
            self.done.emit({'failure': type(exc).__name__})


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Local Register Search')
        self.resize(1250, 850)
        self.path = data_dir() / 'index.sqlite3'
        upgrade_saved_headers(self.path)
        db = connect(self.path)
        saved = db.execute("SELECT value FROM settings WHERE key='folders'").fetchone()
        preferences = db.execute("SELECT value FROM settings WHERE key='desktop_preferences'").fetchone()
        db.close()
        self.preferences = json.loads(preferences[0]) if preferences else {}
        self.recent_searches = self.preferences.get('recent_searches', [])
        self.dark_mode = self.preferences.get('dark_mode', False)
        self.auto_sync = self.preferences.get('auto_sync', False)
        self.watch_snapshot = None
        self.watch_busy = False
        self.export_busy = False
        self.folder_generation = 0
        self.sort = None
        self.result_columns = []
        self.folders = json.loads(saved[0]) if saved else []
        self.jobs = set()
        self.scanning = False
        self.search_busy = False
        self.search_pending = False
        self.generation = 0
        self.page = 0
        self.rows = []
        self.cancel = threading.Event()
        from .layout import build
        build(self)
        self.feedback_timer = QTimer(self)
        self.feedback_timer.setSingleShot(True)
        self.feedback_timer.timeout.connect(self.feedback.hide)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.run_search)
        self.query.textChanged.connect(self.schedule_search)
        for combo in (self.mode, self.file_filter, self.sheet_filter, self.page_size):
            combo.currentIndexChanged.connect(self.schedule_search)
        self.table.itemSelectionChanged.connect(self.show_detail)
        self.table.selectionModel().currentRowChanged.connect(lambda current, previous: self.show_detail())
        self.table.clicked.connect(self.select_record)
        self.table.frozen.clicked.connect(self.select_record)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_results)
        self.table.frozen.horizontalHeader().sectionClicked.connect(self.sort_results)
        self.setAcceptDrops(True)
        self.shortcuts = [QShortcut(QKeySequence('Ctrl+F'), self), QShortcut(QKeySequence('Ctrl+Shift+S'), self)]
        self.shortcuts[0].activated.connect(self.query.setFocus)
        self.shortcuts[1].activated.connect(lambda: self.start_sync(False))
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(10000)
        self.watch_timer.timeout.connect(self.check_folders)
        self.apply_theme()
        self.refresh_recent_searches()
        self.refresh_filters()
        self.run_search()
        if self.auto_sync:
            self.watch_timer.start()
            QTimer.singleShot(1000, lambda: self.start_sync(False))

    def save_preferences(self):
        self.preferences.update(recent_searches=self.recent_searches, dark_mode=self.dark_mode, auto_sync=self.auto_sync)
        db = connect(self.path)
        with db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('desktop_preferences', json.dumps(self.preferences)))
        db.close()

    def refresh_recent_searches(self):
        self.recent.blockSignals(True)
        self.recent.clear()
        self.recent.addItem('Recent searches', '')
        for query in self.recent_searches:
            self.recent.addItem(query, query)
        self.recent.blockSignals(False)

    def submit_search(self):
        query = self.query.text().strip()
        if query:
            self.recent_searches = [query] + [value for value in self.recent_searches if value != query]
            self.recent_searches = self.recent_searches[:15]
            self.save_preferences()
            self.refresh_recent_searches()
        self.timer.stop()
        self.page = 0
        self.run_search()

    def use_recent_search(self, index):
        value = self.recent.itemData(index)
        if value:
            self.query.setText(value)
            self.submit_search()

    def set_dark_mode(self, enabled):
        self.dark_mode = enabled
        self.apply_theme()
        self.save_preferences()

    def apply_theme(self):
        from .layout import DARK_STYLE
        self.setStyleSheet(self.light_style + (DARK_STYLE if self.dark_mode else ''))
        self.table.dark_mode = self.dark_mode
        # Inline label colors would otherwise override the dark stylesheet.
        for label in self.findChildren(QLabel):
            original = label.property('lightStyle')
            if original is None:
                original = label.styleSheet()
                label.setProperty('lightStyle', original)
            if label is self.feedback:
                continue
            label.setStyleSheet(re.sub(r'color:#[0-9a-fA-F]{6}', 'color:#d8e5ed', original).replace('background:#e6f3f5', 'background:#294152') if self.dark_mode else original)
        self.table.viewport().update()
        self.table.frozen.viewport().update()
        self.show_detail()

    def set_auto_sync(self, enabled):
        self.auto_sync = enabled
        self.watch_snapshot = None
        self.save_preferences()
        if enabled:
            self.watch_timer.start()
            self.start_sync(False)
        else:
            self.watch_timer.stop()

    def check_folders(self):
        if not self.auto_sync or not self.folders or self.scanning or self.watch_busy:
            return
        from .desktop_features import folder_snapshot
        self.watch_busy = True
        generation = self.folder_generation
        folders = self.folders[:]
        self.launch(Worker(lambda worker: folder_snapshot(folders, self.cancel)),
                    lambda result: self.folders_checked(generation, result))

    def folders_checked(self, generation, snapshot):
        self.watch_busy = False
        if not self.auto_sync or generation != self.folder_generation:
            return
        if snapshot is None or 'failure' in snapshot:
            self.notify('Folder watch could not read a folder; existing index retained.', True)
            return
        changed = self.watch_snapshot is not None and snapshot != self.watch_snapshot
        self.watch_snapshot = snapshot
        if changed:
            self.start_sync(False)

    def sort_results(self, column):
        keys = ['file', 'sheet', 'row'] + self.result_columns
        if column >= len(keys):
            return
        key = keys[column]
        self.sort = (key, not self.sort[1] if self.sort and self.sort[0] == key else False)
        order = Qt.SortOrder.DescendingOrder if self.sort[1] else Qt.SortOrder.AscendingOrder
        for header in (self.table.horizontalHeader(), self.table.frozen.horizontalHeader()):
            header.setSortIndicator(column, order)
        self.page = 0
        self.run_search()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.scanning and any(url.isLocalFile() and Path(url.toLocalFile()).is_dir() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.scanning:
            return
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.add_folder_path(url.toLocalFile())
        event.acceptProposedAction()

    def export_results(self):
        if not getattr(self, 'total', 0):
            self.notify('No matching rows to export.', True)
            return
        destination, selected_filter = QFileDialog.getSaveFileName(self, 'Export all matching results', 'Search results.xlsx', 'Excel workbook (*.xlsx);;CSV (*.csv)')
        if not destination:
            return
        if Path(destination).suffix.lower() not in {'.xlsx', '.csv'}:
            destination += '.csv' if 'CSV' in selected_filter else '.xlsx'
        from .desktop_features import export_records
        args = (self.path, self.query.text(), self.mode.currentText(), self.file_filter.currentData() or '', self.sheet_filter.currentData() or '', 0, -1, self.sort)
        self.export_button.setEnabled(False)
        self.export_busy = True
        self.notify('Exporting all matching results…')
        self.launch(Worker(lambda worker: export_records(destination, search(*args)[1])), self.export_done)

    def export_done(self, result):
        self.export_busy = False
        self.export_button.setEnabled(bool(getattr(self, 'total', 0)))
        self.notify('Export failed: ' + result['failure'] if isinstance(result, dict) else f'Exported {result:,} matching rows.', isinstance(result, dict))

    def notify(self, message, error=False):
        self.feedback.setText(message)
        self.feedback.setStyleSheet('background: %s; color: white; padding: 10px; border-radius: 6px; font-weight: 600;' % ('#a83232' if error else '#167084'))
        self.feedback.show()
        self.feedback_timer.start(3500)

    def button_clicked(self, button):
        button.setProperty('clickedFeedback', True)
        button.style().unpolish(button)
        button.style().polish(button)
        timer = getattr(button, '_feedback_timer', None)
        if timer is None:
            timer = QTimer(button)
            timer.setSingleShot(True)
            def reset():
                button.setProperty('clickedFeedback', False)
                button.style().unpolish(button)
                button.style().polish(button)
            timer.timeout.connect(reset)
            button._feedback_timer = timer
        timer.start(650)

    def cancel_sync(self):
        if self.auto_sync:
            self.set_auto_sync(False)
        self.cancel.set()
        self.cancel_button.setEnabled(False)
        self.status.setText('Cancelling sync; waiting for the current workbook to finish.')
        self.notify('Cancellation requested')

    def save_folders(self):
        self.folder_generation += 1
        self.watch_snapshot = None
        db = connect(self.path)
        with db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('folders', json.dumps(self.folders)))
        db.close()
        self.folder_list.clear()
        self.folder_list.addItems(self.folders)
        self.format_folders()
        self.update_empty_state()

    def format_folders(self):
        for i, folder in enumerate(self.folders):
            self.folder_list.item(i).setText(Path(folder).name or folder)
            self.folder_list.item(i).setToolTip(folder)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select local register folder')
        if folder:
            self.add_folder_path(folder)

    def add_folder_path(self, folder):
        if self.scanning:
            self.notify('Wait for the current sync to finish.', True)
            return
        try:
            folder = str(local_folder(folder))
            if folder not in self.folders:
                self.folders.append(folder)
                self.save_folders()
                self.notify('Folder added. Click Sync files to import records.')
                if self.auto_sync:
                    self.start_sync(False)
            else:
                self.notify('This folder is already added.')
        except ValueError as exc:
            self.notify(str(exc), True)

    def update_empty_state(self):
        first_run = getattr(self, 'indexed', 0) == 0
        empty = not getattr(self, 'total', 0)
        self.empty_card.setVisible(empty)
        self.table.setVisible(not empty)
        self.empty_title.setText('Get started' if first_run else 'No matching records')
        self.empty_message.setText('Step 1: Add folder\nStep 2: Sync files\nStep 3: Search\n\nDrop a local folder here to add it.' if first_run else 'Try another keyword, or choose All files and All worksheets.')
        self.empty_sync.setVisible(first_run and bool(self.folders))
        self.empty_add.setVisible(first_run)
        self.empty_reset.setVisible(not first_run)
        self.empty_sync.setText('Sync now' + (f' · {Path(self.folders[0]).name}' if len(self.folders) == 1 else ''))

    def reset_filters(self):
        self.query.clear()
        self.file_filter.setCurrentIndex(0)
        self.sheet_filter.setCurrentIndex(0)
        self.submit_search()

    def remove_folder(self):
        index = self.folder_list.currentRow()
        if index >= 0:
            self.folders.pop(index)
            self.save_folders()
            self.status.setText('Folder removed from scanning. Existing records remain until you clear the index in Settings.')
            self.notify('Folder removed from the scan list.')
        else:
            self.notify('Select a folder in the sidebar first.', True)

    def delete_folder_data(self):
        if self.jobs or self.scanning:
            QMessageBox.information(self, 'Busy', 'Wait for current operations to finish before deleting folder data.')
            return
        index = self.folder_list.currentRow()
        if index < 0:
            QMessageBox.information(self, 'Select folder', 'Select a register folder in the sidebar first.')
            return
        folder = self.folders[index]
        if QMessageBox.question(self, 'Clear selected folder index',
                f'Delete saved records for this folder and its subfolders?\n\n{folder}\n\n'
                'Original Excel files remain untouched. The folder will also be removed from the scan list.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.set_auto_sync(False)
        db = connect(self.path)
        try:
            paths = [row[0] for row in db.execute('SELECT path FROM files')
                     if Path(row[0]).is_relative_to(Path(folder))]
            with db:
                db.executemany('DELETE FROM rows WHERE path=?', [(path,) for path in paths])
                db.executemany('DELETE FROM files WHERE path=?', [(path,) for path in paths])
                db.executemany('DELETE FROM sheet_headers WHERE path=?', [(path,) for path in paths])
                remaining = [value for value in self.folders if not Path(value).is_relative_to(Path(folder))]
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('folders', json.dumps(remaining)))
            self.folders = remaining
        finally:
            db.close()
        self.save_folders()
        self.errors.clear()
        self.refresh_filters()
        self.schedule_search()
        self.status.setText(f'Cleared indexed records for {len(paths)} files. Original Excel files are unchanged.')

    def launch(self, job, callback):
        self.jobs.add(job)
        job.done.connect(callback)
        job.finished.connect(lambda: self.jobs.discard(job))
        job.start()

    def start_sync(self, rebuild):
        if not self.folders:
            self.notify('Add a folder before syncing files.', True)
            return
        if self.scanning:
            self.notify('Sync is already running.')
            return
        self.scanning = True
        self.cancel.clear()
        for button in self.controls:
            button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.progress_label.setText('Discovering files…')
        self.status.setText('Scanning local folders…')
        job = Worker(lambda worker: sync(self.path, self.folders[:], self.cancel, worker.progress.emit, rebuild))
        job.progress.connect(self.update_progress)
        self.launch(job, self.sync_done)

    def update_progress(self, done, total, path):
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.progress_label.setText(f'{done:,}/{total:,} files')
        self.status.setText(f'{done}/{total} • {path}')

    def sync_done(self, result):
        self.scanning = False
        for button in self.controls:
            button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if not result.get('cancelled') and 'failure' not in result else 0)
        self.progress_label.setText('Sync finished' if 'failure' not in result else 'Sync failed')
        if 'failure' in result:
            self.status.setText('Indexing failed: ' + result['failure'])
        else:
            self.status.setText(f"{'Cancelled' if result['cancelled'] else 'Completed'}: {result['indexed']} indexed, {result['skipped']} unchanged, {result['removed']} deleted, {len(result['errors'])} errors.")
            self.errors.setPlainText('\n'.join(result['errors']))
        self.refresh_filters()
        if self.auto_sync and 'failure' not in result:
            db = connect(self.path)
            self.watch_snapshot = {row['path']: (row['mtime'], row['size']) for row in db.execute('SELECT path,mtime,size FROM files')
                                   if any(Path(row['path']).is_relative_to(Path(folder)) for folder in self.folders)}
            db.close()
        self.schedule_search()

    def refresh_filters(self):
        db = connect(self.path)
        for combo, title, sql in [(self.file_filter, 'All files', 'SELECT path FROM files ORDER BY path'),
                                  (self.sheet_filter, 'All worksheets', 'SELECT DISTINCT sheet FROM rows ORDER BY sheet')]:
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(title, '')
            for row in db.execute(sql):
                combo.addItem(Path(row[0]).name if combo is self.file_filter else row[0], row[0])
                combo.setItemData(combo.count() - 1, row[0], Qt.ItemDataRole.ToolTipRole)
            index = combo.findData(selected)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)
        last = db.execute("SELECT value FROM settings WHERE key='last_sync'").fetchone()
        stale = db.execute('SELECT count(*) FROM files WHERE stale=1').fetchone()[0]
        if stale:
            self.errors.setPlainText('\n'.join(f"{row['path']}: {row['error']}" for row in db.execute('SELECT path,error FROM files WHERE stale=1')))
        self.setWindowTitle(f"Local Register Search • {stale} stale files" if stale else 'Local Register Search')
        indexed = db.execute('SELECT count(*) FROM files WHERE stale=0').fetchone()[0]
        self.indexed = db.execute('SELECT count(*) FROM rows').fetchone()[0]
        self.index_summary.setText(f"{indexed:,} files indexed | Last sync: {self.format_sync(last[0]) if last else 'Never'}")
        self.index_summary.setToolTip(self.index_summary.text())
        db.close()
        self.update_empty_state()

    def schedule_search(self, *args):
        self.page = 0
        self.generation += 1
        self.timer.start()

    def change_page(self, delta):
        size = self.page_size.currentData()
        if size > 0 and self.page + delta >= 0 and (delta < 0 or (self.page + 1) * size < getattr(self, 'total', 0)):
            self.page += delta
            self.run_search()

    def run_search(self):
        if self.search_busy:
            self.search_pending = True
            return
        self.search_busy = True
        self.search_pending = False
        self.generation += 1
        generation = self.generation
        args = (self.path, self.query.text(), self.mode.currentText(), self.file_filter.currentData() or '',
                self.sheet_filter.currentData() or '', self.page, self.page_size.currentData(), self.sort)
        self.count.setText('Searching…')
        self.launch(Worker(lambda worker: search(*args)), lambda result: self.complete_search(generation, result))

    def complete_search(self, generation, result):
        self.search_busy = False
        self.search_done(generation, result)
        if self.search_pending:
            self.run_search()

    def search_done(self, generation, result):
        if generation != self.generation:
            return
        if isinstance(result, dict):
            self.count.setText('Search failed: ' + result['failure'])
            return
        self.total, self.rows = result
        self.table.reset_lift()
        cells = [json.loads(row['cells']) for row in self.rows]
        last_column = max((column_index_from_string(key) for record in cells for key in record), default=0)
        columns = [get_column_letter(number) for number in range(1, last_column + 1)]
        self.result_columns = columns
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.clearContents()
        self.table.setColumnCount(3 + len(columns))
        header_sets = [json.loads(row.get('headers') or '{}') for row in self.rows]
        labels = []
        for column in columns:
            names = list(dict.fromkeys(headers[column] for headers in header_sets if headers.get(column)))
            labels.append(' / '.join(names) if names else column)
        self.table.setHorizontalHeaderLabels(['File', 'Sheet', 'Row'] + labels)
        for j, column in enumerate(columns, 3):
            self.table.horizontalHeaderItem(j).setToolTip(f'Excel column {column}: {labels[j - 3]}')
        for j in range(3, 3 + len(columns)):
            name = labels[j - 3].casefold()
            width = 310 if 'subject' in name or 'description' in name else 180 if 'letter' in name else 110 if 'date' in name else 70 if name in ('s. n.', 's.n.', 's no', 's. no.') else 130
            self.table.setColumnWidth(j, width)
            self.table.setItemDelegateForColumn(j, self.match_delegate)
        self.table.setRowCount(len(self.rows))
        self.detail.clear()
        for i, row in enumerate(self.rows):
            values = [Path(row['path']).name + (' [STALE]' if row['stale'] else ''),
                      row['sheet'] + (' [hidden]' if row['hidden'] else ''), str(row['rownum'])]
            values.extend(self.display_cell(cells[i].get(column, '')) for column in columns)
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(row['path'] + '\nSheet: ' + row['sheet'] if j == 0 else value)
                self.table.setItem(i, j, item)
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self.table.update_frozen()
        size = self.page_size.currentData()
        start = self.page * size + 1 if size > 0 else 1
        pages = (self.total + size - 1) // size if size > 0 else 1
        self.count.setText(f'Showing {start}-{start + len(self.rows) - 1} of {self.total} rows | Page {self.page + 1}/{pages}' if self.total else ('No indexed records yet. Add a folder and sync to get started.' if not self.indexed else 'No matching rows. Try another keyword or reset filters.'))
        self.update_empty_state()
        if not getattr(self, 'export_busy', False):
            self.export_button.setEnabled(bool(self.total))
        self.page_buttons[0].setEnabled(size > 0 and self.page > 0)
        self.page_buttons[1].setEnabled(size > 0 and (self.page + 1) * size < self.total)
        for button in self.record_buttons:
            button.setEnabled(bool(self.rows))
        if self.rows:
            self.table.selectRow(0)
            self.show_detail()
        else:
            self.file_title.clear()
            self.detail.setHtml('<h3>No results to display</h3><p>Try a different search or select All files and All worksheets.</p>')

    def selected(self):
        selected = self.table.selectionModel().selectedRows()
        index = selected[0].row() if selected else -1
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def select_record(self, index):
        if index.isValid():
            self.table.selectRow(index.row())
            self.show_detail()

    def row_fields(self, row):
        cells = json.loads(row['cells'])
        headers = json.loads(row.get('headers') or '{}')
        columns = sorted(set(cells) | set(headers), key=column_index_from_string)
        return [(column, headers.get(column) or column, cells.get(column, '')) for column in columns]

    @staticmethod
    def format_sync(value):
        try:
            moment = datetime.datetime.fromisoformat(value)
            if moment.tzinfo:
                moment = moment.astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
            months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
            return f'{moment.day:02d} {months[moment.month - 1]} {moment.year}, {moment.hour % 12 or 12}:{moment.minute:02d} {"PM" if moment.hour >= 12 else "AM"}'
        except ValueError:
            return 'Unknown'

    @staticmethod
    def display_cell(value):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T]00:00:00)?', value):
            try:
                date = datetime.date.fromisoformat(value[:10])
                months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
                return f'{date.day:02d} {months[date.month - 1]} {date.year}'
            except ValueError:
                pass
        return value

    def copy_path(self):
        row = self.selected()
        if row:
            QApplication.clipboard().setText(row['path'])
            self.notify('Path copied to clipboard')
            self.copy_confirmation('Copy path')
        else:
            self.notify('Select a record first.', True)

    def copy_confirmation(self, label):
        for button in self.record_buttons:
            if button.property('actionLabel') == label:
                button.setText('Copied!')
                timer = getattr(button, '_copy_timer', None)
                if timer is None:
                    timer = QTimer(button)
                    timer.setSingleShot(True)
                    timer.timeout.connect(lambda b=button, text=label: b.setText(text))
                    button._copy_timer = timer
                timer.start(2000)

    def match_preview(self, text):
        query = self.query.text().strip()
        terms = query.split() if self.mode.currentText() == 'All Words' else [query]
        terms = [term for term in terms if term]
        match = re.search('|'.join(re.escape(term) for term in terms), text, re.IGNORECASE) if terms else None
        start = max(0, match.start() - 55) if match else 0
        end = min(len(text), max(start + 240, match.end() if match else 0))
        return ('…' if start else '') + text[start:end].replace('\n', ' ') + ('…' if end < len(text) else '')

    def highlight(self, value):
        terms = self.query.text().strip().split() if self.mode.currentText() == 'All Words' else [self.query.text().strip()]
        terms = [term for term in terms if term]
        if not terms:
            return html.escape(value)
        pattern = re.compile('|'.join(re.escape(term) for term in sorted(terms, key=len, reverse=True)), re.IGNORECASE)
        parts, start = [], 0
        for match in pattern.finditer(value):
            parts.extend([html.escape(value[start:match.start()]), '<span style="background:#ffe08a;color:#111">', html.escape(match.group()), '</span>'])
            start = match.end()
        parts.append(html.escape(value[start:]))
        return ''.join(parts)

    def show_detail(self):
        row = self.selected()
        if row:
            filename = Path(row['path']).name
            self.file_title.setText(self.file_title.fontMetrics().elidedText(filename, Qt.TextElideMode.ElideMiddle, max(200, self.detail.width() - 20)))
            self.file_title.setToolTip(filename)
            content = f"<p style='color:#52647b;margin:0 0 6px'>{html.escape(row['sheet'])} • Excel row {row['rownum']}" + (' • Hidden worksheet' if row['hidden'] else '') + (' • STALE: source could not be refreshed' if row['stale'] else '') + '</p>'
            wrapped_path = html.escape(row['path']).replace('\\', '\\\u200b').replace('/', '/\u200b').replace('_', '_\u200b')
            content += f"<p style='color:#64748b;font-size:12px;margin:0 0 8px'>{wrapped_path}</p>"
            fields = self.row_fields(row)
            content += f'<p style="color:#52647b;margin:0 0 8px"><b>Complete row · {len(fields)} fields</b> · Scroll for all values</p>'
            content += '<table width="100%" cellspacing="0" cellpadding="9">'
            content += ''.join(f'<tr bgcolor="{"#f4f7fc" if i % 2 == 0 else "#ffffff"}"><td><b>{html.escape(label)}:</b> {self.highlight(self.display_cell(value)).replace(chr(10), "<br>") if value else "<span style=color:#64748b>(empty)</span>"}<br><small>{html.escape(column)}{row["rownum"]}</small></td></tr>' for i, (column, label, value) in enumerate(fields)) + '</table>'
            if self.dark_mode:
                content = content.replace('#f4f7fc', '#223747').replace('#ffffff', '#1b2d3d').replace('#52647b', '#afc4d2').replace('#64748b', '#afc4d2')
            self.detail.setHtml(content)
            self.detail.verticalScrollBar().setValue(0)

    def copy_row(self):
        row = self.selected()
        if row:
            QApplication.clipboard().setText(row['path'] + '\n' + row['sheet'] + f" • Excel row {row['rownum']}\n" + '\n'.join(f'{label} ({column}): {self.display_cell(value)}' for column, label, value in self.row_fields(row)))
            self.notify('Complete row copied to clipboard')
            self.copy_confirmation('Copy row')
        else:
            self.notify('Select a record first.', True)

    def open_path(self, folder):
        row = self.selected()
        if row:
            path = Path(row['path']).parent if folder else Path(row['path'])
            if not path.exists():
                self.notify('Original path is unavailable. Sync to refresh the index.', True)
                QMessageBox.warning(self, 'Unavailable', 'The original path is unavailable. Sync to refresh the index.')
            else:
                opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                self.notify(('Opening folder…' if folder else 'Opening Excel file…') if opened else 'Could not open the path. Check the default application.', not opened)
        else:
            self.notify('Select a record first.', True)

    def delete_index(self):
        if self.jobs:
            QMessageBox.information(self, 'Busy', 'Wait for current operations to finish before deleting the index.')
            return
        if QMessageBox.question(self, 'Clear entire index', 'Clear all saved records and search data?\n\nOriginal Excel files and folders remain untouched. Folder settings stay saved; Sync files will import the data again.', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.set_auto_sync(False)
        db = connect(self.path)
        with db:
            db.execute('DELETE FROM rows')
            db.execute('DELETE FROM files')
            db.execute('DELETE FROM sheet_headers')
            db.execute("DELETE FROM settings WHERE key='last_sync'")
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('VACUUM')
        db.close()
        self.errors.clear()
        self.progress.setValue(0)
        self.refresh_filters()
        self.schedule_search()
        self.status.setText('Local index deleted. Source files were not changed.')

    def closeEvent(self, event):
        if self.jobs:
            self.cancel.set()
            self.status.setText('Finishing background operations; close again when complete.')
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    window = Window()
    window.show()
    if '--smoke-test' in sys.argv:
        def finish():
            if window.jobs:
                QTimer.singleShot(100, finish)
            else:
                app.exit(0 if hasattr(window, 'total') else 1)
        QTimer.singleShot(500, finish)
        QTimer.singleShot(15000, lambda: app.exit(2))
    return app.exec()
