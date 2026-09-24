# -*- coding: utf-8 -*-
"""首次启动的互动式新手教程（聚光灯引导）。

在主窗口上盖一层半透明遮罩，把当前要讲的控件"挖亮"，旁边挂一张说明卡片。
关键步骤要求用户真的做一下（点筛选箭头、拖动列、写公式、右键…），做到了自动
进下一步；做不到也能用「跳过此步」继续，绝不把人卡住。

设计要点（沿用 Stellar Smart Terminal 的同名模块）：
- 遮罩是 MainWindow 的直接子控件，用 setMask 把"洞"抠出去，洞里的点击直接
  落到底下的真实控件上——这就是"互动"的实现方式，不需要转发事件。
- 每 150ms 轮询一次：目标位置变了就重新排版，互动条件满足了就打勾并自动前进。
- 当前是空白新表时载入一份示例数据供练手，教程结束时换回空白表；用户自己的
  文件上，会改数据的步骤（拖列、写公式）只讲解不要求操作——绝不诱导改用户数据。
- 看完或退出都写 onboarding/shown，之后从「帮助 › 新手教程」重看。
"""

from typing import Callable, Optional

import numpy as np
import pandas as pd
from PyQt6.QtCore import Qt, QEvent, QObject, QPoint, QRect, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget,
)

from qtui.i18n import tr

SETTINGS_KEY = "onboarding/shown"
ACCENT = QColor(230, 126, 34)      # 与表头行同款橙色


def should_show_onboarding(settings) -> bool:
    """首次启动判断：设置里没有"已看过"标记就弹（老用户升级后也弹一次）。"""
    try:
        return not settings.value(SETTINGS_KEY, False, type=bool)
    except Exception:                        # noqa: BLE001
        return True


def mark_onboarding_shown(settings) -> None:
    settings.setValue(SETTINGS_KEY, True)


def sample_dataframe() -> pd.DataFrame:
    """教程练手用的示例表：「金额」列留空，让用户自己写公式。"""
    return pd.DataFrame({
        "日期": ["2026-09-01", "2026-09-01", "2026-09-02", "2026-09-02",
               "2026-09-03", "2026-09-03", "2026-09-04", "2026-09-04"],
        "城市": ["上海", "北京", "上海", "广州", "北京", "上海", "广州", "北京"],
        "品类": ["饮料", "零食", "零食", "饮料", "饮料", "日用", "零食", "日用"],
        "销量": [120, 80, 95, 60, 150, 40, 70, 55],
        "单价": [3.5, 12.0, 8.5, 4.0, 3.5, 25.0, 9.0, 18.0],
        "金额": [np.nan] * 8,
        "备注": ["周末促销，货架补满两次", "", "新品试吃，反馈不错", "",
               "天热，冷柜饮料卖得快", "", "", "会员日"],
    })


# ---------------------------------------------------------------------------
# 步骤定义
# ---------------------------------------------------------------------------

class TourStep:
    """一步教程。

    targets: 目标列表，元素为 MainWindow 属性名 / _toolbar_buttons 里的按钮名 /
             可调用对象 (win -> QWidget 或窗口坐标 QRect)；空 = 卡片居中
    done_when: 互动完成条件 (win -> bool)；None = 非互动步骤
    edits_data: 完成这一步要改表格数据——只在示例数据上要求操作
    optional_target: 目标不可见时照常显示（卡片居中），而不是跳过整步
    """

    def __init__(self, key, title, body, hint="", targets=(),
                 done_when: Optional[Callable] = None, edits_data=False,
                 optional_target=False):
        self.key = key
        self.title = title
        self.body = body
        self.hint = hint
        self.targets = list(targets)
        self.done_when = done_when
        self.edits_data = edits_data
        self.optional_target = optional_target


def _visible_filter_popup(win) -> bool:
    from qtui.header_filter import ColumnFilterPopup
    for w in QApplication.topLevelWidgets():
        try:
            if isinstance(w, ColumnFilterPopup) and w.isVisible():
                return True
        except RuntimeError:
            continue
    return False


def _context_menu_open(win) -> bool:
    """本窗口的右键菜单开着没有（menu.exec 的嵌套循环里轮询照常触发）。"""
    popup = QApplication.activePopupWidget()
    try:
        parent = popup.parentWidget() if isinstance(popup, QMenu) else None
        return parent is not None and parent.window() is win
    except RuntimeError:
        return False


