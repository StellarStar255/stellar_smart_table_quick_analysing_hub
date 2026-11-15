"""Formula bar, non-mutating result previews, argument wizard and draft decisions."""
from PyQt6.QtCore import (QObject, Qt, QTimer, QPersistentModelIndex, QModelIndex,
                          QRunnable, QThreadPool, pyqtSignal)
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QDialog, QComboBox, QScrollArea, QFormLayout, QDialogButtonBox,
    QMessageBox, QAbstractItemDelegate)
from core.formula_engine import FormulaEngine
from core.formula_help import (FUNCTIONS, PARAMS, local, diagnose, calls,
                               active_call, error_help)
from .formula_assistant import FormulaAssistant, spec_html


def address(index):
    return FormulaEngine.col_index_to_letter(index.column()) + str(index.row() + 1)


class FormulaBarEdit(QLineEdit):
    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.workbench.take_cell_editor()

    def keyPressEvent(self, event):
        helper = self.formula_assistant
        if helper.composing:
            return super().keyPressEvent(event)
        if helper.handle_key(event):
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.workbench.commit_bar()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.workbench.cancel_bar()
            return
        if event.key() == Qt.Key.Key_F4 and self.text().startswith('='):
            self.workbench.window.table.toggle_absolute_ref(self)
            return
        super().keyPressEvent(event)


class _PreviewSignals(QObject):
    done = pyqtSignal(int, object)


class _PreviewJob(QRunnable):
    def __init__(self, generation, text, frame):
        super().__init__()
        self.generation, self.text, self.frame = generation, text, frame
        self.signals = _PreviewSignals()

    def run(self):
        try:
            value = FormulaEngine(self.frame).evaluate(self.text)
        except Exception:
            value = '#ERROR'
        self.signals.done.emit(self.generation, value)


