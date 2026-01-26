from __future__ import annotations

# pylint: disable=import-error,no-name-in-module,broad-exception-caught
import json
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QMetaObject, Qt, Q_ARG, QTimer, Slot

from sa_ui_operations import IntegerSetting, PluginInterface

from ...constants import (
    LAUNCHER_COMMAND_SETTING_KEY,
    LAUNCHER_ROWS_JSON_GLOBAL_KEY,
    LAUNCHER_WINDOWS_JSON_GLOBAL_KEY,
    AUTOLOGIN_ENTER_CHAR_TIMEOUT_SECONDS_SETTING_KEY,
    AUTOLOGIN_ERROR_POLICY_SETTING_KEY,
    AUTOLOGIN_LOGIN_TIMEOUT_SECONDS_SETTING_KEY,
    AUTOLOGIN_PIN_BLOCK_TIMEOUT_SECONDS_SETTING_KEY,
    AUTOLOGIN_PIN_DELAY_MS_SETTING_KEY,
    AUTOLOGIN_PIN_DIGIT_TIMEOUT_SECONDS_SETTING_KEY,
    AUTOLOGIN_RETRY_ATTEMPTS_SETTING_KEY,
    AUTOLOGIN_SELECT_SERVER_TIMEOUT_SECONDS_SETTING_KEY,
    AUTOLOGIN_WAIT_HWND_TIMEOUT_SECONDS_SETTING_KEY,
    LOGIN_ENTER_DELAY_SECONDS_SETTING_KEY,
    REFRESH_INTERVAL_SECONDS_SETTING_KEY,
)
from ..utils import login_state
from ..utils.windows import (
    WindowInfo,
    find_hwnd_by_pid_and_exact_title,
    focus_hwnd,
    get_foreground_pid,
    list_visible_windows_with_exact_title,
    pid_exists,
    terminate_process,
)
from .ui import LaunchRowWidget, LauncherWidget
from ....modules.login.auto_login import (
    ENTER_CHAR_TIMEOUT_S_DEFAULT,
    LOGIN_TIMEOUT_S_DEFAULT,
    PIN_AFTER_DIGIT_MOVE_DELAY_S,
    PIN_BLOCK_TIMEOUT_S_DEFAULT,
    PIN_DIGIT_TIMEOUT_S_DEFAULT,
    SELECT_SERVER_TIMEOUT_S_DEFAULT,
    auto_login,
)


WINDOW_TITLE = "Requiem"