def _header_row_rect(win) -> Optional[QRect]:
    """列名行（视图第 0 行）在窗口坐标下的矩形。"""
    table = win.table
    vp = table.viewport()
    if table.model() is None or table.model().rowCount() == 0:
        return None
    y = table.rowViewportPosition(0)
    h = table.rowHeight(0)
    if y < 0 or h <= 0:
        return None
    # 只框到最后一列为止，表格右侧的空白不算列名行
    header = table.horizontalHeader()
    last = header.count() - 1
    right = header.sectionViewportPosition(last) + header.sectionSize(last) if last >= 0 else 0
    width = min(vp.width(), right) if right > 0 else vp.width()
    top_left = vp.mapTo(win, QPoint(0, y))
    return QRect(top_left, QSize(width, h))


class _Baseline:
    """"比进入这一步时多了/变了"这类判断需要记住的基线。"""

    def __init__(self):
        self.columns = ()
        self.formulas = 0


def _columns(win):
    return tuple(map(str, win.model.df.columns))


def build_steps(baseline: _Baseline) -> list:
    return [
        TourStep(
            "welcome", tr("欢迎使用 Smart Table Hub"),
            tr("花两分钟手把手认识一遍主要功能。每一步都可以直接上手试，也可以随时退出；"
               "以后从「帮助 › 新手教程」能再看一遍。")),
        TourStep(
            "files", tr("打开与保存"),
            tr("新建、打开 Excel / CSV、保存都在这里。也可以直接把文件拖进窗口打开。"
               "勾选「自动保存」后，改动会在 30 秒后自动写回文件。"),
            targets=["新建", "打开", "保存", "保存为", "关闭文件"]),
        TourStep(
            "header_row", tr("橙色这一行是列名"),
            tr("和 Excel 一样，最上面的字母是列坐标（A、B、C…），橙色这一行是列名。"
               "双击列名可以直接改名；公式里第 2 行起才是数据（如 D2）。"),
            targets=[_header_row_rect]),
        TourStep(
            "filter", tr("点列名右侧的 ▼ 筛选"),
            tr("每个列名右侧都有个小箭头：勾选要保留的值、搜索、升序/降序，"
               "或点「更多条件」按大于/包含/为空等条件筛选。筛选后表格上方会出现条件标签，"
               "点标签上的 × 即可去掉。"),
            hint=tr("点任意列名右侧的 ▼ 试试"),
            targets=[_header_row_rect], done_when=_visible_filter_popup),
        TourStep(
            "move_column", tr("拖动列头调整列顺序"),
            tr("先点字母列头选中整列（按住 Shift 可连选多列），再按住选中的列头左右拖，"
               "蓝线处就是落点。公式引用、背景色、列宽都会跟着走，⌘Z 可撤销。"),
            hint=tr("点字母 B 选中「城市」列，再按住它拖到别处"),
            targets=["table.horizontalHeader"],
            done_when=lambda w: (_columns(w) != baseline.columns
                                 and sorted(_columns(w)) == sorted(baseline.columns)),
            edits_data=True),
        TourStep(
            "formula", tr("写公式"),
            tr("以 = 开头就是公式，支持 SUM、AVERAGE、IF、VLOOKUP 等常用函数。"
               "输入时直接点别的单元格可插入引用；拖选区右下角的小方块能把公式填充下去，"
               "⌘D 向下填充、⌥= 自动求和。"),
            hint=tr("在「金额」列第一格输入 =D2*E2 并回车（列被拖动过就按新位置写）"),
            targets=["table"],
            done_when=lambda w: len(w.model.formulas) > baseline.formulas,
            edits_data=True),
        TourStep(
            "context_menu", tr("右键菜单里功能最全"),
            tr("在单元格、字母列头、行号上点右键：插入/删除行列、设置背景色、按此列排序、"
               "把图片路径列设为「图片列」在右侧预览图片等，都在这里。"),
            hint=tr("在表格里点一下右键"),
            targets=["table"], done_when=_context_menu_open),
        TourStep(
            "data_tools", tr("排序、筛选与统计"),
            tr("「排序」支持多列组合排序；「筛选」是按条件的高级筛选；"
               "「统计」给出选中列的描述性统计。菜单「统计」里还有求和、平均值等快捷计算。"),
            targets=["插入行", "删除行", "排序", "筛选", "统计"]),
        TourStep(
            "cell_preview", tr("单元格内容面板"),
            tr("长文本在格子里显示不全？选中单元格，这里就能看到完整内容，并且可以直接编辑。"
               "面板可以拖到别处，也能在「视图」菜单里关掉或重新打开。"),
            targets=["cell_preview_dock"]),
        TourStep(
            "sheets", tr("底部的 Sheet 标签"),
            tr("打开多 Sheet 的 Excel 时，这里点击切换、双击重命名、拖动排序、右键删除或复制，"
               "「+」新建 Sheet。⌘PgUp / ⌘PgDn 也能切换。"),
            targets=["sheet_tabs", "sheet_add_btn"]),
        TourStep(
            "python", tr("用 Python 分析数据"),
            tr("菜单「分析 › Python数据分析」打开分析窗口：当前表就是变量 df，"
               "可以用 pandas 写任意分析，结果能回写成新 Sheet。"
               "「视图 › Python数据分析工具栏」还能打开一行快速执行的工具栏。"),
            targets=["python_toolbar"], optional_target=True),
        TourStep(
            "shortcuts", tr("几个好用的快捷键"),
            tr("⌘F 查找替换　　⌘G 定位单元格\n"
               "⌘Z 撤销　　⇧⌘Z 重做\n"
               "⌘D 向下填充　　⌘R 向右填充\n"
               "⌥= 自动求和　　⌘; 插入日期\n"
               "⇧⌘= / ⇧⌘- 按选区插入 / 删除行列\n"
               "⇧⌘L 清除全部筛选　　⌘+ / ⌘- 缩放")),
        TourStep(
            "done", tr("准备好了"),
            tr("教程到此结束。示例数据会在退出教程时清掉，不会留在你的文件里。"
               "随时可以从「帮助 › 新手教程」再看一遍。")),
    ]


