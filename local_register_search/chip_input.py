"""Phrase chips serialized into the existing search query interface."""
import json
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QPushButton, QLabel

COLORS = ('#FFE08A', '#CDEBE7', '#DDE3FF', '#F8D8E6', '#DCEBC5', '#FFE0C2', '#D5EAFB', '#E9D9F5')


def is_keyword(value):
    return any(char.isalnum() for char in value)


def quoted(value):
    return json.dumps(value, ensure_ascii=False)


class ChipInput(QWidget):
    textChanged = Signal(str)
    searchRequested = Signal()
    MAX_CHIPS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        from .layout import WrapLayout
        self.chips = []
        self.editing = None
        self.flow = WrapLayout(self)
        self.flow.setContentsMargins(8, 6, 8, 6)
        self.editor = QLineEdit()
        self.editor.setMinimumWidth(220)
        self.editor.setStyleSheet('font-size:18px;border:none;background:transparent;padding:6px;')
        self.editor.installEventFilter(self)
        self.editor.textChanged.connect(lambda: self.textChanged.emit(self.text()))
        self.editor.returnPressed.connect(self.enter)
        self.clear_button = QPushButton('×')
        self.clear_button.setAccessibleName('Clear all keywords')
        self.clear_button.setToolTip('Clear all')
        self.clear_button.setFixedSize(32, 32)
        self.clear_button.setStyleSheet('padding:0;border:none;background:transparent;')
        self.clear_button.clicked.connect(self.clear)
        self.rebuild()
        self.setFocusProxy(self.editor)
        self.setObjectName('chipInput')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet('QWidget#chipInput {background:white;border:1px solid #c7d2dd;border-radius:6px;}')

    def keyword_colors(self):
        terms = list(self.chips)
        draft = self.editor.text().strip()
        if is_keyword(draft):
            color = self.editing[1] if self.editing else next((c for c in COLORS if c not in [color for _, color in terms]), COLORS[-1])
            terms.insert(self.editing[0] if self.editing else len(terms), (draft, color))
        return terms[:self.MAX_CHIPS]

    def set_theme(self, dark):
        background, foreground = ('#22394a', '#e5eff5') if dark else ('white', '#142338')
        self.setStyleSheet(f'QWidget#chipInput {{background:{background};border:1px solid #c7d2dd;border-radius:6px;}}')
        self.editor.setStyleSheet(f'font-size:18px;border:none;background:transparent;color:{foreground};padding:6px;')

    def text(self):
        if not self.chips and not self.editing and len(self.editor.text().strip()) < 2:
            return ''
        return ' '.join(quoted(value) for value, color in self.keyword_colors())

    def load_query(self, query):
        decoder = json.JSONDecoder()
        terms, remaining = [], query.strip()
        try:
            while remaining:
                value, end = decoder.raw_decode(remaining)
                if not isinstance(value, str):
                    raise ValueError()
                if is_keyword(value):
                    terms.append(value)
                remaining = remaining[end:].strip()
        except ValueError:
            self.setText(query)
            return
        self.chips = [(term, COLORS[i]) for i, term in enumerate(terms[:self.MAX_CHIPS])]
        self.editing = None
        self.editor.clear()
        self.rebuild()
        self.textChanged.emit(self.text())

    def setText(self, value):
        self.chips.clear()
        self.editing = None
        self.editor.setText(value)
        self.rebuild()
        self.textChanged.emit(self.text())

    def clear(self):
        self.chips.clear()
        self.editing = None
        self.editor.clear()
        self.rebuild()
        self.textChanged.emit('')
        self.searchRequested.emit()

    def enter(self):
        value = self.editor.text().strip()
        if not value:
            self.searchRequested.emit()
            return
        if not is_keyword(value):
            self.editor.clear()
            return
        if len(self.chips) >= self.MAX_CHIPS:
            self.editor.setToolTip('Maximum 8 keywords. Edit or remove a chip first.')
            return
        colors = [color for _, color in self.chips]
        color = self.editing[1] if self.editing else next(c for c in COLORS if c not in colors)
        position = self.editing[0] if self.editing else len(self.chips)
        self.chips.insert(position, (value, color))
        self.editing = None
        self.editor.blockSignals(True)
        self.editor.clear()
        self.editor.blockSignals(False)
        self.rebuild()
        self.textChanged.emit(self.text())

    def remove_chip(self, index):
        self.chips.pop(index)
        if self.editing and index < self.editing[0]:
            self.editing = (self.editing[0] - 1, self.editing[1])
        self.rebuild()
        self.textChanged.emit(self.text())
        self.searchRequested.emit()

    def edit_chip(self, index):
        if self.editor.text().strip():
            self.enter()
            if self.editor.text().strip():
                return
        value, color = self.chips.pop(index)
        self.editing = (index, color)
        self.editor.setText(value)
        self.rebuild()
        self.editor.selectAll()
        self.editor.setFocus()

    def eventFilter(self, watched, event):
        if watched is self.editor and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Backspace and not self.editor.text() and self.chips:
                self.remove_chip(len(self.chips) - 1)
                return True
        elif event.type() == QEvent.Type.MouseButtonDblClick and watched.property('chipIndex') is not None:
            self.edit_chip(watched.property('chipIndex'))
            return True
        return super().eventFilter(watched, event)

    def rebuild(self):
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget()
            if widget not in (self.editor, self.clear_button):
                widget.hide()
                widget.deleteLater()
        for index, (value, color) in enumerate(self.chips):
            chip = QWidget()
            chip.setStyleSheet(f'background:{color};color:#142338;border-radius:6px;')
            row = QHBoxLayout(chip)
            row.setContentsMargins(8, 2, 4, 2)
            label = QLabel(value)
            label.setMaximumWidth(300)
            label.setWordWrap(True)
            label.setToolTip(value + '\nDouble-click to edit')
            label.setProperty('chipIndex', index)
            label.installEventFilter(self)
            chip.setProperty('chipIndex', index)
            chip.installEventFilter(self)
            row.addWidget(label)
            close = QPushButton('×')
            close.setFixedSize(26, 26)
            close.setStyleSheet('padding:0;border:none;background:transparent;color:#142338;')
            close.setAccessibleName('Remove keyword ' + value)
            close.clicked.connect(lambda checked=False, i=index: self.remove_chip(i))
            row.addWidget(close)
            self.flow.addWidget(chip)
        self.editor.setPlaceholderText('Add another keyword...' if self.chips else 'Search names, phone numbers, dates, phrases...')
        self.editor.setVisible(len(self.chips) < self.MAX_CHIPS)
        self.flow.addWidget(self.editor)
        self.clear_button.setProperty('alignRight', True)
        self.flow.addWidget(self.clear_button)
        self.flow.invalidate()
        self.updateGeometry()