class ArgumentWizard(QDialog):
    """Edits argument expressions. Picking a range never changes the source cell."""
    pickRequested = pyqtSignal(object)

    def __init__(self, parent=None, name='SUM', arguments=()):
        super().__init__(parent)
        self.setWindowTitle(local('函数参数向导', 'Function Arguments'))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(650, 560)
        self.selected_formula = None
        self.fields = []
        layout = QVBoxLayout(self)
        self.function = QComboBox()
        for spec in sorted(FUNCTIONS.values(), key=lambda s: s.name):
            self.function.addItem(spec.name + ' — ' + spec.description, spec.name)
        layout.addWidget(self.function)
        self.description = QLabel()
        self.description.setTextFormat(Qt.TextFormat.RichText)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.add = QPushButton(local('添加参数', 'Add arguments'))
        self.remove = QPushButton(local('移除最后一组', 'Remove last group'))
        self.add.clicked.connect(self.add_group)
        self.remove.clicked.connect(self.remove_group)
        row.addWidget(self.add); row.addWidget(self.remove); row.addStretch()
        layout.addLayout(row)
        self.formula = QLineEdit()
        self.formula.setReadOnly(True)
        self.formula.setAccessibleName(local('生成的公式', 'Generated formula'))
        layout.addWidget(self.formula)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.problem)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(local('填入公式', 'Use formula'))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(local('取消', 'Cancel'))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.function.currentIndexChanged.connect(self.rebuild)
        self.function.setCurrentIndex(max(0, self.function.findData(name)))
        self.rebuild()
        while len(self.fields) < len(arguments) and self.spec.repeat:
            self.add_group()
        for field, value in zip(self.fields, arguments):
            field.setText(value)
        self.refresh()

    @property
    def spec(self):
        return FUNCTIONS[self.function.currentData()]

    def rebuild(self, *_):
        previous = self.scroll.takeWidget()
        if previous:
            previous.deleteLater()
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.scroll.setWidget(self.body)
        self.fields = []
        self.description.setText(spec_html(self.spec))
        for _ in self.spec.params:
            self.add_field()
        self.add.setVisible(bool(self.spec.repeat))
        self.remove.setVisible(bool(self.spec.repeat))
        self.refresh()

    def add_field(self):
        i = len(self.fields)
        param = PARAMS[self.spec.parameter_key(i)]
        body = QWidget()
        layout = QVBoxLayout(body); layout.setContentsMargins(0, 0, 0, 4)
        row = QHBoxLayout()
        field = QLineEdit()
        field.setAccessibleName(local(*param[:2]) + f' {i + 1}')
        field.setPlaceholderText(local('输入值、文本或区域，如 B2:B10', 'Value, quoted text or range, e.g. B2:B10'))
        pick = QPushButton(local('选取区域', 'Pick range'))
        pick.clicked.connect(lambda _, f=field: self.pickRequested.emit(f))
        row.addWidget(field, 1); row.addWidget(pick)
        layout.addLayout(row)
        help_label = QLabel(local(*param[2:])); help_label.setWordWrap(True)
        layout.addWidget(help_label)
        label = local(*param[:2])
        if i >= self.spec.minimum:
            label += local('（可选）', ' (optional)')
        self.form.addRow(label, body)
        self.fields.append(field)
        field.textChanged.connect(self.refresh)

    def add_group(self):
        for _ in range(self.spec.repeat):
            self.add_field()
        self.refresh()

    def remove_group(self):
        count = self.spec.repeat
        if len(self.fields) > len(self.spec.params):
            for _ in range(count):
                self.fields.pop()
                self.form.removeRow(self.form.rowCount() - 1)
        self.refresh()

    def refresh(self, *_):
        values = [f.text().strip() for f in self.fields]
        while len(values) > self.spec.minimum and values and not values[-1]:
            values.pop()
        text = '=' + self.spec.name + '(' + ','.join(values) + ')'
        self.formula.setText(text)
        problem = diagnose(text, final=True)
        self.problem.setText(problem.message if problem else local('填入后可预览结果；确认提交才会修改单元格。', 'Preview after inserting; the cell changes only when you commit.'))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(problem is None)
        self.remove.setEnabled(len(self.fields) > len(self.spec.params))

    def accept(self):
        if diagnose(self.formula.text(), final=True) is None:
            self.selected_formula = self.formula.text()
            super().accept()