class LauncherPlugin(PluginInterface):
    """Плагин: запуск новых окон и мониторинг активных окон Requiem."""

    def __init__(self) -> None:
        self._widgets: dict[str, LauncherWidget] = {}
        self._tab_contexts: dict[str, object] = {}
        self._console_out: dict[str, object] = {}
        self._monitoring_active: dict[str, bool] = {}
        # QObject living in UI thread, for thread-safe Qt/QSettings operations.
        self._ui_bridge: dict[str, QObject] = {}

        self._row_ids: dict[str, list[str]] = {}
        self._row_login: dict[str, str] = {}
        self._row_password: dict[str, str] = {}
        self._row_slot: dict[str, int] = {}
        self._row_nickname: dict[str, str] = {}
        self._row_pin: dict[str, str] = {}
        self._row_selected: dict[str, bool] = {}
        # sequential multi-start (UI-thread via QTimer)
        self._seq_timer: dict[str, QTimer] = {}
        self._seq_queue: dict[str, list[str]] = {}
        self._seq_current: dict[str, str] = {}
        self._seq_deadline_ts: dict[str, float] = {}
        self._seq_attempt: dict[str, int] = {}
        self._row_autologin_done: dict[str, threading.Event] = {}
        self._row_autologin_ok: dict[str, bool] = {}
        self._row_proc: dict[str, subprocess.Popen] = {}
        self._row_pid: dict[str, int] = {}

        # auto-login workers per row (best-effort)
        self._row_autologin_cancel: dict[str, threading.Event] = {}
        self._row_autologin_thread: dict[str, threading.Thread] = {}

        # multi-start UI mode per tab: off | select | running
        self._multi_mode: dict[str, str] = {}
        # cached refresh interval (to avoid reading QSettings from worker thread)
        self._refresh_interval_seconds_cache: dict[str, int] = {}

    def get_key(self) -> str:
        return "launcher_plugin"

    def get_title(self) -> str:
        return "Requiem: Launcher"

    def get_settings(self):
        # Настройки этой вкладки (не глобальные)
        return [
            IntegerSetting(
                key=LOGIN_ENTER_DELAY_SECONDS_SETTING_KEY,
                label="Задержка перед Enter при логине (сек)",
                default_value=1,
                description="Минимум 0. Используется только для этой вкладки.",
            )
            ,
            IntegerSetting(
                key=AUTOLOGIN_WAIT_HWND_TIMEOUT_SECONDS_SETTING_KEY,
                label="Автологин: ждать окно Requiem после запуска (сек)",
                default_value=90,
                description="Минимум 0. 0 = не ждать. Используется в автозапуске.",
            ),
            IntegerSetting(
                key=AUTOLOGIN_LOGIN_TIMEOUT_SECONDS_SETTING_KEY,
                label="Автологин: таймаут экрана логина (сек)",
                default_value=int(LOGIN_TIMEOUT_S_DEFAULT),
                description="Минимум 0. 0 = ждать бесконечно (до отмены).",
            ),
            IntegerSetting(
                key=AUTOLOGIN_SELECT_SERVER_TIMEOUT_SECONDS_SETTING_KEY,
                label="Автологин: таймаут выбора сервера (сек)",
                default_value=int(SELECT_SERVER_TIMEOUT_S_DEFAULT),
                description="Минимум 0. 0 = ждать бесконечно (до отмены).",
            ),
            IntegerSetting(
                key=AUTOLOGIN_ENTER_CHAR_TIMEOUT_SECONDS_SETTING_KEY,
                label="Автологин: таймаут выбора персонажа (сек)",
                default_value=int(ENTER_CHAR_TIMEOUT_S_DEFAULT),
                description="Минимум 0. 0 = ждать бесконечно (до отмены).",
            ),
            IntegerSetting(
                key=AUTOLOGIN_PIN_BLOCK_TIMEOUT_SECONDS_SETTING_KEY,
                label="Автологин: таймаут поиска PIN-блока (сек)",
                default_value=int(PIN_BLOCK_TIMEOUT_S_DEFAULT),
                description="Минимум 0. 0 = ждать бесконечно (до отмены).",
            ),
            IntegerSetting(
                key=AUTOLOGIN_PIN_DIGIT_TIMEOUT_SECONDS_SETTING_KEY,
                label="Автологин: таймаут поиска цифры PIN (сек)",
                default_value=int(PIN_DIGIT_TIMEOUT_S_DEFAULT),
                description="Минимум 0. 0 = ждать бесконечно (до отмены).",
            ),
            IntegerSetting(
                key=AUTOLOGIN_PIN_DELAY_MS_SETTING_KEY,
                label="Автологин: задержка между цифрами PIN (мс)",
                default_value=int(float(PIN_AFTER_DIGIT_MOVE_DELAY_S) * 1000.0),
                description="Минимум 0.",
            ),
            IntegerSetting(
                key=AUTOLOGIN_ERROR_POLICY_SETTING_KEY,
                label="Автозапуск: реакция на ошибку (0-пропустить,1-повторить,2-остановить)",
                default_value=1,
                description="Применяется в последовательном запуске при ok=False/timeout.",
            ),
            IntegerSetting(
                key=AUTOLOGIN_RETRY_ATTEMPTS_SETTING_KEY,
                label="Автозапуск: попыток (для режима 'повторить')",
                default_value=2,
                description="Минимум 1. Общее число попыток на строку.",
            ),
        ]

    def create_widget(self, tab_context):
        tab_id = str(getattr(tab_context, "tab_id", ""))
        self._tab_contexts[tab_id] = tab_context
        self._row_ids.setdefault(tab_id, [])

        self._restore_rows_from_settings(tab_id)

        def _on_add_row() -> None:
            self._add_launch_row(tab_id)

        def _on_focus_check(pid: int) -> None:
            self._focus_check_pid(tab_id, int(pid))

        def _on_override(pid: int, login: str) -> None:
            self._override_login_pid(tab_id, str(login).strip(), int(pid))

        def _on_multi_clicked() -> None:
            self._handle_multi_button(tab_id)

        w = LauncherWidget(
            on_add_row=_on_add_row,
            on_multi_clicked=_on_multi_clicked,
            on_focus_check=_on_focus_check,
            on_override_login=_on_override,
            on_sync_state=lambda: self._sync_ui_state(tab_id),
        )
        self._widgets[tab_id] = w
        self._multi_mode.setdefault(tab_id, "off")
        # Cache refresh interval in UI thread
        try:
            self._refresh_interval_seconds_cache[tab_id] = int(self._get_refresh_interval_seconds(tab_id))
        except Exception:
            self._refresh_interval_seconds_cache[tab_id] = 10

        # UI-thread bridge: любые вызовы QSettings/save_global_value — только здесь.
        plugin = self
        ctx = tab_context

        class _UiBridge(QObject):
            @Slot(str)
            def save_windows_snapshot_json(self, payload: str) -> None:
                try:
                    ctx.save_global_value(LAUNCHER_WINDOWS_JSON_GLOBAL_KEY, str(payload))
                except Exception:
                    pass

            @Slot()
            def persist_rows(self) -> None:
                try:
                    # pylint: disable=protected-access
                    plugin._persist_rows_to_settings(tab_id)
                except Exception:
                    pass

        self._ui_bridge[tab_id] = _UiBridge(parent=w)

        # timer lives in UI thread (parent = widget)
        if tab_id not in self._seq_timer:
            t = QTimer(w)
            t.setInterval(200)
            t.timeout.connect(lambda tid=tab_id: self._seq_tick(tid))
            self._seq_timer[tab_id] = t

        if not self._row_ids.get(tab_id):
            self._add_launch_row(tab_id)
        else:
            for row_id in list(self._row_ids.get(tab_id, [])):
                self._ensure_row_widget(tab_id, row_id)

        return w

    # -----------------
    # Console
    # -----------------
    def _console(self, tab_id: str, text: str) -> None:
        fn = self._console_out.get(tab_id)
        if fn is not None:
            fn(str(text))

    def _set_error(self, tab_id: str, msg: str) -> None:
        self._console(tab_id, f"[ERROR] {msg}")

    # -----------------
    # Persistence (per tab)
    # -----------------
    def _settings_key_rows(self) -> str:
        return "launcher/rows_json"

    def _restore_rows_from_settings(self, tab_id: str) -> None:
        ctx = self._tab_contexts.get(tab_id)
        if ctx is None:
            return
        try:
            raw = ctx.settings.value(ctx.key(self._settings_key_rows()), "", type=str)
        except Exception:
            raw = ""
        raw = str(raw or "")
        if not raw.strip():
            # Если у вкладки пусто (например, новая вкладка), пробуем взять общий (global) слепок.
            try:
                raw = str(ctx.get_global_value(LAUNCHER_ROWS_JSON_GLOBAL_KEY, "", value_type=str) or "")
            except Exception:
                raw = ""
        if not raw.strip():
            return
        try:
            data = json.loads(raw)
        except Exception:
            return
        if not isinstance(data, list):
            return
        self._row_ids[tab_id] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            row_id = f"{tab_id}_row_{i}"
            self._row_ids[tab_id].append(row_id)
            self._row_login[row_id] = str(item.get("login", "") or "").strip()
            # пароль сохраняем по запросу пользователя (осторожно: хранится в QSettings как текст)
            self._row_password[row_id] = str(item.get("password", "") or "")
            try:
                slot = int(item.get("slot", 1) or 1)
            except Exception:
                slot = 1
            if slot < 1:
                slot = 1
            if slot > 8:
                slot = 8
            self._row_slot[row_id] = slot
            self._row_nickname[row_id] = str(item.get("nickname", "") or "").strip()
            self._row_pin[row_id] = str(item.get("pin", "") or "").strip()[:4]
            # selection is session-only (do not persist) to avoid UI lag
            self._row_selected[row_id] = False
            try:
                self._row_pid[row_id] = int(item.get("pid", 0) or 0)
            except Exception:
                self._row_pid[row_id] = 0

    def _persist_rows_to_settings(self, tab_id: str) -> None:
        ctx = self._tab_contexts.get(tab_id)
        if ctx is None:
            return
        rows = []
        for row_id in self._row_ids.get(tab_id, []):
            rows.append(
                {
                    "login": str(self._row_login.get(row_id, "") or "").strip(),
                    "password": str(self._row_password.get(row_id, "") or ""),
                    "slot": int(self._row_slot.get(row_id, 1) or 1),
                    "nickname": str(self._row_nickname.get(row_id, "") or "").strip(),
                    "pin": str(self._row_pin.get(row_id, "") or "").strip()[:4],
                    "pid": int(self._row_pid.get(row_id, 0) or 0),
                }
            )
        try:
            payload = json.dumps(rows, ensure_ascii=False)
            ctx.save_value(self._settings_key_rows(), payload)
            # Дублируем в глобальное состояние, чтобы другие плагины могли видеть список клиентов.
            ctx.save_global_value(LAUNCHER_ROWS_JSON_GLOBAL_KEY, payload)
        except Exception:
            pass

    # -----------------
    # Helpers
    # -----------------
    @staticmethod
    def _parse_command(cmd: str) -> list[str]:
        return shlex.split(cmd, posix=False)

    def _get_refresh_interval_seconds(self, tab_id: str) -> int:
        ctx = self._tab_contexts.get(tab_id)
        default_v = 10
        if ctx is None:
            return default_v
        try:
            v = int(
                ctx.get_global_value(
                    f"settings/{REFRESH_INTERVAL_SECONDS_SETTING_KEY}",
                    default_v,
                    value_type=int,
                )
            )
        except Exception:
            v = default_v
        if v < 1:
            self._console(tab_id, "[WARN] Частота обновления меньше 1 сек — будет использовано значение 1 сек.")
            return 1
        return v

    def _get_refresh_interval_seconds_cached(self, tab_id: str) -> int:
        v = int(self._refresh_interval_seconds_cache.get(tab_id, 10) or 10)
        if v < 1:
            return 1
        return v

    def _get_login_enter_delay_seconds(self, tab_id: str) -> float:
        ctx = self._tab_contexts.get(tab_id)
        default_v = 1
        if ctx is None:
            return float(default_v)
        key_local = f"settings/{LOGIN_ENTER_DELAY_SECONDS_SETTING_KEY}"
        try:
            # tab-local value (preferred)
            settings_key = ctx.key(key_local)
            if ctx.settings.contains(settings_key):
                v = float(ctx.settings.value(settings_key, default_v, type=int))
            else:
                # migration: if user had it in global settings earlier, reuse once
                v = float(ctx.get_global_value(key_local, default_v, value_type=int))
                ctx.save_value(key_local, int(v))
        except Exception:
            v = float(default_v)
        if v < 0:
            return 0.0
        return float(v)

    def _get_tab_int_setting(self, tab_id: str, *, key: str, default_v: int, min_v: int = 0) -> int:
        ctx = self._tab_contexts.get(tab_id)
        if ctx is None:
            return int(max(min_v, int(default_v)))
        key_local = f"settings/{key}"
        try:
            settings_key = ctx.key(key_local)
            if ctx.settings.contains(settings_key):
                v = int(ctx.settings.value(settings_key, default_v, type=int))
            else:
                v = int(default_v)
        except Exception:
            v = int(default_v)
        if v < int(min_v):
            return int(min_v)
        return int(v)

    def _get_autologin_error_policy(self, tab_id: str) -> int:
        # 0=skip, 1=retry, 2=stop
        v = self._get_tab_int_setting(tab_id, key=AUTOLOGIN_ERROR_POLICY_SETTING_KEY, default_v=1, min_v=0)
        if v > 2:
            return 2
        return int(v)

    def _get_autologin_retry_attempts(self, tab_id: str) -> int:
        v = self._get_tab_int_setting(tab_id, key=AUTOLOGIN_RETRY_ATTEMPTS_SETTING_KEY, default_v=2, min_v=1)
        if v < 1:
            return 1
        return int(v)

    def _seq_deadline_seconds(self, tab_id: str) -> float:
        """
        Best-effort deadline for sequential start of one row.
        If any stage timeout is 0 (infinite), returns 0.0 (no deadline).
        """
        wait_hwnd = float(
            self._get_tab_int_setting(tab_id, key=AUTOLOGIN_WAIT_HWND_TIMEOUT_SECONDS_SETTING_KEY, default_v=90, min_v=0)
        )
        login_to = float(
            self._get_tab_int_setting(
                tab_id, key=AUTOLOGIN_LOGIN_TIMEOUT_SECONDS_SETTING_KEY, default_v=int(LOGIN_TIMEOUT_S_DEFAULT), min_v=0
            )
        )
        srv_to = float(
            self._get_tab_int_setting(
                tab_id,
                key=AUTOLOGIN_SELECT_SERVER_TIMEOUT_SECONDS_SETTING_KEY,
                default_v=int(SELECT_SERVER_TIMEOUT_S_DEFAULT),
                min_v=0,
            )
        )
        char_to = float(
            self._get_tab_int_setting(
                tab_id,
                key=AUTOLOGIN_ENTER_CHAR_TIMEOUT_SECONDS_SETTING_KEY,
                default_v=int(ENTER_CHAR_TIMEOUT_S_DEFAULT),
                min_v=0,
            )
        )
        pin_block_to = float(
            self._get_tab_int_setting(
                tab_id,
                key=AUTOLOGIN_PIN_BLOCK_TIMEOUT_SECONDS_SETTING_KEY,
                default_v=int(PIN_BLOCK_TIMEOUT_S_DEFAULT),
                min_v=0,
            )
        )
        pin_digit_to = float(
            self._get_tab_int_setting(
                tab_id,
                key=AUTOLOGIN_PIN_DIGIT_TIMEOUT_SECONDS_SETTING_KEY,
                default_v=int(PIN_DIGIT_TIMEOUT_S_DEFAULT),
                min_v=0,
            )
        )
        if any(x <= 0.0 for x in (wait_hwnd, login_to, srv_to, char_to, pin_block_to, pin_digit_to)):
            return 0.0
        # 4 digits worst-case + small buffer
        total = wait_hwnd + login_to + srv_to + char_to + pin_block_to + (4.0 * pin_digit_to) + 30.0
        if total < 30.0:
            return 30.0
        return float(total)

    def _rows_state(self, tab_id: str) -> list[login_state.LoginRowState]:
        out: list[login_state.LoginRowState] = []
        for row_id in self._row_ids.get(tab_id, []):
            out.append(
                login_state.LoginRowState(
                    row_id=row_id,
                    login=str(self._row_login.get(row_id, "") or "").strip(),
                    nickname=str(self._row_nickname.get(row_id, "") or "").strip(),
                    pid=int(self._row_pid.get(row_id, 0) or 0),
                )
            )
        return out

    def _row_password_value(self, row_id: str) -> str:
        return str(self._row_password.get(row_id, "") or "")

    def _row_slot_value(self, row_id: str) -> int:
        try:
            v = int(self._row_slot.get(row_id, 1) or 1)
        except Exception:
            v = 1
        if v < 1:
            return 1
        if v > 8:
            return 8
        return v

    def _row_nickname_value(self, row_id: str) -> str:
        return str(self._row_nickname.get(row_id, "") or "").strip()

    def _row_pin_value(self, row_id: str) -> str:
        raw = str(self._row_pin.get(row_id, "") or "")
        digits = "".join([c for c in raw if c.isdigit()])
        return digits[:4]

    def _cancel_autologin(self, row_id: str) -> None:
        ev = self._row_autologin_cancel.get(row_id)
        if ev is not None:
            ev.set()
        # если последовательный запуск ждёт завершения — размораживаем
        done = self._row_autologin_done.get(row_id)
        if done is not None:
            self._row_autologin_ok[row_id] = False
            done.set()

    def _cancel_all_autologin_for_tab(self, tab_id: str) -> None:
        for rid in list(self._row_ids.get(tab_id, [])):
            self._cancel_autologin(rid)

    def _seq_stop(self, tab_id: str) -> None:
        t = self._seq_timer.get(tab_id)
        if t is not None:
            # Важно: execute() у PluginInterface часто живёт в другом потоке,
            # поэтому stop() делаем через QueuedConnection (в поток владельца QTimer).
            try:
                QMetaObject.invokeMethod(t, "stop", Qt.QueuedConnection)
            except Exception:
                # fallback (если уже в UI-потоке)
                try:
                    t.stop()
                except Exception:
                    pass
        self._seq_queue.pop(tab_id, None)
        self._seq_current.pop(tab_id, None)
        self._seq_deadline_ts.pop(tab_id, None)
        self._seq_attempt.pop(tab_id, None)
        # reset multi-start UI
        self._multi_mode[tab_id] = "off"
        self._clear_multistart_selection(tab_id)
        self._sync_multistart_ui(tab_id)

    def _start_selected_sequential(self, tab_id: str) -> None:
        # запускаем в UI-потоке (кнопка из UI), без python-thread
        widget = self._widgets.get(tab_id)
        if widget is None:
            return
        if not self._monitoring_active.get(tab_id, False):
            self._console(tab_id, "[WARN] Сначала нажмите Run (мониторинг должен быть активен).")
            return

        selected_ids = [rid for rid in self._row_ids.get(tab_id, []) if bool(self._row_selected.get(rid, False))]
        if not selected_ids:
            self._console(tab_id, "[WARN] Мультизапуск: ничего не выбрано.")
            return

        self._seq_queue[tab_id] = list(selected_ids)
        self._seq_current.pop(tab_id, None)
        self._seq_deadline_ts.pop(tab_id, None)

        t = self._seq_timer.get(tab_id)
        if t is not None and not t.isActive():
            t.start()
        # run first tick immediately
        self._seq_tick(tab_id)

    def _seq_tick(self, tab_id: str) -> None:
        # Runs in UI thread (QTimer parented to widget).
        if not self._monitoring_active.get(tab_id, False):
            self._seq_stop(tab_id)
            return
        if tab_id not in self._seq_queue:
            # nothing to do
            t = self._seq_timer.get(tab_id)
            if t is not None:
                t.stop()
            return

        # If currently waiting for a row to finish autologin
        cur = self._seq_current.get(tab_id, "")
        if cur:
            ev = self._row_autologin_done.get(cur)
            if ev is not None and ev.is_set():
                ok = bool(self._row_autologin_ok.get(cur, False))
                if ok:
                    self._console(tab_id, f"[OK] Мультизапуск: nick={self._row_nickname_value(cur)!r} ok=True")
                    self._seq_current.pop(tab_id, None)
                    self._seq_deadline_ts.pop(tab_id, None)
                    self._seq_attempt.pop(tab_id, None)
                    return
                # ok=False -> apply policy
                if self._seq_handle_failure(tab_id, cur, reason="ok=false"):
                    return
                self._seq_current.pop(tab_id, None)
                self._seq_deadline_ts.pop(tab_id, None)
                self._seq_attempt.pop(tab_id, None)
                return

            deadline = float(self._seq_deadline_ts.get(tab_id, 0.0) or 0.0)
            if deadline > 0.0 and time.time() >= deadline:
                self._console(tab_id, f"[WARN] Мультизапуск: timeout nick={self._row_nickname_value(cur)!r}")
                self._cancel_autologin(cur)
                if self._seq_handle_failure(tab_id, cur, reason="deadline"):
                    return
                self._seq_current.pop(tab_id, None)
                self._seq_deadline_ts.pop(tab_id, None)
                self._seq_attempt.pop(tab_id, None)
            return

        # pick next
        queue = list(self._seq_queue.get(tab_id, []))
        active_logins = login_state.active_logins(self._rows_state(tab_id))
        active_nicks = login_state.active_nicknames(self._rows_state(tab_id))
        while queue:
            rid = queue.pop(0)
            # persist shortened queue
            self._seq_queue[tab_id] = list(queue)

            if int(self._row_pid.get(rid, 0) or 0) > 0:
                continue  # already running
            lg = str(self._row_login.get(rid, "") or "").strip()
            if lg and (lg in active_logins):
                continue  # login already running elsewhere
            nk = str(self._row_nickname.get(rid, "") or "").strip()
            if nk and (nk in active_nicks):
                continue  # nickname already running elsewhere

            self._console(tab_id, f"[RUN] Мультизапуск: старт nick={self._row_nickname_value(rid)!r}")
            self._launch_new_window(tab_id, rid)

            # if autologin not started (invalid fields) -> continue
            ev = self._row_autologin_done.get(rid)
            if ev is None:
                continue
            self._seq_current[tab_id] = rid
            self._seq_attempt[tab_id] = 1
            d = float(self._seq_deadline_seconds(tab_id))
            self._seq_deadline_ts[tab_id] = 0.0 if d <= 0.0 else float(time.time() + d)
            return

        # done
        self._console(tab_id, "[OK] Мультизапуск: завершён")
        self._seq_stop(tab_id)

    def _sync_ui_state(self, tab_id: str) -> None:
        widget = self._widgets.get(tab_id)
        if widget is None or not hasattr(widget, "_row_widgets_by_id"):
            return

        monitoring_on = bool(self._monitoring_active.get(tab_id, False))
        widget.set_run_mode(monitoring_on=monitoring_on)
        rows = self._rows_state(tab_id)

        # clear stale pids when process doesn't exist
        changed = False
        for r in rows:
            if r.is_active and not pid_exists(int(r.pid)):
                self._console(tab_id, f"[WARN] PID={r.pid} (login={r.login!r}) не существует -> сброс.")
                self._row_pid[r.row_id] = 0
                self._row_proc.pop(r.row_id, None)
                self._cancel_autologin(r.row_id)
                changed = True
        if changed:
            self._persist_rows_to_settings(tab_id)
            rows = self._rows_state(tab_id)

        active_logins = login_state.active_logins(rows)
        active_nicks = login_state.active_nicknames(rows)

        # nick uniqueness among all rows
        nick_counts: dict[str, int] = {}
        for r in rows:
            n = str(r.nickname or "").strip()
            if n:
                nick_counts[n] = nick_counts.get(n, 0) + 1

        multi_mode = str(self._multi_mode.get(tab_id, "off") or "off")
        multistart_select = monitoring_on and (multi_mode == "select")
        selected_nicks: set[str] = set()
        selected_logins: set[str] = set()
        for rid in self._row_ids.get(tab_id, []):
            if bool(self._row_selected.get(rid, False)):
                nk = str(self._row_nickname.get(rid, "") or "").strip()
                if nk:
                    selected_nicks.add(nk)
                lg = str(self._row_login.get(rid, "") or "").strip()
                if lg:
                    selected_logins.add(lg)
        ordered_ids = list(self._row_ids.get(tab_id, []))
        row_widgets_by_id: dict[str, LaunchRowWidget] = getattr(widget, "_row_widgets_by_id")

        for idx, row_id in enumerate(ordered_ids):
            row_w = row_widgets_by_id.get(row_id)
            if row_w is None:
                continue
            login = str(self._row_login.get(row_id, "") or "").strip()
            password = self._row_password_value(row_id)
            slot = self._row_slot_value(row_id)
            pin = self._row_pin_value(row_id)
            nick = self._row_nickname_value(row_id)
            pid = int(self._row_pid.get(row_id, 0) or 0)
            is_active = pid > 0

            same_login_active_elsewhere = (not is_active) and bool(login) and (login in active_logins)
            same_nick_active_elsewhere = (not is_active) and bool(nick) and (nick in active_nicks)
            nick_unique = (not nick) or (nick_counts.get(nick, 0) <= 1)

            start_enabled = (
                monitoring_on
                and (not is_active)
                and (not same_login_active_elsewhere)
                and (not same_nick_active_elsewhere)
                and bool(login)
                and bool(password)
                and (1 <= int(slot) <= 8)
                and bool(nick)
                and bool(nick_unique)
                and (len(str(pin)) == 4)
            )
            terminate_enabled = monitoring_on and bool(is_active)
            check_enabled = monitoring_on and bool(is_active)
            focus_toggle_enabled = monitoring_on and bool(is_active)

            # По требованию: при RUN запрещаем менять строки и скрываем удаление
            allow_edit = (not monitoring_on) and (not is_active)
            allow_delete = (not monitoring_on) and (not is_active)

            move_up_enabled = (not monitoring_on) and (idx > 0)
            move_down_enabled = (not monitoring_on) and (idx < (len(ordered_ids) - 1))

            row_w.set_state(
                select_visible=bool(multistart_select),
                selected=bool(self._row_selected.get(row_id, False)),
                select_enabled=False,  # managed отдельно в _sync_multistart_ui()
                nickname_ok=bool(nick) and bool(nick_unique),
                pid=pid,
                is_active=is_active,
                start_enabled=start_enabled,
                terminate_enabled=terminate_enabled,
                check_enabled=check_enabled,
                focus_toggle_enabled=focus_toggle_enabled,
                allow_edit=allow_edit,
                allow_delete=allow_delete,
                move_up_enabled=move_up_enabled,
                move_down_enabled=move_down_enabled,
            )

        # available nicknames for monitor rows: only those not already active
        # UI показывает "ник (логин)", но внутреннее значение должно оставаться "ник".
        nicknames = login_state.unique_nicknames_in_order(rows)
        nick_to_login: dict[str, str] = {}
        for r in rows:
            nk = str(r.nickname or "").strip()
            if nk and nk not in nick_to_login:
                nick_to_login[nk] = str(r.login or "").strip()
        available = [
            {"nickname": nk, "login": str(nick_to_login.get(nk, "") or "").strip()}
            for nk in nicknames
            if nk not in active_nicks
        ]
        widget.logins_changed.emit(available)  # signal name kept for compatibility
        self._sync_multistart_ui(tab_id)

    # -----------------
    # Multi-start UI / selection (no persistence)
    # -----------------
    def _clear_multistart_selection(self, tab_id: str) -> None:
        for rid in list(self._row_ids.get(tab_id, [])):
            self._row_selected[rid] = False

    def _sync_multistart_ui(self, tab_id: str) -> None:
        widget = self._widgets.get(tab_id)
        if widget is None or not hasattr(widget, "_row_widgets_by_id"):
            return

        monitoring_on = bool(self._monitoring_active.get(tab_id, False))
        mode = str(self._multi_mode.get(tab_id, "off") or "off").strip().lower()

        # button + loader
        if not monitoring_on:
            widget.set_multi_ui(mode="hidden", enabled=False)
        elif mode == "running":
            widget.set_multi_ui(mode="running", enabled=False)
        elif mode == "select":
            any_selected = any(bool(self._row_selected.get(rid, False)) for rid in self._row_ids.get(tab_id, []))
            widget.set_multi_ui(mode="ready", enabled=bool(any_selected))
        else:
            widget.set_multi_ui(mode="arm", enabled=True)

        # checkboxes only in select mode, enabled only when nickname is not running
        rows = self._rows_state(tab_id)
        active_nicks = login_state.active_nicknames(rows)
        nick_counts: dict[str, int] = {}
        for r in rows:
            n = str(r.nickname or "").strip()
            if n:
                nick_counts[n] = nick_counts.get(n, 0) + 1

        selected_nicks: set[str] = set()
        selected_logins: set[str] = set()
        for rid in self._row_ids.get(tab_id, []):
            if bool(self._row_selected.get(rid, False)):
                nk = str(self._row_nickname.get(rid, "") or "").strip()
                if nk:
                    selected_nicks.add(nk)
                lg = str(self._row_login.get(rid, "") or "").strip()
                if lg:
                    selected_logins.add(lg)

        row_widgets_by_id: dict[str, LaunchRowWidget] = getattr(widget, "_row_widgets_by_id")
        for rid in self._row_ids.get(tab_id, []):
            row_w = row_widgets_by_id.get(rid)
            if row_w is None:
                continue
            nk = str(self._row_nickname.get(rid, "") or "").strip()
            lg = str(self._row_login.get(rid, "") or "").strip()
            pid = int(self._row_pid.get(rid, 0) or 0)
            is_active = pid > 0

            visible = monitoring_on and (mode == "select")
            enabled = (
                visible
                and (not is_active)
                and bool(nk)
                and (nk not in active_nicks)
                and (nick_counts.get(nk, 0) <= 1)
            )
            # блокируем остальные чекбоксы с тем же ником
            if (nk in selected_nicks) and (not bool(self._row_selected.get(rid, False))):
                enabled = False
            # блокируем остальные чекбоксы с тем же логином
            if lg and (lg in selected_logins) and (not bool(self._row_selected.get(rid, False))):
                enabled = False

            try:
                row_w.set_multistart_state(
                    visible=visible,
                    checked=bool(self._row_selected.get(rid, False)),
                    enabled=bool(enabled),
                )
            except Exception:
                pass

    def _handle_multi_button(self, tab_id: str) -> None:
        # state machine:
        #  off -> select (show checkboxes)
        #  select -> running (start sequential)
        #  running -> ignore
        if not bool(self._monitoring_active.get(tab_id, False)):
            return

        mode = str(self._multi_mode.get(tab_id, "off") or "off").strip().lower()
        if mode == "running":
            return

        if mode == "off":
            self._multi_mode[tab_id] = "select"
            self._clear_multistart_selection(tab_id)
            self._sync_multistart_ui(tab_id)
            return

        if mode == "select":
            # start sequential
            if not any(bool(self._row_selected.get(rid, False)) for rid in self._row_ids.get(tab_id, [])):
                self._sync_multistart_ui(tab_id)
                return
            self._multi_mode[tab_id] = "running"
            self._sync_multistart_ui(tab_id)
            self._start_selected_sequential(tab_id)
            return

    def _seq_handle_failure(self, tab_id: str, row_id: str, *, reason: str) -> bool:
        """
        Returns True if sequence should keep waiting (e.g. retry started) or was stopped.
        Returns False if caller should proceed to next row.
        """
        policy = int(self._get_autologin_error_policy(tab_id))
        attempts_max = int(self._get_autologin_retry_attempts(tab_id))
        attempt = int(self._seq_attempt.get(tab_id, 1) or 1)

        nick = self._row_nickname_value(row_id)
        if policy == 2:
            self._console(tab_id, f"[STOP] Мультизапуск: ошибка ({reason}) nick={nick!r} -> остановить")
            self._seq_stop(tab_id)
            return True

        if policy == 1 and attempt < attempts_max:
            next_attempt = attempt + 1
            self._seq_attempt[tab_id] = next_attempt
            self._console(tab_id, f"[RUN] Мультизапуск: повтор {next_attempt}/{attempts_max} nick={nick!r} ({reason})")
            # retry = terminate process + relaunch (existing functions)
            try:
                self._terminate_row_process(tab_id, row_id)
            except Exception as e:
                self._console(tab_id, f"[ERROR] Мультизапуск: не удалось завершить процесс для retry: {e}")
                self._seq_stop(tab_id)
                return True
            self._launch_new_window(tab_id, row_id)
            ev = self._row_autologin_done.get(row_id)
            if ev is None:
                # relaunch failed -> treat as skipped
                return False
            self._seq_current[tab_id] = row_id
            d = float(self._seq_deadline_seconds(tab_id))
            self._seq_deadline_ts[tab_id] = 0.0 if d <= 0.0 else float(time.time() + d)
            return True

        # skip (default) or retries exhausted
        if policy == 1 and attempt >= attempts_max:
            self._console(tab_id, f"[WARN] Мультизапуск: попытки исчерпаны {attempt}/{attempts_max} nick={nick!r} -> пропуск")
        return False

    # -----------------
    # Row widgets
    # -----------------
    def _new_row_id(self, tab_id: str) -> str:
        return f"{tab_id}_{int(time.time() * 1000)}_{len(self._row_ids.get(tab_id, []))}"

    def _ensure_row_widget(self, tab_id: str, row_id: str) -> None:
        widget = self._widgets.get(tab_id)
        if widget is None:
            return
        if not hasattr(widget, "_row_widgets_by_id"):
            setattr(widget, "_row_widgets_by_id", {})
        row_widgets_by_id: dict[str, LaunchRowWidget] = getattr(widget, "_row_widgets_by_id")
        if row_id in row_widgets_by_id:
            return

        row_w = LaunchRowWidget(
            initial_login=str(self._row_login.get(row_id, "") or ""),
            initial_password=str(self._row_password.get(row_id, "") or ""),
            initial_slot=int(self._row_slot_value(row_id)),
            initial_nickname=str(self._row_nickname_value(row_id)),
            initial_pin=str(self._row_pin_value(row_id)),
        )

        def _on_login_changed(new_login: str) -> None:
            self._row_login[row_id] = str(new_login).strip()
            self._persist_rows_to_settings(tab_id)
            self._sync_ui_state(tab_id)

        def _on_password_changed(new_password: str) -> None:
            self._row_password[row_id] = str(new_password or "")
            self._persist_rows_to_settings(tab_id)
            self._sync_ui_state(tab_id)

        def _on_slot_changed(new_slot: int) -> None:
            try:
                slot = int(new_slot)
            except Exception:
                slot = 1
            if slot < 1:
                slot = 1
            if slot > 8:
                slot = 8
            self._row_slot[row_id] = slot
            self._persist_rows_to_settings(tab_id)
            self._sync_ui_state(tab_id)

        def _on_nickname_changed(new_nick: str) -> None:
            self._row_nickname[row_id] = str(new_nick or "").strip()
            self._persist_rows_to_settings(tab_id)
            self._sync_ui_state(tab_id)

        def _on_pin_changed(new_pin: str) -> None:
            self._row_pin[row_id] = str(new_pin or "").strip()[:4]
            self._persist_rows_to_settings(tab_id)
            self._sync_ui_state(tab_id)

        def _on_selected_changed(v: bool) -> None:
            self._row_selected[row_id] = bool(v)
            self._sync_multistart_ui(tab_id)

        def _on_start() -> None:
            if not self._monitoring_active.get(tab_id, False):
                self._console(tab_id, "[WARN] Сначала нажмите Run (мониторинг должен быть активен).")
                return
            self._launch_new_window(tab_id, row_id)

        def _on_terminate() -> None:
            self._terminate_row_process(tab_id, row_id)

        def _on_check() -> None:
            pid = int(self._row_pid.get(row_id, 0) or 0)
            self._focus_check_pid(tab_id, pid)

        def _on_focus_toggle() -> None:
            pid = int(self._row_pid.get(row_id, 0) or 0)
            self._toggle_focus_pid(tab_id, pid)

        def _on_move_up() -> None:
            self._move_row(tab_id, row_id, -1)

        def _on_move_down() -> None:
            self._move_row(tab_id, row_id, +1)

        def _on_delete() -> None:
            self._delete_launch_row(tab_id, row_id)

        row_w.login_changed.connect(_on_login_changed)
        row_w.password_changed.connect(_on_password_changed)
        row_w.slot_changed.connect(_on_slot_changed)
        row_w.nickname_changed.connect(_on_nickname_changed)
        row_w.pin_changed.connect(_on_pin_changed)
        row_w.selected_changed.connect(_on_selected_changed)
        row_w.start_clicked.connect(_on_start)
        row_w.terminate_clicked.connect(_on_terminate)
        row_w.check_clicked.connect(_on_check)
        row_w.focus_toggle_clicked.connect(_on_focus_toggle)
        row_w.move_up_clicked.connect(_on_move_up)
        row_w.move_down_clicked.connect(_on_move_down)
        row_w.delete_clicked.connect(_on_delete)

        row_widgets_by_id[row_id] = row_w
        widget.add_launch_row_widget(row_w)
        self._sync_ui_state(tab_id)

    def _add_launch_row(self, tab_id: str) -> None:
        row_id = self._new_row_id(tab_id)
        self._row_ids.setdefault(tab_id, []).append(row_id)
        self._row_login[row_id] = ""
        self._row_password[row_id] = ""
        self._row_slot[row_id] = 1
        self._row_nickname[row_id] = ""
        self._row_pin[row_id] = ""
        self._row_selected[row_id] = False
        self._row_pid[row_id] = 0
        self._ensure_row_widget(tab_id, row_id)
        self._persist_rows_to_settings(tab_id)
        self._sync_ui_state(tab_id)

    def _delete_launch_row(self, tab_id: str, row_id: str) -> None:
        if int(self._row_pid.get(row_id, 0) or 0) > 0:
            self._console(tab_id, "[WARN] Нельзя удалить настройку с активным процессом. Сначала завершите процесс.")
            return
        self._cancel_autologin(row_id)
        self._row_proc.pop(row_id, None)
        self._row_pid.pop(row_id, None)
        if row_id in self._row_ids.get(tab_id, []):
            self._row_ids[tab_id] = [x for x in self._row_ids[tab_id] if x != row_id]
        self._row_login.pop(row_id, None)
        self._row_password.pop(row_id, None)
        self._row_slot.pop(row_id, None)
        self._row_nickname.pop(row_id, None)
        self._row_pin.pop(row_id, None)
        self._row_selected.pop(row_id, None)

        widget = self._widgets.get(tab_id)
        if widget is not None and hasattr(widget, "_row_widgets_by_id"):
            row_widgets_by_id: dict[str, LaunchRowWidget] = getattr(widget, "_row_widgets_by_id")
            row_w = row_widgets_by_id.pop(row_id, None)
            if row_w is not None:
                widget.remove_launch_row_widget(row_w)

        self._persist_rows_to_settings(tab_id)
        self._sync_ui_state(tab_id)

    # -----------------
    # Focus + processes
    # -----------------
    def _focus_check_pid(self, tab_id: str, pid: int) -> None:
        pid = int(pid or 0)
        if pid <= 0:
            self._console(tab_id, "[WARN] Проверка: PID не задан.")
            return
        hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=WINDOW_TITLE))
        if hwnd <= 0:
            self._console(tab_id, f"[WARN] Окно '{WINDOW_TITLE}' для PID={pid} не найдено.")
            return
        try:
            focus_hwnd(hwnd)
        except Exception as e:
            self._console(tab_id, f"[ERROR] Не удалось переключить фокус на PID={pid}: {e}")
            return

        widget = self._widgets.get(tab_id)
        if widget is None:
            return

        def _back() -> None:
            try:
                w = widget.window()
                w.raise_()
                w.activateWindow()
            except Exception:
                pass

        QTimer.singleShot(1000, _back)

    def _toggle_focus_pid(self, tab_id: str, pid: int) -> None:
        pid = int(pid or 0)
        if pid <= 0:
            self._console(tab_id, "[WARN] Переключение фокуса: PID не задан.")
            return

        widget = self._widgets.get(tab_id)
        if widget is None:
            return

        if get_foreground_pid() == pid:
            try:
                w = widget.window()
                w.raise_()
                w.activateWindow()
            except Exception as e:
                self._console(tab_id, f"[ERROR] Не удалось вернуть фокус в GUI: {e}")
            return

        hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=WINDOW_TITLE))
        if hwnd <= 0:
            self._console(tab_id, f"[WARN] Окно '{WINDOW_TITLE}' для PID={pid} не найдено.")
            return
        try:
            focus_hwnd(hwnd)
        except Exception as e:
            self._console(tab_id, f"[ERROR] Не удалось переключить фокус на PID={pid}: {e}")

    def _terminate_row_process(self, tab_id: str, row_id: str) -> None:
        pid = int(self._row_pid.get(row_id, 0) or 0)
        if pid <= 0:
            return
        self._cancel_autologin(row_id)
        proc = self._row_proc.get(row_id)
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
            else:
                terminate_process(pid)
        except Exception as e:
            self._console(tab_id, f"[ERROR] Не удалось завершить процесс PID={pid}: {e}")
            return
        self._row_proc.pop(row_id, None)
        self._row_pid[row_id] = 0
        self._persist_rows_to_settings(tab_id)
        self._sync_ui_state(tab_id)
        self._console(tab_id, f"[OK] Процесс PID={pid} завершён.")

    def _auto_login_after_launch(self, tab_id: str, row_id: str, pid: int) -> None:
        """Стартует worker автологина (в фоне), если есть пароль."""
        pid = int(pid or 0)
        if pid <= 0:
            return
        login = str(self._row_login.get(row_id, "") or "").strip()
        password = str(self._row_password.get(row_id, "") or "")
        slot = int(self._row_slot_value(row_id))
        nickname = str(self._row_nickname_value(row_id))
        pin = str(self._row_pin_value(row_id))
        if not login or not password:
            return
        if len(pin) != 4:
            return

        # cancel previous worker if any
        self._cancel_autologin(row_id)
        cancel = threading.Event()
        self._row_autologin_cancel[row_id] = cancel

        done = threading.Event()
        self._row_autologin_done[row_id] = done
        self._row_autologin_ok[row_id] = False

        t = threading.Thread(
            target=self._auto_login_worker,
            args=(tab_id, row_id, pid, login, password, slot, nickname, pin, cancel),
            name=f"autologin-{pid}",
            daemon=True,
        )
        self._row_autologin_thread[row_id] = t
        t.start()

    def _auto_login_worker(
        self,
        tab_id: str,
        row_id: str,
        pid: int,
        login: str,
        password: str,
        slot: int,
        nickname: str,
        pin: str,
        cancel: threading.Event,
    ) -> None:
        pid = int(pid or 0)
        if pid <= 0:
            return

        ok = False
        try:
            # Таймаут ожидания появления окна Requiem после запуска процесса
            wait_hwnd_timeout = float(
                self._get_tab_int_setting(
                    tab_id,
                    key=AUTOLOGIN_WAIT_HWND_TIMEOUT_SECONDS_SETTING_KEY,
                    default_v=90,
                    min_v=0,
                )
            )
            start_ts = time.time()
            hwnd = 0
            while not cancel.is_set() and (wait_hwnd_timeout <= 0.0 or (time.time() - start_ts) < wait_hwnd_timeout):
                hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=WINDOW_TITLE))
                if hwnd > 0:
                    break
                time.sleep(0.2)
            if cancel.is_set():
                return
            if hwnd <= 0:
                self._console(tab_id, f"[WARN] Автологин: окно '{WINDOW_TITLE}' для PID={pid} не найдено (timeout).")
                return

            login_timeout_s = float(
                self._get_tab_int_setting(
                    tab_id,
                    key=AUTOLOGIN_LOGIN_TIMEOUT_SECONDS_SETTING_KEY,
                    default_v=int(LOGIN_TIMEOUT_S_DEFAULT),
                    min_v=0,
                )
            )
            select_server_timeout_s = float(
                self._get_tab_int_setting(
                    tab_id,
                    key=AUTOLOGIN_SELECT_SERVER_TIMEOUT_SECONDS_SETTING_KEY,
                    default_v=int(SELECT_SERVER_TIMEOUT_S_DEFAULT),
                    min_v=0,
                )
            )
            enter_char_timeout_s = float(
                self._get_tab_int_setting(
                    tab_id,
                    key=AUTOLOGIN_ENTER_CHAR_TIMEOUT_SECONDS_SETTING_KEY,
                    default_v=int(ENTER_CHAR_TIMEOUT_S_DEFAULT),
                    min_v=0,
                )
            )
            pin_block_timeout_s = float(
                self._get_tab_int_setting(
                    tab_id,
                    key=AUTOLOGIN_PIN_BLOCK_TIMEOUT_SECONDS_SETTING_KEY,
                    default_v=int(PIN_BLOCK_TIMEOUT_S_DEFAULT),
                    min_v=0,
                )
            )
            pin_digit_timeout_s = float(
                self._get_tab_int_setting(
                    tab_id,
                    key=AUTOLOGIN_PIN_DIGIT_TIMEOUT_SECONDS_SETTING_KEY,
                    default_v=int(PIN_DIGIT_TIMEOUT_S_DEFAULT),
                    min_v=0,
                )
            )
            pin_delay_ms = float(
                self._get_tab_int_setting(
                    tab_id,
                    key=AUTOLOGIN_PIN_DELAY_MS_SETTING_KEY,
                    default_v=int(float(PIN_AFTER_DIGIT_MOVE_DELAY_S) * 1000.0),
                    min_v=0,
                )
            )
            pin_delay_s = max(0.0, pin_delay_ms / 1000.0)

            # Логика поиска шаблонов/ввода вынесена в `requiem_auto_click.modules.login.auto_login`.
            ok = bool(
                auto_login(
                    hwnd=int(hwnd),
                    login=str(login),
                    password=str(password),
                    character_slot=int(slot),
                    character_nickname=str(nickname or ""),
                    pin_code=str(pin or ""),
                    delay_before_enter_s=float(self._get_login_enter_delay_seconds(tab_id)),
                    timeout_s=float(login_timeout_s),
                    select_server_timeout_s=float(select_server_timeout_s),
                    enter_char_timeout_s=float(enter_char_timeout_s),
                    pin_block_timeout_s=float(pin_block_timeout_s),
                    pin_digit_timeout_s=float(pin_digit_timeout_s),
                    pin_delay_s=float(pin_delay_s),
                    cancel=cancel,
                    log=lambda s: self._console(tab_id, str(s)),
                )
            )
        except Exception as e:
            self._console(tab_id, f"[ERROR] Автологин: исключение: {e}")
            ok = False
        finally:
            self._row_autologin_ok[row_id] = bool(ok)
            ev = self._row_autologin_done.get(row_id)
            if ev is not None:
                ev.set()


    # -----------------
    # Ordering
    # -----------------
    def _move_row(self, tab_id: str, row_id: str, delta: int) -> None:
        ids = list(self._row_ids.get(tab_id, []))
        if row_id not in ids:
            return
        i = ids.index(row_id)
        j = i + int(delta)
        if j < 0 or j >= len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        self._row_ids[tab_id] = ids
        self._persist_rows_to_settings(tab_id)

        widget = self._widgets.get(tab_id)
        if widget is not None and hasattr(widget, "_row_widgets_by_id"):
            row_widgets_by_id: dict[str, LaunchRowWidget] = getattr(widget, "_row_widgets_by_id")
            ordered_widgets = [row_widgets_by_id[rid] for rid in ids if rid in row_widgets_by_id]
            widget.set_launch_rows_order(ordered_widgets)
        self._sync_ui_state(tab_id)

    # -----------------
    # Launch/override
    # -----------------
    def _launch_new_window(self, tab_id: str, row_id: str) -> None:
        tab_context = self._tab_contexts.get(tab_id)
        if tab_context is None:
            return

        login = str(self._row_login.get(row_id, "") or "").strip()
        if not login:
            self._set_error(tab_id, "Укажите логин перед запуском.")
            return
        if not str(self._row_password.get(row_id, "") or ""):
            self._set_error(tab_id, "Укажите пароль перед запуском.")
            return
        nick = str(self._row_nickname_value(row_id) or "").strip()
        if not nick:
            self._set_error(tab_id, "Укажите ник персонажа перед запуском.")
            return
        # Ник должен быть уникальным среди всех настроек
        nick_count = 0
        for rid in self._row_ids.get(tab_id, []):
            if str(self._row_nickname_value(rid) or "").strip() == nick:
                nick_count += 1
        if nick_count > 1:
            self._set_error(tab_id, f"Ник {nick!r} должен быть уникальным.")
            return
        if len(self._row_pin_value(row_id)) != 4:
            self._set_error(tab_id, "Укажите PIN (4 цифры) перед запуском.")
            return
        if int(self._row_pid.get(row_id, 0) or 0) > 0:
            self._set_error(tab_id, "Процесс уже привязан к этой настройке.")
            return

        rows = self._rows_state(tab_id)
        if login in login_state.active_logins(rows):
            self._set_error(tab_id, f"По логину {login!r} уже есть активный процесс. Запуск заблокирован.")
            self._sync_ui_state(tab_id)
            return

        cmd = str(
            tab_context.get_global_value(f"settings/{LAUNCHER_COMMAND_SETTING_KEY}", "", value_type=str) or ""
        ).strip()
        if not cmd:
            self._set_error(
                tab_id,
                "Не задан путь к исполняемому файлу + аргументы. Откройте ⚙ и заполните поле "
                "'Путь к exe + аргументы лаунчера'.",
            )
            return

        try:
            argv = self._parse_command(cmd)
        except Exception as e:
            self._set_error(tab_id, f"Не удалось разобрать команду запуска: {e}")
            return
        if not argv:
            self._set_error(tab_id, "Команда запуска пуста после разбора. Проверьте значение в настройках.")
            return

        exe_raw = os.path.expandvars(argv[0])
        exe_path = Path(exe_raw).expanduser()
        if not exe_path.is_absolute():
            exe_path = (Path.cwd() / exe_path).resolve()
        if not exe_path.exists() or not exe_path.is_file():
            self._set_error(tab_id, f"Неверный путь к exe: {str(exe_path)!r}")
            return
        if exe_path.name.lower() == "requiem.exe":
            self._set_error(tab_id, "Нельзя указывать оригинальный Requiem.exe. Используйте копию/переименование.")
            return

        argv = [str(exe_path)] + argv[1:]
        try:
            self._console(tab_id, f"[RUN] Запуск: {cmd}")
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            proc = subprocess.Popen(
                argv,
                cwd=str(exe_path.parent),
                creationflags=creationflags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as e:
            self._set_error(tab_id, f"Не удалось запустить процесс: {e}")
            return

        self._row_proc[row_id] = proc
        self._row_pid[row_id] = int(proc.pid)
        self._persist_rows_to_settings(tab_id)
        self._sync_ui_state(tab_id)
        self._console(tab_id, f"[OK] Процесс запущен. PID={proc.pid}")
        self._auto_login_after_launch(tab_id, row_id, int(proc.pid))

    def _override_login_pid(self, tab_id: str, login: str, pid: int) -> None:
        # теперь "login" здесь — это выбранный ник (сигнал/именования оставлены ради совместимости)
        nickname = str(login).strip()
        pid = int(pid)
        if not nickname:
            self._set_error(tab_id, "Не выбран ник для переопределения.")
            return
        if pid <= 0:
            self._set_error(tab_id, "Некорректный PID для переопределения.")
            return

        rows = self._rows_state(tab_id)
        if nickname in login_state.active_nicknames(rows):
            self._set_error(tab_id, f"По нику {nickname!r} уже есть активный процесс.")
            return
        target_row_id = login_state.first_inactive_row_for_nickname(rows, nickname)
        if target_row_id is None:
            self._set_error(tab_id, f"Не найдена настройка запуска для ника {nickname!r}.")
            return

        self._row_pid[target_row_id] = pid
        self._persist_rows_to_settings(tab_id)
        self._sync_ui_state(tab_id)
        self._console(tab_id, f"[OK] Процесс переопределён: nick={nickname!r} -> PID={pid}")

    # -----------------
    # Main loop
    # -----------------
    def execute(self, tab_context, console_output_fn, stop_flag=None):
        tab_id = str(getattr(tab_context, "tab_id", ""))
        self._console_out[tab_id] = console_output_fn
        self._monitoring_active[tab_id] = True

        widget = self._widgets.get(tab_id)
        bridge = self._ui_bridge.get(tab_id)
        if widget is not None:
            # Любые изменения UI — только через UI-поток.
            try:
                QMetaObject.invokeMethod(widget, "_set_monitoring", Qt.QueuedConnection, Q_ARG(bool, True))
            except Exception:
                # fallback: через сигнал (обычно тоже будет queued), но не вызываем _sync_ui_state из этого потока
                try:
                    widget.monitoring_changed.emit(True)
                except Exception:
                    pass

        def stopped() -> bool:
            try:
                return bool(stop_flag and stop_flag())
            except Exception:
                return True

        interval_s = self._get_refresh_interval_seconds_cached(tab_id)
        self._console(tab_id, f"[RUN] Мониторинг окон '{WINDOW_TITLE}' запущен (каждые {interval_s} сек).")

        try:
            while not stopped():
                try:
                    windows: list[WindowInfo] = list_visible_windows_with_exact_title(WINDOW_TITLE)
                    all_pids = {int(w.pid) for w in windows if int(w.pid) > 0}

                    # Снимок активных окон (для других вкладок/плагинов).
                    try:
                        snap = {
                            "ts": float(time.time()),
                            "windows": [{"pid": int(w.pid), "hwnd": int(w.hwnd), "title": str(w.title)} for w in windows],
                        }
                        payload = json.dumps(snap, ensure_ascii=False)
                        # Важно: QSettings/global values обновляем только в UI-потоке.
                        if bridge is not None:
                            QMetaObject.invokeMethod(
                                bridge,
                                "save_windows_snapshot_json",
                                Qt.QueuedConnection,
                                Q_ARG(str, payload),
                            )
                    except Exception:
                        pass

                    # если pid сохранён, но окна больше нет -> сброс
                    changed = False
                    for rid in self._row_ids.get(tab_id, []):
                        pid = int(self._row_pid.get(rid, 0) or 0)
                        if pid > 0 and pid not in all_pids:
                            login = str(self._row_login.get(rid, "") or "").strip()
                            self._console(
                                tab_id,
                                f"[WARN] PID={pid} (login={login!r}) не найден среди окон '{WINDOW_TITLE}' -> сброс.",
                            )
                            self._row_pid[rid] = 0
                            self._row_proc.pop(rid, None)
                            changed = True
                    if changed:
                        # Важно: QSettings/tab-local values обновляем только в UI-потоке.
                        if bridge is not None:
                            QMetaObject.invokeMethod(bridge, "persist_rows", Qt.QueuedConnection)
                        # UI сам подхватит изменение через свой periodic sync / on_sync_state
                        # (не трогаем UI из рабочего потока).

                    managed_pids = {int(self._row_pid.get(rid, 0) or 0) for rid in self._row_ids.get(tab_id, [])}
                    managed_pids.discard(0)
                    items = [
                        {"pid": w.pid, "hwnd": w.hwnd, "title": w.title}
                        for w in windows
                        if int(w.pid) not in managed_pids
                    ]
                    if widget is not None:
                        # Обновление UI-списка окон строго в UI-потоке.
                        try:
                            QMetaObject.invokeMethod(
                                widget,
                                "_set_windows",
                                Qt.QueuedConnection,
                                Q_ARG(object, items),
                            )
                        except Exception:
                            # fallback через сигнал
                            try:
                                widget.windows_changed.emit(items)
                            except Exception:
                                pass
                except Exception as e:
                    self._console(tab_id, f"[ERROR] Ошибка при поиске окон: {e}")

                interval_s = self._get_refresh_interval_seconds_cached(tab_id)
                steps = max(1, int(interval_s * 10))
                for _ in range(steps):
                    if stopped():
                        break
                    time.sleep(0.1)
        finally:
            self._monitoring_active[tab_id] = False
            self._seq_stop(tab_id)
            self._cancel_all_autologin_for_tab(tab_id)
            if widget is not None:
                # UI cleanup строго в UI-потоке.
                try:
                    QMetaObject.invokeMethod(widget, "_set_monitoring", Qt.QueuedConnection, Q_ARG(bool, False))
                except Exception:
                    try:
                        widget.monitoring_changed.emit(False)
                    except Exception:
                        pass
            self._console(tab_id, "[STOP] Мониторинг окон остановлен.")
            self._console_out.pop(tab_id, None)