# ---------------------------------------------------------------------------
# 遮罩 + 卡片
# ---------------------------------------------------------------------------

_HOLE_PAD = 4
_HOLE_RADIUS = 6
_CARD_WIDTH = 380
_CARD_GAP = 14


class OnboardingOverlay(QWidget):
    """盖在主窗口上的聚光灯遮罩，自带说明卡片。"""

    next_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    skip_requested = pyqtSignal()

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setObjectName("onboardingOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hole: Optional[QRect] = None
        self._build_card()
        self.apply_palette()

    def _build_card(self):
        self.card = QFrame(self)
        self.card.setObjectName("onboardingCard")
        self.card.setFixedWidth(_CARD_WIDTH)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)
        self.counter_label = QLabel(self.card)
        self.counter_label.setObjectName("onboardingCounter")
        self.title_label = QLabel(self.card)
        self.title_label.setObjectName("onboardingTitle")
        self.title_label.setWordWrap(True)
        self.body_label = QLabel(self.card)
        self.body_label.setObjectName("onboardingBody")
        self.body_label.setWordWrap(True)
        self.body_label.setTextFormat(Qt.TextFormat.PlainText)
        self.hint_label = QLabel(self.card)
        self.hint_label.setObjectName("onboardingHint")
        self.hint_label.setWordWrap(True)
        self.hint_label.hide()

        row = QHBoxLayout()
        row.setSpacing(8)
        self.skip_btn = QPushButton(self.card)
        self.skip_btn.setObjectName("onboardingSkip")
        self.skip_btn.setFlat(True)
        self.skip_btn.clicked.connect(self.skip_requested.emit)
        self.prev_btn = QPushButton(self.card)
        self.prev_btn.clicked.connect(self.prev_requested.emit)
        self.next_btn = QPushButton(self.card)
        self.next_btn.setObjectName("onboardingNext")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self.next_requested.emit)
        for b in (self.skip_btn, self.prev_btn, self.next_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 回车/方向键交给遮罩处理
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.prev_btn)
        row.addWidget(self.next_btn)

        lay.addWidget(self.counter_label)
        lay.addWidget(self.title_label)
        lay.addWidget(self.body_label)
        lay.addWidget(self.hint_label)
        lay.addSpacing(4)
        lay.addLayout(row)

    def apply_palette(self):
        """颜色取自系统调色板，浅色/深色模式都适用；强调色用表头橙。"""
        pal = self.palette()
        dark = pal.window().color().lightness() < 128
        self._dim = QColor(0, 0, 0, 160 if dark else 110)
        accent = ACCENT.name()
        self.card.setStyleSheet(f"""
            QFrame#onboardingCard {{
                background-color: palette(window);
                border: 1px solid palette(mid);
                border-radius: 12px;
            }}
            QLabel {{ background: transparent; border: none; color: palette(text); }}
            QLabel#onboardingCounter {{ color: palette(placeholder-text); font-size: 11px; }}
            QLabel#onboardingTitle {{ font-size: 16px; font-weight: 600; }}
            QLabel#onboardingBody {{ font-size: 13px; }}
            QLabel#onboardingHint {{
                color: {accent}; font-size: 13px; font-weight: 600;
                padding: 6px 8px; border-radius: 6px; background-color: palette(base);
            }}
            QPushButton {{
                padding: 5px 14px; border-radius: 6px; font-size: 13px;
                border: 1px solid palette(mid); background-color: palette(button);
                color: palette(button-text);
            }}
            QPushButton#onboardingNext {{
                background-color: {accent}; color: #ffffff; border: none; font-weight: 600;
            }}
            QPushButton#onboardingNext:hover {{ background-color: {ACCENT.lighter(112).name()}; }}
            QPushButton#onboardingSkip {{
                background: transparent; border: none; padding: 5px 4px;
                color: palette(placeholder-text);
            }}
            QPushButton#onboardingSkip:hover {{ color: palette(text); }}
        """)
        self.update()

    def set_content(self, counter, title, body, hint, can_prev, next_text,
                    skip_text, prev_text):
        self.counter_label.setText(counter)
        self.title_label.setText(title)
        self.body_label.setText(body)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        self.prev_btn.setText(prev_text)
        self.prev_btn.setVisible(can_prev)
        self.next_btn.setText(next_text)
        self.skip_btn.setText(skip_text)

    def set_hole(self, rect: Optional[QRect]):
        """rect 为窗口坐标系下的目标区域（None = 无目标，卡片居中）。"""
        self._hole = (rect.adjusted(-_HOLE_PAD, -_HOLE_PAD, _HOLE_PAD, _HOLE_PAD)
                      if rect is not None else None)
        self._relayout()

    def hole(self) -> Optional[QRect]:
        return QRect(self._hole) if self._hole is not None else None

    def _relayout(self):
        full = self.rect()
        self._place_card()
        if self._hole is not None:
            # 卡片可能落在洞里（目标是整张表时）：mask 要把卡片加回来，
            # 否则卡片被裁掉、点击也会穿透到底下
            mask = QRegion(full) - QRegion(self._hole.intersected(full))
            self.setMask(mask.united(QRegion(self.card.geometry())))
        else:
            self.clearMask()
        self.update()

    def _place_card(self):
        cw = self.card.width()
        # 高度按固定宽度下换行后的实际高度算：换行 QLabel 的 sizeHint 按启发式宽度估，
        # 正文一长卡片就矮了、上下截字
        ch = max(self.card.sizeHint().height(),
                 self.card.layout().totalHeightForWidth(cw))
        self.card.resize(cw, ch)
        full = self.rect()
        hole = self._hole
        if hole is None:
            self.card.move(max(0, (full.width() - cw) // 2),
                           max(0, (full.height() - ch) // 2))
            return
        if hole.width() * hole.height() > full.width() * full.height() * 0.4:
            # 目标是整张表：卡片放在洞内右下角，尽量少挡左上方要操作的单元格
            x = hole.right() - cw - 24
            y = hole.bottom() - ch - 24
        else:
            # 优先放在洞下方，放不下放上方，再不行放左右；水平方向对齐洞的左边
            x = hole.left()
            if hole.bottom() + _CARD_GAP + ch <= full.height():
                y = hole.bottom() + _CARD_GAP
            elif hole.top() - _CARD_GAP - ch >= 0:
                y = hole.top() - _CARD_GAP - ch
            else:
                y = max(0, min(hole.top(), full.height() - ch))
                if hole.right() + _CARD_GAP + cw <= full.width():
                    x = hole.right() + _CARD_GAP
                else:
                    x = hole.left() - _CARD_GAP - cw
        x = max(8, min(x, full.width() - cw - 8))
        y = max(8, min(y, full.height() - ch - 8))
        self.card.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self.rect()
        if self._hole is None:
            painter.fillRect(full, self._dim)
            return
        hole = self._hole
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(float(full.x()), float(full.y()),
                     float(full.width()), float(full.height()))
        path.addRoundedRect(float(hole.x()), float(hole.y()), float(hole.width()),
                            float(hole.height()), _HOLE_RADIUS, _HOLE_RADIUS)
        painter.fillPath(path, self._dim)
        # 高亮环画在洞的外沿（洞内被 mask 裁掉，画进去也看不见）
        painter.setPen(QPen(ACCENT, 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(hole.adjusted(-2, -2, 2, 2),
                                _HOLE_RADIUS + 2, _HOLE_RADIUS + 2)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.skip_requested.emit()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.next_requested.emit()
        elif key == Qt.Key.Key_Left:
            self.prev_requested.emit()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        # 点遮罩（洞外、卡片外）什么都不做，但吃掉事件以免穿透到底下控件
        event.accept()


# ---------------------------------------------------------------------------
# 控制器
# ---------------------------------------------------------------------------

class OnboardingTour(QObject):
    """驱动教程步骤：定位目标、轮询互动条件、翻页、收尾。"""

    finished = pyqtSignal()

    POLL_MS = 150
    ADVANCE_DELAY_MS = 700

    def __init__(self, window, settings=None):
        super().__init__(window)
        self.window = window
        self._settings = settings
        self._baseline = _Baseline()
        self.steps = build_steps(self._baseline)
        self.index = -1
        self._done_flag = False
        self._finished = False
        self._last_rect = None
        self.sample_loaded = False
        self.overlay = OnboardingOverlay(window)
        self.overlay.hide()
        self.overlay.next_requested.connect(self.next)
        self.overlay.prev_requested.connect(self.prev)
        self.overlay.skip_requested.connect(self.skip)
        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._tick)
        self._advance_timer = QTimer(self)
        self._advance_timer.setSingleShot(True)
        self._advance_timer.timeout.connect(self._advance_after_done)
        window.installEventFilter(self)

    # ----- 对外 -----

    def start(self):
        self._maybe_load_sample()
        self.overlay.setGeometry(self.window.rect())
        self.overlay.show()
        self.overlay.raise_()
        self.overlay.setFocus()
        self._poll.start()
        self._goto(0)

    def next(self):
        self._advance_timer.stop()
        self._goto(self.index + 1)

    def prev(self):
        self._advance_timer.stop()
        self._goto(self.index - 1, backwards=True)

    def skip(self):
        self._finish()

    def is_active(self) -> bool:
        return self.index >= 0 and not self._finished

    @property
    def current_step(self) -> Optional[TourStep]:
        if 0 <= self.index < len(self.steps):
            return self.steps[self.index]
        return None

    def is_interactive(self, step: TourStep) -> bool:
        """会改数据的步骤只在示例数据上要求操作，用户自己的表只讲解。"""
        if step.done_when is None:
            return False
        return self.sample_loaded or not step.edits_data

    # ----- 示例数据 -----

    def _is_blank_document(self) -> bool:
        win = self.window
        return (win.current_file is None and not win.model.modified
                and not win.sheet_names and win.model.df.isna().all().all())

    def _maybe_load_sample(self):
        if not self._is_blank_document():
            return
        win = self.window
        win.model.set_dataframe(sample_dataframe())
        win.model.modified = False
        # 按内容自适应会把两字列名挤成"…"（列名行还要放筛选箭头），直接给足宽度
        header = win.table.horizontalHeader()
        for col, name in enumerate(win.model.df.columns):
            header.resizeSection(col, 260 if name == "备注" else 110)
        win._update_title()
        win.update_statusbar(tr("已载入教程示例数据（退出教程时清除）"))
        self.sample_loaded = True

    def _unload_sample(self):
        """示例数据仍是当前文档（没被另存、没换成别的文件）就换回空白表。"""
        win = self.window
        if self.sample_loaded and win.current_file is None:
            win.new_file(confirm=False)
        self.sample_loaded = False

    # ----- 步骤跳转 -----

    def _goto(self, index, backwards=False):
        self._done_flag = False
        if index >= len(self.steps):
            self._finish()
            return
        index = max(index, 0)
        # 目标全不可见的步骤直接跳过（面板可能被用户关掉）
        direction = -1 if backwards else 1
        while 0 <= index < len(self.steps):
            step = self.steps[index]
            if (not step.targets or step.optional_target
                    or self._target_rect(step) is not None):
                break
            index += direction
        if index >= len(self.steps):
            self._finish()
            return
        self.index = max(index, 0)
        self._baseline.columns = _columns(self.window)
        self._baseline.formulas = len(self.window.model.formulas)
        self._render()
        self.overlay.raise_()
        self.overlay.setFocus()

    def _render(self):
        step = self.current_step
        if step is None:
            return
        total = len(self.steps)
        interactive = self.is_interactive(step)
        hint = ""
        if interactive:
            hint = tr("✓ 做得好，马上进入下一步") if self._done_flag else step.hint
        if self.index == total - 1:
            next_text = tr("完成")
        elif interactive and not self._done_flag:
            next_text = tr("跳过此步")
        else:
            next_text = tr("下一步")
        self.overlay.set_content(
            counter=tr("第 {} / {} 步").format(self.index + 1, total),
            title=step.title, body=step.body, hint=hint,
            can_prev=self.index > 0, next_text=next_text,
            skip_text=tr("退出教程"), prev_text=tr("上一步"))
        rect = self._target_rect(step) if step.targets else None
        self._last_rect = rect
        self.overlay.set_hole(rect)

    # ----- 目标定位 -----

    def _resolve(self, target):
        win = self.window
        if callable(target):
            return target(win)
        registry = getattr(win, "_toolbar_buttons", {}) or {}
        if target in registry:
            return registry[target]
        obj = win
        for part in target.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                return None
        return obj() if callable(obj) and not isinstance(obj, QWidget) else obj

    def _target_rect(self, step) -> Optional[QRect]:
        union = None
        for target in step.targets:
            try:
                obj = self._resolve(target)
                if isinstance(obj, QWidget):
                    if not obj.isVisible():
                        continue
                    r = QRect(obj.mapTo(self.window, QPoint(0, 0)), obj.size())
                elif isinstance(obj, QRect):
                    r = obj
                else:
                    continue
            except RuntimeError:
                continue
            if r.width() <= 0 or r.height() <= 0:
                continue
            union = r if union is None else union.united(r)
        return union

    # ----- 轮询 -----

    def _tick(self):
        step = self.current_step
        if step is None:
            return
        try:
            if self.overlay.geometry() != self.window.rect():
                self.overlay.setGeometry(self.window.rect())
            if step.targets:
                rect = self._target_rect(step)
                if rect != self._last_rect:
                    self._last_rect = rect
                    self.overlay.set_hole(rect)
            if (self.is_interactive(step) and not self._done_flag
                    and step.done_when(self.window)):
                self._done_flag = True
                self._render()
                self._advance_timer.start(self.ADVANCE_DELAY_MS)
        except RuntimeError:
            self._poll.stop()        # 窗口/控件已销毁

    def _advance_after_done(self):
        if self.is_active() and self._done_flag:
            self.next()

    # ----- 收尾 -----

    def _finish(self):
        if self._finished:
            return
        self._finished = True
        self._poll.stop()
        self._advance_timer.stop()
        self.index = -1
        try:
            self.window.removeEventFilter(self)
            self.overlay.hide()
            self.overlay.deleteLater()
        except RuntimeError:
            pass
        try:
            self._unload_sample()
        except Exception:                    # noqa: BLE001
            pass
        if self._settings is not None:
            try:
                mark_onboarding_shown(self._settings)
            except Exception:                # noqa: BLE001
                pass
        self.finished.emit()

    def eventFilter(self, obj, event):
        if obj is self.window and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            if self.is_active():
                try:
                    self.overlay.setGeometry(self.window.rect())
                    self.overlay.raise_()
                except RuntimeError:
                    pass
        return False
