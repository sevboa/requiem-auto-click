from __future__ import annotations

# pylint: disable=import-error,no-name-in-module,broad-exception-caught

import time

from sa_ui_operations import PluginInterface

# pylint: disable=import-error,no-name-in-module
from PySide6.QtCore import QMetaObject, Qt, Q_ARG

from ..utils import login_state
from ..utils.launcher_rows import LauncherRow, load_launcher_rows_raw_anywhere, parse_launcher_rows_json
from .ui import CaptureRoiWidget, ClientItem


WINDOW_TITLE = "Requiem"


class CaptureRoiPlugin(PluginInterface):
    """Плагин: быстрый снимок ROI по хоткею для выбранного клиента."""

    def __init__(self) -> None:
        self._widgets: dict[str, CaptureRoiWidget] = {}
        self._tab_contexts: dict[str, object] = {}
        self._console_out: dict[str, object] = {}
        self._monitoring_active: dict[str, bool] = {}

    def get_key(self) -> str:
        return "capture_roi_plugin"

    def get_title(self) -> str:
        return "Requiem: Снимок области"

    def create_widget(self, tab_context):
        tab_id = str(getattr(tab_context, "tab_id", ""))
        self._tab_contexts[tab_id] = tab_context

        w = CaptureRoiWidget(window_title=WINDOW_TITLE, on_get_clients=lambda: self._get_clients_for_tab(tab_id))
        self._widgets[tab_id] = w
        return w

    # -----------------
    # Launcher clients -> combo model
    # -----------------
    def _get_clients_for_tab(self, tab_id: str) -> list[ClientItem]:
        ctx = self._tab_contexts.get(tab_id)
        raw = load_launcher_rows_raw_anywhere(ctx)
        rows: list[LauncherRow] = parse_launcher_rows_json(raw)

        states: list[login_state.LoginRowState] = [
            login_state.LoginRowState(
                row_id=f"r{i}",
                login=r.login,
                nickname=r.nickname,
                pid=r.pid,
            )
            for i, r in enumerate(rows)
        ]

        # В комбобокс нужны ники (как primary identity в LauncherPlugin).
        # Активность определяется по нику: если есть активный pid для ника — зелёный.
        nicknames = login_state.unique_nicknames_in_order(states)
        nick_to_login: dict[str, str] = {}
        for r in rows:
            nk = str(r.nickname or "").strip()
            if nk and nk not in nick_to_login:
                nick_to_login[nk] = str(r.login or "").strip()

        out: list[ClientItem] = []
        for nick in nicknames:
            pid = int(login_state.active_pid_for_nickname(states, nick))
            out.append(
                ClientItem(
                    nickname=str(nick).strip(),
                    login=str(nick_to_login.get(str(nick).strip(), "") or "").strip(),
                    pid=pid,
                )
            )
        return out

    # -----------------
    # Main loop (optional)
    # -----------------
    def execute(self, tab_context, console_output_fn, stop_flag=None):
        tab_id = str(getattr(tab_context, "tab_id", ""))
        self._console_out[tab_id] = console_output_fn
        self._monitoring_active[tab_id] = True
        w = self._widgets.get(tab_id)

        def stopped() -> bool:
            try:
                return bool(stop_flag and stop_flag())
            except Exception:
                return True

        # Run включает прослушивание хоткея в UI (RegisterHotKey -> WM_HOTKEY приходит в QWidget).
        if w is not None:
            QMetaObject.invokeMethod(w, "set_run_active", Qt.QueuedConnection, Q_ARG(bool, True))
        try:
            console_output_fn("[RUN] Активно: Ctrl+Shift+S (1-й раз: фокус на клиент, 2-й раз: снимок).")
            while not stopped():
                time.sleep(0.2)
        finally:
            if w is not None:
                QMetaObject.invokeMethod(w, "set_run_active", Qt.QueuedConnection, Q_ARG(bool, False))
            self._monitoring_active[tab_id] = False
            self._console_out.pop(tab_id, None)

