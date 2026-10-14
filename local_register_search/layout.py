"""Desktop layout for the local register browser."""
import html
from PySide6.QtCore import Qt, QVariantAnimation, QEasingCurve, QRectF, QEvent, QPointF, QSize, QRect, QPoint, QTimer
from PySide6.QtGui import QTextDocument, QTextOption, QTextCursor, QTextBlockFormat, QFontMetrics, QAbstractTextDocumentLayout, QPalette, QPainter, QColor, QPen, QPolygonF, QPixmap, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QLineEdit, QComboBox, QSplitter, QTableWidget, QTextBrowser,
    QProgressBar, QFrame, QMessageBox, QDialog, QStyledItemDelegate, QStyle, QHeaderView,
    QStyleOptionViewItem, QTableView, QGridLayout, QCheckBox, QLayout, QStackedWidget, QScrollArea, QSizePolicy)
from .filter_dropdown import ContentCombo


class WrapLayout(QLayout):
    """Wrap navigation and controls without clipping at smaller widths."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(10)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def arrange(self, rect, measure):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, height = area.x(), area.y(), 0
        for item in self.items:
            size = item.sizeHint()
            if x > area.x() and x + size.width() > area.right() + 1:
                x, y, height = area.x(), y + height + self.spacing(), 0
            if item.widget() and item.widget().property('alignRight'):
                x = max(x, area.right() - size.width() + 1)
            if not measure:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            height = max(height, size.height())
        return y + height - rect.y() + margins.bottom()


DARK_STYLE = '''
    QWidget { color: #d8e5ed; }
    QMainWindow, QWidget#root, QDialog, QStackedWidget,
    QScrollArea, QWidget#pageContent, QWidget#pageViewport { background: #111e2a; }
    QFrame#card { background: #1b2d3d; border-color: #385064; }
    QPushButton { background: #22394a; color: #d8e5ed; border-color: #5d9daf; }
    QPushButton:hover { background: #31546a; }
    QPushButton#primary { background: #167084; color: #ffffff; border-color: #167084; }
    QPushButton#primary:hover { background: #125e70; }
    QPushButton#danger { background: #462d35; color: #ffb4ab; border-color: #a94b4b; }
    QPushButton#danger:hover { background: #603842; }
    QPushButton:disabled { background: #1a2a38; color: #8295a5; border-color: #344b5d; }
    QPushButton#primary:disabled, QPushButton#danger:disabled { background: #1a2a38; color: #8295a5; border-color: #344b5d; }
    QLineEdit, QComboBox, QComboBox[filterControl="true"] { background: #22394a; color: #e5eff5; border-color: #52768a; }
    QComboBox QAbstractItemView, QComboBox[filterControl="true"] QAbstractItemView { background: #22394a; color: #e5eff5; }
    QTableView { background: #1b2d3d; alternate-background-color: #223747; selection-background-color: #326778; selection-color: #ffffff; }
    QHeaderView::section { background: #294152; color: #d8e5ed; border-color: #385064; }
    QTextBrowser { background: #1b2d3d; color: #d8e5ed; }
    QSplitter::handle { background: #111e2a; }
    QCheckBox { color: #d8e5ed; }
    QScrollBar { background: #1b2d3d; }
    QScrollBar::handle { background: #52768a; border-radius: 4px; min-width: 16px; min-height: 16px; }
    QListWidget#folderList {color:#d8e5ed;background:#1b2d3d;border-color:#385064;}
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
    elif name == 'Open':
        painter.drawPolyline(QPolygonF([QPointF(10, 3), QPointF(17, 3), QPointF(17, 10)]))
        painter.drawLine(8, 12, 17, 3)
        painter.drawPolyline(QPolygonF([QPointF(7, 3), QPointF(3, 3), QPointF(3, 17), QPointF(17, 17), QPointF(17, 13)]))
    else:
        painter.drawRoundedRect(QRectF(4, 2, 12, 16), 1, 1)
        painter.drawLine(7, 7, 13, 7)
        painter.drawLine(7, 11, 13, 11)
    painter.end()
    return QIcon(pixmap)


class FilterCombo(ContentCombo):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#126175'), 1.8))
        x, y = self.width() - 9, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))
        painter.end()


class FrozenView(QTableView):
    def paintEvent(self, event):
        super().paintEvent(event)


class LiftTable(QTableWidget):
    """Table with synchronized frozen metadata columns and gentle row hover."""
    def __init__(self, *args):
        super().__init__(*args)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.viewport().installEventFilter(self)
        self.hover_row = -1
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
        self.hover_row = -1
        self.viewport().update()
        if hasattr(self, 'frozen'):
            self.frozen.viewport().update()

    def set_hover(self, row):
        if row == self.hover_row:
            return
        self.hover_row = row
        self.viewport().update()
        self.frozen.viewport().update()

    def mouseMoveEvent(self, event):
        self.set_hover(self.indexAt(event.position().toPoint()).row())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.set_hover(-1)
        super().leaveEvent(event)



class MatchDelegate(QStyledItemDelegate):
    def __init__(self, window):
        super().__init__(window.table)
        self.window = window

    def visible_lines(self, value, font, width, column):
        metrics = QFontMetrics(font)
        return [metrics.elidedText(value.replace('\n', ' ').replace('\r', ' '),
                                  Qt.TextElideMode.ElideMiddle if column == 0 else Qt.TextElideMode.ElideRight,
                                  max(1, width))]

    def document(self, value, font, width, column, alignment, highlight=True):
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setDocumentMargin(0)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        doc.setDefaultTextOption(option)
        lines = self.visible_lines(value, font, max(1, width), column)
        align = 'right' if alignment & Qt.AlignmentFlag.AlignRight else 'left'
        fragments, cursor = [], 0
        for line in lines:
            shown = line[:-3] if line.endswith('...') else line
            position = value.find(shown, cursor) if column else -1
            fragments.append((self.window.highlight(shown, value, position) if position >= 0 else self.window.highlight(shown)) if highlight else html.escape(shown))
            if line.endswith('...'):
                fragments[-1] += '...'
            if position >= 0:
                cursor = position + len(shown)
        content = f'</p><p style="margin:0;text-align:{align}">'.join(fragments)
        doc.setHtml(f'<p style="margin:0;text-align:{align}">{content}</p>')
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        block = QTextBlockFormat()
        block.setLineHeight(100, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(block)
        doc.setTextWidth(max(1, width))
        return doc

    def sizeHint(self, option, index):
        self.initStyleOption(option, index)
        width = self.window.table.columnWidth(index.column())
        return QSize(width, max(18, option.fontMetrics.height() + 2))

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        value = option.text
        if index.data(Qt.ItemDataRole.UserRole) == 'empty-cell':
            value = '-'
        option.text = ''
        style = option.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if not self.window.dark_mode:
            color = '#E6F4F2' if selected else '#F0F8F7' if index.row() == self.window.table.hover_row else '#fbfcfd' if index.row() % 2 else '#ffffff'
            painter.fillRect(option.rect, QColor(color))
        rect = option.rect.adjusted(6, 1, -6, -1)
        doc = self.document(value, option.font, rect.width(), index.column(), option.displayAlignment, index.data(Qt.ItemDataRole.UserRole) not in ('metadata', 'empty-cell'))
        if index.data(Qt.ItemDataRole.UserRole) == 'empty-cell':
            doc.setHtml('<p style="margin:0">-</p>')
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, QColor('#d8e5ed' if self.window.dark_mode else '#0c1c30'))
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.left(), rect.top() + max(0, (rect.height() - doc.size().height()) / 2))
        doc.documentLayout().draw(painter, context)
        painter.restore()


def build(window):
    window.resize(1120, 940)
    window.setMinimumSize(640, 520)
    window.setStyleSheet('''
        QWidget { font-family: "Segoe UI"; font-size: 12px; color: #142338; }
        QMainWindow, QWidget#root, QWidget#pageContent, QWidget#pageViewport { background: #f8fafb; }
        QWidget#topbar { background: #142839; }
        QWidget#topbar QLabel { color: #c6d2de; background: transparent; }
        QWidget#topbar QPushButton { color: #eff6fa; background: transparent; border: none; text-align: left; padding: 13px; }
        QWidget#topbar QPushButton:hover, QWidget#topbar QPushButton:checked { background: #205e70; }
        QWidget#topbar QPushButton#danger { color: #ffb4ab; background: #462d35; border: 1px solid #a94b4b; padding: 8px; }
        QLabel { background: transparent; }
        QPushButton { background: white; border: 1px solid #428395; border-radius: 6px; padding: 10px 16px; color: #126175; }
        QPushButton:hover { background: #e6f3f5; }
        QPushButton:disabled { color: #98a7b4; border-color: #dce3e9; background: #f5f7f9; }
        QPushButton#primary { color: white; background: #167084; border-color: #167084; font-weight: 600; }
        QPushButton#primary:hover { background: #125e70; }
        QPushButton#danger { color: #b42318; border-color: #edc7c2; background: #fff7f6; }
        QPushButton#primary:disabled, QPushButton#danger:disabled { color: #98a7b4; border-color: #dce3e9; background: #f5f7f9; }
        QPushButton:pressed, QPushButton#primary:pressed, QPushButton#danger:pressed,
        QWidget#topbar QPushButton:pressed { background: #0c4959; color: white; border-color: #0c4959; }
        QPushButton[clickedFeedback="true"], QPushButton#primary[clickedFeedback="true"],
        QPushButton#danger[clickedFeedback="true"], QWidget#topbar QPushButton[clickedFeedback="true"] {
            background: #287f91; color: white; border: 1px solid #65c3ce;
        }
        QPushButton:focus { border: 2px solid #167084; }
        QLineEdit, QComboBox { background: white; border: 1px solid #c7d2dd; border-radius: 6px; padding: 10px; }
        QLineEdit:focus, QComboBox:focus { border-color: #167084; }
        QComboBox QAbstractItemView { background: white; selection-background-color: #def1f3; selection-color: #142338; }
        QComboBox[filterControl="true"] { background: #e6f3f5; color: #126175; border: 1px solid #7fb1bd; border-radius: 5px; padding: 4px 8px; }
        QComboBox[filterControl="true"]:hover { background: #d7ecef; border-color: #167084; }
        QComboBox[filterControl="true"]:focus { background: #def1f3; border: 2px solid #167084; padding: 3px 7px; }
        QComboBox[filterControl="true"]::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 16px; border: none; background: transparent; }
        QComboBox[filterControl="true"]::down-arrow { image: none; }
        QComboBox[filterControl="true"] QAbstractItemView { background: #f8fafb; color: #126175; border: 1px solid #7fb1bd; padding: 4px; selection-background-color: #167084; selection-color: white; }
        QFrame#card { background: white; border: 1px solid #dbe3eb; border-radius: 7px; }
        QTableView { background: white; alternate-background-color: #fbfcfd; border: none; selection-background-color: #E6F4F2; selection-color: #0c1c30; }
        QTableView { font-size:12px; }
        QTableWidget::item { padding: 1px 6px; border-bottom: 1px solid #e8edf2; }
        QHeaderView::section { background: #f2f6f8; border: none; border-bottom: 1px solid #dbe3eb; padding: 3px 6px; color: #35465c; font-size:12px; font-weight:normal; }
        QTextBrowser { background: white; border: none; padding: 8px; }
        QListWidget { border: none; background: transparent; color: #edf4f8; }
        QListWidget::item { padding: 12px 8px; }
        QListWidget::item:selected { background: #205e70; border-radius: 5px; }
        QProgressBar { border: none; background: #e1e9ed; max-height: 5px; }
        QProgressBar::chunk { background: #167084; }
        QSplitter::handle { background: #f8fafb; width: 10px; }
        QToolTip { background: #142839; color: white; padding: 6px; border: none; }
    ''')
    window.setStyleSheet(window.styleSheet() + '''
        QWidget#topbar QLabel {font-weight:600;}
        QWidget#topbar QPushButton {padding:8px 14px;text-align:center;}
        QPushButton[compactControl="true"], QWidget#topbar QPushButton[compactControl="true"] {padding:4px 8px;}
        QListWidget#folderList {color:#142338;background:white;border:1px solid #dbe3eb;border-radius:7px;}
        QListWidget#folderList::item:selected {color:white;background:#205e70;}
    ''')
    window.light_style = window.styleSheet()
    root = QWidget()
    root.setObjectName('root')
    window.setCentralWidget(root)
    outer = QVBoxLayout(root)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    window.controls = []

    def button(name, action, layout, primary=False):
        item = QPushButton(name)
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setProperty('compactControl', True)
        item.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        item.setIconSize(QSize(18, 18))
        item.setIcon(action_icon(name, '#e0edf4' if primary else '#58a6b7'))
        if primary:
            item.setObjectName('primary')
        item.clicked.connect(lambda: window.button_clicked(item))
        item.clicked.connect(action)
        layout.addWidget(item)
        return item

    top = QWidget()
    top.setObjectName('topbar')
    top.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    top_layout = WrapLayout(top)
    top_layout.setContentsMargins(14, 6, 14, 6)
    outer.addWidget(top)
    brand = QWidget()
    branding = QHBoxLayout(brand)
    branding.setContentsMargins(0, 0, 4, 0)
    logo = QLabel('MR')
    logo.setStyleSheet('background:#167084;color:white;font-weight:700;border-radius:6px;padding:7px;')
    branding.addWidget(logo)
    branding.addWidget(QLabel('Master Register Search'))
    top_layout.addWidget(brand)
    window.nav_buttons = []
    tabs = QWidget()
    navigation = QHBoxLayout(tabs)
    navigation.setContentsMargins(0, 0, 0, 0)
    navigation.setSpacing(6)
    top_layout.addWidget(tabs)
    for index, name in enumerate(('Search', 'Folders', 'Sync status', 'Settings')):
        tab = button(name, lambda checked=False, i=index: window.show_tab(i), navigation)
        tab.setCheckable(True)
        tab.setIcon(QIcon())
        window.nav_buttons.append(tab)
    offline = QLabel('● Offline · Local files only')
    offline.setProperty('alignRight', True)
    offline.setText('● Offline · Local files only')
    offline.setStyleSheet('color:#74db99;font-size:11px;padding:7px;')
    top_layout.addWidget(offline)

    window.sync_panel = QWidget()
    syncing = QVBoxLayout(window.sync_panel)
    syncing.setContentsMargins(24, 8, 24, 8)
    window.progress = QProgressBar()
    window.progress.setTextVisible(False)
    syncing.addWidget(window.progress)
    live = QHBoxLayout()
    window.status = QLabel('Ready. Add folders and sync files, then search.')
    window.status.setWordWrap(True)
    live.addWidget(window.status, 1)
    window.progress_label = QLabel()
    live.addWidget(window.progress_label)
    window.cancel_button = button('Cancel sync', window.cancel_sync, live)
    window.cancel_button.setEnabled(False)
    syncing.addLayout(live)
    outer.addWidget(window.sync_panel)

    window.feedback = QLabel()
    window.feedback.setWordWrap(True)
    window.feedback.hide()
    outer.addWidget(window.feedback)
    window.pages = QStackedWidget()
    outer.addWidget(window.pages, 1)

    search_page = QWidget()
    layout = QVBoxLayout(search_page)
    layout.setContentsMargins(14, 10, 14, 8)
    layout.setSpacing(8)
    window.pages.addWidget(search_page)
    searchbar = QHBoxLayout()
    from .chip_input import ChipInput
    window.query = ChipInput()
    window.query.setMinimumHeight(34)
    searchbar.addWidget(window.query, 1)
    search_button = button('Search', window.submit_search, searchbar, True)
    search_button.setIcon(QIcon())
    search_button.setFixedHeight(32)
    search_button.setStyleSheet('padding:4px 14px;')
    window.query.searchRequested.connect(window.submit_search)
    layout.addLayout(searchbar)

    filter_container = QWidget()
    filters = WrapLayout(filter_container)
    filters.setSpacing(8)
    from .filter_dropdown import SearchableCombo
    window.folder_filter = SearchableCombo('No folders found')
    window.folder_filter.setToolTip('Select a register folder to manage it in Folders or Settings. Use File and Worksheet to filter results.')
    window.mode = FilterCombo()
    window.mode.addItems(['All Words', 'Any word'])
    window.file_filter = SearchableCombo('No files found')
    window.sheet_filter = SearchableCombo('No sheets found')
    for label, combo in [('Folder', window.folder_filter), ('Match', window.mode),
                         ('File', window.file_filter), ('Worksheet', window.sheet_filter)]:
        group = QWidget()
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QLabel(label))
        group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        combo.setProperty('filterControl', True)
        row.addWidget(combo)
        filters.addWidget(group)
    window.index_summary = QLabel()
    window.index_summary.setStyleSheet('color:#52647b;font-size:10px;')
    window.index_summary.setMaximumWidth(280)
    window.index_summary.setWordWrap(True)
    filters.addWidget(window.index_summary)
    for name, action in [('Add folder', window.add_folder), ('Sync files', lambda: window.start_sync(False))]:
        item = button(name, action, filters)
        item.setIcon(QIcon())
        window.controls.append(item)
    layout.addWidget(filter_container)
    # Recent-search storage and shortcuts are retained; the picker lives in Settings.
    window.recent = QComboBox()
    window.recent.setMinimumWidth(220)
    window.recent.currentIndexChanged.connect(window.use_recent_search)

    window.results_split = QSplitter(Qt.Orientation.Horizontal)
    window.results_split.setHandleWidth(10)
    layout.addWidget(window.results_split, 1)
    results = QFrame()
    results.setObjectName('card')
    result_layout = QVBoxLayout(results)
    result_layout.setContentsMargins(0, 0, 0, 0)
    result_header = QHBoxLayout()
    result_header.setContentsMargins(12, 8, 12, 8)
    title = QLabel('Search results ·')
    title.setText('Search results')
    title.setStyleSheet('font-size:13px;font-weight:600;')
    result_header.addWidget(title)
    window.result_count = QLabel()
    window.result_count.setStyleSheet('font-size:10px;color:#52647b;')
    window.result_count.setWordWrap(True)
    result_header.addWidget(window.result_count, 1)
    window.export_button = button('Export results', window.export_results, result_header)
    window.export_button.setIcon(QIcon())
    window.export_button.setFixedHeight(26)
    window.export_button.setEnabled(False)
    window.export_button.setToolTip('Export all matching rows to Excel or CSV')
    result_layout.addLayout(result_header)
    window.table = LiftTable(0, 3)
    window.table.setHorizontalHeaderLabels(['File', 'Sheet', 'Row'])
    window.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    window.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    window.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    window.table.setAlternatingRowColors(True)
    window.table.setShowGrid(False)
    window.table.setWordWrap(False)
    window.table.verticalHeader().hide()
    window.table.verticalHeader().setMinimumSectionSize(18)
    window.table.verticalHeader().setMaximumSectionSize(22)
    window.table.verticalHeader().setDefaultSectionSize(20)
    window.table.frozen.verticalHeader().setMinimumSectionSize(18)
    window.table.frozen.verticalHeader().setMaximumSectionSize(22)
    for column, width in enumerate((220, 130, 55)):
        window.table.setColumnWidth(column, width)
    for table_header in (window.table.horizontalHeader(), window.table.frozen.horizontalHeader()):
        table_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        table_header.setFixedHeight(26)
        table_header.setSectionsClickable(True)
        table_header.setSortIndicatorShown(True)
        table_header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    window.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    window.table.horizontalHeader().setStretchLastSection(False)
    window.table.frozen.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    window.table.frozen.horizontalHeader().setStretchLastSection(False)
    window.table.horizontalHeader().setMinimumSectionSize(48)
    window.table.frozen.horizontalHeader().setMinimumSectionSize(48)
    window.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    window.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    window.match_delegate = MatchDelegate(window)
    window.table.setItemDelegate(window.match_delegate)
    window.table.frozen.setItemDelegate(window.match_delegate)
    window.table.update_frozen()
    window.column_resize_timer = QTimer(window)
    window.column_resize_timer.setSingleShot(True)
    window.column_resize_timer.setInterval(120)
    window.column_resize_timer.timeout.connect(window.finish_column_resize)
    window.table.horizontalHeader().sectionResized.connect(window.table_column_resized)
    result_layout.addWidget(window.table, 1)
    window.empty_card = QWidget()
    empty = QVBoxLayout(window.empty_card)
    empty.setContentsMargins(32, 32, 32, 32)
    empty.addStretch(1)
    window.empty_title = QLabel('Search your registers')
    window.empty_title.setStyleSheet('font-size:28px;font-weight:700;')
    empty.addWidget(window.empty_title)
    window.empty_message = QLabel()
    window.empty_message.setWordWrap(True)
    window.empty_message.setStyleSheet('font-size:18px;')
    empty.addWidget(window.empty_message)
    actions = QHBoxLayout()
    window.empty_add = button('Add folder', window.add_folder, actions)
    window.empty_sync = button('Sync now', lambda: window.start_sync(False), actions, True)
    window.empty_reset = button('Reset search and filters', window.reset_filters, actions)
    window.controls.extend([window.empty_add, window.empty_sync])
    actions.addStretch(1)
    empty.addLayout(actions)
    empty.addStretch(1)
    result_layout.addWidget(window.empty_card, 1)
    window.results_split.addWidget(results)

    window.detail_drawer = QFrame()
    window.detail_drawer.setObjectName('card')
    window.detail_drawer.setMinimumWidth(300)
    window.detail_drawer.setMaximumWidth(330)
    detail_layout = QVBoxLayout(window.detail_drawer)
    detail_layout.setContentsMargins(8, 10, 8, 10)
    detail_layout.setSpacing(6)
    drawer_header = QHBoxLayout()
    drawer_title = QLabel('Record details')
    drawer_title.setStyleSheet('font-size:13px;font-weight:600;')
    drawer_header.addWidget(drawer_title, 1)
    window.drawer_close = button('×', window.detail_drawer.hide, drawer_header)
    window.drawer_close.setAccessibleName('Close record details')
    window.drawer_close.setToolTip('Close record details')
    window.drawer_close.setFixedSize(24, 24)
    window.drawer_close.setIcon(QIcon())
    window.drawer_close.setText('×')
    window.drawer_close.setStyleSheet('padding:0;border:none;background:transparent;')
    detail_layout.addLayout(drawer_header)
    window.file_title = QLabel()
    window.file_title.setMinimumWidth(0)
    window.file_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    window.file_title.setStyleSheet('font-size:11px;color:#52647b;')
    detail_layout.addWidget(window.file_title)
    window.detail = QTextBrowser()
    window.detail.setOpenExternalLinks(False)
    window.detail.setOpenLinks(False)
    window.detail.anchorClicked.connect(window.detail_link_clicked)
    window.detail.setStyleSheet('padding:0;font-size:12px;')
    window.detail.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
    record_grid = QGridLayout()
    record_grid.setContentsMargins(0, 2, 4, 2)
    record_grid.setHorizontalSpacing(6)
    record_grid.setVerticalSpacing(6)
    record_grid.setColumnStretch(0, 1)
    record_grid.setColumnStretch(1, 1)
    window.record_buttons = []
    for name, action, primary, grid_row, grid_column, span in [
        ('Open Excel file', lambda: window.open_path(False), True, 0, 0, 2),
        ('Open file', lambda: window.open_referenced_file(), False, 1, 0, 1),
        ('Open folder', lambda: window.open_path(True), False, 1, 1, 1),
        ('Copy path', window.copy_path, False, 2, 0, 1),
        ('Copy row', window.copy_row, False, 2, 1, 1)]:
        slot = QHBoxLayout()
        item = button(name, action, slot, primary)
        item.setProperty('actionLabel', name)
        item.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        item.setFixedHeight(28)
        item.setIconSize(QSize(14, 14))
        item.setStyleSheet('padding:4px 8px;font-size:12px;')
        item.setIcon(action_icon('Open' if name == 'Open file' else name,
                                 '#ffffff' if primary else '#126175' if name.startswith('Open') else '#142338'))
        item.setIcon(QIcon())
        slot.takeAt(0)
        record_grid.addWidget(item, grid_row, grid_column, 1, span)
        window.record_buttons.append(item)
        if name == 'Open file':
            window.open_file_button = item
            item.hide()
    secondary_width = max(item.sizeHint().width() for item in window.record_buttons[1:])
    record_grid.setColumnMinimumWidth(0, secondary_width)
    record_grid.setColumnMinimumWidth(1, secondary_width)
    detail_layout.addLayout(record_grid)
    window.record_meta = QLabel()
    window.record_meta.setWordWrap(True)
    window.record_meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    window.record_meta.setFixedHeight(48)
    window.record_meta.setStyleSheet('color:#52647b;font-size:11px;padding:2px 0;')
    detail_layout.addWidget(window.record_meta)
    field_header = QHBoxLayout()
    window.field_count = QLabel('0 fields')
    window.field_count.setStyleSheet('font-weight:600;font-size:11px;')
    window.field_filter = QLineEdit()
    window.field_filter.setPlaceholderText('Filter fields...')
    window.field_filter.setClearButtonEnabled(True)
    window.field_filter.setStyleSheet('padding:3px 6px;font-size:11px;')
    window.field_filter.textChanged.connect(window.render_detail_fields)
    field_header.addWidget(window.field_count)
    field_header.addWidget(window.field_filter, 1)
    detail_layout.addLayout(field_header)
    detail_layout.addWidget(window.detail, 1)
    window.results_split.addWidget(window.detail_drawer)
    window.results_split.setStretchFactor(0, 1)
    window.results_split.setStretchFactor(1, 0)
    window.detail_drawer.hide()

    footer = QHBoxLayout()
    window.count = QLabel('Click a row to see its full details in a side drawer.')
    window.count.setWordWrap(True)
    footer.addWidget(window.count, 1)
    window.page_buttons = [button('Previous', lambda: window.change_page(-1), footer),
                           button('Next', lambda: window.change_page(1), footer)]
    window.page_size = QComboBox(search_page)
    window.page_size.addItem('50 per page', 50)
    window.page_size.hide()
    footer.setContentsMargins(12, 6, 12, 6)
    for item in window.page_buttons:
        item.setIcon(QIcon())
    result_layout.addLayout(footer)

    def page(title):
        area = QScrollArea()
        area.viewport().setObjectName('pageViewport')
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName('pageContent')
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box = QVBoxLayout(content)
        box.setContentsMargins(24, 24, 24, 24)
        heading = QLabel(title)
        heading.setStyleSheet('font-size:26px;font-weight:600;')
        box.addWidget(heading)
        area.setWidget(content)
        window.pages.addWidget(area)
        return box

    folders = page('Register folders')
    folders.addWidget(QLabel('Select a folder to manage its saved records.'))
    window.folder_list = QListWidget()
    window.folder_list.setMinimumHeight(180)
    window.folder_list.setObjectName('folderList')
    window.folder_list.addItems(window.folders)
    folders.addWidget(window.folder_list, 1)
    folder_actions = QHBoxLayout()
    window.controls.append(button('Add folder', window.add_folder, folder_actions))
    window.controls.append(button('Remove selected folder', window.remove_folder, folder_actions))
    folder_actions.addStretch(1)
    folders.addLayout(folder_actions)

    sync_page = page('Sync status')
    window.sync_summary = QLabel()
    window.sync_summary.setWordWrap(True)
    sync_page.addWidget(window.sync_summary)
    window.sync_status = QLabel()
    window.sync_status.setWordWrap(True)
    sync_page.addWidget(window.sync_status)
    window.errors = QTextBrowser()
    sync_page.addWidget(window.errors, 1)
    sync_actions = QHBoxLayout()
    window.controls.append(button('Sync files', lambda: window.start_sync(False), sync_actions))
    sync_actions.addStretch(1)
    sync_page.addLayout(sync_actions)

    settings = page('Settings')
    dark = QCheckBox('Dark mode')
    dark.setChecked(window.dark_mode)
    dark.toggled.connect(window.set_dark_mode)
    settings.addWidget(dark)
    watch = QCheckBox('Auto-sync folders (check every 10 seconds)')
    watch.setChecked(window.auto_sync)
    watch.toggled.connect(window.set_auto_sync)
    settings.addWidget(watch)
    include_section = QCheckBox('Include section heading in search')
    include_section.setChecked(window.include_section)
    include_section.toggled.connect(window.set_include_section)
    settings.addWidget(include_section)
    settings.addWidget(QLabel('Recent searches'))
    settings.addWidget(window.recent)
    recent_actions = QHBoxLayout()
    button('Clear recent searches', lambda: (window.recent_searches.clear(), window.save_preferences(), window.refresh_recent_searches(), window.notify('Recent searches cleared.')), recent_actions)
    window.controls.append(button('Rebuild index', lambda: window.start_sync(True), recent_actions))
    recent_actions.addStretch(1)
    settings.addLayout(recent_actions)
    settings.addSpacing(22)
    danger = QLabel('Danger zone')
    danger.setStyleSheet('color:#b42318;font-size:20px;font-weight:600;')
    settings.addWidget(danger)
    warning = QLabel('Clear saved search records. Original Excel files remain untouched.\nChoose a folder from the Folder dropdown or Folders tab before clearing its index.')
    warning.setWordWrap(True)
    settings.addWidget(warning)
    danger_actions = WrapLayout()
    window.clear_folder_button = button('Clear folder index', window.delete_folder_data, danger_actions)
    window.clear_all_button = button('Clear all index', window.delete_index, danger_actions)
    for item in (window.clear_folder_button, window.clear_all_button):
        item.setObjectName('danger')
    settings.addLayout(danger_actions)
    settings.addStretch(1)

    window.folder_filter.currentIndexChanged.connect(window.select_folder_from_dropdown)
    window.folder_list.currentRowChanged.connect(window.select_folder_from_list)
    window.format_folders()
    window.show_tab(0)
    # Presentation mirrors worker state without changing the sync implementation.
    def refresh_presentation():
        window.sync_panel.setVisible(window.scanning or getattr(window, 'index_update_needed', False))
        window.progress.setVisible(window.scanning)
        window.progress_label.setVisible(window.scanning)
        window.cancel_button.setVisible(window.scanning)
        window.sync_summary.setText(window.index_summary.text())
        window.sync_status.setText(window.status.text())
        busy = bool(window.jobs) or window.scanning
        window.clear_folder_button.setEnabled(not busy and window.folder_list.currentRow() >= 0)
        window.clear_all_button.setEnabled(not busy)
        if not window.rows:
            window.detail_drawer.hide()
    window.presentation_timer = QTimer(window)
    window.presentation_timer.setInterval(150)
    window.presentation_timer.timeout.connect(refresh_presentation)
    window.presentation_timer.start()
    refresh_presentation()
