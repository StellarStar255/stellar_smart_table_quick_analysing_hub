"""Formula editor assistance shared by cell editors and the function library."""
from html import escape
import re

from PyQt6.QtCore import Qt, QObject, QEvent, QPoint, QTimer
from PyQt6.QtGui import QGuiApplication, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QDialog, QDialogButtonBox, QLineEdit,
    QComboBox, QTextBrowser, QAbstractItemView, QPlainTextEdit, QWidget, QScrollArea,
)
from core.formula_help import (
    FUNCTIONS, PARAMS, CATEGORIES, local, search_functions,
    completion_span, completion_functions, active_call, diagnose, reference_tokens,
)

REFERENCE_COLORS = ('#2674d9', '#c05a24', '#9b45b6', '#168568', '#bd4775', '#7772b8')


def spec_html(spec, active=None):
    parts = []
    for i, key in enumerate(spec.params):
        label = escape(local(*PARAMS[key][:2]))
        if i >= spec.minimum:
            label = '[' + label + ']'
        selected = active == i or (active is not None and active >= len(spec.params)
                                   and spec.parameter_key(active) == key and i >= len(spec.params) - spec.repeat)
        parts.append('<b><u>' + label + '</u></b>' if selected else label)
    if spec.repeat:
        parts.append('…')
    html = '<b>' + spec.name + '</b>(' + ', '.join(parts) + ')<br>'
    html += escape(spec.description)
    if active is not None and spec.params:
        key = spec.parameter_key(active)
        p = PARAMS[key]
        html += '<br><b>' + escape(local(*p[:2])) + '</b>: ' + escape(local(*p[2:]))
    html += '<br>' + local('示例：', 'Example: ') + '<code>' + escape(spec.example) + '</code>'
    if any(spec.note):
        html += '<br>' + escape(local(*spec.note))
    return html


