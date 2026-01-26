from __future__ import annotations

# pylint: disable=import-error,no-name-in-module,broad-exception-caught
import json
import time

from sa_ui_operations import IntegerSetting, PluginInterface

# pylint: disable=import-error,no-name-in-module
from PySide6.QtCore import QMetaObject, Qt, Q_ARG

from ...constants import LAUNCHER_WINDOWS_JSON_GLOBAL_KEY
from ..utils import login_state
from ..utils.launcher_rows import LauncherRow, load_launcher_rows_raw_anywhere, parse_launcher_rows_json
from .ui import MailboxWidget, ClientItem


WINDOW_TITLE = "Requiem"

class MailboxPlugin(PluginInterface):
    """Плагин: проверка наличия окна почтового ящика по шаблону."""

    def __init__(self) -> None:
        self._widgets: dict[str, MailboxWidget] = {}
        self._tab_contexts: dict[str, object] = {}
        self._console_out: dict[str, object] = {}

    def _console(self, tab_id: str, text: str) -> None:
        fn = self._console_out.get(tab_id)
        if fn is not None:
            try:
                fn(str(text))
            except Exception:
                pass

    def get_key(self) -> str:
        return "mailbox_plugin"

    def get_title(self) -> str:
        return "Requiem: Почта"

    def get_settings(self):
        # Тайминги (в миллисекундах) — храним как настройки вкладки (tab-local).
        return [
            IntegerSetting(
                key="mailbox_click_settle_ms",
                label="Почта: задержка после клика (мс)",
                default_value=50,
                description="Небольшая стабилизация после любого клика.",
            ),
            IntegerSetting(
                key="mailbox_double_click_gap_ms",
                label="Почта: пауза между двойным кликом (мс)",
                default_value=50,
                description="Между двумя кликами открытия письма.",
            ),
            IntegerSetting(
                key="mailbox_open_mail_wait_ms",
                label="Почта: ждать открытия письма (мс)",
                default_value=1000,
                description="После двойного клика по письму.",
            ),
            IntegerSetting(
                key="mailbox_after_get_content_ms",
                label="Почта: пауза после 'получить содержимое' (мс)",
                default_value=200,
                description="Пауза перед ожиданием окна удаления.",
            ),
            IntegerSetting(
                key="mailbox_after_delete_click_ms",
                label="Почта: пауза после 'удалить' (мс)",
                default_value=100,
                description="Пауза перед ожиданием окна удаления.",
            ),
            IntegerSetting(
                key="mailbox_wait_confirm_poll_ms",
                label="Почта: poll окна подтверждения (мс)",
                default_value=100,
                description="Частота проверки появления окна подтверждения.",
            ),
            IntegerSetting(
                key="mailbox_wait_confirm_poll_get_content_ms",
                label="Почта: poll подтверждения (получить содержимое) (мс)",
                default_value=100,
                description="Частота проверки появления подтверждения auto-delete после 'получить содержимое'.",
            ),
            IntegerSetting(
                key="mailbox_wait_confirm_poll_delete_ms",
                label="Почта: poll подтверждения (удалить письмо) (мс)",
                default_value=100,
                description="Частота проверки появления подтверждения после клика 'удалить'.",
            ),
            IntegerSetting(
                key="mailbox_wait_confirm_timeout_ms",
                label="Почта: ждать подтверждение после 'получить содержимое' (мс)",
                default_value=1000,
                description="Если не появилось — retry.",
            ),
            IntegerSetting(
                key="mailbox_wait_confirm_timeout_delete_ms",
                label="Почта: ждать подтверждение после 'удалить' (мс)",
                default_value=2000,
                description="Если не появилось — ошибка.",
            ),
            IntegerSetting(
                key="mailbox_confirm_close_delay_ms",
                label="Почта: пауза после клика подтверждения (мс)",
                default_value=200,
                description="Перед проверкой, что окно подтверждения исчезло.",
            ),
            IntegerSetting(
                key="mailbox_confirm_close_timeout_ms",
                label="Почта: ждать исчезновение окна подтверждения (мс)",
                default_value=1000,
                description="Если окно остаётся — ошибка.",
            ),
            IntegerSetting(
                key="mailbox_confirm_close_poll_ms",
                label="Почта: poll исчезновения окна подтверждения (мс)",
                default_value=100,
                description="Частота проверки исчезновения подтверждения после клика.",
            ),
            IntegerSetting(
                key="mailbox_auto_confirm_roi_x",
                label="Почта: auto-delete confirm ROI x",
                default_value=395,
                description="ROI подтверждения auto-delete (x).",
            ),
            IntegerSetting(
                key="mailbox_auto_confirm_roi_y",
                label="Почта: auto-delete confirm ROI y",
                default_value=324,
                description="ROI подтверждения auto-delete (y).",
            ),
            IntegerSetting(
                key="mailbox_auto_confirm_roi_w",
                label="Почта: auto-delete confirm ROI w",
                default_value=97,
                description="ROI подтверждения auto-delete (w).",
            ),
            IntegerSetting(
                key="mailbox_auto_confirm_roi_h",
                label="Почта: auto-delete confirm ROI h",
                default_value=20,
                description="ROI подтверждения auto-delete (h).",
            ),
            IntegerSetting(
                key="mailbox_auto_confirm_click_x",
                label="Почта: auto-delete confirm click x",
                default_value=444,
                description="Клик подтверждения auto-delete (client x).",
            ),
            IntegerSetting(
                key="mailbox_auto_confirm_click_y",
                label="Почта: auto-delete confirm click y",
                default_value=333,
                description="Клик подтверждения auto-delete (client y).",
            ),
            IntegerSetting(
                key="mailbox_manual_confirm_roi_x",
                label="Почта: manual-delete confirm ROI x",
                default_value=395,
                description="ROI подтверждения manual-delete (x).",
            ),
            IntegerSetting(
                key="mailbox_manual_confirm_roi_y",
                label="Почта: manual-delete confirm ROI y",
                default_value=292,
                description="ROI подтверждения manual-delete (y).",
            ),
            IntegerSetting(
                key="mailbox_manual_confirm_roi_w",
                label="Почта: manual-delete confirm ROI w",
                default_value=97,
                description="ROI подтверждения manual-delete (w).",
            ),
            IntegerSetting(
                key="mailbox_manual_confirm_roi_h",
                label="Почта: manual-delete confirm ROI h",
                default_value=20,
                description="ROI подтверждения manual-delete (h).",
            ),
            IntegerSetting(
                key="mailbox_manual_confirm_click_x",
                label="Почта: manual-delete confirm click x",
                default_value=444,
                description="Клик подтверждения manual-delete (client x).",
            ),
            IntegerSetting(
                key="mailbox_manual_confirm_click_y",
                label="Почта: manual-delete confirm click y",
                default_value=300,
                description="Клик подтверждения manual-delete (client y).",
            ),
        ]

    def _confirm_specs_for_tab(self, tab_id: str):
        from ....modules.mailbox_manager import MailboxConfirmSpec

        ctx = self._tab_contexts.get(tab_id)
        if ctx is None:
            return (
                MailboxConfirmSpec((395, 324), (97, 20), (444, 333), "auto-delete"),
                MailboxConfirmSpec((395, 292), (97, 20), (444, 300), "manual-delete"),
            )

        def _get_ms(key: str, default_v: int) -> int:
            try:
                settings_key = ctx.key(f"settings/{key}")
                if ctx.settings.contains(settings_key):
                    return int(ctx.settings.value(settings_key, default_v, type=int))
            except Exception:
                pass
            return int(default_v)

        auto = MailboxConfirmSpec(
            (int(_get_ms("mailbox_auto_confirm_roi_x", 395)), int(_get_ms("mailbox_auto_confirm_roi_y", 292))),
            (int(_get_ms("mailbox_auto_confirm_roi_w", 97)), int(_get_ms("mailbox_auto_confirm_roi_h", 20))),
            (int(_get_ms("mailbox_auto_confirm_click_x", 444)), int(_get_ms("mailbox_auto_confirm_click_y", 300))),
            "auto-delete",
        )
        manual = MailboxConfirmSpec(
            (int(_get_ms("mailbox_manual_confirm_roi_x", 395)), int(_get_ms("mailbox_manual_confirm_roi_y", 292))),
            (int(_get_ms("mailbox_manual_confirm_roi_w", 97)), int(_get_ms("mailbox_manual_confirm_roi_h", 20))),
            (int(_get_ms("mailbox_manual_confirm_click_x", 444)), int(_get_ms("mailbox_manual_confirm_click_y", 300))),
            "manual-delete",
        )
        return (auto, manual)

    def _timings_for_tab(self, tab_id: str):
        from ....modules.mailbox_manager import MailboxTimings

        ctx = self._tab_contexts.get(tab_id)
        if ctx is None:
            return MailboxTimings()

        def _get_ms(key: str, default_v: int) -> int:
            try:
                settings_key = ctx.key(f"settings/{key}")
                if ctx.settings.contains(settings_key):
                    return int(ctx.settings.value(settings_key, default_v, type=int))
            except Exception:
                pass
            return int(default_v)

        # backward compat: старый ключ poll
        poll_fallback_ms = _get_ms("mailbox_wait_confirm_poll_ms", 100)

        return MailboxTimings(
            click_settle_s=float(_get_ms("mailbox_click_settle_ms", 50)) / 1000.0,
            double_click_gap_s=float(_get_ms("mailbox_double_click_gap_ms", 50)) / 1000.0,
            open_first_mail_wait_s=float(_get_ms("mailbox_open_mail_wait_ms", 1000)) / 1000.0,
            after_click_get_content_before_wait_s=float(_get_ms("mailbox_after_get_content_ms", 200)) / 1000.0,
            after_click_delete_before_wait_s=float(_get_ms("mailbox_after_delete_click_ms", 100)) / 1000.0,
            wait_deletion_confirm_timeout_s=float(_get_ms("mailbox_wait_confirm_timeout_ms", 1000)) / 1000.0,
            wait_deletion_confirm_timeout_delete_s=float(_get_ms("mailbox_wait_confirm_timeout_delete_ms", 2000)) / 1000.0,
            wait_deletion_confirm_poll_get_content_s=float(_get_ms("mailbox_wait_confirm_poll_get_content_ms", poll_fallback_ms)) / 1000.0,
            wait_deletion_confirm_poll_delete_s=float(_get_ms("mailbox_wait_confirm_poll_delete_ms", poll_fallback_ms)) / 1000.0,
            deletion_confirm_post_click_delay_s=float(_get_ms("mailbox_confirm_close_delay_ms", 200)) / 1000.0,
            deletion_confirm_disappear_timeout_s=float(_get_ms("mailbox_confirm_close_timeout_ms", 1000)) / 1000.0,
            deletion_confirm_disappear_poll_s=float(_get_ms("mailbox_confirm_close_poll_ms", 100)) / 1000.0,
        )

    def create_widget(self, tab_context):
        tab_id = str(getattr(tab_context, "tab_id", ""))
        self._tab_contexts[tab_id] = tab_context

        w = MailboxWidget(
            window_title=WINDOW_TITLE,
            on_get_clients=lambda: self._get_active_clients_for_tab(tab_id),
            on_log=lambda s, tid=tab_id: self._console(tid, s),
            on_get_timings=lambda tid=tab_id: self._timings_for_tab(tid),
            on_get_confirm_specs=lambda tid=tab_id: self._confirm_specs_for_tab(tid),
        )
        self._widgets[tab_id] = w
        return w

    def _get_active_clients_for_tab(self, tab_id: str) -> list[ClientItem]:
        ctx = self._tab_contexts.get(tab_id)

        # 1) launcher rows (nick/login/pid mapping)
        raw = load_launcher_rows_raw_anywhere(ctx)
        rows: list[LauncherRow] = parse_launcher_rows_json(raw)

        states: list[login_state.LoginRowState] = [
            login_state.LoginRowState(row_id=f"r{i}", login=r.login, nickname=r.nickname, pid=r.pid)
            for i, r in enumerate(rows)
        ]

        nicknames = login_state.unique_nicknames_in_order(states)
        nick_to_login: dict[str, str] = {}
        for r in rows:
            nk = str(r.nickname or "").strip()
            if nk and nk not in nick_to_login:
                nick_to_login[nk] = str(r.login or "").strip()

        # 2) launcher windows snapshot (pid -> hwnd), обновляется LauncherPlugin'ом с общей частотой
        pid_to_hwnd: dict[int, int] = {}
        try:
            snap_raw = str(ctx.get_global_value(LAUNCHER_WINDOWS_JSON_GLOBAL_KEY, "", value_type=str) or "")
        except Exception:
            snap_raw = ""
        if snap_raw.strip():
            try:
                snap = json.loads(snap_raw)
            except Exception:
                snap = {}
            wins = snap.get("windows", []) if isinstance(snap, dict) else []
            if isinstance(wins, list):
                for w in wins:
                    if not isinstance(w, dict):
                        continue
                    try:
                        pid = int(w.get("pid", 0) or 0)
                        hwnd = int(w.get("hwnd", 0) or 0)
                    except Exception:
                        continue
                    if pid > 0 and hwnd > 0:
                        pid_to_hwnd[pid] = hwnd

        out: list[ClientItem] = []
        for nick in nicknames:
            pid = int(login_state.active_pid_for_nickname(states, nick))
            if pid <= 0:
                continue  # показываем только активные
            hwnd = int(pid_to_hwnd.get(pid, 0) or 0)
            if hwnd <= 0:
                # берём только окна, которые есть в снимке Launcher (чтобы не делать свой поиск окон)
                continue
            out.append(
                ClientItem(
                    nickname=str(nick).strip(),
                    login=str(nick_to_login.get(str(nick).strip(), "") or "").strip(),
                    pid=pid,
                    hwnd=hwnd,
                )
            )
        return out

    def execute(self, tab_context, console_output_fn, stop_flag=None):
        # Этот плагин не требует фоновой логики, но поддерживаем Run/Stop для совместимости.
        tab_id = str(getattr(tab_context, "tab_id", ""))
        self._console_out[tab_id] = console_output_fn

        def stopped() -> bool:
            try:
                return bool(stop_flag and stop_flag())
            except Exception:
                return True

        try:
            console_output_fn("[RUN] Плагин 'Почта' активен.")
            w = self._widgets.get(tab_id)
            if w is not None:
                QMetaObject.invokeMethod(w, "set_run_active", Qt.QueuedConnection, Q_ARG(bool, True))
            while not stopped():
                time.sleep(0.2)
        finally:
            w = self._widgets.get(tab_id)
            if w is not None:
                QMetaObject.invokeMethod(w, "set_run_active", Qt.QueuedConnection, Q_ARG(bool, False))
            self._console_out.pop(tab_id, None)

