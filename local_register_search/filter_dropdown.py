"""Searchable list popup with bounded height and desktop-aware placement."""
import re
from PySide6.QtCore import Qt, QEvent, QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QComboBox, QFrame, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy, QStyle, QStyleOptionComboBox


class ContentCombo(QComboBox):
    """Size the closed control to its selected text; keep popup sizing separate."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.currentTextChanged.connect(self.updateGeometry)

    def sizeHint(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text_size = self.fontMetrics().size(Qt.TextFlag.TextSingleLine, self.currentText())
        if not self.itemIcon(self.currentIndex()).isNull():
            text_size.setWidth(text_size.width() + self.iconSize().width() + 4)
            text_size.setHeight(max(text_size.height(), self.iconSize().height()))
        return self.style().sizeFromContents(QStyle.ContentsType.CT_ComboBox, option, text_size, self)

    def minimumSizeHint(self):
        return self.sizeHint()


def natural_key(value):
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r'(\d+)', str(value)))


class SearchableCombo(ContentCombo):
    MAX_VISIBLE_ITEMS = 12

    def __init__(self, empty_message, parent=None):
        super().__init__(parent)
        self.empty_message = empty_message
        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setObjectName('filterPopup')
        layout = QVBoxLayout(self.popup)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText('Type to filter...')
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.installEventFilter(self)
        layout.addWidget(self.filter_input)
        self.list_view = QListWidget()
        self.list_view.setUniformItemSizes(True)
        self.list_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_view.itemClicked.connect(self.choose)
        self.list_view.itemActivated.connect(self.choose)
        self.list_view.installEventFilter(self)
        layout.addWidget(self.list_view)
        self.filter_input.textChanged.connect(self.filter_items)

    def clear(self):
        self.hidePopup()
        super().clear()

    def showPopup(self):
        if not self.count():
            return
        dark = getattr(self.window(), 'dark_mode', False)
        background, foreground, border = ('#22394a', '#e5eff5', '#52768a') if dark else ('#f8fafb', '#142338', '#7fb1bd')
        self.popup.setStyleSheet(f'''
            QFrame#filterPopup {{background:{background};border:1px solid {border};border-radius:6px;}}
            QLineEdit {{background:{background};color:{foreground};padding:7px;border:1px solid {border};border-radius:4px;}}
            QListWidget {{background:{background};color:{foreground};border:none;}}
            QListWidget::item {{padding:8px;}}
            QListWidget::item:selected {{background:#167084;color:white;}}
            QListWidget::item:disabled {{color:#98a7b4;}}
        ''')
        self.filter_input.clear()
        self.filter_items('')
        self.popup.show()
        self.position_popup()
        self.filter_input.setFocus()

    def hidePopup(self):
        if hasattr(self, 'popup'):
            self.popup.hide()
        super().hidePopup()

    def filter_items(self, text):
        self.list_view.clear()
        indices = [0] + sorted((i for i in range(1, self.count())
                                if text.casefold() in self.itemText(i).casefold()),
                               key=lambda i: natural_key(self.itemText(i)))
        height = max(32, self.fontMetrics().height() + 16)
        for index in indices:
            item = QListWidgetItem(self.itemText(index))
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(self.itemData(index, Qt.ItemDataRole.ToolTipRole) or self.itemText(index))
            item.setSizeHint(QSize(0, height))
            self.list_view.addItem(item)
            if index == self.currentIndex():
                self.list_view.setCurrentItem(item)
        if len(indices) == 1 and text:
            empty = QListWidgetItem(self.empty_message)
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_view.addItem(empty)
        self.position_popup()

    def position_popup(self):
        from PySide6.QtCore import QPoint
        origin = self.mapToGlobal(QPoint(0, 0))
        screen = QGuiApplication.screenAt(origin) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        row_height = max(32, self.fontMetrics().height() + 16)
        desired_height = min(self.MAX_VISIBLE_ITEMS, max(1, self.list_view.count())) * row_height + self.filter_input.sizeHint().height() + 24
        width = min(available.width(), max(self.width(), max((self.fontMetrics().horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=0) + 60))
        below = max(0, available.bottom() - (origin.y() + self.height()) + 1)
        above = max(0, origin.y() - available.top())
        use_below = below >= desired_height or below >= above
        height = min(desired_height, below if use_below else above, available.height())
        x = min(max(origin.x(), available.left()), available.right() - width + 1)
        y = origin.y() + self.height() if use_below else origin.y() - height
        y = max(available.top(), min(y, available.bottom() - height + 1))
        self.popup.setGeometry(x, y, width, height)

    def choose(self, item):
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is not None and item.flags() & Qt.ItemFlag.ItemIsEnabled:
            self.setCurrentIndex(index)
            self.hidePopup()
            self.setFocus()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.hidePopup()
                return True
            if watched is self.filter_input and event.key() == Qt.Key.Key_Down:
                self.list_view.setCurrentRow(1 if self.list_view.count() > 1 else 0)
                self.list_view.setFocus()
                return True
            if watched is self.filter_input and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.choose(self.list_view.item(1 if self.filter_input.text() and self.list_view.count() > 1 else 0))
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.text() and event.text().isprintable() and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            self.showPopup()
            self.filter_input.setText(event.text())
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.showPopup()
            return
        super().keyPressEvent(event)
