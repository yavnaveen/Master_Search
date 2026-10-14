import html
import json
import os
import re
import threading
import sys
import datetime
from pathlib import Path
from openpyxl.utils.cell import column_index_from_string, get_column_letter
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QTextBrowser,
    QLabel, QListWidget, QFileDialog, QMessageBox, QSplitter, QProgressBar, QMenu)
from .database import connect, data_dir, needs_search_update
from .indexer import sync, local_folder
from .search import search
from .search_terms import valid_query, match_spans


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
        self.setWindowTitle('Master Register Search - v2')
        self.resize(1250, 850)
        self.path = data_dir() / 'index.sqlite3'
        db = connect(self.path)
        saved = db.execute("SELECT value FROM settings WHERE key='folders'").fetchone()
        preferences = db.execute("SELECT value FROM settings WHERE key='desktop_preferences'").fetchone()
        db.close()
        self.preferences = json.loads(preferences[0]) if preferences else {}
        self.column_widths = self.preferences.get('column_widths', {})
        self.include_section = self.preferences.get('include_section', True)
        self.rendering_results = False
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
        self.query.textChanged.connect(self.input_changed)
        self.file_filter.currentIndexChanged.connect(self.refresh_worksheets)
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

    def input_changed(self, *args):
        self.timer.stop()
        self.generation += 1
        self.search_pending = False
        self.export_button.setEnabled(False)
        if not valid_query(self.query.text()):
            self.search_done(self.generation, (0, []))

    def use_recent_search(self, index):
        value = self.recent.itemData(index)
        if value:
            self.query.load_query(value)
            self.submit_search()

    def set_dark_mode(self, enabled):
        self.dark_mode = enabled
        self.apply_theme()
        self.save_preferences()

    def set_include_section(self, enabled):
        self.include_section = enabled
        self.preferences['include_section'] = enabled
        self.save_preferences()
        self.schedule_search()

    def apply_theme(self):
        from .layout import DARK_STYLE
        self.setStyleSheet(self.light_style + (DARK_STYLE if self.dark_mode else ''))
        self.table.dark_mode = self.dark_mode
        self.query.set_theme(self.dark_mode)
        # Inline label colors would otherwise override the dark stylesheet.
        for label in self.findChildren(QLabel):
            original = label.property('lightStyle')
            if original is None:
                original = label.styleSheet()
                label.setProperty('lightStyle', original)
            if label is self.feedback:
                continue
            if self.dark_mode:
                style = re.sub(r'(?<![\w-])color\s*:\s*#(?:52647b|35465c|64748b|142338)\b', 'color:#afc4d2', original, flags=re.IGNORECASE)
                style = re.sub(r'(?<![\w-])color\s*:\s*#b42318\b', 'color:#ffb4ab', style, flags=re.IGNORECASE)
                label.setStyleSheet(style)
            else:
                label.setStyleSheet(original)
        self.table.viewport().update()
        self.table.frozen.viewport().update()
        self.show_detail()
        self.update_empty_state()

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
        keys = ['file', 'sheet', 'row'] + self.result_columns + ['section']
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
        args = (self.path, getattr(self, 'active_query', self.query.text()), self.mode.currentText(), self.file_filter.currentData() or '', self.sheet_filter.currentData() or '', 0, -1, self.sort, self.include_section)
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
        if hasattr(self, 'folder_filter'):
            selected = self.folder_filter.currentData()
            self.folder_filter.blockSignals(True)
            self.folder_filter.clear()
            self.folder_filter.addItem('Select folder', '')
            for folder in self.folders:
                self.folder_filter.addItem(Path(folder).name or folder, folder)
                self.folder_filter.setItemData(self.folder_filter.count() - 1, folder, Qt.ItemDataRole.ToolTipRole)
            self.folder_filter.setCurrentIndex(max(0, self.folder_filter.findData(selected)))
            self.folder_filter.blockSignals(False)
            self.select_folder_from_dropdown()

    def show_tab(self, index):
        self.pages.setCurrentIndex(index)
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index)
        if index == 0:
            self.query.setFocus()

    def select_folder_from_dropdown(self, *args):
        folder = self.folder_filter.currentData()
        self.folder_list.blockSignals(True)
        self.folder_list.setCurrentRow(self.folders.index(folder) if folder in self.folders else -1)
        self.folder_list.blockSignals(False)

    def select_folder_from_list(self, row):
        folder = self.folders[row] if 0 <= row < len(self.folders) else ''
        self.folder_filter.blockSignals(True)
        self.folder_filter.setCurrentIndex(max(0, self.folder_filter.findData(folder)))
        self.folder_filter.blockSignals(False)

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
        for label in (self.count, self.result_count, self.empty_message):
            base = label.property('noDataBaseStyle')
            if base is None:
                base = label.property('lightStyle')
                if base is None:
                    base = label.styleSheet()
                label.setProperty('noDataBaseStyle', base)
            style = re.sub(r'(?<![\w-])color\s*:\s*#[0-9a-fA-F]{6}', 'color:#afc4d2', base) if self.dark_mode else base
            if first_run and label is self.empty_message:
                style += 'color:' + ('#ffb4ab' if self.dark_mode else '#b42318') + ';font-weight:600;'
            label.setStyleSheet(style)
        empty = not getattr(self, 'total', 0)
        self.empty_card.setVisible(empty)
        self.table.setVisible(not empty)
        self.empty_title.setText('No searchable data' if first_run else 'No matching records')
        self.empty_message.setText(self.no_data_message() if first_run else 'Try another keyword, or choose All files and All worksheets.')
        if first_run:
            self.count.clear()
            self.result_count.clear()
        elif not valid_query(self.query.text()):
            self.empty_title.setText('Search your registers')
            self.empty_message.setText('Type a keyword and press Enter.')
        if not first_run and getattr(self, 'index_update_needed', False) and valid_query(self.query.text()):
            self.empty_message.setText('Search index update ho raha hai. Sync files dobara dabayein')
        self.empty_sync.setVisible(first_run and bool(self.folders))
        self.empty_add.setVisible(first_run)
        self.empty_reset.setVisible(not first_run)
        self.empty_sync.setText('Sync now' + (f' · {Path(self.folders[0]).name}' if len(self.folders) == 1 else ''))

    def no_data_message(self):
        if self.scanning:
            return 'Files are syncing. Search will be available after data is indexed.'
        if self.folders:
            return 'No searchable data is synced. Click Sync files to import Excel records before searching.'
        return 'No searchable data is available. Please add a folder and sync files before searching.'

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
            self.notify('Select a folder in the Folders tab first.', True)

    def delete_folder_data(self):
        if self.jobs or self.scanning:
            QMessageBox.information(self, 'Busy', 'Wait for current operations to finish before deleting folder data.')
            return
        index = self.folder_list.currentRow()
        if index < 0:
            QMessageBox.information(self, 'Select folder', 'Select a register folder from the Folder dropdown or Folders tab first.')
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
        self.query.clear()
        self.timer.stop()
        self.page = 0
        self.generation += 1
        self.search_pending = False
        self.search_done(self.generation, (0, []))

    def refresh_filters(self):
        db = connect(self.path)
        for combo, title, sql in [(self.file_filter, 'All files', 'SELECT path FROM files ORDER BY path')]:
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
        self.refresh_worksheets()
        last = db.execute("SELECT value FROM settings WHERE key='last_sync'").fetchone()
        stale = db.execute('SELECT count(*) FROM files WHERE stale=1').fetchone()[0]
        if stale:
            self.errors.setPlainText('\n'.join(f"{row['path']}: {row['error']}" for row in db.execute('SELECT path,error FROM files WHERE stale=1')))
        self.setWindowTitle(f"Master Register Search - v2 • {stale} stale files" if stale else 'Master Register Search - v2')
        indexed = db.execute('SELECT count(*) FROM files WHERE stale=0').fetchone()[0]
        self.indexed = db.execute('SELECT count(*) FROM rows').fetchone()[0]
        self.index_update_needed = needs_search_update(db)
        if self.index_update_needed:
            self.status.setText('Search index update ho raha hai. Sync files dobara dabayein')
        self.index_summary.setText(f"{indexed:,} files indexed | Last sync: {self.format_sync(last[0]) if last else 'Never'}")
        self.index_summary.setToolTip(self.index_summary.text())
        db.close()
        self.update_empty_state()

    def refresh_worksheets(self, *args):
        from .filter_dropdown import natural_key
        selected = self.sheet_filter.currentData()
        file = self.file_filter.currentData() or ''
        db = connect(self.path)
        try:
            records = db.execute('SELECT DISTINCT sheet FROM rows WHERE path=?', (file,)) if file else db.execute('SELECT DISTINCT sheet FROM rows')
            sheets = sorted((row[0] for row in records), key=natural_key)
        finally:
            db.close()
        self.sheet_filter.blockSignals(True)
        self.sheet_filter.clear()
        self.sheet_filter.addItem('All worksheets', '')
        for sheet in sheets:
            self.sheet_filter.addItem(sheet, sheet)
            self.sheet_filter.setItemData(self.sheet_filter.count() - 1, sheet, Qt.ItemDataRole.ToolTipRole)
        self.sheet_filter.setCurrentIndex(max(0, self.sheet_filter.findData(selected)))
        self.sheet_filter.blockSignals(False)

    def schedule_search(self, *args):
        self.page = 0
        self.generation += 1
        self.export_button.setEnabled(False)
        if not valid_query(self.query.text()):
            self.timer.stop()
            self.search_pending = False
            self.search_done(self.generation, (0, []))
            return
        self.timer.start()

    def change_page(self, delta):
        size = self.page_size.currentData()
        if size > 0 and self.page + delta >= 0 and (delta < 0 or (self.page + 1) * size < getattr(self, 'total', 0)):
            self.page += delta
            self.run_search()

    def run_search(self):
        self.export_button.setEnabled(False)
        db = connect(self.path)
        try:
            self.indexed = db.execute('SELECT count(*) FROM rows').fetchone()[0]
        finally:
            db.close()
        if not self.indexed:
            self.generation += 1
            self.search_pending = False
            self.search_done(self.generation, (0, []))
            return
        if not valid_query(self.query.text()):
            self.generation += 1
            self.search_done(self.generation, (0, []))
            return
        if self.search_busy:
            self.search_pending = True
            return
        self.search_busy = True
        self.search_pending = False
        self.generation += 1
        generation = self.generation
        self.requested_keywords = self.query.keyword_colors()
        args = (self.path, self.query.text(), self.mode.currentText(), self.file_filter.currentData() or '',
                self.sheet_filter.currentData() or '', self.page, self.page_size.currentData(), self.sort, self.include_section)
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
        self.active_keywords = getattr(self, 'requested_keywords', self.query.keyword_colors())
        from .chip_input import quoted
        self.active_query = ' '.join(quoted(value) for value, color in self.active_keywords) if self.rows else ''
        self.rendering_results = True
        self.table.reset_lift()
        cells = [json.loads(row['cells']) for row in self.rows]
        header_sets = [json.loads(row.get('headers') or '{}') for row in self.rows]
        last_column = max((column_index_from_string(key) for record in cells + header_sets for key in record), default=0)
        columns = [get_column_letter(number) for number in range(1, last_column + 1)]
        self.result_columns = columns
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.clearContents()
        self.table.setColumnCount(5 + len(columns))
        mixed_headers = any(row.get('mixed_headers') for row in self.rows) or len({json.dumps(headers, sort_keys=True) for headers in header_sets}) > 1
        labels = []
        for column in columns:
            names = list(dict.fromkeys(headers[column].strip() for headers in header_sets if headers.get(column, '').strip()))
            labels.append(names[0] if names and not mixed_headers else f'Column {column}')
        self.table.setHorizontalHeaderLabels(['File', 'Sheet', 'Row'] + labels + ['Section', 'Open'])
        open_column = 4 + len(columns)
        self.table.setColumnWidth(open_column, 48)
        self.table.setColumnWidth(3 + len(columns), self.column_widths.get('section', 240))
        for j, (key, width) in enumerate(zip(('file', 'sheet', 'row'), (180, 110, 45))):
            self.table.setColumnWidth(j, min(260, self.column_widths.get(key, width)) if j == 0 else width)
        for j, column in enumerate(columns, 3):
            self.table.horizontalHeaderItem(j).setToolTip(f'Excel column {column}: {labels[j - 3]}')
        for j in range(3, 3 + len(columns)):
            name = labels[j - 3].casefold()
            width = 310 if 'subject' in name or 'description' in name else 180 if 'letter' in name else 110 if 'date' in name else 70 if name in ('s. n.', 's.n.', 's no', 's. no.') else 130
            if labels[j - 3].startswith('Column '):
                values = [self.display_cell(record.get(columns[j - 3], '')) for record in cells]
                metrics = self.table.fontMetrics()
                width = min(240, max(70, metrics.horizontalAdvance(labels[j - 3]) + 16,
                                     max((metrics.horizontalAdvance(value.replace('\n', ' ')) + 16 for value in values), default=0)))
            self.table.setColumnWidth(j, self.column_widths.get('cell:' + columns[j - 3] + ':' + labels[j - 3], width))
            self.table.setItemDelegateForColumn(j, self.match_delegate)
        self.table.setRowCount(len(self.rows))
        self.detail.clear()
        from .layout import action_icon
        for i, row in enumerate(self.rows):
            values = [Path(row['path']).name + (' [STALE]' if row['stale'] else ''),
                      row['sheet'] + (' [hidden]' if row['hidden'] else ''), str(row['rownum'])]
            values.extend(self.display_cell(cells[i].get(column, '')) for column in columns)
            values.append(row.get('row_section') or '')
            for j, value in enumerate(values):
                item = QTableWidgetItem(value if value else '-')
                if not value:
                    item.setData(Qt.ItemDataRole.UserRole, 'empty-cell')
                elif j < 3:
                    item.setData(Qt.ItemDataRole.UserRole, 'metadata')
                numeric = j == 2 or (j >= 3 and bool(re.fullmatch(r'[+-]?(?:\d[\d,]*(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?', value.strip())))
                item.setTextAlignment((Qt.AlignmentFlag.AlignRight if numeric else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(row['path'] + '\nSheet: ' + row['sheet'] if j == 0 else value)
                self.table.setItem(i, j, item)
            open_button = QPushButton()
            open_button.setIcon(action_icon('Open'))
            open_button.setIconSize(QSize(14, 14))
            open_button.setCursor(Qt.CursorShape.PointingHandCursor)
            open_button.setAccessibleName('Open Excel file')
            open_button.setToolTip(f"Open {Path(row['path']).name} — {row['sheet']}, row {row['rownum']}")
            open_button.setStyleSheet('padding:0px 4px;')
            open_button.clicked.connect(lambda checked=False, record=row: self.open_path(False, record))
            self.table.setCellWidget(i, open_column, open_button)
        for column in (1, 2):
            self.table.resizeColumnToContents(column)
        self.table.resizeRowsToContents()
        self.rendering_results = False
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self.table.update_frozen()
        size = self.page_size.currentData()
        start = self.page * size + 1 if size > 0 else 1
        self.count.setText(f'Showing {start}-{start + len(self.rows) - 1} of {self.total} results' if self.total else (self.no_data_message() if not self.indexed else 'Type a keyword and press Enter.' if not valid_query(self.query.text()) else 'No matching rows. Try another keyword or reset filters.'))
        self.result_count.setText(self.count.text())
        if self.total:
            self.result_count.setText(self.count.text() + ' · Keywords: ' + ', '.join(value for value, color in self.active_keywords))
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
            self.detail_file_paths = {}
            self.detail_fields = []
            self.record_meta.clear()
            self.field_count.setText('0 fields')
            self.open_file_button.hide()
            self.detail.setHtml('<h3>No results to display</h3><p>Try a different search or select All files and All worksheets.</p>')

    def table_column_resized(self, column, old, width):
        if self.rendering_results:
            return
        if column == 0 and width > 260:
            self.table.setColumnWidth(0, 260)
            return
        if column < 3:
            key = ('file', 'sheet', 'row')[column]
        elif column == 3 + len(self.result_columns):
            key = 'section'
        elif column - 3 < len(self.result_columns):
            key = 'cell:' + self.result_columns[column - 3] + ':' + self.table.horizontalHeaderItem(column).text()
        else:
            return
        self.column_widths[key] = width
        self.column_resize_timer.start()

    def finish_column_resize(self):
        self.table.resizeRowsToContents()
        self.table.update_frozen()
        self.preferences['column_widths'] = self.column_widths
        self.save_preferences()

    def selected(self):
        selected = self.table.selectionModel().selectedRows()
        index = selected[0].row() if selected else -1
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def select_record(self, index):
        if index.isValid():
            self.table.selectRow(index.row())
            self.show_detail()
            self.detail_drawer.show()
            self.results_split.setSizes([max(280, self.results_split.width() - 330), 330])

    def row_fields(self, row):
        cells = json.loads(row['cells'])
        headers = json.loads(row.get('headers') or '{}')
        columns = sorted(set(cells) | set(headers), key=column_index_from_string)
        return [(column, headers.get(column) or f'Column {column}', cells.get(column, '')) for column in columns]

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
        spans = match_spans(text, self.query.text())
        start = max(0, spans[0][0] - 55) if spans else 0
        end = min(len(text), max(start + 240, spans[0][1] if spans else 0))
        return ('…' if start else '') + text[start:end].replace('\n', ' ') + ('…' if end < len(text) else '')

    def highlight(self, value, source=None, offset=0):
        from .chip_input import quoted
        ranges = []
        for term, color in getattr(self, 'active_keywords', self.query.keyword_colors()):
            ranges.extend((begin, end, color) for begin, end in match_spans(source if source is not None else value, quoted(term)))
        colors = {}
        for begin, end, color in ranges:
            for index in range(begin, end):
                colors.setdefault(index, color)
        parts, start = [], 0
        while start < len(value):
            color = colors.get(start + offset)
            end = start + 1
            while end < len(value) and colors.get(end + offset) == color:
                end += 1
            text = html.escape(value[start:end])
            parts.append(f'<span style="background:{color};color:#111">{text}</span>' if color else text)
            start = end
        return ''.join(parts)

    def show_detail(self):
        row = self.selected()
        if row:
            filename = Path(row['path']).name
            self.file_title.setText(self.file_title.fontMetrics().elidedText(filename, Qt.TextElideMode.ElideMiddle, max(200, self.detail.width() - 20)))
            self.file_title.setToolTip(filename)
            fields = self.row_fields(row)
            identity = (row['path'], row['sheet'], row['rownum'])
            if identity != getattr(self, 'detail_identity', None):
                self.expanded_notes = set()
                self.field_filter.blockSignals(True)
                self.field_filter.clear()
                self.field_filter.blockSignals(False)
            self.detail_identity = identity
            self.detail_fields = fields
            self.detail_section = row.get('row_section') or ''
            metadata = row['sheet'] + f" • Excel row {row['rownum']}"
            metadata += ' • Hidden worksheet' if row['hidden'] else ''
            metadata += ' • STALE' if row['stale'] else ''
            short_path = self.record_meta.fontMetrics().elidedText(row['path'], Qt.TextElideMode.ElideRight, max(220, self.detail_drawer.width() - 24))
            self.record_meta.setText(metadata + '\n' + short_path)
            self.record_meta.setToolTip(row['path'])
            from .desktop_features import referenced_file
            self.detail_file_paths = {column: path for column, label, value in fields
                                      if (path := referenced_file(value, row['path'])) is not None}
            old_menu = self.open_file_button.menu()
            self.open_file_button.setMenu(None)
            if old_menu:
                old_menu.deleteLater()
            if len(self.detail_file_paths) > 1:
                menu = QMenu(self.open_file_button)
                for column, path in self.detail_file_paths.items():
                    action = menu.addAction(f'{column}: {path.name}')
                    action.triggered.connect(lambda checked=False, key=column: self.open_referenced_file(QUrl('record-file:' + key)))
                self.open_file_button.setMenu(menu)
            self.open_file_button.setVisible(bool(self.detail_file_paths))
            self.open_file_button.setToolTip('\n'.join(str(path) for path in self.detail_file_paths.values()))
            self.render_detail_fields()
            self.detail.verticalScrollBar().setValue(0)

    def render_detail_fields(self, *_):
        fields = getattr(self, 'detail_fields', [])
        query = self.field_filter.text().strip().casefold()
        shown_fields = [(column, label, value) for column, label, value in fields
                        if not query or query in (column + ' ' + label + ' ' + value).casefold()]
        shown_fields.sort(key=lambda field: 'note' not in field[1].casefold())
        self.field_count.setText(f'{len(shown_fields)} of {len(fields)} fields' if query else f'{len(fields)} fields')
        muted = '#afc4d2' if self.dark_mode else '#52647b'
        content = ''
        if getattr(self, 'detail_section', ''):
            content += '<p style="margin:0 0 6px"><b>Section:</b> ' + self.highlight(self.detail_section) + '</p>'
        content += '<table width="100%" cellspacing="0" cellpadding="5">'
        for i, (column, label, value) in enumerate(shown_fields):
            background = ('#223747' if i % 2 == 0 else '#1b2d3d') if self.dark_mode else ('#f2f6f8' if i % 2 == 0 else '#ffffff')
            descriptive_header = len(label) > 60 or '\n' in label or (label.strip().casefold().startswith('note:') and len(label) > 25)
            field_label = f'Column {column}' if descriptive_header else label
            if descriptive_header:
                key = column + ':header'
                expanded_header = key in getattr(self, 'expanded_notes', set())
                header_text = label if expanded_header or len(label) <= 140 else label[:140] + '…'
                heading = 'Note' if label.strip().casefold().startswith('note') else f'Header — Column {column}'
                content += f'<tr bgcolor="{background}"><td colspan="2" valign="top"><b>{heading}</b><br>{self.highlight(header_text).replace(chr(10), "<br>")}'
                if len(label) > 140:
                    content += f'<br><a href="expand-note:{key}" style="color:#126175;text-decoration:none;">{"Show less" if expanded_header else "Show more"}</a>'
                content += '</td></tr>'
            display = self.display_cell(value)
            note = 'note' in label.casefold() and not descriptive_header
            expanded = column in getattr(self, 'expanded_notes', set())
            shortened = note and len(display) > 100 and not expanded
            text = display[:100] + '…' if shortened else display
            shown = self.highlight(text).replace(chr(10), '<br>') if value else '-'
            if note and len(display) > 100:
                shown += f'<br><a href="expand-note:{column}" style="color:#126175;text-decoration:none;">{"Show less" if expanded else "Show more"}</a>'
            content += f'<tr bgcolor="{background}"><td width="30%" valign="top" style="color:{muted};font-size:11px;">{html.escape(field_label)}</td><td width="70%" valign="top">{shown}</td></tr>'
        content += '</table>'
        if query and not shown_fields:
            content += f'<p style="color:{muted}">No matching fields.</p>'
        self.detail.setHtml(content)

    def detail_link_clicked(self, url):
        if url.scheme() != 'expand-note':
            return
        column = url.path()
        if column in self.expanded_notes:
            self.expanded_notes.remove(column)
        else:
            self.expanded_notes.add(column)
        self.render_detail_fields()

    def open_referenced_file(self, url=None):
        if url is None:
            paths = getattr(self, 'detail_file_paths', {})
            if len(paths) != 1:
                return
            url = QUrl('record-file:' + next(iter(paths)))
        if url.scheme() != 'record-file':
            return
        path = getattr(self, 'detail_file_paths', {}).get(url.path())
        if path is None:
            return
        if not path.is_file():
            self.notify('File is unavailable: ' + str(path), True)
            QMessageBox.warning(self, 'Unavailable', 'The referenced file is unavailable:\n' + str(path))
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self.notify('Opening file…' if opened else 'Could not open the file. Check the default application.', not opened)

    def copy_row(self):
        row = self.selected()
        if row:
            QApplication.clipboard().setText(row['path'] + '\n' + row['sheet'] + f" • Excel row {row['rownum']}\n" + '\n'.join(f'{label} ({column}): {self.display_cell(value)}' for column, label, value in self.row_fields(row)))
            self.notify('Complete row copied to clipboard')
            self.copy_confirmation('Copy row')
        else:
            self.notify('Select a record first.', True)

    def open_path(self, folder, row=None):
        row = row if row is not None else self.selected()
        if row:
            path = Path(row['path']).parent if folder else Path(row['path'])
            if not path.exists():
                self.notify('Original path is unavailable. Sync to refresh the index.', True)
                QMessageBox.warning(self, 'Unavailable', 'The original path is unavailable. Sync to refresh the index.')
            else:
                from .desktop_features import open_excel_record
                opened = (not folder and open_excel_record(path, row['sheet'], row['rownum'])) or QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
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
