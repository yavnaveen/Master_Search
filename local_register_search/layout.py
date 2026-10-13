"""Desktop layout for the local register browser."""
import html
from PySide6.QtCore import Qt, QVariantAnimation, QEasingCurve, QRectF, QEvent, QPointF, QSize
from PySide6.QtGui import QTextDocument, QAbstractTextDocumentLayout, QPalette, QPainter, QColor, QPen, QPolygonF, QPixmap, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QLineEdit, QComboBox, QSplitter, QTableWidget, QTextBrowser,
    QProgressBar, QFrame, QMessageBox, QDialog, QStyledItemDelegate, QStyle, QHeaderView,
    QStyleOptionViewItem, QTableView, QGridLayout, QCheckBox)


DARK_STYLE = '''
    QWidget { color: #d8e5ed; }
    QMainWindow, QWidget#root, QDialog { background: #111e2a; }
    QFrame#card { background: #1b2d3d; border-color: #385064; }
    QPushButton { background: #22394a; color: #d8e5ed; border-color: #5d9daf; }
    QPushButton:hover { background: #31546a; }
    QPushButton:disabled { background: #1a2a38; color: #8295a5; border-color: #344b5d; }
    QPushButton#primary:disabled, QPushButton#danger:disabled { background: #1a2a38; color: #8295a5; border-color: #344b5d; }
    QLineEdit, QComboBox, QComboBox[filterControl="true"] { background: #22394a; color: #e5eff5; border-color: #52768a; }
    QComboBox QAbstractItemView, QComboBox[filterControl="true"] QAbstractItemView { background: #22394a; color: #e5eff5; }
    QTableView { background: #1b2d3d; alternate-background-color: #223747; selection-background-color: #326778; selection-color: #ffffff; }
    QHeaderView::section { background: #294152; color: #d8e5ed; border-color: #385064; }
    QTextBrowser { background: #1b2d3d; color: #d8e5ed; }
    QSplitter::handle { background: #111e2a; }
    QCheckBox { spacing: 10px; padding: 10px; }
'''


def action_icon(name, color='#58a6b7'):
    """A consistent 18px outline icon family, drawn locally without assets."""
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name == 'Search':
        painter.drawEllipse(QRectF(3, 3, 10, 10))
        painter.drawLine(12, 12, 17, 17)
    elif 'folder' in name.lower():
        painter.drawPolyline(QPolygonF([QPointF(2, 16), QPointF(2, 5), QPointF(8, 5), QPointF(10, 7), QPointF(18, 7), QPointF(18, 16), QPointF(2, 16)]))
        if name == 'Add folder':
            painter.drawLine(10, 9, 10, 14)
            painter.drawLine(7, 12, 13, 12)
    elif 'Sync' in name or 'Rebuild' in name:
        painter.drawArc(QRectF(3, 3, 14, 14), 35 * 16, 280 * 16)
        painter.drawPolyline(QPolygonF([QPointF(14, 2), QPointF(17, 5), QPointF(13, 6)]))
    elif name == 'Settings':
        for y, x in [(5, 7), (10, 13), (15, 7)]:
            painter.drawLine(3, y, 17, y)
            painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
    elif name.startswith('Copy'):
        painter.drawRoundedRect(QRectF(7, 6, 10, 11), 1, 1)
        painter.drawPolyline(QPolygonF([QPointF(5, 13), QPointF(3, 13), QPointF(3, 3), QPointF(12, 3)]))
    elif name == 'Export results':
        painter.drawLine(10, 2, 10, 12)
        painter.drawPolyline(QPolygonF([QPointF(6, 8), QPointF(10, 12), QPointF(14, 8)]))
        painter.drawPolyline(QPolygonF([QPointF(3, 12), QPointF(3, 17), QPointF(17, 17), QPointF(17, 12)]))
    else:
        painter.drawRoundedRect(QRectF(4, 2, 12, 16), 1, 1)
        painter.drawLine(7, 7, 13, 7)
        painter.drawLine(7, 11, 13, 11)
    painter.end()
    return QIcon(pixmap)


