# pylint: disable=import-error,no-name-in-module,broad-exception-caught

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Callable

import mss  # type: ignore

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..utils.windows import find_hwnd_by_pid_and_exact_title, focus_hwnd, pid_exists

import re

# pylint: disable=import-error,no-name-in-module
from PySide6.QtCore import QRegularExpression

try:
    import win32api  # type: ignore
    import win32gui  # type: ignore
except Exception:  # pragma: no cover
    win32api = None
    win32gui = None


WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004


user32 = ctypes.windll.user32


@dataclass(frozen=True)
class ClientItem:
    nickname: str
    login: str
    pid: int

    def label(self) -> str:
        # В списке показываем ник, а рядом (в скобках) логин.
        nick = str(self.nickname or "").strip()
        lg = str(self.login or "").strip()
        if not nick:
            return "—"
        return f"{nick} ({lg})" if lg else nick


def _virtual_screen_rect() -> tuple[int, int, int, int]:
    """Возвращает (left, top, right, bottom) виртуального экрана (включая мульти-монитор)."""
    with mss.mss() as sct:
        m0 = sct.monitors[0]  # all monitors bounding box
        left = int(m0.get("left", 0))
        top = int(m0.get("top", 0))
        width = int(m0.get("width", 0))
        height = int(m0.get("height", 0))
        return (left, top, left + width, top + height)


def _clamp_roi(left: int, top: int, width: int, height: int) -> tuple[int, int, int, int]:
    """Обрезает ROI под границы виртуального экрана. Возвращает (left, top, width, height)."""
    left = int(left)
    top = int(top)
    width = max(1, int(width))
    height = max(1, int(height))

    vleft, vtop, vright, vbottom = _virtual_screen_rect()
    right = left + width
    bottom = top + height

    # clamp
    left = max(vleft, min(left, vright - 1))
    top = max(vtop, min(top, vbottom - 1))
    right = max(left + 1, min(right, vright))
    bottom = max(top + 1, min(bottom, vbottom))
    return (int(left), int(top), int(right - left), int(bottom - top))


