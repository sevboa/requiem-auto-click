from __future__ import annotations

# pylint: disable=broad-exception-caught
# pylint: disable=import-error,no-name-in-module
from PySide6.QtCore import QRegularExpression, QTimer, Signal, Slot
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QWidget,
    QVBoxLayout,
)


class LaunchRowWidget(QWidget):
    """Строка настроек запуска + состояние процесса."""

    login_changed = Signal(str)  # login
    password_changed = Signal(str)  # password (plain text, not persisted by default)
    slot_changed = Signal(int)  # 1..8
    nickname_changed = Signal(str)  # character nickname
    pin_changed = Signal(str)  # 4-digit pin
    selected_changed = Signal(bool)
    start_clicked = Signal()
    terminate_clicked = Signal()
    check_clicked = Signal()
    focus_toggle_clicked = Signal()
    move_up_clicked = Signal()
    move_down_clicked = Signal()
    delete_clicked = Signal()

    def __init__(
        self,
        *,
        initial_login: str = "",
        initial_password: str = "",
        initial_slot: int = 1,
        initial_nickname: str = "",
        initial_pin: str = "",
        parent=None,
    ):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)

        self.select_cb = QCheckBox()
        self.select_cb.setToolTip("Выбрать для мультизапуска")
        self.select_cb.setVisible(False)

        # порядок: кнопки вверх/вниз (компактно, без зазора)
        arrows_box = QWidget()
        arrows = QVBoxLayout(arrows_box)
        arrows.setContentsMargins(0, 0, 0, 0)
        arrows.setSpacing(0)
        self.up_btn = QPushButton("↑")
        self.down_btn = QPushButton("↓")
        for b in (self.up_btn, self.down_btn):
            b.setFixedWidth(14)
            b.setFixedHeight(12)
        arrows.addWidget(self.up_btn)
        arrows.addWidget(self.down_btn)

        self.indicator = QLabel()
        self.indicator.setFixedSize(12, 12)
        self._set_indicator(active=False)

        self.login_edit = QLineEdit()
        self.login_edit.setPlaceholderText("login")
        self.login_edit.setText(str(initial_login or ""))
        self.login_edit.setFixedWidth(110)
        self.login_edit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("password")
        self.password_edit.setText(str(initial_password or ""))
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setFixedWidth(110)

        self.slot_combo = QComboBox()
        self.slot_combo.setToolTip("Слот персонажа (1–8)")
        for i in range(1, 9):
            self.slot_combo.addItem(str(i), i)
        try:
            initial_slot_i = int(initial_slot)
        except Exception:
            initial_slot_i = 1
        if initial_slot_i < 1:
            initial_slot_i = 1
        if initial_slot_i > 8:
            initial_slot_i = 8
        idx = self.slot_combo.findData(initial_slot_i)
        if idx >= 0:
            self.slot_combo.setCurrentIndex(idx)

        self.nickname_edit = QLineEdit()
        self.nickname_edit.setPlaceholderText("nick")
        self.nickname_edit.setText(str(initial_nickname or ""))
        self.nickname_edit.setFixedWidth(110)

        self.pin_edit = QLineEdit()
        self.pin_edit.setPlaceholderText("PIN (4)")
        self.pin_edit.setText(str(initial_pin or "")[:4])
        self.pin_edit.setEchoMode(QLineEdit.Password)
        self.pin_edit.setMaxLength(4)
        self.pin_edit.setFixedWidth(60)
        self.pin_edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,4}$")))

        self.pid_label = QLabel("PID: —")
        self.pid_label.setMinimumWidth(90)
        self.pid_label.setStyleSheet("color: #333;")

        self.start_btn = QPushButton("▶")
        self.start_btn.setToolTip("Запустить")
        self.terminate_btn = QPushButton("⏹")
        self.terminate_btn.setToolTip("Завершить")
        self.check_btn = QPushButton("👀")
        self.check_btn.setToolTip("Проверить")
        self.focus_toggle_btn = QPushButton("🚶")
        self.focus_toggle_btn.setToolTip("Переключить фокус")
        self.delete_btn = QPushButton("🗑️")
        self.delete_btn.setToolTip("Удалить")
        self.delete_btn.setStyleSheet("QPushButton { color: #b00020; }")

        # компактные кнопки (иконки)
        for b in (self.start_btn, self.terminate_btn, self.check_btn, self.focus_toggle_btn, self.delete_btn):
            b.setFixedWidth(24)
            b.setFixedHeight(22)

        self.terminate_btn.setVisible(False)
        self.check_btn.setEnabled(False)
        self.focus_toggle_btn.setEnabled(False)

        self.delete_sep = QFrame()
        self.delete_sep.setFrameShape(QFrame.VLine)
        self.delete_sep.setFrameShadow(QFrame.Sunken)

        row.addWidget(self.select_cb, 0)
        row.addWidget(arrows_box, 0)
        row.addWidget(self.indicator, 0)
        row.addWidget(QLabel("Логин:"), 0)
        row.addWidget(self.login_edit, 1)
        row.addWidget(QLabel("Пароль:"), 0)
        row.addWidget(self.password_edit, 0)
        row.addWidget(QLabel("Слот:"), 0)
        row.addWidget(self.slot_combo, 0)
        row.addWidget(QLabel("Ник:"), 0)
        row.addWidget(self.nickname_edit, 0)
        row.addWidget(QLabel("PIN:"), 0)
        row.addWidget(self.pin_edit, 0)
        row.addWidget(self.pid_label, 0)
        row.addWidget(self.start_btn, 0)
        row.addWidget(self.terminate_btn, 0)
        row.addWidget(self.check_btn, 0)
        row.addWidget(self.focus_toggle_btn, 0)
        row.addWidget(self.delete_sep, 0)
        row.addWidget(self.delete_btn, 0)

        self.login_edit.textChanged.connect(lambda t: self.login_changed.emit(str(t)))
        self.password_edit.textChanged.connect(lambda t: self.password_changed.emit(str(t)))
        self.slot_combo.currentIndexChanged.connect(lambda _: self.slot_changed.emit(int(self.slot_combo.currentData())))
        self.nickname_edit.textChanged.connect(lambda t: self.nickname_changed.emit(str(t)))
        self.pin_edit.textChanged.connect(lambda t: self.pin_changed.emit(str(t)))
        self.select_cb.toggled.connect(lambda v: self.selected_changed.emit(bool(v)))
        self.start_btn.clicked.connect(self.start_clicked.emit)
        self.terminate_btn.clicked.connect(self.terminate_clicked.emit)
        self.check_btn.clicked.connect(self.check_clicked.emit)
        self.focus_toggle_btn.clicked.connect(self.focus_toggle_clicked.emit)
        self.up_btn.clicked.connect(self.move_up_clicked.emit)
        self.down_btn.clicked.connect(self.move_down_clicked.emit)
        self.delete_btn.clicked.connect(self.delete_clicked.emit)

    def set_state(
        self,
        *,
        select_visible: bool,
        selected: bool,
        select_enabled: bool,
        nickname_ok: bool,
        pid: int,
        is_active: bool,
        start_enabled: bool,
        terminate_enabled: bool,
        check_enabled: bool,
        focus_toggle_enabled: bool,
        allow_edit: bool,
        allow_delete: bool,
        move_up_enabled: bool,
        move_down_enabled: bool,
    ) -> None:
        pid = int(pid or 0)
        self.set_multistart_state(
            visible=bool(select_visible),
            checked=bool(selected),
            enabled=bool(select_enabled),
        )

        # подсветка неуникального/пустого ника
        if bool(nickname_ok):
            self.nickname_edit.setStyleSheet("")
        else:
            self.nickname_edit.setStyleSheet("QLineEdit { border: 1px solid #b00020; }")

        self._set_indicator(active=bool(is_active))
        self.pid_label.setText("PID: —" if pid <= 0 else f"PID: {pid}")

        self.login_edit.setEnabled(bool(allow_edit))
        self.password_edit.setEnabled(bool(allow_edit))
        self.slot_combo.setEnabled(bool(allow_edit))
        self.nickname_edit.setEnabled(bool(allow_edit))
        self.pin_edit.setEnabled(bool(allow_edit))
        self.delete_btn.setEnabled(bool(allow_delete))
        self.delete_btn.setVisible(bool(allow_delete))
        self.delete_sep.setVisible(bool(allow_delete))

        if bool(is_active):
            self.start_btn.setVisible(False)
            self.terminate_btn.setVisible(True)
            self.terminate_btn.setEnabled(bool(terminate_enabled))
            self.check_btn.setEnabled(bool(check_enabled))
            self.focus_toggle_btn.setEnabled(bool(focus_toggle_enabled))
        else:
            self.start_btn.setVisible(True)
            self.start_btn.setEnabled(bool(start_enabled))
            self.terminate_btn.setVisible(False)
            self.check_btn.setEnabled(False)
            self.focus_toggle_btn.setEnabled(False)

        self.up_btn.setEnabled(bool(move_up_enabled))
        self.down_btn.setEnabled(bool(move_down_enabled))

    def set_multistart_state(self, *, visible: bool, checked: bool, enabled: bool) -> None:
        self.select_cb.setVisible(bool(visible))
        self.select_cb.blockSignals(True)
        try:
            self.select_cb.setChecked(bool(checked))
        finally:
            self.select_cb.blockSignals(False)
        self.select_cb.setEnabled(bool(enabled))

    def get_login(self) -> str:
        return str(self.login_edit.text() or "").strip()

    def get_password(self) -> str:
        return str(self.password_edit.text() or "")

    def get_slot(self) -> int:
        try:
            v = int(self.slot_combo.currentData())
        except Exception:
            v = 1
        if v < 1:
            return 1
        if v > 8:
            return 8
        return v

    def get_nickname(self) -> str:
        return str(self.nickname_edit.text() or "").strip()

    def get_pin(self) -> str:
        # only digits, max 4
        raw = str(self.pin_edit.text() or "")
        digits = "".join([c for c in raw if c.isdigit()])
        return digits[:4]

    def _set_indicator(self, *, active: bool) -> None:
        if active:
            self.indicator.setStyleSheet("background-color: #2e7d32; border-radius: 2px;")
        else:
            self.indicator.setStyleSheet("background-color: #808080; border-radius: 2px;")


