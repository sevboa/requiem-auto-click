from __future__ import annotations

# pylint: disable=import-error,no-name-in-module,broad-exception-caught
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..utils.windows import find_hwnd_by_pid_and_exact_title, pid_exists


@dataclass(frozen=True)
class ClientItem:
    nickname: str
    login: str
    pid: int

    def label(self) -> str:
        nick = str(self.nickname or "").strip()
        lg = str(self.login or "").strip()
        if not nick:
            return "—"
        return f"{nick} ({lg})" if lg else nick


class DisassembleCellWidget(QFrame):
    """
    Визуальная ячейка 5x5:
    - клик ЛКМ: назначить значение (сколько раз разобрать)
    - клик ПКМ: очистить
    - значение показываем в правом верхнем углу
    """

    clicked = Signal(int, int, int)  # row, col, mouseButton(Qt.MouseButton)

    def __init__(self, *, row: int, col: int, cell_px: int, parent=None) -> None:
        super().__init__(parent)
        self._row = int(row)
        self._col = int(col)
        self._value: int | None = None

        self.setObjectName("DisassembleCellWidget")
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Plain)
        self.setFixedSize(int(cell_px), int(cell_px))
        self.setCursor(Qt.PointingHandCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(3, 3, 3, 3)
        root.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        top.addStretch(1)

        self.corner = QLabel("")
        self.corner.setObjectName("DisassembleCellCorner")
        f = QFont()
        f.setPointSize(7)
        f.setBold(True)
        self.corner.setFont(f)
        self.corner.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.corner.setMinimumWidth(18)
        top.addWidget(self.corner, 0, Qt.AlignRight | Qt.AlignTop)

        root.addLayout(top)
        root.addStretch(1)

        self._apply_style(selected=False)
        self._refresh_text()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        # PySide6 Qt.MouseButton не всегда приводится к int() напрямую,
        # поэтому берём `.value` (если есть) и только потом int().
        btn_i: int
        try:
            btn = event.button()
            btn_i = int(getattr(btn, "value", btn))
        except Exception:
            try:
                btn_i = int(getattr(Qt.MouseButton.LeftButton, "value", 1))
            except Exception:
                btn_i = 1
        self.clicked.emit(int(self._row), int(self._col), int(btn_i))
        super().mousePressEvent(event)

    def set_value(self, value: int | None) -> None:
        if value is None:
            self._value = None
        else:
            v = int(value)
            self._value = v if v > 0 else None
        self._refresh_text()
        self._apply_style(selected=self._value is not None)

    def get_value(self) -> int | None:
        return self._value

    def _refresh_text(self) -> None:
        if self._value is None:
            self.corner.setText("")
            self.setToolTip(f"Ячейка [{self._row + 1},{self._col + 1}]: не задано")
        else:
            self.corner.setText(f"x{int(self._value)}")
            self.setToolTip(
                f"Ячейка [{self._row + 1},{self._col + 1}]: разобрать {int(self._value)} раз(а)"
            )

    def _apply_style(self, *, selected: bool) -> None:
        if selected:
            self.setStyleSheet(
                """
                QFrame#DisassembleCellWidget {
                    background: #eef6ff;
                    border: 1px solid #1e88e5;
                    border-radius: 6px;
                }
                QLabel#DisassembleCellCorner {
                    color: #0d47a1;
                }
                """
            )
        else:
            self.setStyleSheet(
                """
                QFrame#DisassembleCellWidget {
                    background: #fafafa;
                    border: 1px solid #cfcfcf;
                    border-radius: 6px;
                }
                QLabel#DisassembleCellCorner {
                    color: #555;
                }
                """
            )


class DisassembleWidget(QWidget):
    """
    UI настройки разбора:
    - выбор рюкзака (1..8)
    - выбор количества разборов
    - матрица 5x5 (ячейки выбранного рюкзака)
    """

    config_changed = Signal()  # generic signal for future integrations
    start_disassemble_clicked = Signal()
    stop_disassemble_clicked = Signal()
    selected_nickname_changed = Signal(str)
    collapsed_mask_changed = Signal(int)

    def __init__(
        self,
        *,
        window_title: str,
        on_get_clients: Callable[[], list[ClientItem]],
        initial_selected_nickname: str = "",
        initial_collapsed_mask: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._window_title = str(window_title)
        self._on_get_clients = on_get_clients
        self._run_active: bool = False
        self._selected_target_value: int = 1
        self._preferred_nickname: str = str(initial_selected_nickname or "").strip()
        self._profile_loading: bool = False

        # values[backpack_index][row][col] -> int|None
        self._values: dict[int, list[list[int | None]]] = {
            bi: [[None for _ in range(5)] for _ in range(5)] for bi in range(8)
        }
        mask = int(initial_collapsed_mask or 0)
        self._collapsed: dict[int, bool] = {bi: bool(mask & (1 << bi)) for bi in range(8)}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Весь экран — в вертикальном скролле, чтобы при узком окне появлялась прокрутка.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("QScrollArea { border: 0px; background: transparent; }")
        root.addWidget(self._scroll, 1)

        content = QWidget()
        self._scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ---- Top row: client + target ----
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        g_client = QGroupBox("Клиент (ник)")
        v_client = QVBoxLayout(g_client)
        v_client.setContentsMargins(10, 10, 10, 10)
        v_client.setSpacing(6)

        row_client = QHBoxLayout()
        row_client.setSpacing(8)
        self.client_combo = QComboBox()
        self.client_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(12, 12)
        self._set_status_dot(active=False)

        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self._refresh_clients)

        row_client.addWidget(QLabel("Ник:"), 0)
        row_client.addWidget(self.client_combo, 1)
        row_client.addWidget(self.status_dot, 0)
        row_client.addWidget(self.refresh_btn, 0)
        v_client.addLayout(row_client)

        self.status_label = QLabel("Статус: —")
        self.status_label.setStyleSheet("color: #555;")
        self.status_label.setWordWrap(True)
        v_client.addWidget(self.status_label)

        top_row.addWidget(g_client, 2)

        g_target = QGroupBox("Назначение")
        v_target = QVBoxLayout(g_target)
        v_target.setContentsMargins(10, 10, 10, 10)
        v_target.setSpacing(6)

        row_target = QHBoxLayout()
        row_target.setSpacing(8)
        self.target_spin = QSpinBox()
        self.target_spin.setRange(0, 1000)
        self.target_spin.setValue(int(self._selected_target_value))
        self.target_spin.setFixedWidth(90)
        self.target_spin.setToolTip("Клик по ячейке назначит это значение. 0 = очистить.")
        row_target.addWidget(QLabel("Разобрать (раз):"), 0)
        row_target.addWidget(self.target_spin, 0)

        self.quick_1 = QPushButton("x1")
        self.quick_5 = QPushButton("x5")
        self.quick_10 = QPushButton("x10")
        self.quick_25 = QPushButton("x25")
        for b in (self.quick_1, self.quick_5, self.quick_10, self.quick_25):
            b.setFixedWidth(48)
        row_target.addWidget(self.quick_1, 0)
        row_target.addWidget(self.quick_5, 0)
        row_target.addWidget(self.quick_10, 0)
        row_target.addWidget(self.quick_25, 0)
        row_target.addStretch(1)
        v_target.addLayout(row_target)

        hint = QLabel(
            "ЛКМ по ячейке: назначить выбранное значение.\n"
            "ПКМ: очистить.\n"
            "Разбор будет запускаться для выбранного окна клиента."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        v_target.addWidget(hint)

        top_row.addWidget(g_target, 1)

        layout.addLayout(top_row, 0)

        # ---- Backpacks strip (horizontal) ----
        g_strip = QGroupBox("Рюкзаки (1–8)")
        gv = QVBoxLayout(g_strip)
        gv.setContentsMargins(10, 10, 10, 10)
        gv.setSpacing(4)

        self._strip_container = QWidget()
        self._strip_layout = QHBoxLayout(self._strip_container)
        self._strip_layout.setContentsMargins(0, 0, 0, 0)
        self._strip_layout.setSpacing(8)

        self._backpack_widgets: dict[int, BackpackWidget] = {}
        for bi in range(8):
            bw = BackpackWidget(
                backpack_index=int(bi),
                title=f"Рюкзак {bi + 1}",
                cell_px=28,
                on_cell_clicked=lambda r, c, btn, b=bi: self._on_cell_clicked(int(b), int(r), int(c), int(btn)),
                on_toggle_collapsed=lambda collapsed, b=bi: self._set_backpack_collapsed(int(b), bool(collapsed)),
            )
            self._backpack_widgets[int(bi)] = bw
            self._strip_layout.addWidget(bw, 0)
        self._strip_layout.addStretch(1)
        # restore collapsed state (без сигналов)
        for bi in range(8):
            bw = self._backpack_widgets.get(int(bi))
            if bw is not None:
                bw.set_collapsed(bool(self._collapsed.get(int(bi), False)))

        self.backpacks_scroll = QScrollArea()
        self.backpacks_scroll.setWidget(self._strip_container)
        self.backpacks_scroll.setWidgetResizable(True)
        self.backpacks_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.backpacks_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Высота — строго под размер матрицы, без лишней вертикали.
        try:
            max_h = max(w.sizeHint().height() for w in self._backpack_widgets.values())
        except Exception:
            max_h = 210
        self.backpacks_scroll.setMinimumHeight(int(max_h + 18))
        self.backpacks_scroll.setMaximumHeight(int(max_h + 18))
        self.backpacks_scroll.setStyleSheet("QScrollArea { border: 1px solid #e0e0e0; background: #ffffff; }")
        gv.addWidget(self.backpacks_scroll, 0)

        layout.addWidget(g_strip, 0)

        # Сжимаемое/разжимаемое пространство ПОД рюкзаками (чтобы верх не расползался)
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # ---- Start button row ----
        start_row = QHBoxLayout()
        start_row.setSpacing(10)
        self.start_btn = QPushButton("Начать разбор")
        self.start_btn.setEnabled(False)
        self.start_loader = QProgressBar()
        self.start_loader.setRange(0, 0)  # indeterminate
        self.start_loader.setVisible(False)
        self.start_loader.setFixedWidth(200)
        start_row.addWidget(self.start_btn, 0)
        start_row.addWidget(self.start_loader, 0)
        start_row.addStretch(1)
        layout.addLayout(start_row, 0)

        # wiring
        self.client_combo.currentIndexChanged.connect(lambda _: self._on_client_changed())
        self.target_spin.valueChanged.connect(lambda v: self._on_target_changed(int(v)))
        self.quick_1.clicked.connect(lambda: self._set_target_value(1))
        self.quick_5.clicked.connect(lambda: self._set_target_value(5))
        self.quick_10.clicked.connect(lambda: self._set_target_value(10))
        self.quick_25.clicked.connect(lambda: self._set_target_value(25))
        self.start_btn.clicked.connect(self._start_clicked)

        # timers (UI-thread)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(800)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        QTimer.singleShot(0, self._refresh_clients)
        QTimer.singleShot(0, self._apply_view_from_model_all)

    @Slot(bool)
    def set_run_active(self, active: bool) -> None:
        self._run_active = bool(active)
        if not self._run_active:
            # Если весь скрипт/вкладка остановлены — принудительно останавливаем "разбор".
            self.set_busy(False)
            try:
                self.stop_disassemble_clicked.emit()
            except Exception:
                pass
        self._update_start_enabled()

    @Slot(bool)
    def set_busy(self, busy: bool) -> None:
        busy = bool(busy)
        self.start_loader.setVisible(busy)
        self.start_btn.setVisible(not busy)
        self._update_start_enabled()

    def _update_start_enabled(self) -> None:
        can = bool(self._run_active) and bool(self._selected_nickname_is_active())
        self.start_btn.setEnabled(bool(can))

    # ----------------
    # Clients + status (как в Capture ROI)
    # ----------------
    def _refresh_clients(self) -> None:
        clients = list(self._on_get_clients() or [])
        current_nick = self._get_selected_nickname()
        self.client_combo.blockSignals(True)
        try:
            self.client_combo.clear()
            for c in clients:
                self.client_combo.addItem(c.label(), str(c.nickname or "").strip())
            # 1) prefer persisted nickname
            if self._preferred_nickname:
                idx = self.client_combo.findData(self._preferred_nickname)
                if idx >= 0:
                    self.client_combo.setCurrentIndex(idx)
            # 2) fallback to current nickname (before refresh)
            elif current_nick:
                idx = self.client_combo.findData(current_nick)
                if idx >= 0:
                    self.client_combo.setCurrentIndex(idx)
        finally:
            self.client_combo.blockSignals(False)
        self._refresh_status()

    def _on_client_changed(self) -> None:
        nick = self._get_selected_nickname()
        self._preferred_nickname = str(nick or "").strip()
        self._refresh_status()
        if not self._profile_loading:
            try:
                self.selected_nickname_changed.emit(self._preferred_nickname)
            except Exception:
                pass

    def _selected_nickname_is_active(self) -> bool:
        nickname = self._get_selected_nickname()
        if not nickname:
            return False
        pid = int(self._resolve_pid_for_nickname(nickname))
        if pid <= 0 or (not pid_exists(pid)):
            return False
        hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=self._window_title))
        return hwnd > 0

    def _get_selected_nickname(self) -> str:
        idx = int(self.client_combo.currentIndex())
        if idx < 0:
            return ""
        try:
            return str(self.client_combo.itemData(idx) or "").strip()
        except Exception:
            return str(self.client_combo.currentText() or "").strip()

    def _resolve_pid_for_nickname(self, nickname: str) -> int:
        nickname = str(nickname or "").strip()
        if not nickname:
            return 0
        try:
            clients = list(self._on_get_clients() or [])
        except Exception:
            clients = []
        for c in clients:
            if str(getattr(c, "nickname", "") or "").strip() == nickname:
                return int(getattr(c, "pid", 0) or 0)
        return 0

    def _refresh_status(self) -> None:
        nickname = self._get_selected_nickname()
        if not nickname:
            self._set_status_dot(active=False)
            self.status_label.setText("Статус: клиент не выбран.")
            return

        pid = int(self._resolve_pid_for_nickname(nickname))
        if pid <= 0:
            self._set_status_dot(active=False)
            self.status_label.setText(f"Статус: выключен (ник={nickname!r}).")
            return

        if not pid_exists(pid):
            self._set_status_dot(active=False)
            self.status_label.setText(f"Статус: процесс PID={pid} не существует (ник={nickname!r}).")
            return

        hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=self._window_title))
        if hwnd <= 0:
            self._set_status_dot(active=False)
            self.status_label.setText(f"Статус: окно '{self._window_title}' не найдено (ник={nickname!r}, PID={pid}).")
            return

        self._set_status_dot(active=True)
        self.status_label.setText(f"Статус: активно (ник={nickname!r}, PID={pid}, HWND={hwnd}).")
        self._update_start_enabled()

    def _set_status_dot(self, *, active: bool) -> None:
        if active:
            self.status_dot.setStyleSheet("background-color: #2e7d32; border-radius: 2px;")
        else:
            self.status_dot.setStyleSheet("background-color: #808080; border-radius: 2px;")

    # ----------------
    # Matrix model
    # ----------------
    def get_value(self, *, backpack_index: int, row: int, col: int) -> int | None:
        bi = int(backpack_index)
        r = int(row)
        c = int(col)
        if bi not in self._values:
            return None
        if not (0 <= r < 5 and 0 <= c < 5):
            return None
        return self._values[bi][r][c]

    def set_value(self, *, backpack_index: int, row: int, col: int, value: int | None) -> None:
        bi = int(backpack_index)
        r = int(row)
        c = int(col)
        if bi not in self._values:
            return
        if not (0 <= r < 5 and 0 <= c < 5):
            return
        if value is None:
            self._values[bi][r][c] = None
        else:
            v = int(value)
            self._values[bi][r][c] = v if v > 0 else None
        self._apply_view_from_model_for_backpack(bi)
        if not self._profile_loading:
            self.config_changed.emit()

    def _on_target_changed(self, v: int) -> None:
        v = int(v)
        if v < 0:
            v = 0
        if v > 1000:
            v = 1000
        self._selected_target_value = int(v)

    def _set_target_value(self, v: int) -> None:
        v = int(v)
        if v < 0:
            v = 0
        if v > 1000:
            v = 1000
        self.target_spin.setValue(int(v))

    def _set_backpack_collapsed(self, backpack_index: int, collapsed: bool) -> None:
        bi = int(backpack_index)
        self._collapsed[bi] = bool(collapsed)
        w = self._backpack_widgets.get(bi)
        if w is not None:
            w.set_collapsed(bool(collapsed))
        if not self._profile_loading:
            try:
                self.collapsed_mask_changed.emit(int(self._collapsed_mask()))
            except Exception:
                pass

    def _collapsed_mask(self) -> int:
        m = 0
        for bi in range(8):
            if bool(self._collapsed.get(int(bi), False)):
                m |= 1 << int(bi)
        return int(m)

    # ----------------
    # Public snapshots (for worker thread)
    # ----------------
    def get_selected_nickname(self) -> str:
        """Текущий выбранный ник (для запуска разбора)."""
        return str(self._get_selected_nickname() or "").strip()

    def export_targets(self) -> list[list[list[int]]]:
        """
        Возвращает 8×5×5 матрицу целей:
        targets[backpack][row][col] -> int (0 = пропуск).
        """
        out: list[list[list[int]]] = []
        for bi in range(8):
            bag: list[list[int]] = []
            model = self._values.get(int(bi)) or [[None for _ in range(5)] for _ in range(5)]
            for r in range(5):
                row: list[int] = []
                for c in range(5):
                    v = model[r][c]
                    row.append(int(v) if v is not None else 0)
                bag.append(row)
            out.append(bag)
        return out

    def get_collapsed_mask(self) -> int:
        """Текущая маска свёрнутых рюкзаков (бит 0..7)."""
        return int(self._collapsed_mask())

    def apply_profile(
        self,
        *,
        targets: list[list[list[int]]] | None,
        collapsed_mask: int,
    ) -> None:
        """
        Применяет сохранённый профиль (без генерации сигналов сохранения).
        """
        self._preferred_nickname = str(self._preferred_nickname or "").strip()
        self._profile_loading = True
        try:
            # targets
            if targets is None:
                self._values = {bi: [[None for _ in range(5)] for _ in range(5)] for bi in range(8)}
            else:
                new_vals: dict[int, list[list[int | None]]] = {}
                for bi in range(8):
                    bag = (targets[bi] if bi < len(targets) else None) or []
                    mat: list[list[int | None]] = []
                    for r in range(5):
                        row = (bag[r] if r < len(bag) else None) or []
                        out_row: list[int | None] = []
                        for c in range(5):
                            v = int(row[c] or 0) if c < len(row) else 0
                            out_row.append(int(v) if int(v) > 0 else None)
                        mat.append(out_row)
                    new_vals[int(bi)] = mat
                self._values = new_vals

            # collapsed
            m = int(collapsed_mask or 0)
            for bi in range(8):
                self._collapsed[int(bi)] = bool(m & (1 << int(bi)))
                bw = self._backpack_widgets.get(int(bi))
                if bw is not None:
                    bw.set_collapsed(bool(self._collapsed[int(bi)]))

            self._apply_view_from_model_all()
        finally:
            self._profile_loading = False

    def _on_cell_clicked(self, backpack_index: int, row: int, col: int, mouse_button: int) -> None:
        bi = int(backpack_index)
        r = int(row)
        c = int(col)
        if not (0 <= bi < 8 and 0 <= r < 5 and 0 <= c < 5):
            return

        right_btn = int(getattr(Qt.MouseButton.RightButton, "value", 2))
        if int(mouse_button) == int(right_btn):
            self.set_value(backpack_index=bi, row=r, col=c, value=None)
            return

        # ЛКМ: назначить выбранное значение; если уже такое стоит — убрать (toggle).
        v = int(self._selected_target_value)
        current = self.get_value(backpack_index=bi, row=r, col=c)
        if v <= 0:
            self.set_value(backpack_index=bi, row=r, col=c, value=None)
            return
        if current is not None and int(current) == int(v):
            self.set_value(backpack_index=bi, row=r, col=c, value=None)
            return
        self.set_value(backpack_index=bi, row=r, col=c, value=int(v))

    def _start_clicked(self) -> None:
        if not self._run_active:
            return
        if not self._selected_nickname_is_active():
            return
        self.set_busy(True)
        try:
            self.start_disassemble_clicked.emit()
        except Exception:
            pass

    def _apply_view_from_model_for_backpack(self, backpack_index: int) -> None:
        bi = int(backpack_index)
        model = self._values.get(bi) or [[None for _ in range(5)] for _ in range(5)]
        w = self._backpack_widgets.get(bi)
        if w is None:
            return
        assigned = 0
        for r in range(5):
            for c in range(5):
                v = model[r][c]
                if v is not None:
                    assigned += 1
                w.set_cell_value(r, c, v)
        w.set_badge_text(f"{assigned}/25")

    def _apply_view_from_model_all(self) -> None:
        for bi in range(8):
            self._apply_view_from_model_for_backpack(bi)