class FilterCombo(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#126175'), 1.8))
        x, y = self.width() - 18, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))
        painter.end()


class FrozenView(QTableView):
    def paintEvent(self, event):
        super().paintEvent(event)
        self.parent().paint_lifts(self)


class LiftTable(QTableWidget):
    """Animate a painted row above the grid while retaining its hit target."""
    def __init__(self, *args):
        super().__init__(*args)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.viewport().installEventFilter(self)
        self.hover_row = -1
        self.lifts = {}
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(180)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.animate_lift)
        self.starts = {}
        self.verticalScrollBar().valueChanged.connect(lambda: self.set_hover(-1))
        self.horizontalScrollBar().valueChanged.connect(lambda: self.set_hover(-1))
        self.model().modelAboutToBeReset.connect(self.reset_lift)
        self.model().rowsAboutToBeRemoved.connect(self.reset_lift)
        self.frozen = FrozenView(self)
        self.frozen.setModel(self.model())
        self.frozen.setSelectionModel(self.selectionModel())
        self.frozen.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.frozen.verticalHeader().hide()
        self.frozen.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frozen.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.frozen.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.frozen.setMouseTracking(True)
        self.frozen.viewport().setMouseTracking(True)
        self.frozen.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.frozen.viewport().installEventFilter(self)
        self.frozen.setAlternatingRowColors(True)
        self.verticalScrollBar().valueChanged.connect(self.frozen.verticalScrollBar().setValue)
        self.frozen.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self.horizontalHeader().sectionResized.connect(self.update_frozen)
        self.frozen.horizontalHeader().sectionResized.connect(self.resize_frozen_column)
        self.verticalHeader().sectionResized.connect(lambda row, old, size: self.frozen.setRowHeight(row, size))

    def resize_frozen_column(self, column, old, size):
        if column < 3 and self.columnWidth(column) != size:
            self.setColumnWidth(column, size)

    def update_frozen(self, *args):
        for column in range(self.columnCount()):
            self.frozen.setColumnHidden(column, column >= 3)
            if column < 3:
                self.frozen.setColumnWidth(column, self.columnWidth(column))
        self.frozen.verticalHeader().setDefaultSectionSize(self.verticalHeader().defaultSectionSize())
        width = sum(self.columnWidth(column) for column in range(min(3, self.columnCount())))
        self.frozen.setGeometry(self.frameWidth(), self.frameWidth(), width,
                                self.viewport().height() + self.horizontalHeader().height())
        self.frozen.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'frozen'):
            self.update_frozen()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
            view = self.frozen if hasattr(self, 'frozen') and watched is self.frozen.viewport() else self
            self.set_hover(view.indexAt(event.position().toPoint()).row())
        elif event.type() == QEvent.Type.Leave:
            self.set_hover(-1)
        return super().eventFilter(watched, event)

    def reset_lift(self, *args):
        self.animation.stop()
        self.hover_row = -1
        self.lifts.clear()
        self.viewport().update()
        if hasattr(self, 'frozen'):
            self.frozen.viewport().update()

    def set_hover(self, row):
        if row == self.hover_row:
            return
        self.animation.stop()
        self.hover_row = row
        self.starts = dict(self.lifts)
        if row >= 0:
            self.starts.setdefault(row, 0.0)
        self.animation.start()

    def animate_lift(self, fraction):
        self.lifts = {row: start + ((1.0 if row == self.hover_row else 0.0) - start) * fraction
                      for row, start in self.starts.items()}
        self.lifts = {row: amount for row, amount in self.lifts.items() if amount > 0.001}
        self.viewport().update()
        self.frozen.viewport().update()

    def mouseMoveEvent(self, event):
        self.set_hover(self.indexAt(event.position().toPoint()).row())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.set_hover(-1)
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        self.paint_lifts(self)

    def paint_lifts(self, view):
        painter = QPainter(view.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for row, amount in self.lifts.items():
            if row >= self.rowCount():
                continue
            top = view.rowViewportPosition(row)
            height = view.rowHeight(row)
            if top + height < 0 or top > view.viewport().height():
                continue
            offset = round(5 * amount)
            rect = QRectF(1, top - offset, view.viewport().width() - 2, height - 1)
            painter.setPen(Qt.PenStyle.NoPen)
            for spread in range(6, 0, -1):
                painter.setBrush(QColor(20, 40, 57, round((9 - spread) * amount)))
                painter.drawRoundedRect(rect.adjusted(-spread, spread / 2, spread, spread), 5, 5)
            for column in range(self.columnCount()):
                if view.isColumnHidden(column):
                    continue
                index = self.model().index(row, column)
                cell = view.visualRect(index).translated(0, -offset)
                if not cell.intersects(view.viewport().rect()):
                    continue
                option = QStyleOptionViewItem()
                view.initViewItemOption(option)
                option.rect = cell
                if self.selectionModel().isSelected(index):
                    option.state |= QStyle.StateFlag.State_Selected
                option.state &= ~QStyle.StateFlag.State_MouseOver
                painter.save()
                painter.setClipRect(cell)
                selected = self.selectionModel().isSelected(index)
                dark = getattr(self, 'dark_mode', False)
                painter.fillRect(cell, QColor(('#326778' if selected else '#223747' if row % 2 else '#1b2d3d') if dark else ('#a6dce4' if selected else '#fbfcfd' if row % 2 else '#ffffff')))
                self.itemDelegateForIndex(index).paint(painter, option, index)
                painter.fillRect(cell, QColor(22, 112, 132, round(16 * amount)))
                painter.restore()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(22, 112, 132, round(115 * amount)))
            painter.drawRoundedRect(rect, 4, 4)
        painter.end()