class WindowRowWidget(QWidget):
    """Строка мониторинга: PID + выбор логина + переопределить + проверить."""

    override_clicked = Signal(int, str)  # pid, login
    check_clicked = Signal(int)  # pid

    def __init__(self, *, pid: int, title: str, available_logins, parent=None):
        super().__init__(parent)
        self.pid = int(pid)
        self.title = str(title)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.pid_label = QLabel(f"PID={self.pid}")
        self.pid_label.setMinimumWidth(90)

        self.title_label = QLabel(self.title)
        self.title_label.setStyleSheet("color: #555;")
        self.title_label.setMinimumWidth(120)

        self.login_combo = QComboBox()
        self.login_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.override_btn = QPushButton("Переопределить")
        self.check_btn = QPushButton("Проверить")

        row.addWidget(self.pid_label, 0)
        row.addWidget(self.title_label, 1)
        row.addWidget(QLabel("Ник:"), 0)
        row.addWidget(self.login_combo, 1)
        row.addWidget(self.override_btn, 0)
        row.addWidget(self.check_btn, 0)

        self.override_btn.clicked.connect(self._emit_override)
        self.check_btn.clicked.connect(lambda: self.check_clicked.emit(self.pid))

        self.set_available_logins(available_logins)

    def _emit_override(self) -> None:
        nick = ""
        try:
            nick = str(self.login_combo.currentData() or "").strip()
        except Exception:
            nick = ""
        if not nick:
            nick = str(self.login_combo.currentText() or "").strip()
        self.override_clicked.emit(self.pid, nick)

    def set_available_logins(self, logins: list[str]) -> None:
        # current selection is stored as nickname in itemData
        current = ""
        try:
            current = str(self.login_combo.currentData() or "").strip()
        except Exception:
            current = str(self.login_combo.currentText() or "").strip()
        self.login_combo.blockSignals(True)
        try:
            self.login_combo.clear()
            # logins может быть list[str] (старый формат) или list[{"nickname":..,"login":..}]
            for it in (logins or []):
                if isinstance(it, dict):
                    nickname = str(it.get("nickname", "") or "").strip()
                    login = str(it.get("login", "") or "").strip()
                else:
                    nickname = str(it or "").strip()
                    login = ""
                if not nickname:
                    continue
                label = f"{nickname} ({login})" if login else nickname
                self.login_combo.addItem(label, nickname)
            if current:
                idx = self.login_combo.findData(current)
                if idx >= 0:
                    self.login_combo.setCurrentIndex(idx)
        finally:
            self.login_combo.blockSignals(False)
        self.override_btn.setEnabled(self.login_combo.count() > 0)


