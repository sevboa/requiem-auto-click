# pylint: disable=import-error,no-name-in-module,broad-exception-caught
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..utils.windows import pid_exists
from ....modules.mailbox_manager import (
    MailboxCancelledError,
    MailboxConfirmSpec,
    MailboxManager,
    MailboxTimings,
)


@dataclass(frozen=True)
class ClientItem:
    nickname: str
    login: str
    pid: int
    hwnd: int

    def label(self) -> str:
        nick = str(self.nickname or "").strip()
        lg = str(self.login or "").strip()
        if not nick:
            return "—"
        return f"{nick} ({lg})" if lg else nick


class MailboxWidget(QWidget):
    """UI: выбор активного клиента -> фокус -> проверка окна почты по шаблону."""

    check_finished = Signal(bool, str)  # ok, message
    get_mail_finished = Signal(bool, str)  # ok, message

    def __init__(
        self,
        *,
        window_title: str,
        on_get_clients: Callable[[], list[ClientItem]],
        on_log: Callable[[str], None] | None = None,
        on_get_timings: Callable[[], MailboxTimings] | None = None,
        on_get_confirm_specs: Callable[[], tuple[MailboxConfirmSpec, MailboxConfirmSpec]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._window_title = str(window_title)
        self._on_get_clients = on_get_clients
        self._on_log = on_log
        self._on_get_timings = on_get_timings
        self._on_get_confirm_specs = on_get_confirm_specs
        self._run_active: bool = False
        self._busy: bool = False
        self._cancel = threading.Event()

        self._clients: list[ClientItem] = []
        self._check_lock = threading.Lock()
        self._get_mail_lock = threading.Lock()

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        g_client = QGroupBox("Клиент (ник)")
        v_client = QVBoxLayout(g_client)
        v_client.setContentsMargins(10, 10, 10, 10)
        v_client.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.client_combo = QComboBox()
        self.client_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self._refresh_clients)

        row.addWidget(QLabel("Ник:"), 0)
        row.addWidget(self.client_combo, 1)
        row.addWidget(self.refresh_btn, 0)
        v_client.addLayout(row)

        self.client_status = QLabel("Статус: —")
        self.client_status.setStyleSheet("color: #555;")
        self.client_status.setWordWrap(True)
        v_client.addWidget(self.client_status)

        root.addWidget(g_client, 0)

        g_check = QGroupBox("Почтовый ящик")
        v_check = QVBoxLayout(g_check)
        v_check.setContentsMargins(10, 10, 10, 10)
        v_check.setSpacing(8)

        # Row 1: simple window check
        row_check = QHBoxLayout()
        row_check.setSpacing(10)

        self.check_btn = QPushButton("Проверить окно почты")
        self.check_btn.clicked.connect(self._check_clicked)

        self.check_result_label = QLabel("—")
        self.check_result_label.setStyleSheet("color: #555; font-weight: 700;")
        self.check_result_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row_check.addWidget(self.check_btn, 0)
        row_check.addWidget(self.check_result_label, 1)
        v_check.addLayout(row_check, 0)

        # Row 2: main script starter ("Получить письма")
        row_get = QHBoxLayout()
        row_get.setSpacing(10)

        self.mail_count_spin = QSpinBox()
        self.mail_count_spin.setRange(1, 50)
        self.mail_count_spin.setValue(10)
        self.mail_count_spin.setFixedWidth(80)
        self.mail_count_spin.setToolTip("Ограничитель: сколько писем обрабатывать (1–50)")

        self.get_mail_btn = QPushButton("Получить письма")
        self.get_mail_btn.clicked.connect(self._get_mail_clicked)

        self.get_mail_result_label = QLabel("—")
        self.get_mail_result_label.setStyleSheet("color: #555; font-weight: 700;")
        self.get_mail_result_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row_get.addWidget(QLabel("Писем:"), 0)
        row_get.addWidget(self.mail_count_spin, 0)
        row_get.addWidget(self.get_mail_btn, 0)
        row_get.addWidget(self.get_mail_result_label, 1)
        v_check.addLayout(row_get, 0)

        root.addWidget(g_check, 0)
        root.addStretch(1)

        self.client_combo.currentIndexChanged.connect(lambda _: self._refresh_client_status())
        self.check_finished.connect(self._on_check_finished)
        self.get_mail_finished.connect(self._on_get_mail_finished)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(800)
        self._status_timer.timeout.connect(self._refresh_client_status)
        self._status_timer.start()

        QTimer.singleShot(0, self._refresh_clients)
        QTimer.singleShot(0, lambda: self.set_run_active(False))

    def _log(self, text: str) -> None:
        fn = self._on_log
        if fn is None:
            return
        try:
            fn(str(text))
        except Exception:
            pass

    def _timings(self) -> MailboxTimings:
        fn = self._on_get_timings
        if fn is None:
            return MailboxTimings()
        try:
            t = fn()
            return t if isinstance(t, MailboxTimings) else MailboxTimings()
        except Exception:
            return MailboxTimings()

    def _confirm_specs(self) -> tuple[MailboxConfirmSpec, MailboxConfirmSpec]:
        fn = self._on_get_confirm_specs
        if fn is None:
            return (
                MailboxConfirmSpec((395, 324), (97, 20), (444, 333), "auto-delete"),
                MailboxConfirmSpec((395, 292), (97, 20), (444, 300), "manual-delete"),
            )
        try:
            auto, manual = fn()
            if not isinstance(auto, MailboxConfirmSpec) or not isinstance(manual, MailboxConfirmSpec):
                raise TypeError("invalid confirm specs")
            return (auto, manual)
        except Exception:
            return (
                MailboxConfirmSpec((395, 324), (97, 20), (444, 333), "auto-delete"),
                MailboxConfirmSpec((395, 292), (97, 20), (444, 300), "manual-delete"),
            )

    @Slot(bool)
    def set_run_active(self, active: bool) -> None:
        self._run_active = bool(active)
        if self._run_active:
            self._cancel.clear()
        else:
            # при Stop просим остановить все текущие потоки/скрипты
            self._cancel.set()
            self._busy = False
        self._update_enabled()
        self._log(f"[MAILBOX] UI: run_active={self._run_active}")

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._update_enabled()

    def _bring_focus_back_to_gui(self) -> None:
        try:
            w = self.window()
            w.raise_()
            w.activateWindow()
        except Exception:
            pass

    def _set_result_ok(self, text: str = "OK") -> None:
        self.check_result_label.setText(str(text))
        self.check_result_label.setStyleSheet("color: #2e7d32; font-weight: 800;")

    def _set_result_error(self, text: str) -> None:
        self.check_result_label.setText(str(text))
        self.check_result_label.setStyleSheet("color: #b00020; font-weight: 800;")

    def _set_result_busy(self, text: str = "Проверяю…") -> None:
        self.check_result_label.setText(str(text))
        self.check_result_label.setStyleSheet("color: #555; font-weight: 700;")

    def _set_get_mail_result_ok(self) -> None:
        self.get_mail_result_label.setText("Успех")
        self.get_mail_result_label.setToolTip("")
        self.get_mail_result_label.setStyleSheet("color: #2e7d32; font-weight: 800;")

    def _set_get_mail_result_fail(self, reason: str) -> None:
        self.get_mail_result_label.setText("Провал")
        self.get_mail_result_label.setToolTip(str(reason or "").strip())
        self.get_mail_result_label.setStyleSheet("color: #b00020; font-weight: 800;")

    def _set_get_mail_result_busy(self, text: str = "Работаю…") -> None:
        self.get_mail_result_label.setText(str(text))
        self.get_mail_result_label.setToolTip("")
        self.get_mail_result_label.setStyleSheet("color: #555; font-weight: 700;")

    def _set_controls_enabled(self, enabled: bool) -> None:
        # compatibility wrapper (old name) -> now controlled by _busy + run state
        self._set_busy(not bool(enabled))

    def _update_enabled(self) -> None:
        not_busy = not bool(self._busy)
        run_ok = bool(self._run_active)

        # выбор клиента и обновление списка доступны даже без Run
        self.refresh_btn.setEnabled(not_busy)
        self.client_combo.setEnabled(not_busy)

        # action buttons only in Run
        self.check_btn.setEnabled(run_ok and not_busy)
        self.mail_count_spin.setEnabled(run_ok and not_busy)
        self.get_mail_btn.setEnabled(run_ok and not_busy)

    def _refresh_clients(self) -> None:
        try:
            self._clients = list(self._on_get_clients() or [])
        except Exception:
            self._clients = []

        current_text = str(self.client_combo.currentText() or "")
        self.client_combo.blockSignals(True)
        try:
            self.client_combo.clear()
            for c in self._clients:
                nick = str(c.nickname or "").strip()
                if not nick:
                    continue
                self.client_combo.addItem(c.label(), nick)  # store nickname
            if current_text:
                idx = self.client_combo.findText(current_text)
                if idx >= 0:
                    self.client_combo.setCurrentIndex(idx)
        finally:
            self.client_combo.blockSignals(False)

        self._refresh_client_status()
        self._update_enabled()

    def _selected_nickname(self) -> str:
        idx = int(self.client_combo.currentIndex())
        if idx < 0:
            return ""
        try:
            return str(self.client_combo.itemData(idx) or "").strip()
        except Exception:
            return str(self.client_combo.currentText() or "").strip()

    def _resolve_client(self, nickname: str) -> ClientItem | None:
        nickname = str(nickname or "").strip()
        if not nickname:
            return None
        for c in self._clients:
            if str(c.nickname or "").strip() == nickname:
                return c
        return None

    def _refresh_client_status(self) -> None:
        nick = self._selected_nickname()
        if not nick:
            self.client_status.setText("Статус: клиент не выбран.")
            return
        c = self._resolve_client(nick)
        pid = int(getattr(c, "pid", 0) or 0) if c is not None else 0
        hwnd = int(getattr(c, "hwnd", 0) or 0) if c is not None else 0
        if pid <= 0 or hwnd <= 0:
            self.client_status.setText(f"Статус: выключен (ник={nick!r}).")
            return
        if not pid_exists(pid):
            self.client_status.setText(f"Статус: процесс PID={pid} не существует (ник={nick!r}).")
            return
        self.client_status.setText(f"Статус: активно (ник={nick!r}, PID={pid}, HWND={hwnd}).")

    def _check_clicked(self) -> None:
        if not self._run_active:
            self._set_result_error("Сначала нажмите Run")
            return
        nickname = self._selected_nickname()
        if not nickname:
            self._set_result_error("Выберите клиента")
            return

        c = self._resolve_client(nickname)
        pid = int(getattr(c, "pid", 0) or 0) if c is not None else 0
        hwnd = int(getattr(c, "hwnd", 0) or 0) if c is not None else 0
        if pid <= 0 or hwnd <= 0:
            self._set_result_error("Клиент не активен")
            return

        if not pid_exists(pid):
            self._set_result_error("Процесс не существует")
            return

        # prevent concurrent checks
        if not self._check_lock.acquire(blocking=False):
            return

        self._set_busy(True)
        self._set_result_busy("Проверяю…")
        self._log(f"[MAILBOX] Проверка окна почты: nick={nickname!r}, pid={pid}, hwnd={hwnd}")

        def _worker() -> None:
            ok = False
            msg = ""
            try:
                if self._cancel.is_set():
                    raise MailboxCancelledError("Остановлено (плагин выключен)")
                mgr = MailboxManager.for_hwnd(
                    hwnd=int(hwnd),
                    window_title_substring=self._window_title,
                    log=self._log,
                    cancel=lambda: bool(self._cancel.is_set() or (not self._run_active)),
                    timings=self._timings(),
                    confirm_auto_delete=self._confirm_specs()[0],
                    confirm_manual_delete=self._confirm_specs()[1],
                )
                hit = mgr.check_mailbox_window(timeout_s=0.8)
                ok = hit is not None
                msg = "OK" if ok else "Окно почтового ящика ненайдено"
            except MailboxCancelledError as e:
                ok = False
                msg = str(e)
                self._log(f"[MAILBOX] check cancelled: {e}")
            except Exception as e:
                ok = False
                msg = f"Ошибка: {e}"
                self._log(f"[MAILBOX] Ошибка проверки окна почты: {e}")
            finally:
                self.check_finished.emit(bool(ok), str(msg))

        threading.Thread(target=_worker, name="mailbox-check", daemon=True).start()

    def _get_mail_clicked(self) -> None:
        if not self._run_active:
            self._set_get_mail_result_fail("Сначала нажмите Run")
            return
        nickname = self._selected_nickname()
        if not nickname:
            self._set_get_mail_result_fail("Клиент не выбран")
            return

        c = self._resolve_client(nickname)
        pid = int(getattr(c, "pid", 0) or 0) if c is not None else 0
        hwnd = int(getattr(c, "hwnd", 0) or 0) if c is not None else 0
        if pid <= 0 or hwnd <= 0:
            self._set_get_mail_result_fail("Клиент не активен")
            return

        if not pid_exists(pid):
            self._set_get_mail_result_fail("Процесс не существует")
            return

        # prevent concurrent run
        if not self._get_mail_lock.acquire(blocking=False):
            return

        self._set_busy(True)
        self._set_get_mail_result_busy("Работаю…")
        mail_limit = int(self.mail_count_spin.value())
        self._log(f"[MAILBOX] Получить письма: старт nick={nickname!r}, pid={pid}, hwnd={hwnd}, limit={mail_limit}")

        def _worker() -> None:
            ok = False
            msg = ""
            try:
                mgr = MailboxManager.for_hwnd(
                    hwnd=int(hwnd),
                    window_title_substring=self._window_title,
                    log=self._log,
                    cancel=lambda: bool(self._cancel.is_set() or (not self._run_active)),
                    timings=self._timings(),
                    confirm_auto_delete=self._confirm_specs()[0],
                    confirm_manual_delete=self._confirm_specs()[1],
                )
                mgr.prepare_get_mails(mail_limit=int(mail_limit))
                ok = True
                msg = "ok"
                self._log("[MAILBOX] Получить письма: завершено успешно (пока только этап вкладки).")
            except MailboxCancelledError as e:
                ok = False
                msg = str(e)
                self._log(f"[MAILBOX] get-mails cancelled: {e}")
            except Exception as e:
                ok = False
                msg = str(e)
                self._log(f"[MAILBOX] Получить письма: ПРОВАЛ: {e}")
            finally:
                self.get_mail_finished.emit(bool(ok), str(msg))

        threading.Thread(target=_worker, name="mailbox-get-mails", daemon=True).start()

    @Slot(bool, str)
    def _on_check_finished(self, ok: bool, msg: str) -> None:
        try:
            if ok:
                self._set_result_ok(msg or "OK")
            else:
                self._set_result_error(msg or "Окно почтового ящика ненайдено")
        finally:
            self._set_busy(False)
            try:
                self._check_lock.release()
            except Exception:
                pass
            # вернуть фокус обратно в GUI
            QTimer.singleShot(50, self._bring_focus_back_to_gui)

    @Slot(bool, str)
    def _on_get_mail_finished(self, ok: bool, msg: str) -> None:
        try:
            if ok:
                self._set_get_mail_result_ok()
            else:
                self._set_get_mail_result_fail(msg or "Ошибка")
        finally:
            self._set_busy(False)
            try:
                self._get_mail_lock.release()
            except Exception:
                pass
            QTimer.singleShot(50, self._bring_focus_back_to_gui)