class FormulaLibrary(QDialog):
    """Searchable catalogue. Selecting inserts syntax, never changes cell values."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(local('插入函数', 'Insert Function'))
        self.resize(660, 520)
        self.selected_function = None
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(local('搜索函数或用途，例如：求和、查找、SUM', 'Search function or purpose, e.g. sum, lookup, SUM'))
        self.search.setAccessibleName(local('搜索函数', 'Search functions'))
        self.category = QComboBox()
        self.category.addItem(local('全部函数', 'All functions'), None)
        for key, label in CATEGORIES.items():
            self.category.addItem(local(*label), key)
        row = QHBoxLayout()
        row.addWidget(self.search, 1)
        row.addWidget(self.category)
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.setAccessibleName(local('可用函数', 'Available functions'))
        layout.addWidget(self.list, 1)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        layout.addWidget(self.details, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(local('插入', 'Insert'))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(local('取消', 'Cancel'))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.search.textChanged.connect(self.refresh)
        self.category.currentIndexChanged.connect(self.refresh)
        self.list.currentItemChanged.connect(self.show_details)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        self.refresh()

    def refresh(self, *_):
        self.list.clear()
        for spec in search_functions(self.search.text(), self.category.currentData()):
            item = QListWidgetItem(f'{spec.name}    {spec.description}')
            item.setData(Qt.ItemDataRole.UserRole, spec.name)
            self.list.addItem(item)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.list.count() > 0)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.details.setPlainText(local('没有匹配的函数。请尝试其他名称或用途。', 'No matching functions. Try another name or purpose.'))

    def show_details(self, current, previous=None):
        if current is None:
            return
        spec = FUNCTIONS[current.data(Qt.ItemDataRole.UserRole)]
        html = spec_html(spec)
        for key in dict.fromkeys(spec.params):
            p = PARAMS[key]
            html += '<p><b>' + escape(local(*p[:2])) + '</b>: ' + escape(local(*p[2:])) + '</p>'
        html += '<p>' + local('第 1 行为表头，数据从第 2 行开始。文本使用英文双引号。', 'Row 1 contains headers; data starts at row 2. Enclose text in double quotes.') + '</p>'
        self.details.setHtml(html)

    def accept(self):
        item = self.list.currentItem()
        if item is not None:
            self.selected_function = item.data(Qt.ItemDataRole.UserRole)
            super().accept()


class FormulaAssistant(QObject):
    """A non-activating panel: keyboard and table pointing stay in the editor.

    The delegate calls handle_key before its own Tab/Enter navigation. The panel
    never owns keyboard focus, so clicking a suggestion cannot commit an editor.
    """
    def __init__(self, editor, view, library_callback=None):
        super().__init__(editor)
        self.editor = editor
        self.view = view
        self.library_callback = library_callback
        self.suppressed = False
        self.composing = False
        self.diagnostic = None
        self.forced = False
        self.span = None
        # A normal child overlay has no native tooltip timeout or activation
        # behavior. It stays with this document instead of another app window.
        self.panel = QFrame(view.window())
        self.panel.setObjectName('formula_assistant')
        self.panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.panel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.panel.setFrameShape(QFrame.Shape.StyledPanel)
        self.panel.setStyleSheet('QFrame#formula_assistant { background: palette(base); border: 1px solid palette(mid); border-radius: 5px; }')
        self.body = QWidget(self.panel)
        layout = QVBoxLayout(self.body)
        self.scroll = QScrollArea(self.panel)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.body)
        outer = QVBoxLayout(self.panel)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.addWidget(self.scroll)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setAccessibleName(local('公式候选', 'Formula suggestions'))
        layout.addWidget(self.list)
        self.help = QLabel()
        self.help.setTextFormat(Qt.TextFormat.RichText)
        self.help.setWordWrap(True)
        self.help.setAccessibleName(local('函数参数说明', 'Function argument help'))
        layout.addWidget(self.help)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setTextFormat(Qt.TextFormat.PlainText)
        self.problem.setAccessibleName(local('公式诊断', 'Formula diagnostic'))
        layout.addWidget(self.problem)
        row = QHBoxLayout()
        self.fix = QPushButton(local('应用建议', 'Apply suggestion'))
        self.fix.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.fix.clicked.connect(self.apply_fix)
        row.addWidget(self.fix)
        self.browse = QPushButton(local('浏览全部函数…', 'Browse all functions…'))
        self.browse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.browse.clicked.connect(self.open_library)
        row.addWidget(self.browse)
        row.addStretch()
        layout.addLayout(row)
        self.keys = QLabel(local('↑↓ 选择 · Tab 插入 · Enter 提交 · Esc 收起', '↑↓ Select · Tab Insert · Enter Commit · Esc Dismiss'))
        layout.addWidget(self.keys)
        self.list.itemClicked.connect(self.accept_completion)
        self.list.currentItemChanged.connect(self.show_candidate)
        self.editor.textChanged.connect(self.on_text)
        self.editor.cursorPositionChanged.connect(self.queue_refresh)
        self.editor.installEventFilter(self)
        self.view.window().installEventFilter(self)
        self.view.viewport().installEventFilter(self)
        self.editor.destroyed.connect(self.panel.deleteLater)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.refresh)

    def queue_refresh(self, *_):
        self.timer.start()

    def on_text(self, *_):
        self.suppressed = False
        self.forced = False
        self.timer.start()
        # Highlight references immediately, without reading model data.
        if self.editor.hasFocus():
            self.highlight_references()

    def highlight_references(self):
        if not hasattr(self.view, '_formula_reference_spans'):
            self.view._formula_reference_spans = []
        refs = reference_tokens(self.editor.text()) if self.editor.text().startswith('=') else []
        colors = {}
        for t in refs:
            key = t.text.upper().replace('$', '')
            colors.setdefault(key, REFERENCE_COLORS[len(colors) % len(REFERENCE_COLORS)])
        self.view._formula_reference_spans = [(t.text, colors[t.text.upper().replace('$', '')]) for t in refs]
        self.view.viewport().update()

    def refresh(self, *_):
        try:
            if (not self.editor.isVisible() or not self.editor.hasFocus()
                    or not self.editor.text().startswith('=') or self.composing or self.suppressed):
                self.hide()
                return
        except RuntimeError:
            return
        self.highlight_references()
        text = self.editor.text()
        # Qt offsets are UTF-16. The analyzer operates in Python characters.
        cursor = len(text.encode('utf-16-le')[:self.editor.cursorPosition() * 2].decode('utf-16-le', errors='ignore'))
        self.span = completion_span(text, cursor)
        old_name = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        self.list.blockSignals(True)
        self.list.clear()
        correction = False
        if self.span and not self.forced:
            query = self.span[2]
            specs, correction = completion_functions(query)
            for spec in specs:
                item = QListWidgetItem(f'{spec.name}    {spec.description}')
                if correction:
                    item.setText(item.text() + local(' · 拼写建议', ' · Spelling suggestion'))
                item.setData(Qt.ItemDataRole.UserRole, spec.name)
                self.list.addItem(item)
            row = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.ItemDataRole.UserRole) == old_name), 0)
            if self.list.count():
                self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self.list.setVisible(self.list.count() > 0)
        self.list.setFixedHeight(min(6, max(1, self.list.count())) * (self.list.fontMetrics().height() + 8) + 6)
        self.help.clear()
        active = active_call(text, cursor)
        if self.list.count():
            self.show_candidate(self.list.currentItem())
        elif active and active[0].name in FUNCTIONS:
            self.help.setText(spec_html(FUNCTIONS[active[0].name], active[1]))
        elif text.strip() == '=':
            self.help.setText(local('选择函数，或输入单元格与运算符，例如 =B2*C2。', 'Choose a function or enter references and operators, e.g. =B2*C2.'))
        else:
            self.help.setText(local('Enter 确认 · Esc 取消 · 可点选表格插入引用。',
                                    'Enter commits · Esc cancels · Click cells to insert references.'))
        partial_name = self.span and self.list.count() and not correction
        self.diagnostic = diagnose(text, final=self.forced or not partial_name)
        d = self.diagnostic
        compact = not self.list.count() and not active and d is None
        self.browse.setVisible(not compact)
        self.keys.setVisible(not compact)
        self.problem.setText(('⚠ ' if d and (self.forced or not d.incomplete) else '') + d.message if d else '')
        if d and self.forced:
            self.problem.setText(self.problem.text() + local(' 修改后再次提交；Esc 取消本次编辑。', ' Edit and submit again; Esc cancels this edit.'))
        self.problem.setVisible(d is not None)
        self.fix.setVisible(d is not None and d.replacement is not None)
        if d and d.replacement is not None:
            self.fix.setText(local('应用建议：', 'Apply: ') + (d.replacement or local('删除', 'Delete')))
        refs = self.view._formula_reference_spans if hasattr(self.view, '_formula_reference_spans') else []
        if refs and self.help.text():
            self.help.setText(self.help.text() + '<br>' + ' '.join(
                '<span style="color:' + color + '">■ ' + escape(ref) + '</span>' for ref, color in refs[:8]))
        if not (self.list.count() or self.help.text() or d):
            self.hide()
            return
        self.position()
        self.panel.show()
        self.panel.raise_()

    def position(self):
        host = self.editor.window()
        if self.panel.parentWidget() is not host:
            self.panel.setParent(host)
            host.installEventFilter(self)
        screen = self.editor.screen() or QGuiApplication.primaryScreen()
        bounds = host.rect()
        width = min(460, max(200, bounds.width() - 16), screen.availableGeometry().width() - 16)
        self.panel.setFixedWidth(width)
        self.body.setMinimumWidth(max(160, width - 24))
        self.body.layout().activate()
        desired_height = self.body.layout().sizeHint().height() + 4
        origin = host.mapFromGlobal(self.editor.mapToGlobal(QPoint(0, self.editor.height() + 4)))
        workbench = getattr(self.editor, 'workbench', None)
        if workbench and self.editor is workbench.edit:
            # Keep the fixed bar's result preview visible above its suggestions.
            origin.setY(host.mapFromGlobal(workbench.widget.mapToGlobal(QPoint(0, workbench.widget.height() + 4))).y())
        x = max(bounds.left() + 4, min(origin.x(), bounds.right() - width - 4))
        editor_top = host.mapFromGlobal(self.editor.mapToGlobal(QPoint(0, 0))).y()
        below = bounds.bottom() - origin.y() - 4
        above = editor_top - bounds.top() - 8
        if below >= desired_height or below >= above:
            height = min(desired_height, max(64, below))
            y = origin.y()
        else:
            height = min(desired_height, max(64, above))
            y = editor_top - height - 4
        self.panel.setFixedHeight(height)
        self.panel.move(x, max(bounds.top() + 4, y))

    def show_candidate(self, item, previous=None):
        if item:
            self.help.setText(spec_html(FUNCTIONS[item.data(Qt.ItemDataRole.UserRole)]))

    def replace(self, start, end, replacement):
        text = self.editor.text()
        # Use insert(), preserving native editor undo history.
        a = len(text[:start].encode('utf-16-le')) // 2
        b = len(text[:end].encode('utf-16-le')) // 2
        self.editor.setSelection(a, b - a)
        self.editor.insert(replacement)
        self.editor.setFocus()
        self.refresh()

    def accept_completion(self, *_):
        if self.timer.isActive():
            self.timer.stop()
            self.refresh()
        item = self.list.currentItem()
        if not item or not self.span:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        start, end, _ = self.span
        following = self.editor.text()[end:]
        suffix = '' if re.match(r'\s*\(', following) else '('
        self.replace(start, end, name + suffix)
        if not suffix:
            # Move into the existing opening parenthesis.
            shift = re.match(r'\s*\(', following).end()
            self.editor.setCursorPosition(len(self.editor.text()[:start + len(name) + shift].encode('utf-16-le')) // 2)
        self.suppressed = False
        self.refresh()

    def apply_fix(self):
        if self.timer.isActive():
            self.timer.stop()
            self.refresh()
        d = self.diagnostic
        if d and d.replacement is not None:
            self.replace(d.start, d.end, d.replacement)

    def open_library(self):
        self.panel.hide()
        self.editor._formula_library_open = True
        try:
            if self.library_callback:
                self.library_callback()
            else:
                dialog = FormulaLibrary(self.view.window())
                if dialog.exec() and dialog.selected_function:
                    self.insert_function(dialog.selected_function)
        finally:
            self.editor._formula_library_open = False
            self.editor.setFocus()
            self.refresh()

    def insert_function(self, name):
        text = self.editor.text()
        if not text.startswith('='):
            self.editor.selectAll()
            self.editor.insert('=' + name + '(')
        else:
            cursor = len(text.encode('utf-16-le')[:self.editor.cursorPosition() * 2].decode('utf-16-le', errors='ignore'))
            span = completion_span(text, cursor)
            if span:
                self.span = span
                following = text[span[1]:]
                self.replace(span[0], span[1], name + ('' if re.match(r'\s*\(', following) else '('))
            else:
                self.editor.insert(name + '(')
        self.suppressed = False
        self.refresh()

    def validate_commit(self):
        if not self.editor.text().startswith('='):
            return True
        d = diagnose(self.editor.text(), final=True)
        if d is None:
            return True
        self.forced = True
        self.suppressed = False
        self.refresh()
        self.editor.setFocus()
        text = self.editor.text()
        start = len(text[:d.start].encode('utf-16-le')) // 2
        length = len(text[d.start:d.end].encode('utf-16-le')) // 2
        self.editor.setSelection(start, length)
        return False

    def handle_key(self, event):
        key = event.key()
        mods = event.modifiers()
        if self.composing:
            return False
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Up, Qt.Key.Key_Down) and self.timer.isActive():
            self.timer.stop()
            self.refresh()
        if key == Qt.Key.Key_F1 and self.editor.text().startswith('='):
            self.open_library()
            return True
        if key == Qt.Key.Key_Escape and self.panel.isVisible():
            self.suppressed = True
            self.hide()
            return True
        if self.panel.isVisible() and self.list.count():
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and mods == Qt.KeyboardModifier.NoModifier:
                delta = 1 if key == Qt.Key.Key_Down else -1
                self.list.setCurrentRow((self.list.currentRow() + delta) % self.list.count())
                return True
            if key == Qt.Key.Key_Tab and mods == Qt.KeyboardModifier.NoModifier:
                self.accept_completion()
                return True
        return False

    def hide(self):
        self.panel.hide()

    def clear(self):
        self.timer.stop()
        self.hide()
        self.view._formula_reference_spans = []
        self.view.viewport().update()

    def eventFilter(self, obj, event):
        if obj is not self.editor:
            if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
                self.hide()
            elif event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.WindowActivate):
                self.timer.start()
            return False
        if event.type() == QEvent.Type.FocusIn:
            self.timer.start()
        elif event.type() == QEvent.Type.InputMethod:
            self.composing = bool(event.preeditString())
            if self.composing:
                self.hide()
            else:
                self.timer.start()
        elif event.type() == QEvent.Type.Hide:
            self.clear()
        elif event.type() in (QEvent.Type.Move, QEvent.Type.Resize) and self.panel.isVisible():
            self.position()
        elif event.type() == QEvent.Type.FocusOut:
            # The panel has no focusable controls. Library dialogs deliberately
            # suspend the delegate's FocusOut commit below.
            self.hide()
            self.view._formula_reference_spans = []
            self.view.viewport().update()
        return False


class FormulaPreviewEdit(QPlainTextEdit):
    """The long-text preview uses the same catalogue and cursor analysis.

    Plain text keeps its existing delayed save behavior. Formula drafts are only
    committed on Enter or context change, so typing never stores half a formula.
    """
    def focusInEvent(self, event):
        super().focusInEvent(event)
        workbench = getattr(self, 'workbench', None)
        if workbench:
            workbench.take_for_preview()

    def text(self):
        return self.toPlainText()

    def cursorPosition(self):
        return self.textCursor().position()

    def setCursorPosition(self, position):
        cursor = self.textCursor()
        cursor.setPosition(position)
        self.setTextCursor(cursor)

    def setSelection(self, start, length):
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def hasSelectedText(self):
        return self.textCursor().hasSelection()

    def insert(self, text):
        self.insertPlainText(text)

    def keyPressEvent(self, event):
        helper = getattr(self, 'formula_assistant', None)
        if helper and helper.composing:
            return super().keyPressEvent(event)
        if helper and helper.handle_key(event):
            return
        if self.text().startswith('=') and event.key() == Qt.Key.Key_F4:
            self.workbench.window.table.toggle_absolute_ref(self)
            return
        if self.text().startswith('=') and event.key() == Qt.Key.Key_Escape:
            self.workbench.window._cancel_preview_formula()
            return
        if self.text().startswith('=') and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if helper.validate_commit():
                    self.workbench.window._save_cell_preview()
                return
            # Formulas are single expressions; Enter validates and saves them.
            if helper.validate_commit():
                self.workbench.window._save_cell_preview()
            return
        super().keyPressEvent(event)