class RotatedStripWidget(QWidget):
    """Узкая вертикальная плашка с вертикальным текстом снизу вверх."""

    def __init__(self, *, text: str, parent=None) -> None:
        super().__init__(parent)
        self._text = str(text)
        self.setMinimumWidth(22)
        self.setMaximumWidth(22)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def set_text(self, text: str) -> None:
        self._text = str(text)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        _ = event
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.fillRect(self.rect(), Qt.GlobalColor.transparent)
            # фон
            p.fillRect(self.rect(), Qt.GlobalColor.lightGray)
            p.setPen(Qt.GlobalColor.black)

            # Текст снизу вверх: поворот -90° и рисование по "ширине" исходного виджета.
            p.translate(0, self.height())
            p.rotate(-90)
            # после rotate: ширина/высота поменялись местами
            p.drawText(0, 0, int(self.height()), int(self.width()), Qt.AlignCenter, self._text)
        finally:
            p.end()


class BackpackWidget(QFrame):
    """Один рюкзак: либо компактная матрица 5x5, либо свернутая вертикальная полоска."""

    def __init__(
        self,
        *,
        backpack_index: int,
        title: str,
        cell_px: int,
        on_cell_clicked: Callable[[int, int, int], None],
        on_toggle_collapsed: Callable[[bool], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._bi = int(backpack_index)
        self._title = str(title)
        self._cell_px = int(cell_px)
        self._on_cell_clicked = on_cell_clicked
        self._on_toggle_collapsed = on_toggle_collapsed
        self._collapsed = False

        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Plain)
        self.setStyleSheet("QFrame { border: 1px solid #d0d0d0; border-radius: 6px; background: #ffffff; }")

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(8, 8, 8, 8)
        self._root_layout.setSpacing(6)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # header
        header = QHBoxLayout()
        header.setSpacing(6)
        self.title_label = QLabel(self._title)
        self.title_label.setStyleSheet("font-weight: 700; color: #333;")

        self.badge = QLabel("0/25")
        self.badge.setStyleSheet("color: #666;")

        self.collapse_btn = QToolButton()
        self.collapse_btn.setCheckable(True)
        self.collapse_btn.setChecked(False)
        self.collapse_btn.setText("▾")
        self.collapse_btn.setToolTip("Свернуть/развернуть рюкзак")
        self.collapse_btn.clicked.connect(self._toggle_clicked)

        # Кнопка сворачивания — слева, над матрицей.
        header.addWidget(self.collapse_btn, 0)
        header.addWidget(self.title_label, 0)
        header.addWidget(self.badge, 0)
        header.addStretch(1)
        self._root_layout.addLayout(header, 0)

        # stacked: expanded grid vs collapsed strip
        self._stack = QStackedLayout()
        self._root_layout.addLayout(self._stack, 1)

        # expanded widget
        expanded = QWidget()
        ev = QVBoxLayout(expanded)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(0)

        self._cells: list[list[DisassembleCellWidget]] = []
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        for r in range(5):
            row_cells: list[DisassembleCellWidget] = []
            for c in range(5):
                cell = DisassembleCellWidget(row=r, col=c, cell_px=self._cell_px)
                cell.clicked.connect(lambda rr, cc, btn, b=self: b.on_cell_clicked_safe(rr, cc, btn))
                row_cells.append(cell)
                grid.addWidget(cell, r, c)
            self._cells.append(row_cells)
        ev.addLayout(grid, 0)
        # Не добавляем stretch: высота должна быть ровно по матрице, без "пустого" вертикального роста.

        # collapsed widget
        collapsed = QWidget()
        cv = QVBoxLayout(collapsed)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self.strip = RotatedStripWidget(text=self._title)
        cv.addWidget(self.strip, 1)

        self._stack.addWidget(expanded)   # index 0
        self._stack.addWidget(collapsed)  # index 1
        self._stack.setCurrentIndex(0)

        # Зафиксируем высоту под матрицу (квадрат) + шапку.
        grid_h = 5 * int(self._cell_px) + 4 * int(grid.verticalSpacing())
        # header ~ 22px + margins/spacings
        total_h = int(8 + 22 + 6 + grid_h + 8)
        self.setMinimumHeight(total_h)
        self.setMaximumHeight(total_h)

    def on_cell_clicked_safe(self, row: int, col: int, btn: int) -> None:
        try:
            self._on_cell_clicked(int(row), int(col), int(btn))
        except Exception:
            pass

    def _toggle_clicked(self) -> None:
        self.set_collapsed(bool(self.collapse_btn.isChecked()))
        try:
            self._on_toggle_collapsed(bool(self._collapsed))
        except Exception:
            pass

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.collapse_btn.blockSignals(True)
        try:
            self.collapse_btn.setChecked(bool(self._collapsed))
            self.collapse_btn.setText("▸" if self._collapsed else "▾")
        finally:
            self.collapse_btn.blockSignals(False)

        # При сворачивании скрываем всё кроме кнопки (и самой полоски/контента).
        self.title_label.setVisible(not self._collapsed)
        self.badge.setVisible(not self._collapsed)

        self._stack.setCurrentIndex(1 if self._collapsed else 0)
        # В свернутом состоянии делаем виджет реально узким (без пустой полосы справа):
        # - уменьшаем внутренние отступы
        # - фиксируем ширину по максимуму (кнопка, вертикальная полоска) + margins
        if self._collapsed:
            self._root_layout.setContentsMargins(4, 4, 4, 4)
            btn_w = int(self.collapse_btn.sizeHint().width())
            strip_w = int(self.strip.minimumWidth())
            m = self._root_layout.contentsMargins()
            fixed_w = max(btn_w, strip_w) + int(m.left() + m.right()) + 6
            self.setMinimumWidth(int(fixed_w))
            self.setMaximumWidth(int(fixed_w))
        else:
            self._root_layout.setContentsMargins(8, 8, 8, 8)
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)

    def set_cell_value(self, row: int, col: int, value: int | None) -> None:
        r = int(row)
        c = int(col)
        if not (0 <= r < 5 and 0 <= c < 5):
            return
        self._cells[r][c].set_value(value)

    def set_badge_text(self, text: str) -> None:
        self.badge.setText(str(text))