class CaptureRoiWidget(QWidget):
    """UI: выбор клиента -> размер области -> захват по хоткею -> превью."""

    # (login, pid) selected changed; used by plugin to refresh list etc.
    request_refresh_clients = Signal()

    def __init__(
        self,
        *,
        window_title: str,
        on_get_clients: Callable[[], list[ClientItem]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._window_title = str(window_title)
        self._on_get_clients = on_get_clients

        self._hotkey_id = 0xBEEF  # arbitrary
        self._run_active: bool = False
        self._armed: bool = False
        self._armed_login: str = ""
        self._armed_hwnd: int = 0
        self._last_pixmap: QPixmap | None = None
        self._last_save_suggest_name: str = "roi.png"
        self._mode: str = "hotkey"  # "hotkey" | "coords"

        self._roi_re = re.compile(
            r"^\s*x\s*=\s*(?P<x>-?\d+)\s*,\s*y\s*=\s*(?P<y>-?\d+)\s*,\s*w\s*=\s*(?P<w>\d+)\s*,\s*h\s*=\s*(?P<h>\d+)\s*$",
            re.IGNORECASE,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ---- Top row: client + size ----
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        g_client = QGroupBox("Клиент (логин)")
        v_client = QVBoxLayout(g_client)
        v_client.setContentsMargins(10, 10, 10, 10)
        v_client.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.client_combo = QComboBox()
        self.client_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(12, 12)
        self._set_status_dot(active=False)

        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self._refresh_clients)

        row.addWidget(QLabel("Логин:"), 0)
        row.addWidget(self.client_combo, 1)
        row.addWidget(self.status_dot, 0)
        row.addWidget(self.refresh_btn, 0)
        v_client.addLayout(row)

        self.status_label = QLabel("Статус: —")
        self.status_label.setStyleSheet("color: #555;")
        self.status_label.setWordWrap(True)
        v_client.addWidget(self.status_label)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(QLabel("Режим:"), 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("По хоткею (Ctrl+Shift+S)", "hotkey")
        self.mode_combo.addItem("По координатам (кнопка)", "coords")
        mode_row.addWidget(self.mode_combo, 1)
        v_client.addLayout(mode_row)

        top_row.addWidget(g_client, 1)

        self.roi_group = QGroupBox("Размер области (px)")
        h_roi = QHBoxLayout(self.roi_group)
        h_roi.setContentsMargins(10, 10, 10, 10)
        h_roi.setSpacing(8)

        self.w_spin = QSpinBox()
        self.w_spin.setRange(1, 8000)
        self.w_spin.setValue(300)
        self.w_spin.setSuffix(" px")

        self.h_spin = QSpinBox()
        self.h_spin.setRange(1, 8000)
        self.h_spin.setValue(200)
        self.h_spin.setSuffix(" px")

        h_roi.addWidget(QLabel("Ширина:"), 0)
        h_roi.addWidget(self.w_spin, 0)
        h_roi.addWidget(QLabel("Высота:"), 0)
        h_roi.addWidget(self.h_spin, 0)
        h_roi.addStretch(1)
        top_row.addWidget(self.roi_group, 1)
        root.addLayout(top_row)

        self.hint_line = QLabel(
            "Run → Ctrl+Shift+S: 1-й раз фокус на клиент, 2-й раз снимок ROI (top-left = курсор)."
        )
        self.hint_line.setStyleSheet("color: #666;")
        self.hint_line.setWordWrap(True)
        root.addWidget(self.hint_line)

        self.status_msg = QLabel("")
        self.status_msg.setStyleSheet("color: #444;")
        self.status_msg.setWordWrap(True)
        root.addWidget(self.status_msg)

        # ---- Режим "по координатам" ----
        self.coords_mode_group = QGroupBox("Проверка по координатам")
        vcm = QVBoxLayout(self.coords_mode_group)
        vcm.setContentsMargins(10, 10, 10, 10)
        vcm.setSpacing(8)

        row_cm = QHBoxLayout()
        row_cm.setSpacing(8)

        self.coords_ref_combo = QComboBox()
        self.coords_ref_combo.addItem("От левого верхнего угла", "tl")
        self.coords_ref_combo.addItem("От центра", "center")
        self.coords_ref_combo.addItem("От правого нижнего угла", "br")

        self.coords_input = QLineEdit()
        self.coords_input.setPlaceholderText("x=12,y=10,w=50,h=50")
        self.coords_input.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")

        # regex validation (requested)
        rx = QRegularExpression(r"^\s*x\s*=\s*-?\d+\s*,\s*y\s*=\s*-?\d+\s*,\s*w\s*=\s*\d+\s*,\s*h\s*=\s*\d+\s*$")
        self.coords_input.setValidator(QRegularExpressionValidator(rx, self.coords_input))

        self.capture_coords_btn = QPushButton("Снять по координатам")
        self.capture_coords_btn.setEnabled(False)
        self.capture_coords_btn.clicked.connect(self._capture_by_coords_clicked)

        row_cm.addWidget(QLabel("Система:"), 0)
        row_cm.addWidget(self.coords_ref_combo, 0)
        row_cm.addWidget(QLabel("ROI:"), 0)
        row_cm.addWidget(self.coords_input, 1)
        row_cm.addWidget(self.capture_coords_btn, 0)
        vcm.addLayout(row_cm)

        root.addWidget(self.coords_mode_group)

        # ---- Координаты + сохранение ----
        coords_row = QHBoxLayout()
        coords_row.setSpacing(10)

        def _make_coord_cell(title: str) -> tuple[QWidget, QLineEdit]:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(4)
            lab = QLabel(str(title))
            lab.setStyleSheet("color: #333; font-weight: 600;")
            edit = QLineEdit()
            edit.setReadOnly(True)
            edit.setPlaceholderText("—")
            edit.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
            v.addWidget(lab, 0)
            v.addWidget(edit, 0)
            return box, edit

        cell1, self.coord_tl = _make_coord_cell("От левого верхнего угла")
        cell2, self.coord_center = _make_coord_cell("От центра")
        cell3, self.coord_br = _make_coord_cell("От правого нижнего угла")

        coords_row.addWidget(cell1, 1)
        coords_row.addWidget(cell2, 1)
        coords_row.addWidget(cell3, 1)

        self.save_btn = QPushButton("Сохранить PNG")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_png_clicked)
        coords_row.addWidget(self.save_btn, 0)

        coords_container = QWidget()
        coords_container.setLayout(coords_row)
        root.addWidget(coords_container)

        # Важно: показываем оригинальный размер без масштабирования.
        # Если картинка больше области — появятся скроллбары.
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.preview_label.setStyleSheet("background: #fafafa;")
        self.preview_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidget(self.preview_label)
        self.preview_scroll.setWidgetResizable(False)
        self.preview_scroll.setMinimumHeight(220)
        self.preview_scroll.setStyleSheet("QScrollArea { border: 1px solid #e0e0e0; background: #ffffff; }")
        self.preview_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.preview_scroll, 1)

        # events
        self.client_combo.currentIndexChanged.connect(lambda _: self._refresh_status())
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.coords_input.textChanged.connect(self._update_coords_input_state)

        # timers
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(700)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        # initial load
        QTimer.singleShot(0, self._refresh_clients)
        QTimer.singleShot(0, self._apply_mode_ui)

    # ----------------
    # Run state (called by Plugin.execute)
    # ----------------
    @Slot(bool)
    def set_run_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._run_active:
            return
        self._run_active = active
        if self._run_active and self._mode == "hotkey":
            ok = self._register_hotkey_ctrl_shift_s()
            if not ok:
                self.status_msg.setText("Не удалось зарегистрировать хоткей Ctrl+Shift+S (возможно занят).")
        else:
            self._unregister_hotkey()
            self._reset_armed()
            # ничего не выводим: индикатор Run есть в базовом GUI
            self.status_msg.setText("")

    # ----------------
    # Clients + status
    # ----------------
    def _refresh_clients(self) -> None:
        clients = list(self._on_get_clients() or [])
        current = self.client_combo.currentText()
        self.client_combo.blockSignals(True)
        try:
            self.client_combo.clear()
            for c in clients:
                # store nickname (internal id), show "nick (login)"
                self.client_combo.addItem(c.label(), str(c.nickname or "").strip())
            if current:
                idx = self.client_combo.findText(current)
                if idx >= 0:
                    self.client_combo.setCurrentIndex(idx)
        finally:
            self.client_combo.blockSignals(False)
        self._refresh_status()

    def _get_client_by_nickname(self, nickname: str) -> ClientItem | None:
        nickname = str(nickname or "").strip()
        if not nickname:
            return None
        try:
            clients = list(self._on_get_clients() or [])
        except Exception:
            clients = []
        for c in clients:
            if str(getattr(c, "nickname", "") or "").strip() == nickname:
                return c
        return None

    def _get_selected_nickname(self) -> str:
        idx = int(self.client_combo.currentIndex())
        if idx < 0:
            return ""
        try:
            return str(self.client_combo.itemData(idx) or "").strip()
        except Exception:
            return str(self.client_combo.currentText() or "").strip()

    def _resolve_pid_for_nickname(self, nickname: str) -> int:
        """Берём актуальный PID по нику из on_get_clients()."""
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

    def _set_status_dot(self, *, active: bool) -> None:
        if active:
            self.status_dot.setStyleSheet("background-color: #2e7d32; border-radius: 2px;")
        else:
            self.status_dot.setStyleSheet("background-color: #808080; border-radius: 2px;")

    def _reset_armed(self) -> None:
        self._armed = False
        self._armed_login = ""
        self._armed_hwnd = 0

    def _register_hotkey_ctrl_shift_s(self) -> bool:
        # RegisterHotKey requires a native HWND of a window that receives WM_HOTKEY.
        hwnd = int(self.winId())  # force native handle for this widget
        mods = MOD_CONTROL | MOD_SHIFT
        vk = ord("S")
        try:
            return bool(user32.RegisterHotKey(hwnd, int(self._hotkey_id), int(mods), int(vk)))
        except Exception:
            return False

    def _unregister_hotkey(self) -> None:
        hwnd = int(self.winId())
        try:
            _ = user32.UnregisterHotKey(hwnd, int(self._hotkey_id))
        except Exception:
            pass

    def nativeEvent(self, eventType, message):  # noqa: N802 (Qt naming)
        _ = eventType
        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if int(msg.message) == WM_HOTKEY and int(msg.wParam) == int(self._hotkey_id):
                self._on_hotkey_toggle()
                return True, 0
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def _on_hotkey_toggle(self) -> None:
        if not self._run_active or self._mode != "hotkey":
            return
        if not self._armed:
            self._arm_capture()
        else:
            self._capture_once()

    def _on_mode_changed(self, *_args) -> None:
        self._apply_mode_ui()

    def _apply_mode_ui(self) -> None:
        mode = str(self.mode_combo.currentData() or "hotkey")
        if mode not in {"hotkey", "coords"}:
            mode = "hotkey"
        if mode == self._mode:
            return
        self._mode = mode

        # Hide/show controls
        is_hotkey = self._mode == "hotkey"
        self.roi_group.setVisible(is_hotkey)
        self.hint_line.setVisible(is_hotkey)
        self.coords_mode_group.setVisible(not is_hotkey)

        # If switching away from hotkey, stop armed state and unregister hotkey
        if not is_hotkey:
            self._unregister_hotkey()
            self._reset_armed()

        # If switching to hotkey and Run is active, (re)register
        if is_hotkey and self._run_active:
            _ = self._register_hotkey_ctrl_shift_s()

        self._update_coords_input_state()

    def _update_coords_input_state(self) -> None:
        if self._mode != "coords":
            self.capture_coords_btn.setEnabled(False)
            return
        txt = str(self.coords_input.text() or "").strip()
        m = self._roi_re.match(txt)
        if not m:
            self.capture_coords_btn.setEnabled(False)
            return
        w = int(m.group("w"))
        h = int(m.group("h"))
        self.capture_coords_btn.setEnabled(w > 0 and h > 0)

    def _capture_by_coords_clicked(self) -> None:
        if self._mode != "coords":
            return
        nickname = self._get_selected_nickname()
        if not nickname:
            self.status_msg.setText("Выберите клиента.")
            return
        pid = int(self._resolve_pid_for_nickname(nickname))
        if pid <= 0 or (not pid_exists(pid)):
            self.status_msg.setText(f"Клиент выключен: ник={nickname!r}.")
            return
        hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=self._window_title))
        if hwnd <= 0:
            self.status_msg.setText(f"Окно '{self._window_title}' не найдено (ник={nickname!r}, PID={pid}).")
            return

        raw = str(self.coords_input.text() or "").strip()
        m = self._roi_re.match(raw)
        if not m:
            self.status_msg.setText("Некорректный формат ROI. Ожидается: x=12,y=10,w=50,h=50")
            return
        x = int(m.group("x"))
        y = int(m.group("y"))
        w = int(m.group("w"))
        h = int(m.group("h"))
        if w <= 0 or h <= 0:
            self.status_msg.setText("w и h должны быть > 0.")
            return

        (ox, oy), (cw, ch) = self._get_client_origin_and_size(hwnd)
        ref = str(self.coords_ref_combo.currentData() or "tl")
        cx = int(cw // 2)
        cy = int(ch // 2)
        if ref == "tl":
            tlx, tly = int(x), int(y)
        elif ref == "center":
            tlx, tly = int(cx + x), int(cy + y)
        else:  # "br"
            tlx, tly = int(cw - x), int(ch - y)

        left_screen = int(ox + tlx)
        top_screen = int(oy + tly)
        left, top, w2, h2 = _clamp_roi(left_screen, top_screen, w, h)

        # capture (best-effort focus)
        try:
            focus_hwnd(hwnd)
        except Exception:
            pass

        qpix = None
        try:
            with mss.mss() as sct:
                shot = sct.grab({"left": int(left), "top": int(top), "width": int(w2), "height": int(h2)})
                bytes_per_line = int(w2) * 3
                qimg = QImage(shot.rgb, int(w2), int(h2), bytes_per_line, QImage.Format_RGB888).copy()
                qpix = QPixmap.fromImage(qimg)
        except Exception as e:
            self._bring_focus_back_to_gui()
            self.status_msg.setText(f"Ошибка захвата: {e}")
            return

        self._bring_focus_back_to_gui()
        self.status_msg.setText("Снимок по координатам получен.")

        # Update coordinate fields consistently (client coords)
        rel_x = int(left - ox)
        rel_y = int(top - oy)
        from_center_x = int(rel_x - cx)
        from_center_y = int(rel_y - cy)
        from_right = int(cw - rel_x)
        from_bottom = int(ch - rel_y)
        self.coord_tl.setText(f"x={rel_x},y={rel_y},w={w2},h={h2}")
        self.coord_center.setText(f"x={from_center_x},y={from_center_y},w={w2},h={h2}")
        self.coord_br.setText(f"x={from_right},y={from_bottom},w={w2},h={h2}")

        c = self._get_client_by_nickname(nickname)
        login = str(getattr(c, "login", "") or "").strip() if c is not None else ""
        stem = str(nickname).strip() or "client"
        if login:
            stem = f"{stem}_{login}"
        self._last_save_suggest_name = f"{stem}_x{rel_x}_y{rel_y}_w{w2}_h{h2}.png".replace(" ", "_")

        if qpix is not None:
            self._set_preview_pixmap(qpix)

    def _arm_capture(self) -> None:
        nickname = self._get_selected_nickname()
        if not nickname:
            self.status_msg.setText("Выберите логин клиента.")
            return

        pid = int(self._resolve_pid_for_nickname(nickname))
        if pid <= 0 or (not pid_exists(pid)):
            self.status_msg.setText(f"Клиент выключен: ник={nickname!r}.")
            return

        hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=self._window_title))
        if hwnd <= 0:
            self.status_msg.setText(f"Окно '{self._window_title}' не найдено (ник={nickname!r}, PID={pid}).")
            return

        self._armed = True
        self._armed_login = str(nickname)
        self._armed_hwnd = int(hwnd)
        self.status_msg.setText("Шаг 1/2: фокус на клиент. Наведи курсор на top-left ROI и нажми Ctrl+Shift+S ещё раз.")
        try:
            focus_hwnd(hwnd)
        except Exception as e:
            self.status_msg.setText(f"Не удалось переключить фокус на клиента: {e}")
            self._reset_armed()
            return

    def _get_cursor_pos_screen(self) -> tuple[int, int]:
        if win32api is not None:
            try:
                x, y = win32api.GetCursorPos()
                return (int(x), int(y))
            except Exception:
                pass
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return (int(pt.x), int(pt.y))

    def _get_client_origin_and_size(self, hwnd: int) -> tuple[tuple[int, int], tuple[int, int]]:
        if win32gui is None:
            return ((0, 0), (0, 0))
        ox, oy = win32gui.ClientToScreen(int(hwnd), (0, 0))
        l, t, r, b = win32gui.GetClientRect(int(hwnd))
        return ((int(ox), int(oy)), (int(r - l), int(b - t)))

    def _capture_once(self) -> None:
        hwnd = int(self._armed_hwnd or 0)
        login = str(self._armed_login or "").strip()
        if hwnd <= 0 or not login:
            self._reset_armed()
            return

        x, y = self._get_cursor_pos_screen()
        roi_w = int(self.w_spin.value())
        roi_h = int(self.h_spin.value())
        left, top, w, h = _clamp_roi(int(x), int(y), roi_w, roi_h)

        # cursor position (screen coords)
        # capture
        qpix = None
        try:
            with mss.mss() as sct:
                shot = sct.grab({"left": int(left), "top": int(top), "width": int(w), "height": int(h)})
                # shot.rgb is RGB bytes
                # Важно: для RGB888 Qt ожидает выравнивание строк по 4 байта.
                # Если не передать bytesPerLine явно, на ширинах вроде 50px превью "перекручивается".
                bytes_per_line = int(w) * 3
                qimg = QImage(shot.rgb, int(w), int(h), bytes_per_line, QImage.Format_RGB888).copy()
                qpix = QPixmap.fromImage(qimg)
        except Exception as e:
            self._bring_focus_back_to_gui()
            self.status_msg.setText(f"Ошибка захвата: {e}")
            self._reset_armed()
            return

        self._bring_focus_back_to_gui()
        self._reset_armed()
        self.status_msg.setText("Снимок получен.")

        (ox, oy), (cw, ch) = self._get_client_origin_and_size(hwnd)
        rel_x = int(left - ox)
        rel_y = int(top - oy)

        cx = int(cw // 2)
        cy = int(ch // 2)
        from_center_x = int(rel_x - cx)
        from_center_y = int(rel_y - cy)

        from_right = int(cw - rel_x)
        from_bottom = int(ch - rel_y)

        self.coord_tl.setText(f"x={rel_x},y={rel_y},w={w},h={h}")
        self.coord_center.setText(f"x={from_center_x},y={from_center_y},w={w},h={h}")
        self.coord_br.setText(f"x={from_right},y={from_bottom},w={w},h={h}")

        self._last_save_suggest_name = f"{login}_x{rel_x}_y{rel_y}_w{w}_h{h}.png".replace(" ", "_")
        if qpix is not None:
            self._set_preview_pixmap(qpix)

    def _set_preview_pixmap(self, pix: QPixmap) -> None:
        self._last_pixmap = pix
        self.save_btn.setEnabled(True)
        self.preview_label.setPixmap(pix)
        # Под оригинальный размер (чтобы QScrollArea корректно считала область прокрутки)
        self.preview_label.resize(pix.size())

    def _save_png_clicked(self) -> None:
        if self._last_pixmap is None:
            return
        suggested = str(self._last_save_suggest_name or "roi.png")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить изображение", suggested, "PNG (*.png)")
        path = str(path or "").strip()
        if not path:
            return
        if not path.lower().endswith(".png"):
            path = path + ".png"
        ok = bool(self._last_pixmap.save(path, "PNG"))
        if ok:
            self.status_msg.setText(f"Сохранено: {path}")
        else:
            self.status_msg.setText("Не удалось сохранить PNG.")

    def _bring_focus_back_to_gui(self) -> None:
        try:
            w = self.window()
            w.raise_()
            w.activateWindow()
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self._unregister_hotkey()
        except Exception:
            pass
        self._reset_armed()
        super().closeEvent(event)