class LauncherWidget(QWidget):
    monitoring_changed = Signal(bool)
    windows_changed = Signal(object)  # list[dict]
    logins_changed = Signal(object)  # list[str]

    def __init__(self, *, on_add_row, on_multi_clicked, on_focus_check, on_override_login, on_sync_state, parent=None):
        super().__init__(parent)
        self._on_add_row = on_add_row
        self._on_multi_clicked = on_multi_clicked
        self._on_focus_check = on_focus_check
        self._on_override_login = on_override_login
        self._on_sync_state = on_sync_state
        self._monitoring_enabled = False

        self._hwnd_by_pid: dict[int, int] = {}
        self._launch_rows: list[LaunchRowWidget] = []
        self._window_rows_by_pid: dict[int, WindowRowWidget] = {}
        self._available_logins: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # --- Header (не должен растягиваться по высоте)
        header = QWidget()
        header_v = QVBoxLayout(header)
        header_v.setContentsMargins(0, 0, 0, 0)
        header_v.setSpacing(4)

        hint = QLabel(
            "Перед началом работы проверьте, что в настройках указан путь к исполняемому файлу "
            "с аргументами лаунчера, например {путь к папке}\\Requiem2.exe 128.241.93.208 "
            "-FromLauncher 0/0 0 0 7 3 0"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555;")
        header_v.addWidget(hint)

        hint2 = QLabel(
            "Важно: имя исполняемого файла должно отличаться от оригинального Requiem.exe. "
            "Для этого скопируйте или переименуйте оригинальный файл и укажите путь к копии."
        )
        hint2.setWordWrap(True)
        hint2.setStyleSheet("color: #555;")
        header_v.addWidget(hint2)

        header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(header, 0)

        launch_group = QGroupBox("Настройки запуска")
        launch_v = QVBoxLayout(launch_group)
        launch_v.setSpacing(8)
        launch_v.setContentsMargins(10, 10, 10, 10)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.add_row_btn = QPushButton("Добавить строку")
        self.add_row_btn.clicked.connect(self._on_add_row)
        self.multi_btn = QPushButton("Мультизапуск")
        self.multi_btn.setVisible(False)
        self.multi_btn.clicked.connect(lambda: self._on_multi_clicked())

        self.multi_loader = QProgressBar()
        self.multi_loader.setRange(0, 0)  # indeterminate
        self.multi_loader.setVisible(False)
        self.multi_loader.setFixedWidth(170)
        top_row.addWidget(self.add_row_btn, 0)
        top_row.addWidget(self.multi_btn, 0)
        top_row.addWidget(self.multi_loader, 0)
        top_row.addStretch(1)
        launch_v.addLayout(top_row, 0)

        self.launch_rows_container = QWidget()
        self.launch_rows_layout = QVBoxLayout(self.launch_rows_container)
        self.launch_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.launch_rows_layout.setSpacing(6)
        launch_v.addWidget(self.launch_rows_container, 0)

        layout.addWidget(launch_group)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        monitor_group = QGroupBox("Окна Requiem (мониторинг)")
        monitor_v = QVBoxLayout(monitor_group)
        monitor_v.setSpacing(6)
        monitor_v.setContentsMargins(10, 10, 10, 10)

        self.monitor_hint = QLabel("Нажмите Run, чтобы начать мониторинг окон...")
        self.monitor_hint.setStyleSheet("color: #555;")
        self.monitor_hint.setWordWrap(True)
        monitor_v.addWidget(self.monitor_hint)

        self.windows_container = QWidget()
        self.windows_layout = QVBoxLayout(self.windows_container)
        self.windows_layout.setContentsMargins(0, 0, 0, 0)
        self.windows_layout.setSpacing(6)
        monitor_v.addWidget(self.windows_container)

        layout.addWidget(monitor_group, 0)

        # Весь лишний вертикальный простор уходит сюда (а не в header/группы).
        layout.addStretch(1)

        self.monitoring_changed.connect(self._set_monitoring)
        self.windows_changed.connect(self._set_windows)
        self.logins_changed.connect(self._set_available_logins)

        # Важно: не запускаем периодический "полный sync", чтобы UI не лагал.

    @Slot(bool)
    def _set_monitoring(self, enabled: bool) -> None:
        self._monitoring_enabled = bool(enabled)
        if enabled:
            self.monitor_hint.setText("")
            self.monitor_hint.setVisible(False)
        else:
            for pid in list(self._window_rows_by_pid.keys()):
                row = self._window_rows_by_pid.pop(pid)
                self.windows_layout.removeWidget(row)
                row.setParent(None)
                row.deleteLater()
            self._hwnd_by_pid = {}
            self.monitor_hint.setText("Нажмите Run, чтобы начать мониторинг окон...")
            self.monitor_hint.setVisible(True)
        self._refresh_launch_buttons()

    def add_launch_row_widget(self, row_w: LaunchRowWidget) -> None:
        self._launch_rows.append(row_w)
        self.launch_rows_layout.addWidget(row_w)
        self._refresh_launch_buttons()

    def remove_launch_row_widget(self, row_w: LaunchRowWidget) -> None:
        if row_w in self._launch_rows:
            self._launch_rows.remove(row_w)
        self.launch_rows_layout.removeWidget(row_w)
        row_w.setParent(None)
        row_w.deleteLater()
        self._refresh_launch_buttons()

    def set_launch_rows_order(self, ordered: list[LaunchRowWidget]) -> None:
        while self.launch_rows_layout.count():
            item = self.launch_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                self.launch_rows_layout.removeWidget(w)
        self._launch_rows = []
        for w in ordered:
            self._launch_rows.append(w)
            self.launch_rows_layout.addWidget(w)

    def set_multi_start_enabled(self, enabled: bool) -> None:
        self.multi_btn.setEnabled(bool(enabled))
        self._refresh_launch_buttons()

    def set_run_mode(self, *, monitoring_on: bool) -> None:
        # По требованию: когда RUN включён — нельзя менять строки (только управлять процессами)
        self.add_row_btn.setVisible(not bool(monitoring_on))
        self.multi_btn.setVisible(bool(monitoring_on))

    def set_multi_ui(self, *, mode: str, enabled: bool = True) -> None:
        """
        mode:
          - hidden: скрыть кнопку и лоадер
          - arm: показать кнопку "Мультизапуск"
          - ready: показать кнопку "Запустить последовательно"
          - running: скрыть кнопку, показать лоадер
        """
        m = str(mode or "").strip().lower()
        if m == "hidden":
            self.multi_btn.setVisible(False)
            self.multi_loader.setVisible(False)
            return
        if m == "running":
            self.multi_btn.setVisible(False)
            self.multi_loader.setVisible(True)
            return
        # arm / ready
        self.multi_loader.setVisible(False)
        self.multi_btn.setVisible(True)
        self.multi_btn.setEnabled(bool(enabled))
        self.multi_btn.setText("Запустить последовательно" if m == "ready" else "Мультизапуск")

    def _refresh_launch_buttons(self) -> None:
        try:
            self._on_sync_state()
        except Exception:
            pass

    @Slot(object)
    def _set_available_logins(self, logins) -> None:
        # logins может быть list[str] (старый формат) или list[{"nickname":..,"login":..}]
        self._available_logins = list(logins or [])
        for pid, wrow in self._window_rows_by_pid.items():
            _ = pid
            wrow.set_available_logins(self._available_logins)

    @Slot(object)
    def _set_windows(self, windows) -> None:
        windows = windows or []
        new_pids = set()
        self._hwnd_by_pid = {}
        for w in windows:
            try:
                pid = int(w.get("pid", 0))
                hwnd = int(w.get("hwnd", 0))
                title = str(w.get("title", ""))
            except Exception:
                continue
            if pid <= 0 or hwnd <= 0:
                continue
            self._hwnd_by_pid[pid] = hwnd
            new_pids.add(pid)
            if pid not in self._window_rows_by_pid:
                row = WindowRowWidget(pid=pid, title=title, available_logins=self._available_logins)
                row.override_clicked.connect(self._on_override_login)
                row.check_clicked.connect(self._on_focus_check)
                self._window_rows_by_pid[pid] = row
                self.windows_layout.addWidget(row)

        for pid in list(self._window_rows_by_pid.keys()):
            if pid not in new_pids:
                row = self._window_rows_by_pid.pop(pid)
                self.windows_layout.removeWidget(row)
                row.setParent(None)
                row.deleteLater()

        if self._monitoring_enabled and len(self._window_rows_by_pid) == 0:
            self.monitor_hint.setText("Нет неопределённых окон Requiem.")
            self.monitor_hint.setVisible(True)
        elif self._monitoring_enabled:
            self.monitor_hint.setText("")
            self.monitor_hint.setVisible(False)