class MatchDelegate(QStyledItemDelegate):
    def __init__(self, window):
        super().__init__(window.table)
        self.window = window

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        value = option.text
        option.text = ''
        style = option.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        amount = self.window.table.lifts.get(index.row(), 0.0)
        if amount:
            painter.fillRect(option.rect, QColor(22, 112, 132, round(40 * amount)))
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(0)
        content = self.window.highlight(value)
        if index.column() == 0 and '\n' in value:
            filename, sheet = value.split('\n', 1)
            filename = option.fontMetrics.elidedText(filename, Qt.TextElideMode.ElideMiddle, max(40, option.rect.width() - 20))
            content = f'<b>{html.escape(filename)}</b><br><span style="color:#52647b">{html.escape(sheet)}</span>'
        doc.setHtml(content.replace('\n', '<br>'))
        rect = option.rect.adjusted(10, 10, -10, -8)
        doc.setTextWidth(rect.width())
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, QColor('#d8e5ed' if self.window.dark_mode else '#0c1c30'))
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.topLeft())
        doc.documentLayout().draw(painter, context)
        painter.restore()


def build(window):
    window.resize(1500, 940)
    window.setMinimumSize(1100, 720)
    window.setStyleSheet('''
        QWidget { font-family: "Segoe UI"; font-size: 14px; color: #142338; }
        QMainWindow, QWidget#root { background: #f8fafb; }
        QWidget#sidebar { background: #142839; }
        QWidget#sidebar QLabel { color: #c6d2de; background: transparent; }
        QWidget#sidebar QPushButton { color: #eff6fa; background: transparent; border: none; text-align: left; padding: 13px; }
        QWidget#sidebar QPushButton:hover, QWidget#sidebar QPushButton#active { background: #205e70; }
        QWidget#sidebar QPushButton#danger { color: #ffb4ab; background: #462d35; border: 1px solid #a94b4b; padding: 8px; }
        QLabel { background: transparent; }
        QPushButton { background: white; border: 1px solid #428395; border-radius: 6px; padding: 10px 16px; color: #126175; }
        QPushButton:hover { background: #e6f3f5; }
        QPushButton:disabled { color: #98a7b4; border-color: #dce3e9; background: #f5f7f9; }
        QPushButton#primary { color: white; background: #167084; border-color: #167084; font-weight: 600; }
        QPushButton#primary:hover { background: #125e70; }
        QPushButton#danger { color: #b42318; border-color: #edc7c2; background: #fff7f6; }
        QPushButton#primary:disabled, QPushButton#danger:disabled { color: #98a7b4; border-color: #dce3e9; background: #f5f7f9; }
        QPushButton:pressed, QPushButton#primary:pressed, QPushButton#danger:pressed,
        QWidget#sidebar QPushButton:pressed { background: #0c4959; color: white; border-color: #0c4959; }
        QPushButton[clickedFeedback="true"], QPushButton#primary[clickedFeedback="true"],
        QPushButton#danger[clickedFeedback="true"], QWidget#sidebar QPushButton[clickedFeedback="true"] {
            background: #287f91; color: white; border: 1px solid #65c3ce;
        }
        QPushButton:focus { border: 2px solid #167084; }
        QLineEdit, QComboBox { background: white; border: 1px solid #c7d2dd; border-radius: 6px; padding: 10px; }
        QLineEdit:focus, QComboBox:focus { border-color: #167084; }
        QComboBox QAbstractItemView { background: white; selection-background-color: #def1f3; selection-color: #142338; }
        QComboBox[filterControl="true"] { background: #e6f3f5; color: #126175; border: 1px solid #7fb1bd; border-radius: 10px; padding: 10px 36px 10px 14px; font-weight: 600; }
        QComboBox[filterControl="true"]:hover { background: #d7ecef; border-color: #167084; }
        QComboBox[filterControl="true"]:focus { background: #def1f3; border: 2px solid #167084; padding: 9px 35px 9px 13px; }
        QComboBox[filterControl="true"]::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 30px; border: none; background: transparent; }
        QComboBox[filterControl="true"]::down-arrow { image: none; }
        QComboBox[filterControl="true"] QAbstractItemView { background: #f8fafb; color: #126175; border: 1px solid #7fb1bd; padding: 4px; selection-background-color: #167084; selection-color: white; }
        QFrame#card { background: white; border: 1px solid #dbe3eb; border-radius: 7px; }
        QTableView { background: white; alternate-background-color: #fbfcfd; border: none; selection-background-color: #a6dce4; selection-color: #0c1c30; }
        QTableWidget::item { padding: 8px; border-bottom: 1px solid #e8edf2; }
        QHeaderView::section { background: #f2f6f8; border: none; border-bottom: 1px solid #dbe3eb; padding: 12px; color: #35465c; }
        QTextBrowser { background: white; border: none; padding: 8px; }
        QListWidget { border: none; background: transparent; color: #edf4f8; }
        QListWidget::item { padding: 12px 8px; }
        QListWidget::item:selected { background: #205e70; border-radius: 5px; }
        QProgressBar { border: none; background: #e1e9ed; max-height: 5px; }
        QProgressBar::chunk { background: #167084; }
        QSplitter::handle { background: #f8fafb; width: 10px; }
        QToolTip { background: #142839; color: white; padding: 6px; border: none; }
    ''')
    window.light_style = window.styleSheet()
    root = QWidget()
    root.setObjectName('root')
    window.setCentralWidget(root)
    outer = QHBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    sidebar = QWidget()
    sidebar.setObjectName('sidebar')
    sidebar.setFixedWidth(260)
    outer.addWidget(sidebar)
    side = QVBoxLayout(sidebar)
    side.setContentsMargins(20, 26, 20, 20)
    logo = QLabel('LR')
    logo.setStyleSheet('font-size:48px;font-weight:700;color:white;')
    side.addWidget(logo)
    brand = QLabel('Local Register\nSearch')
    brand.setStyleSheet('font-size:19px;font-weight:600;color:white;margin-bottom:20px;')
    side.addWidget(brand)
    window.controls = []

    def button(name, action, layout, primary=False):
        item = QPushButton(name)
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setIconSize(QSize(18, 18))
        item.clicked.connect(lambda: window.button_clicked(item))
        if name in {'Search', 'Folders', 'Add folder', 'Sync files', 'Sync status', 'Settings', 'Rebuild index', 'Export results', 'Sync now'}:
            item.setIcon(action_icon(name, '#e0edf4' if layout is side or primary else '#58a6b7'))
        if primary:
            item.setObjectName('primary')
        item.clicked.connect(action)
        layout.addWidget(item)
        return item

    nav = button('Search', lambda: (window.query.setFocus(), window.notify('Type a query and press Enter to search.')), side)
    nav.setObjectName('active')
    button('Folders', lambda: (window.folder_list.setFocus(), window.notify('Select a sidebar folder, or use Add folder.')), side)
    button('Sync status', lambda: QMessageBox.information(window, 'Sync status', window.index_summary.text() + '\n\n' + window.status.text()), side)

    def settings():
        dialog = QDialog(window)
        dialog.setWindowTitle('Index settings')
        dialog.resize(520, 460)
        content = QVBoxLayout(dialog)
        label = QLabel('Manage the saved search index.\nOriginal Excel files remain untouched.')
        label.setWordWrap(True)
        content.addWidget(label)
        dark = QCheckBox('Dark mode')
        dark.setChecked(window.dark_mode)
        dark.toggled.connect(window.set_dark_mode)
        content.addWidget(dark)
        watch = QCheckBox('Auto-sync folders (check every 10 seconds)')
        watch.setChecked(window.auto_sync)
        watch.toggled.connect(window.set_auto_sync)
        content.addWidget(watch)
        button('Clear recent searches', lambda: (window.recent_searches.clear(), window.save_preferences(), window.refresh_recent_searches(), window.notify('Recent searches cleared.')), content)
        def run(action):
            dialog.accept()
            action()
        rebuild = button('Rebuild index', lambda: run(lambda: window.start_sync(True)), content)
        content.addSpacing(18)
        danger = QLabel('Danger zone')
        danger.setStyleSheet('color:#b42318; font-size:18px; font-weight:600;')
        content.addWidget(danger)
        content.addWidget(QLabel('Clearing the index requires confirmation. Auto-sync will stop.'))
        folder_delete = button('Clear selected folder index', lambda: run(window.delete_folder_data), content)
        folder_delete.setObjectName('danger')
        folder_delete.setEnabled(not window.jobs and window.folder_list.currentRow() >= 0)
        delete = button('Clear entire index', lambda: run(window.delete_index), content)
        delete.setObjectName('danger')
        rebuild.setEnabled(not window.scanning)
        delete.setEnabled(not window.jobs)
        dialog.exec()

    button('Settings', settings, side)
    side.addSpacing(18)
    side.addWidget(QLabel('REGISTER FOLDERS'))
    window.folder_list = QListWidget()
    window.folder_list.addItems(window.folders)
    window.format_folders()
    side.addWidget(window.folder_list, 1)
    window.controls.append(button('Remove selected folder', window.remove_folder, side))
    side.addSpacing(18)
    offline = QLabel('●  Offline mode')
    offline.setStyleSheet('color:#74db99;font-size:15px;')
    side.addWidget(offline)
    side.addWidget(QLabel('Local files only'))

    main = QWidget()
    outer.addWidget(main, 1)
    layout = QVBoxLayout(main)
    layout.setContentsMargins(26, 28, 26, 22)
    layout.setSpacing(16)
    header = QHBoxLayout()
    heading = QVBoxLayout()
    title = QLabel('Find any record.')
    title.setStyleSheet('font-size:38px;font-weight:700;color:#0c1c30;')
    heading.addWidget(title)
    subtitle = QLabel('Search across your local Excel registers')
    subtitle.setStyleSheet('font-size:20px;color:#68768a;')
    heading.addWidget(subtitle)
    header.addLayout(heading, 1)
    window.index_summary = QLabel()
    window.index_summary.setAlignment(Qt.AlignmentFlag.AlignRight)
    window.index_summary.setStyleSheet('color:#52647b;font-size:12px;background:#e6f3f5;padding:10px;border-radius:12px;')
    window.index_summary.setWordWrap(True)
    window.index_summary.setMaximumWidth(350)
    window.index_summary.setMaximumHeight(56)
    header.addWidget(window.index_summary)
    layout.addLayout(header)
    searchbar = QHBoxLayout()
    window.query = QLineEdit()
    window.query.setPlaceholderText('Search drawing numbers, names or phrases…')
    window.query.setClearButtonEnabled(True)
    window.query.setMinimumHeight(54)
    window.query.setStyleSheet('font-size:20px;')
    searchbar.addWidget(window.query, 1)
    search_button = button('Search', window.submit_search, searchbar, True)
    search_button.setMinimumHeight(54)
    search_button.setMinimumWidth(130)
    window.query.returnPressed.connect(search_button.click)
    for name, action in [('Add folder', window.add_folder), ('Sync files', lambda: window.start_sync(False))]:
        control = button(name, action, searchbar)
        control.setMinimumHeight(54)
        window.controls.append(control)
    layout.addLayout(searchbar)
    filters = QHBoxLayout()
    filters.setSpacing(16)
    filters.addStretch(1)
    window.mode = FilterCombo()
    window.mode.addItems(['All Words', 'Exact Phrase', 'Contains'])
    window.file_filter, window.sheet_filter = FilterCombo(), FilterCombo()
    for label, combo in [('Match:', window.mode), ('File:', window.file_filter), ('Sheet:', window.sheet_filter)]:
        filters.addWidget(QLabel(label))
        combo.setProperty('filterControl', True)
        combo.setMinimumWidth(140)
        combo.setMaximumWidth(320)
        combo.setMinimumHeight(44)
        filters.addWidget(combo, 3)
    filters.addStretch(1)
    window.recent = QComboBox()
    window.recent.setMaximumWidth(240)
    window.recent.currentIndexChanged.connect(window.use_recent_search)
    filters.addWidget(window.recent)
    layout.addLayout(filters)
    window.progress = QProgressBar()
    window.progress.setTextVisible(False)
    layout.addWidget(window.progress)
    statusbar = QHBoxLayout()
    window.status = QLabel('Ready. Search indexed records or add folders and sync files.')
    window.status.setWordWrap(True)
    window.status.setStyleSheet('color:#68768a;font-size:12px;')
    statusbar.addWidget(window.status, 1)
    window.progress_label = QLabel('')
    statusbar.addWidget(window.progress_label)
    window.cancel_button = button('Cancel sync', window.cancel_sync, statusbar)
    window.cancel_button.setEnabled(False)
    layout.addLayout(statusbar)
    window.feedback = QLabel()
    window.feedback.setWordWrap(True)
    window.feedback.hide()
    layout.addWidget(window.feedback)
    split = QSplitter(Qt.Orientation.Horizontal)
    layout.addWidget(split, 1)
    results = QFrame()
    results.setObjectName('card')
    result_layout = QVBoxLayout(results)
    result_layout.setContentsMargins(0, 0, 0, 0)
    result_header = QHBoxLayout()
    result_header.setContentsMargins(18, 14, 18, 14)
    result_title = QLabel('Search results')
    result_title.setStyleSheet('font-size:21px;font-weight:600;')
    result_header.addWidget(result_title)
    result_header.addStretch(1)
    window.export_button = button('Export results', window.export_results, result_header)
    window.export_button.setToolTip('Export all matching rows to Excel or CSV')
    window.export_button.setEnabled(False)
    result_layout.addLayout(result_header)
    window.table = LiftTable(0, 3)
    window.table.setHorizontalHeaderLabels(['File', 'Sheet', 'Row'])
    window.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    window.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    window.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    window.table.setAlternatingRowColors(True)
    window.table.setShowGrid(True)
    window.table.setWordWrap(True)
    window.table.verticalHeader().hide()
    window.table.verticalHeader().setDefaultSectionSize(60)
    window.table.setColumnWidth(0, 190)
    window.table.setColumnWidth(1, 135)
    window.table.setColumnWidth(2, 55)
    for table_header in (window.table.horizontalHeader(), window.table.frozen.horizontalHeader()):
        table_header.setSectionsClickable(True)
        table_header.setSortIndicatorShown(True)
        table_header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    window.table.horizontalHeader().setStretchLastSection(False)
    window.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    window.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    window.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    window.match_delegate = MatchDelegate(window)
    window.table.setItemDelegate(window.match_delegate)
    window.table.frozen.setItemDelegate(window.match_delegate)
    window.table.update_frozen()
    result_layout.addWidget(window.table, 1)
    window.empty_card = QWidget()
    empty = QVBoxLayout(window.empty_card)
    empty.setContentsMargins(32, 32, 32, 32)
    empty.addStretch(1)
    window.empty_title = QLabel('Get started')
    window.empty_title.setStyleSheet('font-size:30px;font-weight:700;')
    empty.addWidget(window.empty_title)
    window.empty_message = QLabel()
    window.empty_message.setWordWrap(True)
    window.empty_message.setStyleSheet('font-size:18px;')
    empty.addWidget(window.empty_message)
    empty_actions = QHBoxLayout()
    window.empty_add = button('Add folder', window.add_folder, empty_actions)
    window.empty_sync = button('Sync now', lambda: window.start_sync(False), empty_actions, True)
    window.empty_reset = button('Reset search and filters', window.reset_filters, empty_actions)
    window.controls.extend([window.empty_add, window.empty_sync])
    empty_actions.addStretch(1)
    empty.addLayout(empty_actions)
    empty.addStretch(1)
    result_layout.addWidget(window.empty_card, 1)
    split.addWidget(results)
    details = QFrame()
    details.setObjectName('card')
    detail_layout = QVBoxLayout(details)
    detail_layout.setContentsMargins(14, 16, 14, 16)
    detail_layout.setSpacing(6)
    detail_title = QLabel('Record details')
    detail_title.setStyleSheet('font-size:21px;font-weight:600;')
    detail_layout.addWidget(detail_title)
    window.file_title = QLabel()
    window.file_title.setMinimumWidth(0)
    window.file_title.setStyleSheet('font-size:16px;font-weight:600;color:#163b75;')
    detail_layout.addWidget(window.file_title)
    window.detail = QTextBrowser()
    window.detail.setOpenExternalLinks(False)
    window.detail.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    window.detail.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
    detail_layout.addWidget(window.detail, 1)
    record_footer = QWidget()
    record_grid = QGridLayout(record_footer)
    record_grid.setContentsMargins(0, 4, 0, 0)
    record_grid.setSpacing(6)
    detail_layout.addWidget(record_footer)
    window.record_buttons = []
    for i, (name, action, primary) in enumerate([('Open Excel file', lambda: window.open_path(False), True),
                                 ('Open folder', lambda: window.open_path(True), False),
                                 ('Copy path', window.copy_path, False),
                                 ('Copy row', window.copy_row, False)]):
        item = QPushButton(name)
        item.setProperty('actionLabel', name)
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setIconSize(QSize(18, 18))
        item.setIcon(action_icon(name, '#e0edf4' if primary else '#58a6b7'))
        item.clicked.connect(lambda checked=False, b=item: window.button_clicked(b))
        if primary:
            item.setObjectName('primary')
        item.clicked.connect(action)
        record_grid.addWidget(item, i // 2, i % 2)
        window.record_buttons.append(item)
    split.addWidget(details)
    split.setSizes([850, 340])
    split.setStretchFactor(0, 2)
    split.setStretchFactor(1, 1)
    footer = QHBoxLayout()
    window.count = QLabel()
    window.count.setWordWrap(True)
    window.count.setStyleSheet('color:#68768a;font-size:12px;')
    footer.addWidget(window.count, 1)
    window.page_buttons = [button('‹ Previous', lambda: window.change_page(-1), footer),
                           button('Next ›', lambda: window.change_page(1), footer)]
    window.page_size = QComboBox()
    for label, size in [('100 per page', 100), ('50 per page', 50), ('Show all rows', -1)]:
        window.page_size.addItem(label, size)
    window.page_size.setCurrentIndex(0)
    footer.addWidget(window.page_size)
    layout.addLayout(footer)
    window.errors = QTextBrowser()
    window.errors.setMaximumHeight(90)
    window.errors.hide()
    window.errors.textChanged.connect(lambda: window.errors.setVisible(bool(window.errors.toPlainText())))
    layout.addWidget(window.errors)