class FormulaWorkbench(QObject):
    AUTO_PREVIEW_CELLS = 10000

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.loading = False
        self.dirty = False
        self.cell = QPersistentModelIndex()
        self.origin = QPersistentModelIndex()
        self._generation = 0
        self._job = None
        self._queued = None
        self._closed = False
        self.picking = None
        self.pick_anchor = None
        self.wizard = None
        self.widget = QWidget()
        layout = QVBoxLayout(self.widget); layout.setContentsMargins(8, 4, 8, 4); layout.setSpacing(3)
        row = QHBoxLayout()
        self.location = QLabel('—'); self.location.setMinimumWidth(50)
        self.edit = FormulaBarEdit(); self.edit.workbench = self
        self.edit.setMaxLength(2147483647)
        self.edit.setAccessibleName(local('公式栏', 'Formula bar'))
        self.edit.setPlaceholderText(local('输入 = 开始公式；Enter 确认，Esc 取消', 'Start a formula with =; Enter commits, Esc cancels'))
        self.edit.formula_assistant = FormulaAssistant(self.edit, window.table)
        self.confirm = QPushButton('✓'); self.confirm.setToolTip(local('确认提交', 'Commit'))
        self.cancel = QPushButton('✕'); self.cancel.setToolTip(local('取消草稿', 'Cancel draft'))
        self.arguments = QPushButton(local('参数向导', 'Arguments'))
        self.back = QPushButton('↩'); self.back.setToolTip(local('返回出错公式', 'Return to error formula'))
        for button in (self.confirm, self.cancel, self.arguments, self.back):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.location); row.addWidget(self.edit, 1)
        for button in (self.confirm, self.cancel, self.arguments, self.back): row.addWidget(button)
        layout.addLayout(row)
        status = QHBoxLayout()
        self.state = QLabel(); self.state.setTextFormat(Qt.TextFormat.PlainText)
        self.result = QLabel(); self.result.setWordWrap(True); self.result.setTextFormat(Qt.TextFormat.PlainText)
        self.calculate = QPushButton(local('计算预览', 'Calculate preview')); self.calculate.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        status.addWidget(self.state); status.addWidget(self.result, 1); status.addWidget(self.calculate)
        layout.addLayout(status)
        self.confirm.clicked.connect(self.commit_bar)
        self.cancel.clicked.connect(self.cancel_bar)
        self.arguments.clicked.connect(self.open_wizard)
        self.back.clicked.connect(self.return_to_error)
        self.calculate.clicked.connect(lambda: self.preview(force=True))
        self.edit.textChanged.connect(self.bar_changed)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(350)
        self.timer.timeout.connect(self.preview)
        window.model.dataChanged.connect(self.data_changed)
        window.model.modelReset.connect(self.reset)
        self.sync()

    def cell_editor_changed(self, editor):
        if not self.dirty:
            self.set_bar(editor.text())
        if hasattr(self.window, 'cell_preview_text'):
            self.window._preview_loading = True
            self.window.cell_preview_text.setPlainText(editor.text())
            self.window._preview_loading = False
        self.update_state()
        self.schedule()

    def native_editor(self):
        editor = self.window._cell_delegate.active_editor
        return editor if editor is not None and editor.isVisible() and hasattr(editor, 'formula_assistant') else None

    def take_cell_editor(self):
        editor = self.native_editor()
        if editor is None: return
        text = editor.text()
        self.cell = QPersistentModelIndex(editor._edit_index)
        self.window.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
        self.set_bar(text)
        original = self.window.model.data(QModelIndex(self.cell), Qt.ItemDataRole.EditRole) or ''
        self.dirty = text != original
        self.edit.setFocus()
        self.update_state(); self.schedule()

    def take_for_preview(self):
        editor = self.native_editor()
        if editor is None and not self.dirty:
            return
        text = editor.text() if editor is not None else self.edit.text()
        if editor is not None:
            self.window.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
        self.dirty = False
        current = self.window.table.currentIndex()
        if not current.isValid(): return
        self.window._preview_loading = True
        self.window.cell_preview_text.setPlainText(text)
        self.window._preview_loading = False
        self.window._preview_cell = (current.row(), current.column())
        original = self.window.model.data(current, Qt.ItemDataRole.EditRole) or ''
        changed = text != original
        self.window._preview_formula_pending = changed and text.startswith('=')
        if changed and not text.startswith('='):
            self.window._preview_save_timer.start()
        else:
            self.window._preview_save_timer.stop()
        self.set_bar(text)
        self.update_state(); self.schedule()

    def set_bar(self, text):
        self.loading = True
        self.edit.setText(text)
        self.loading = False

    def sync(self):
        if self._closed: return
        idx = self.window.table.currentIndex()
        if not self.dirty:
            self.cell = QPersistentModelIndex(idx)
            self.set_bar(self.window.model.data(idx, Qt.ItemDataRole.EditRole) or '' if idx.isValid() else '')
        self.location.setText(address(idx) if idx.isValid() else '—')
        self.edit.setEnabled(idx.isValid() and idx.row() > 0)
        self.arguments.setEnabled(idx.isValid() and idx.row() > 0)
        self.back.setEnabled(self.origin.isValid())
        self.update_state()
        self.schedule()

    def update_state(self):
        editor = self.native_editor()
        native_dirty = editor is not None and editor.text() != (self.window.model.data(QModelIndex(editor._edit_index), Qt.ItemDataRole.EditRole) or '')
        preview_timer = getattr(self.window, '_preview_save_timer', None)
        preview_dirty = getattr(self.window, '_preview_formula_pending', False) or bool(preview_timer and preview_timer.isActive())
        draft = self.dirty or native_dirty or preview_dirty
        self.state.setText(local('草稿未提交', 'Uncommitted draft') if draft else local('已提交', 'Committed'))
        self.confirm.setEnabled(draft); self.cancel.setEnabled(draft)

    def bar_changed(self, *_):
        if self.loading:
            return
        if not self.cell.isValid():
            self.cell = QPersistentModelIndex(self.window.table.currentIndex())
        if self.cell.isValid():
            original = self.window.model.data(QModelIndex(self.cell), Qt.ItemDataRole.EditRole) or ''
            self.dirty = self.edit.text() != original
        if hasattr(self.window, 'cell_preview_text') and getattr(self.window, '_preview_cell', None) == (self.cell.row(), self.cell.column()):
            self.window._preview_save_timer.stop()
            self.window._preview_formula_pending = False
            self.window._preview_loading = True
            self.window.cell_preview_text.setPlainText(self.edit.text())
            self.window._preview_loading = False
        self.update_state(); self.schedule()

    def commit_bar(self):
        editor = self.native_editor()
        if editor is not None:
            if not editor.formula_assistant.validate_commit(): return False
            self.window.table.commit_and_move(editor, 0, 0)
            self.sync()
            return True
        timer = getattr(self.window, '_preview_save_timer', None)
        if not self.dirty and (getattr(self.window, '_preview_formula_pending', False) or (timer and timer.isActive())):
            if not self.window.cell_preview_text.formula_assistant.validate_commit(): return False
            timer.stop()
            self.window._save_cell_preview(validate=False)
            self.sync()
            return True
        if not self.dirty:
            self.sync()
            return True
        if not self.cell.isValid() or not self.edit.formula_assistant.validate_commit():
            return False
        idx = QModelIndex(self.cell)
        text = self.edit.text()
        self.dirty = False
        self.window.model.setData(idx, text)
        self.sync()
        self.window._load_preview_cell(idx)
        return True

    def cancel_bar(self):
        editor = self.native_editor()
        if editor is not None:
            self.window.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
        timer = getattr(self.window, '_preview_save_timer', None)
        if getattr(self.window, '_preview_formula_pending', False) or (timer and timer.isActive()):
            self.window._cancel_preview_formula()
        self.dirty = False
        self.edit.formula_assistant.suppressed = True
        self.edit.formula_assistant.clear()
        self.sync()

    def ask_draft(self, text):
        """Only incomplete/invalid drafts ask; never change data implicitly."""
        box = QMessageBox(self.window)
        box.setWindowTitle(local('公式草稿尚未完成', 'Unfinished formula draft'))
        d = diagnose(text, final=True)
        box.setText(local('此公式尚未提交。继续操作前，请选择如何处理草稿。', 'This formula has not been committed. Choose how to handle the draft before continuing.'))
        box.setInformativeText((d.message + '\n' if d else '') + text[:300])
        keep = box.addButton(local('继续编辑', 'Keep editing'), QMessageBox.ButtonRole.RejectRole)
        discard = box.addButton(local('放弃草稿', 'Discard draft'), QMessageBox.ButtonRole.DestructiveRole)
        store = box.addButton(local('保留为错误公式', 'Store as error formula'), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(keep); box.setEscapeButton(keep)
        box.exec()
        return 'discard' if box.clickedButton() is discard else 'store' if box.clickedButton() is store else 'edit'

    def resolve_pending(self):
        editor = self.native_editor()
        if editor is not None:
            text = editor.text()
            choice = self.ask_draft(text) if text.startswith('=') and diagnose(text, final=True) else 'store'
            if choice == 'edit':
                editor.setFocus(); editor.formula_assistant.validate_commit()
                return False
            if choice == 'store':
                self.window.model.setData(QModelIndex(editor._edit_index), text)
            self.window.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
        if self.dirty:
            text = self.edit.text()
            choice = self.ask_draft(text) if text.startswith('=') and diagnose(text, final=True) else 'store'
            if choice == 'edit':
                self.edit.setFocus(); self.edit.formula_assistant.validate_commit()
                return False
            if choice == 'discard':
                self.cancel_bar()
            else:
                idx = QModelIndex(self.cell)
                self.dirty = False
                if idx.isValid(): self.window.model.setData(idx, text)
        if getattr(self.window, '_preview_formula_pending', False):
            text = self.window.cell_preview_text.toPlainText()
            choice = self.ask_draft(text) if diagnose(text, final=True) else 'store'
            if choice == 'edit':
                self.window.cell_preview_text.setFocus()
                self.window.cell_preview_text.formula_assistant.validate_commit()
                return False
            if choice == 'discard':
                self.window._cancel_preview_formula()
            else:
                self.window._save_cell_preview(validate=False)
        self.update_state()
        return True

    def source(self):
        editor = self.window._cell_delegate.active_editor
        if editor is not None and editor.isVisible(): return editor
        if getattr(self.window, '_preview_formula_pending', False): return self.window.cell_preview_text
        return self.edit

    def schedule(self, *_):
        self._generation += 1
        self.timer.start()

    def data_changed(self, *_):
        self._generation += 1
        if not self.dirty: self.sync()
        else: self.schedule()

    def reset(self, *_):
        self.dirty = False
        self.cell = QPersistentModelIndex()
        self.origin = QPersistentModelIndex()
        self._queued = None
        self.sync()

    def preview(self, force=False):
        self.timer.stop()
        if self._closed: return
        if self.window.table.currentIndex().row() < 1:
            self.result.clear(); self.calculate.hide()
            return
        source = self.source()
        text = source.text()
        self.calculate.hide()
        if not text.startswith('='):
            self.result.clear(); return
        diagnostic = diagnose(text, final=True)
        if diagnostic:
            self.result.setText(local('预览：', 'Preview: ') + diagnostic.message)
            return
        frame = self.window.model.df
        engine = FormulaEngine(frame)
        cells, ranges = engine.extract_dependency_spec(text)
        work = len(cells) + sum((b - a + 1) * (d - c + 1) for a, b, c, d in ranges)
        idx = self.window.table.currentIndex()
        own = (idx.row() - 1, idx.column())
        if own in cells or any(a <= own[0] <= b and c <= own[1] <= d for a, b, c, d in ranges):
            self.result.setText(local('预计结果：', 'Preview result: ') + '#CIRC! · ' + error_help('#CIRC!'))
            return
        if work > self.AUTO_PREVIEW_CELLS and not force:
            self.result.setText(local('引用范围较大，点击计算预览；输入期间不会反复重算。', 'Large reference range; click Calculate preview. Typing does not repeatedly recalculate.'))
            self.calculate.show(); return
        # Shallow snapshot preserves the frame's structure without copying the
        # entire workbook. Readers never write. Any edit invalidates the result.
        request = (self._generation, text, frame.copy(deep=False))
        self.result.setText(local('正在预览…', 'Calculating preview…'))
        if self._job is not None:
            self._queued = request
        else:
            self.start_job(request)

    def start_job(self, request):
        self._job = _PreviewJob(*request)
        self._job.signals.done.connect(self.preview_done)
        QThreadPool.globalInstance().start(self._job)

    def preview_done(self, generation, value):
        self._job = None
        if not self._closed and generation == self._generation:
            if FormulaEngine.is_error(value):
                text = str(value) + ' · ' + error_help(value)
            else:
                text = str(value)
            self.result.setText(local('预计结果：', 'Preview result: ') + text[:300])
        request, self._queued = self._queued, None
        if request and not self._closed and request[0] == self._generation:
            self.start_job(request)

    def jump_error(self, target):
        row, col = map(int, target.split(','))
        if not self.resolve_pending(): return
        model = self.window.model
        if 0 <= row < model.rowCount() and 0 <= col < model.columnCount():
            if not self.origin.isValid(): self.origin = QPersistentModelIndex(self.window.table.currentIndex())
            idx = model.index(row, col)
            self.window.table.setCurrentIndex(idx)
            self.window.table.scrollTo(idx)
            self.window.table.setFocus()
            self.back.setEnabled(True)

    def return_to_error(self):
        if self.origin.isValid() and self.resolve_pending():
            idx = QModelIndex(self.origin)
            self.origin = QPersistentModelIndex()
            self.window.table.setCurrentIndex(idx)
            self.window.table.scrollTo(idx)
            self.back.setEnabled(False)

    def open_wizard(self):
        if self.wizard and self.wizard.isVisible():
            self.wizard.raise_(); return
        idx = self.window.table.currentIndex()
        if not idx.isValid() or idx.row() == 0: return
        source = self.source()
        text = source.text()
        cursor = len(text.encode('utf-16-le')[:source.cursorPosition() * 2].decode('utf-16-le', errors='ignore'))
        current = active_call(text, cursor)
        call = current[0] if current else next((c for c in calls(text) if c.start == 1 and c.close == len(text) - 1), None)
        name, values = 'SUM', []
        if call and call.name in FUNCTIONS:
            name = call.name
            boundaries = [call.open, *call.commas, call.close if call.close is not None else len(text)]
            values = [text[a + 1:b].strip() for a, b in zip(boundaries, boundaries[1:])]
        source._formula_library_open = True
        dialog = ArgumentWizard(self.window, name, values)
        self.wizard = dialog
        dialog.pickRequested.connect(self.start_pick)
        original_cell = QPersistentModelIndex(idx)
        original_structure = self.window.model.structure_version
        def finished(result):
            self.picking = None; self.pick_anchor = None
            self.window.table.viewport().unsetCursor()
            source._formula_library_open = False
            if (result == QDialog.DialogCode.Accepted and original_cell.isValid()
                    and self.window.model.structure_version == original_structure):
                expression = dialog.selected_formula[1:]
                if call:
                    a, b = call.start, call.close + 1 if call.close is not None else len(text)
                    generated = text[:a] + expression + text[b:]
                elif text.startswith('='):
                    generated = text[:cursor] + expression + text[cursor:]
                    if diagnose(generated, final=True):
                        generated = '=' + expression
                else:
                    generated = '=' + expression
                source.selectAll(); source.insert(generated)
            source.setFocus()
            self.wizard = None
            self.schedule()
        dialog.finished.connect(finished)
        dialog.show()

    def start_pick(self, field):
        self.picking, self.pick_anchor = field, None
        self.wizard.hide()
        self.window.table.setFocus()
        self.window.table.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.window.update_statusbar(local('拖选参数区域，松开鼠标返回向导；Esc 取消选取。', 'Drag to select a range; release to return to the wizard. Esc cancels picking.'))

    def pick(self, index, finish=False):
        if self.picking is None: return
        if not index.isValid():
            if finish: self.finish_pick()
            return
        if self.pick_anchor is None: self.pick_anchor = QPersistentModelIndex(index)
        start = QModelIndex(self.pick_anchor)
        r0, r1 = sorted((start.row(), index.row())); c0, c1 = sorted((start.column(), index.column()))
        ref = address(self.window.model.index(r0, c0))
        if (r0, c0) != (r1, c1): ref += ':' + address(self.window.model.index(r1, c1))
        self.picking.setText(ref)
        if finish: self.finish_pick()

    def finish_pick(self):
        self.picking = None; self.pick_anchor = None
        self.window.table.viewport().unsetCursor()
        if self.wizard:
            self.wizard.show(); self.wizard.raise_(); self.wizard.activateWindow()

    def shutdown(self):
        self._closed = True
        self.timer.stop(); self._generation += 1; self._queued = None
