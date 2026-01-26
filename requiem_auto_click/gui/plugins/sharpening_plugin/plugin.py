from __future__ import annotations

# pylint: disable=import-error,no-name-in-module,broad-exception-caught
import time
import threading
import json
import ctypes
from dataclasses import dataclass

from sa_ui_operations import IntegerSetting, PluginInterface

# pylint: disable=import-error,no-name-in-module
from PySide6.QtCore import QMetaObject, Qt, Q_ARG

from ..utils import login_state
from ..utils.launcher_rows import LauncherRow, load_launcher_rows_raw_anywhere, parse_launcher_rows_json
from ..utils.windows import find_hwnd_by_pid_and_exact_title, focus_hwnd, pid_exists
from .ui import ClientItem, SharpeningWidget

from ....modules.backpack_manager import BackpackManager
from ....modules.clicker import Clicker
from ....modules.image_finder import ImageFinder
from ....modules.sharpening_manager import SharpeningManager
from ....modules.windows_mouse_client import WindowsMouseClient


WINDOW_TITLE = "Requiem"


@dataclass(frozen=True)
class _ItemRead:
    present: bool
    variant: str
    level: int
    reason: str  # empty|reject_ok|unreadable|ok


class SharpeningPlugin(PluginInterface):
    """
    Плагин: настройка заточки (матрица 5x5 по ячейкам рюкзака).

    ВАЖНО (по требованию):
    - без настроек (ни tab-local, ни global)
    - пока только UI-конфигурация
    """

    def __init__(self) -> None:
        self._widgets: dict[str, SharpeningWidget] = {}
        self._tab_contexts: dict[str, object] = {}
        self._console_out: dict[str, object] = {}
        self._worker_stop: dict[str, threading.Event] = {}
        self._worker_thread: dict[str, threading.Thread] = {}

    def get_key(self) -> str:
        return "sharpening_plugin"

    def get_title(self) -> str:
        return "Requiem: Заточка"

    def get_settings(self):
        # Настройки вкладки (tab-local): задержки между действиями заточки (мс).
        return [
            IntegerSetting(
                key="sharpen_after_drag_ms",
                label="Заточка: пауза после перетаскивания (мс)",
                default_value=100,
                description="После drag предмета в слот заточки.",
            ),
            IntegerSetting(
                key="sharpen_after_reject_close_ms",
                label="Заточка: пауза после закрытия reject_ok (мс)",
                default_value=350,
                description="После клика по OK в попапе отказа.",
            ),
            IntegerSetting(
                key="sharpen_after_repeat_ready_ms",
                label="Заточка: пауза после 'Повторить' (мс)",
                default_value=250,
                description="Перед следующей попыткой/проверкой уровня.",
            ),
            IntegerSetting(
                key="sharpen_after_click_auto_ms",
                label="Заточка: пауза после кнопки 'Авто' (мс)",
                default_value=100,
                description="Перед проверкой активной 'Авто'.",
            ),
            IntegerSetting(
                key="sharpen_after_click_ok_ms",
                label="Заточка: пауза после 'ОК' (мс)",
                default_value=500,
                description="После клика подтверждения заточки.",
            ),
            IntegerSetting(
                key="sharpen_after_click_map_ms",
                label="Заточка: пауза после клика по карте (мс)",
                default_value=800,
                description="Ожидание анимации/обновления после заточки.",
            ),
            IntegerSetting(
                key="sharpen_worker_poll_ms",
                label="Заточка: poll (мс)",
                default_value=10,
                description="Частота проверок stop/Backspace внутри цикла (меньше = отзывчивее).",
            ),
        ]

    def _get_tab_int_setting(self, tab_id: str, *, key: str, default_v: int, min_v: int = 0) -> int:
        ctx = self._tab_contexts.get(tab_id)
        if ctx is None:
            return int(max(int(min_v), int(default_v)))
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

    @staticmethod
    def _settings_key_selected_nickname() -> str:
        return "sharpening/selected_nickname"

    @staticmethod
    def _profile_base(nickname: str) -> str:
        nick = str(nickname or "").strip()
        # Keep keys stable and safe for QSettings
        return f"sharpening/profiles/{nick}" if nick else "sharpening/profiles/__none__"

    @classmethod
    def _settings_key_profile_collapsed_mask(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/collapsed_mask"

    @classmethod
    def _settings_key_profile_targets_json(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/targets_json"

    @classmethod
    def _settings_key_profile_groups_json(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/groups_json"

    @classmethod
    def _settings_key_profile_mode(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/mode"

    @classmethod
    def _settings_key_profile_skip_xeon(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/skip_xeon"

    @classmethod
    def _settings_key_profile_safe_first(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/safe_first"

    @classmethod
    def _settings_key_profile_group_need_max30(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/group_need_max30"

    @classmethod
    def _settings_key_profile_group_configs_json(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/group_configs_json"

    def create_widget(self, tab_context):
        tab_id = str(getattr(tab_context, "tab_id", ""))
        self._tab_contexts[tab_id] = tab_context

        # restore persisted UI state (tab-local)
        initial_nick = ""
        try:
            initial_nick = str(
                tab_context.settings.value(
                    tab_context.key(self._settings_key_selected_nickname()),
                    "",
                    type=str,
                )
                or ""
            ).strip()
        except Exception:
            initial_nick = ""

        (
            initial_targets,
            initial_groups,
            initial_mask,
            initial_mode,
            initial_skip_xeon,
            initial_safe_first,
            initial_group_configs,
        ) = self._load_profile(tab_context, initial_nick)

        w = SharpeningWidget(
            window_title=WINDOW_TITLE,
            on_get_clients=lambda: self._get_clients_for_tab(tab_id),
            initial_selected_nickname=initial_nick,
            initial_collapsed_mask=initial_mask,
        )
        w.start_sharpening_clicked.connect(lambda tid=tab_id: self._start_worker(tid))
        w.stop_sharpening_clicked.connect(lambda tid=tab_id: self._stop_worker(tid))
        w.selected_nickname_changed.connect(lambda nick, ctx=tab_context, wid=w: self._on_selected_nickname(ctx, wid, str(nick)))
        w.collapsed_mask_changed.connect(lambda mask, ctx=tab_context, wid=w: self._persist_profile(ctx, wid, mask_only=True))
        w.config_changed.connect(lambda ctx=tab_context, wid=w: self._persist_profile(ctx, wid, mask_only=False))
        self._widgets[tab_id] = w

        # apply initial profile (targets + collapsed) right away
        try:
            w.apply_profile(
                targets=initial_targets,
                collapsed_mask=int(initial_mask),
                mode_key=str(initial_mode or "").strip() or None,
                skip_xeon=bool(initial_skip_xeon),
                safe_first=bool(initial_safe_first),
                group_configs=initial_group_configs,
            )
        except Exception:
            pass
        try:
            if initial_groups is not None:
                self._apply_groups_to_widget(w, initial_groups)
        except Exception:
            pass
        return w

    @staticmethod
    def _apply_groups_to_widget(widget: SharpeningWidget, groups: list) -> None:
        for bi in range(min(8, len(groups))):
            bag = groups[bi] if isinstance(groups[bi], list) else []
            for r in range(min(5, len(bag))):
                row = bag[r] if isinstance(bag[r], list) else []
                for c in range(min(5, len(row))):
                    try:
                        v = int(row[c] or 0)
                    except Exception:
                        v = 0
                    widget.set_group(backpack_index=int(bi), row=int(r), col=int(c), group_id=(v if v > 0 else None))

    def _persist_selected_nickname(self, tab_context, nickname: str) -> None:
        try:
            tab_context.save_value(self._settings_key_selected_nickname(), str(nickname or "").strip())
        except Exception:
            pass

    def _load_profile(
        self, tab_context, nickname: str
    ) -> tuple[list[list[list[int]]] | None, list[list[list[int]]] | None, int, str, bool, bool, list[dict] | None]:
        nick = str(nickname or "").strip()
        # collapsed
        try:
            mask = int(
                tab_context.settings.value(
                    tab_context.key(self._settings_key_profile_collapsed_mask(nick)),
                    0,
                    type=int,
                )
                or 0
            )
        except Exception:
            mask = 0
        # targets
        try:
            raw = str(
                tab_context.settings.value(
                    tab_context.key(self._settings_key_profile_targets_json(nick)),
                    "",
                    type=str,
                )
                or ""
            ).strip()
        except Exception:
            raw = ""

        # groups
        try:
            groups_raw = str(
                tab_context.settings.value(
                    tab_context.key(self._settings_key_profile_groups_json(nick)),
                    "",
                    type=str,
                )
                or ""
            ).strip()
        except Exception:
            groups_raw = ""
        groups_data = None
        if groups_raw:
            try:
                gd = json.loads(groups_raw)
                groups_data = gd if isinstance(gd, list) else None
            except Exception:
                groups_data = None
        # group configs (per-group settings)
        group_configs = None
        try:
            raw_cfg = str(
                tab_context.settings.value(
                    tab_context.key(self._settings_key_profile_group_configs_json(nick)),
                    "",
                    type=str,
                )
                or ""
            ).strip()
        except Exception:
            raw_cfg = ""
        if raw_cfg:
            try:
                v = json.loads(raw_cfg)
                group_configs = v if isinstance(v, list) else None
            except Exception:
                group_configs = None
        # backward compatibility: old single K -> create one row for G1
        if group_configs is None:
            try:
                old_need = int(
                    tab_context.settings.value(
                        tab_context.key(self._settings_key_profile_group_need_max30(nick)),
                        2,
                        type=int,
                    )
                    or 2
                )
            except Exception:
                old_need = 2
            old_need = max(1, min(25, int(old_need)))
            group_configs = [{"group_id": 1, "max_level": 30, "need_count": int(old_need)}]

        if not raw:
            # mode + skip
            mode = ""
            skip_xeon = False
            safe_first = False
            try:
                mode = str(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_mode(nick)), "", type=str) or ""
                ).strip()
            except Exception:
                mode = ""
            try:
                skip_xeon = bool(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_skip_xeon(nick)), 0, type=int) or 0
                )
            except Exception:
                skip_xeon = False
            try:
                safe_first = bool(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_safe_first(nick)), 0, type=int) or 0
                )
            except Exception:
                safe_first = False
            return (None, groups_data, int(mask), mode, bool(skip_xeon), bool(safe_first), group_configs)
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                data = None
            mode = ""
            skip_xeon = False
            safe_first = False
            try:
                mode = str(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_mode(nick)), "", type=str) or ""
                ).strip()
            except Exception:
                mode = ""
            try:
                skip_xeon = bool(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_skip_xeon(nick)), 0, type=int) or 0
                )
            except Exception:
                skip_xeon = False
            try:
                safe_first = bool(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_safe_first(nick)), 0, type=int) or 0
                )
            except Exception:
                safe_first = False
            return (data, groups_data, int(mask), mode, bool(skip_xeon), bool(safe_first), group_configs)
        except Exception:
            mode = ""
            skip_xeon = False
            safe_first = False
            try:
                mode = str(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_mode(nick)), "", type=str) or ""
                ).strip()
            except Exception:
                mode = ""
            try:
                skip_xeon = bool(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_skip_xeon(nick)), 0, type=int) or 0
                )
            except Exception:
                skip_xeon = False
            try:
                safe_first = bool(
                    tab_context.settings.value(tab_context.key(self._settings_key_profile_safe_first(nick)), 0, type=int) or 0
                )
            except Exception:
                safe_first = False
            return (None, groups_data, int(mask), mode, bool(skip_xeon), bool(safe_first), group_configs)

    def _persist_profile(self, tab_context, widget: SharpeningWidget, *, mask_only: bool) -> None:
        nick = str(widget.get_selected_nickname() or "").strip()
        if not nick:
            return
        try:
            tab_context.save_value(self._settings_key_profile_collapsed_mask(nick), int(widget.get_collapsed_mask()))
        except Exception:
            # fallback: не критично
            pass
        if mask_only:
            return
        try:
            payload = json.dumps(widget.export_targets(), ensure_ascii=False)
            tab_context.save_value(self._settings_key_profile_targets_json(nick), payload)
        except Exception:
            pass
        try:
            g_payload = json.dumps(widget.export_groups(), ensure_ascii=False)
            tab_context.save_value(self._settings_key_profile_groups_json(nick), g_payload)
        except Exception:
            pass
        try:
            tab_context.save_value(self._settings_key_profile_mode(nick), str(widget.get_mode_key() or "to_target"))
        except Exception:
            pass
        try:
            tab_context.save_value(self._settings_key_profile_skip_xeon(nick), 1 if bool(widget.get_skip_xeon()) else 0)
        except Exception:
            pass
        try:
            tab_context.save_value(self._settings_key_profile_safe_first(nick), 1 if bool(widget.get_safe_first()) else 0)
        except Exception:
            pass
        try:
            cfg_payload = json.dumps(widget.export_group_configs(), ensure_ascii=False)
            tab_context.save_value(self._settings_key_profile_group_configs_json(nick), cfg_payload)
        except Exception:
            pass

    def _on_selected_nickname(self, tab_context, widget: SharpeningWidget, nickname: str) -> None:
        nick = str(nickname or "").strip()
        self._persist_selected_nickname(tab_context, nick)
        targets, groups, mask, mode, skip_xeon, safe_first, group_configs = self._load_profile(tab_context, nick)
        try:
            widget.apply_profile(
                targets=targets,
                collapsed_mask=int(mask),
                mode_key=str(mode or "").strip() or None,
                skip_xeon=bool(skip_xeon),
                safe_first=bool(safe_first),
                group_configs=group_configs,
            )
        except Exception:
            pass
        try:
            if groups is not None:
                self._apply_groups_to_widget(widget, groups)
        except Exception:
            pass

    # -----------------
    # Launcher clients -> combo model (как в CaptureRoiPlugin)
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

    def execute(self, tab_context, console_output_fn, stop_flag=None):
        tab_id = str(getattr(tab_context, "tab_id", ""))
        self._console_out[tab_id] = console_output_fn

        w = self._widgets.get(tab_id)
        if w is not None:
            QMetaObject.invokeMethod(w, "set_run_active", Qt.QueuedConnection, Q_ARG(bool, True))

        def stopped() -> bool:
            try:
                return bool(stop_flag and stop_flag())
            except Exception:
                return True

        try:
            console_output_fn("[RUN] Плагин 'Заточка' активен (пока только настройка матрицы).")
            while not stopped():
                time.sleep(0.2)
        finally:
            # если вкладка/скрипт выключается — останавливаем воркер и прячем лоадер
            self._stop_worker(tab_id)
            w2 = self._widgets.get(tab_id)
            if w2 is not None:
                QMetaObject.invokeMethod(w2, "set_run_active", Qt.QueuedConnection, Q_ARG(bool, False))
            self._console_out.pop(tab_id, None)

    def _start_worker(self, tab_id: str) -> None:
        stop = self._worker_stop.get(tab_id)
        if stop is None:
            stop = threading.Event()
            self._worker_stop[tab_id] = stop
        if self._worker_thread.get(tab_id) is not None and self._worker_thread[tab_id].is_alive():
            return

        stop.clear()

        # Snapshot UI state in UI thread before starting background worker.
        w0 = self._widgets.get(tab_id)
        if w0 is None:
            return
        nickname = str(w0.get_selected_nickname() or "").strip()
        targets = w0.export_targets()
        groups = w0.export_groups()
        mode_key = str(w0.get_mode_key() or "to_target").strip() or "to_target"
        skip_xeon = bool(w0.get_skip_xeon())
        safe_first = bool(w0.get_safe_first())
        group_configs = w0.export_group_configs()
        # Snapshot timings from UI thread (не читаем QSettings из воркера).
        timings_ms = {
            "after_drag_ms": int(self._get_tab_int_setting(tab_id, key="sharpen_after_drag_ms", default_v=100, min_v=0)),
            "after_reject_close_ms": int(
                self._get_tab_int_setting(tab_id, key="sharpen_after_reject_close_ms", default_v=350, min_v=0)
            ),
            "after_repeat_ready_ms": int(
                self._get_tab_int_setting(tab_id, key="sharpen_after_repeat_ready_ms", default_v=250, min_v=0)
            ),
            "after_click_auto_ms": int(
                self._get_tab_int_setting(tab_id, key="sharpen_after_click_auto_ms", default_v=100, min_v=0)
            ),
            "after_click_ok_ms": int(self._get_tab_int_setting(tab_id, key="sharpen_after_click_ok_ms", default_v=500, min_v=0)),
            "after_click_map_ms": int(self._get_tab_int_setting(tab_id, key="sharpen_after_click_map_ms", default_v=800, min_v=0)),
            "poll_ms": int(self._get_tab_int_setting(tab_id, key="sharpen_worker_poll_ms", default_v=10, min_v=1)),
        }

        def _worker() -> None:
            try:
                self._run_sharpening_worker(
                    tab_id,
                    nickname=nickname,
                    targets=targets,
                    groups=groups,
                    stop=stop,
                    timings_ms=timings_ms,
                    mode_key=mode_key,
                    skip_xeon=skip_xeon,
                    safe_first=safe_first,
                    group_configs=group_configs,
                )
            except Exception as e:
                self._log(tab_id, f"[ERROR] Заточка: исключение: {e}")
            finally:
                # hide loader when stopped/finished
                w = self._widgets.get(tab_id)
                if w is not None:
                    QMetaObject.invokeMethod(w, "set_busy", Qt.QueuedConnection, Q_ARG(bool, False))

        t = threading.Thread(target=_worker, name=f"sharpening-worker-{tab_id}", daemon=True)
        self._worker_thread[tab_id] = t
        t.start()

    def _stop_worker(self, tab_id: str) -> None:
        ev = self._worker_stop.get(tab_id)
        if ev is not None:
            ev.set()
        w = self._widgets.get(tab_id)
        if w is not None:
            QMetaObject.invokeMethod(w, "set_busy", Qt.QueuedConnection, Q_ARG(bool, False))

    def _log(self, tab_id: str, text: str) -> None:
        fn = self._console_out.get(tab_id)
        if fn is None:
            return
        try:
            fn(str(text))
        except Exception:
            pass

    def _pid_for_nickname(self, tab_id: str, nickname: str) -> int:
        nickname = str(nickname or "").strip()
        if not nickname:
            return 0
        for c in (self._get_clients_for_tab(tab_id) or []):
            if str(getattr(c, "nickname", "") or "").strip() == nickname:
                return int(getattr(c, "pid", 0) or 0)
        return 0

    # -----------------
    # Common worker actions (shared between modes)
    # -----------------
    @staticmethod
    def _sleep_ms(stop: threading.Event, timings_ms: dict[str, int], ms: int) -> None:
        ms_i = int(ms or 0)
        if ms_i <= 0:
            return
        poll = max(1, int(timings_ms.get("poll_ms", 10) or 10))
        remaining = ms_i
        while remaining > 0:
            if stop.is_set():
                return
            chunk = min(remaining, poll)
            time.sleep(float(chunk) / 1000.0)
            remaining -= chunk

    def _reset_to_backpack(
        self,
        *,
        stop: threading.Event,
        timings_ms: dict[str, int],
        backpacks: BackpackManager,
        sharpening: SharpeningManager,
        backpack_index: int,
    ) -> None:
        if stop.is_set():
            return
        sharpening.click_repeat(reset_window_top_left=True)
        self._sleep_ms(stop, timings_ms, int(timings_ms.get("after_repeat_ready_ms", 250)))
        backpacks.ensure_backpack_window_available(int(backpack_index))

    def _drag_and_read_item(
        self,
        tab_id: str,
        *,
        stop: threading.Event,
        timings_ms: dict[str, int],
        backpacks: BackpackManager,
        sharpening: SharpeningManager,
        backpack_index: int,
        row: int,
        col: int,
        max_attempts: int = 5,
    ) -> _ItemRead:
        if stop.is_set():
            return _ItemRead(present=False, variant="", level=-1, reason="stopped")

        bi = int(backpack_index)
        r = int(row)
        c = int(col)

        # сначала убеждаемся, что окно рюкзака доступно
        try:
            backpacks.ensure_backpack_window_available(int(bi))
        except Exception:
            pass

        moved = sharpening.drag_item_from_backpack_cell_to_sharpening_cell(backpack_index=int(bi), row=int(r), col=int(c))
        if not moved:
            # попробуем один раз "починить" рюкзак и повторить drag
            try:
                backpacks.ensure_backpack_window_available(int(bi))
            except Exception:
                pass
            moved = sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                backpack_index=int(bi), row=int(r), col=int(c)
            )
        if not moved:
            return _ItemRead(present=False, variant="", level=-1, reason="empty")

        self._sleep_ms(stop, timings_ms, int(timings_ms.get("after_drag_ms", 100)))

        if sharpening.check_reject_ok_popup_and_close():
            self._sleep_ms(stop, timings_ms, int(timings_ms.get("after_reject_close_ms", 350)))
            try:
                backpacks.ensure_backpack_window_available(int(bi))
            except Exception:
                pass
            return _ItemRead(present=False, variant="", level=-1, reason="reject_ok")

        variant = ""
        lvl = -1
        for attempt in range(1, int(max_attempts) + 1):
            if stop.is_set():
                return _ItemRead(present=False, variant=str(variant or ""), level=int(lvl), reason="stopped")
            try:
                variant = sharpening.ensure_item_is_sharpenable()
                lvl = int(sharpening.get_current_sharpening_value(variant=variant))
                if lvl > 30:
                    raise RuntimeError(f"Некорректный уровень заточки: {lvl} (>30)")
                return _ItemRead(present=True, variant=str(variant or ""), level=int(lvl), reason="ok")
            except Exception as e:
                self._log(
                    tab_id,
                    f"[WARN] Распознавание уровня не удалось (попытка {attempt}/{int(max_attempts)}): {e} -> повторный drag",
                )
                _ = sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                    backpack_index=int(bi), row=int(r), col=int(c)
                )
                self._sleep_ms(stop, timings_ms, int(timings_ms.get("after_drag_ms", 100)))
                variant = ""
                lvl = -1
                continue

        return _ItemRead(present=False, variant="", level=-1, reason="unreadable")

    def _click_sharpen_cycle(
        self,
        tab_id: str,
        *,
        stop: threading.Event,
        timings_ms: dict[str, int],
        sharpening: SharpeningManager,
        variant: str,
        skip_xeon: bool,
        blocked_variants: set[str],
    ) -> bool:
        if stop.is_set():
            return False
        sharpening.click_auto()
        self._sleep_ms(stop, timings_ms, int(timings_ms.get("after_click_auto_ms", 100)))
        try:
            sharpening.ensure_auto_button_active()
        except Exception as e:
            if bool(skip_xeon):
                if variant:
                    blocked_variants.add(str(variant))
                    self._log(tab_id, f"[WARN] Ксеоны закончились на {variant} -> блокируем все {variant}: {e}")
                else:
                    self._log(tab_id, f"[WARN] Ксеоны закончились -> пропуск предмета: {e}")
                return False
            raise

        sharpening.click_ok()
        self._sleep_ms(stop, timings_ms, int(timings_ms.get("after_click_ok_ms", 500)))
        sharpening.click_map()
        self._sleep_ms(stop, timings_ms, int(timings_ms.get("after_click_map_ms", 800)))
        sharpening.click_repeat(reset_window_top_left=True)
        self._sleep_ms(stop, timings_ms, int(timings_ms.get("after_repeat_ready_ms", 250)))
        return True

    def _run_sharpening_worker(
        self,
        tab_id: str,
        *,
        nickname: str,
        targets: list,
        groups: list,
        stop: threading.Event,
        timings_ms: dict[str, int],
        mode_key: str,
        skip_xeon: bool,
        safe_first: bool,
        group_configs: list,
    ) -> None:
        user32 = ctypes.windll.user32
        VK_BACKSPACE = 0x08

        # Backspace может нажиматься во время "блокирующих" операций (template matching и т.п.).
        # Чтобы не пропускать короткое нажатие, запускаем отдельный watcher, который выставляет stop.
        def _backspace_watcher() -> None:
            last_state = False
            while not stop.is_set():
                try:
                    state = (user32.GetAsyncKeyState(VK_BACKSPACE) & 0x8000) != 0
                except Exception:
                    state = False
                # фронт нажатия
                if state and not last_state:
                    self._log(tab_id, "[STOP] Заточка: остановлено (Backspace).")
                    stop.set()
                    return
                last_state = bool(state)
                time.sleep(0.02)

        threading.Thread(target=_backspace_watcher, name=f"sharpening-backspace-{tab_id}", daemon=True).start()

        nickname = str(nickname or "").strip()
        if not nickname:
            self._log(tab_id, "[WARN] Заточка: клиент не выбран.")
            return

        pid = int(self._pid_for_nickname(tab_id, nickname))
        if pid <= 0 or (not pid_exists(pid)):
            self._log(tab_id, f"[WARN] Заточка: клиент не активен (ник={nickname!r}).")
            return

        hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=WINDOW_TITLE))
        if hwnd <= 0:
            self._log(tab_id, f"[WARN] Заточка: окно '{WINDOW_TITLE}' не найдено (ник={nickname!r}, PID={pid}).")
            return

        # 1) фокус на выбранное окно
        try:
            focus_hwnd(hwnd)
        except Exception as e:
            self._log(tab_id, f"[WARN] Не удалось переключить фокус: {e}")

        # 2) собрать стек автоматизации, привязанный к HWND (важно при нескольких окнах)
        mouse_client = WindowsMouseClient()
        clicker = Clicker(mouse_client, WINDOW_TITLE, hwnd=int(hwnd))
        image_finder = ImageFinder(WINDOW_TITLE, hwnd_provider=clicker.get_hwnd)
        backpacks = BackpackManager(clicker=clicker, image_finder=image_finder)
        sharpening = SharpeningManager(clicker=clicker, image_finder=image_finder, backpacks=backpacks)

        # 3) подготовка: закрыть открытые рюкзаки и найти окно заточки
        self._log(tab_id, f"[RUN] Заточка: старт (ник={nickname!r}, PID={pid}, HWND={hwnd}).")
        backpacks.close_all_opened_backpacks(refresh=True)
        sharpening.ensure_window_cached(threshold=0.98, timeout_s=2.0, poll_s=0.1)

        # 4) основной цикл, как в RequiemClicker.sharpening_items_to, но БЕЗ ожиданий Backspace/]
        def stopped() -> bool:
            if stop.is_set():
                return True
            return False

        def sleep_ms(ms: int) -> None:
            self._sleep_ms(stop, timings_ms, ms)

        total = sum(
            1
            for bag in (targets or [])
            for row in (bag or [])
            for v in (row or [])
            if int(v or 0) > 0
        )
        done = 0
        # Если ксеоны закончились на конкретном варианте '+' (a1..a5),
        # дальнейшие предметы с тем же вариантом исключаем целиком.
        blocked_variants: set[str] = set()
        mode = str(mode_key or "to_target").strip() or "to_target"
        if mode == "round_robin_plus1":
            self._run_mode_round_robin_plus1(
                tab_id,
                targets=targets,
                stop=stop,
                timings_ms=timings_ms,
                backpacks=backpacks,
                sharpening=sharpening,
                skip_xeon=bool(skip_xeon),
                safe_first=bool(safe_first),
                blocked_variants=blocked_variants,
            )
            self._log(tab_id, "[OK] Заточка: завершено.")
            return
        if mode == "group":
            # В групповом режиме поведение "нет ксеонов -> следующая группа" является частью алгоритма,
            # поэтому считаем skip_xeon принудительно включённым, а safe_first не используется.
            self._run_mode_group_max30(
                tab_id,
                groups=groups,
                group_configs=group_configs,
                stop=stop,
                timings_ms=timings_ms,
                backpacks=backpacks,
                sharpening=sharpening,
                skip_xeon=True,
                safe_first=False,
                blocked_variants=blocked_variants,
            )
            self._log(tab_id, "[OK] Заточка: завершено.")
            return

        # default: to_target
        for backpack_index, bag in enumerate(targets or []):
            if stopped():
                self._log(tab_id, "[STOP] Заточка: остановлено.")
                return
            if not bag:
                continue
            for row_idx, row in enumerate(bag or []):
                if stopped():
                    self._log(tab_id, "[STOP] Заточка: остановлено.")
                    return
                if not row:
                    continue
                for col_idx, target_level_raw in enumerate(row or []):
                    if stopped():
                        self._log(tab_id, "[STOP] Заточка: остановлено.")
                        return
                    target_level = int(target_level_raw or 0)
                    if target_level <= 0:
                        continue
                    if target_level > 30:
                        target_level = 30

                    # точим конкретный предмет до target_level (по фактическому уровню)
                    while True:
                        if stopped():
                            self._log(tab_id, "[STOP] Заточка: остановлено.")
                            return
                        read = self._drag_and_read_item(
                            tab_id,
                            stop=stop,
                            timings_ms=timings_ms,
                            backpacks=backpacks,
                            sharpening=sharpening,
                            backpack_index=int(backpack_index),
                            row=int(row_idx),
                            col=int(col_idx),
                            max_attempts=5,
                        )
                        if not bool(read.present):
                            done += 1
                            if str(read.reason) == "reject_ok":
                                self._log(
                                    tab_id,
                                    f"[OK] Max (reject_ok): рюкзак={backpack_index+1} ({row_idx+1},{col_idx+1}) ({done}/{max(1,total)})",
                                )
                            elif str(read.reason) == "unreadable":
                                self._log(
                                    tab_id,
                                    f"[WARN] Не удалось распознать уровень после 5 попыток -> пропуск предмета ({backpack_index+1}:{row_idx+1},{col_idx+1})",
                                )
                            else:
                                self._log(
                                    tab_id,
                                    f"[OK] Пусто/сломалось: рюкзак={backpack_index+1} ({row_idx+1},{col_idx+1}) ({done}/{max(1,total)})",
                                )
                            self._reset_to_backpack(
                                stop=stop,
                                timings_ms=timings_ms,
                                backpacks=backpacks,
                                sharpening=sharpening,
                                backpack_index=int(backpack_index),
                            )
                            break

                        variant = str(read.variant or "")
                        current_level = int(read.level)

                        # Если этот вариант уже "заблокирован по ксеонам" — пропускаем предмет целиком.
                        if bool(skip_xeon) and variant and (str(variant) in blocked_variants):
                            done += 1
                            self._log(
                                tab_id,
                                f"[WARN] Вариант {variant} заблокирован (ксеоны закончились ранее) -> пропуск предмета "
                                f"({backpack_index+1}:{row_idx+1},{col_idx+1})",
                            )
                            self._reset_to_backpack(
                                stop=stop,
                                timings_ms=timings_ms,
                                backpacks=backpacks,
                                sharpening=sharpening,
                                backpack_index=int(backpack_index),
                            )
                            break

                        if current_level >= target_level:
                            self._reset_to_backpack(
                                stop=stop,
                                timings_ms=timings_ms,
                                backpacks=backpacks,
                                sharpening=sharpening,
                                backpack_index=int(backpack_index),
                            )
                            done += 1
                            self._log(
                                tab_id,
                                f"[OK] Готово {current_level}->{target_level}: рюкзак={backpack_index+1} ({row_idx+1},{col_idx+1}) ({done}/{max(1,total)})",
                            )
                            break

                        # авто-цикл заточки
                        try:
                            is_safe_now = bool(sharpening.is_sharpening_safe())
                        except Exception:
                            is_safe_now = False
                        self._log(
                            tab_id,
                            f"[RUN] попытка заточки {current_level}>{current_level + 1}({target_level}) "
                            f"{'safe' if is_safe_now else 'unsafe'} {variant}",
                        )
                        ok_xeon = self._click_sharpen_cycle(
                            tab_id,
                            stop=stop,
                            timings_ms=timings_ms,
                            sharpening=sharpening,
                            variant=str(variant),
                            skip_xeon=bool(skip_xeon),
                            blocked_variants=blocked_variants,
                        )
                        if not ok_xeon and bool(skip_xeon):
                            self._reset_to_backpack(
                                stop=stop,
                                timings_ms=timings_ms,
                                backpacks=backpacks,
                                sharpening=sharpening,
                                backpack_index=int(backpack_index),
                            )
                            done += 1
                            break

        self._log(tab_id, "[OK] Заточка: завершено.")

    def _run_mode_round_robin_plus1(
        self,
        tab_id: str,
        *,
        targets: list,
        stop: threading.Event,
        timings_ms: dict[str, int],
        backpacks: BackpackManager,
        sharpening: SharpeningManager,
        skip_xeon: bool,
        safe_first: bool,
        blocked_variants: set[str] | None = None,
    ) -> None:
        """
        Режим 2:
        - проходим по всем предметам, для каждого делаем +1 (если < target)
        - предметы с уровнем >= target исключаем из следующих итераций
        - пустые/неперетаскиваемые тоже исключаем
        """
        def stopped() -> bool:
            return bool(stop.is_set())

        def sleep_ms(ms: int) -> None:
            ms_i = int(ms or 0)
            if ms_i <= 0:
                return
            poll = max(1, int(timings_ms.get("poll_ms", 10) or 10))
            remaining = ms_i
            while remaining > 0:
                if stopped():
                    return
                chunk = min(remaining, poll)
                time.sleep(float(chunk) / 1000.0)
                remaining -= chunk

        # stable ordered list of tasks
        active: list[tuple[int, int, int, int]] = []
        for bi, bag in enumerate(targets or []):
            for r, row in enumerate(bag or []):
                for c, v in enumerate(row or []):
                    tv = int(v or 0)
                    if tv > 0:
                        active.append((int(bi), int(r), int(c), min(30, tv)))
        if not active:
            return

        iteration = 0
        blocked = blocked_variants if blocked_variants is not None else set()
        while active and (not stopped()):
            iteration += 1
            next_active: list[tuple[int, int, int, int]] = []
            for (bi, r, c, target_level) in list(active):
                if stopped():
                    return

                # 1) drag item into sharpening slot
                moved = sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                    backpack_index=int(bi),
                    row=int(r),
                    col=int(c),
                )
                if not moved:
                    self._log(tab_id, f"[OK] Итерация {iteration}: пусто -> исключаем ({bi+1}:{r+1},{c+1})")
                    continue

                sleep_ms(int(timings_ms.get("after_drag_ms", 100)))

                if sharpening.check_reject_ok_popup_and_close():
                    sleep_ms(int(timings_ms.get("after_reject_close_ms", 350)))
                    backpacks.ensure_backpack_window_available(int(bi))
                    self._log(tab_id, f"[OK] Итерация {iteration}: reject_ok -> исключаем ({bi+1}:{r+1},{c+1})")
                    continue

                variant = ""
                current_level = -1
                for attempt in range(1, 6):
                    try:
                        variant = sharpening.ensure_item_is_sharpenable()
                        current_level = int(sharpening.get_current_sharpening_value(variant=variant))
                        if current_level > 30:
                            raise RuntimeError(f"Некорректный уровень заточки: {current_level} (>30)")
                        break
                    except Exception as e:
                        self._log(
                            tab_id,
                            f"[WARN] Распознавание уровня не удалось (попытка {attempt}/5): {e} -> повторный drag",
                        )
                        _ = sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                            backpack_index=int(bi),
                            row=int(r),
                            col=int(c),
                        )
                        sleep_ms(int(timings_ms.get("after_drag_ms", 100)))
                        variant = ""
                        current_level = -1
                        continue
                if current_level < 0:
                    self._log(tab_id, f"[WARN] Не удалось распознать уровень после 5 попыток -> исключаем ({bi+1}:{r+1},{c+1})")
                    sharpening.click_repeat(reset_window_top_left=True)
                    sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))
                    backpacks.ensure_backpack_window_available(int(bi))
                    continue

                # Если вариант уже "заблокирован по ксеонам" — исключаем предмет из следующих итераций.
                if bool(skip_xeon) and variant and (str(variant) in blocked):
                    sharpening.click_repeat(reset_window_top_left=True)
                    sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))
                    backpacks.ensure_backpack_window_available(int(bi))
                    self._log(tab_id, f"[WARN] Вариант {variant} заблокирован -> исключаем ({bi+1}:{r+1},{c+1})")
                    continue

                # 2) if already done -> exclude
                if current_level >= int(target_level):
                    sharpening.click_repeat(reset_window_top_left=True)
                    sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))
                    backpacks.ensure_backpack_window_available(int(bi))
                    self._log(tab_id, f"[OK] Итерация {iteration}: готово {current_level}>={target_level} -> исключаем ({bi+1}:{r+1},{c+1})")
                    continue

                # 3) do +1 attempt
                # 3.0) optionally burn all safe levels first
                if bool(safe_first):
                    safe_guard = 0
                    while (not stopped()) and safe_guard < 50:
                        safe_guard += 1
                        # если уже достигли цели — выходим
                        if int(current_level) >= int(target_level):
                            break
                        try:
                            is_safe = bool(sharpening.is_sharpening_safe())
                        except Exception:
                            is_safe = False
                        if not is_safe:
                            break

                        self._log(
                            tab_id,
                            f"[RUN] Итерация {iteration}: safe {current_level}>{current_level+1}({target_level}) ({bi+1}:{r+1},{c+1})",
                        )
                        sharpening.click_auto()
                        sleep_ms(int(timings_ms.get("after_click_auto_ms", 100)))
                        try:
                            sharpening.ensure_auto_button_active()
                        except Exception as e:
                            if bool(skip_xeon):
                                if variant:
                                    blocked.add(str(variant))
                                    self._log(
                                        tab_id,
                                        f"[WARN] Ксеоны закончились на {variant} -> блокируем все {variant} и исключаем ({bi+1}:{r+1},{c+1}): {e}",
                                    )
                                else:
                                    self._log(tab_id, f"[WARN] Ксеоны закончились -> исключаем ({bi+1}:{r+1},{c+1}): {e}")
                                sharpening.click_repeat(reset_window_top_left=True)
                                sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))
                                backpacks.ensure_backpack_window_available(int(bi))
                                current_level = int(target_level)  # force exclude
                                break
                            raise
                        sharpening.click_ok()
                        sleep_ms(int(timings_ms.get("after_click_ok_ms", 500)))
                        sharpening.click_map()
                        sleep_ms(int(timings_ms.get("after_click_map_ms", 800)))
                        sharpening.click_repeat(reset_window_top_left=True)
                        sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))

                        for attempt in range(1, 6):
                            _ = sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                                backpack_index=int(bi),
                                row=int(r),
                                col=int(c),
                            )
                            sleep_ms(int(timings_ms.get("after_drag_ms", 100)))
                            try:
                                variant2 = sharpening.ensure_item_is_sharpenable()
                                current_level = int(sharpening.get_current_sharpening_value(variant=variant2))
                                if int(current_level) > 30:
                                    raise RuntimeError(f"Некорректный уровень заточки: {current_level} (>30)")
                                ok = True
                                break
                            except Exception as e2:
                                self._log(
                                    tab_id,
                                    f"[WARN] Safe-проверка: повторное чтение не удалось ({attempt}/5): {e2}",
                                )
                                continue
                        if not ok:
                            break

                    # если после safe-цикла достигли цели — исключаем
                    if int(current_level) >= int(target_level):
                        backpacks.ensure_backpack_window_available(int(bi))
                        self._log(
                            tab_id,
                            f"[OK] Итерация {iteration}: готово после safe {current_level}>={target_level} -> исключаем ({bi+1}:{r+1},{c+1})",
                        )
                        continue

                try:
                    is_safe_now = bool(sharpening.is_sharpening_safe())
                except Exception:
                    is_safe_now = False
                self._log(
                    tab_id,
                    f"[RUN] Итерация {iteration}: попытка заточки {current_level}>{current_level+1}({target_level}) "
                    f"{'safe' if is_safe_now else 'unsafe'} {variant} ({bi+1}:{r+1},{c+1})",
                )
                sharpening.click_auto()
                sleep_ms(int(timings_ms.get("after_click_auto_ms", 100)))
                try:
                    sharpening.ensure_auto_button_active()
                except Exception as e:
                    if bool(skip_xeon):
                        if variant:
                            blocked.add(str(variant))
                            self._log(
                                tab_id,
                                f"[WARN] Ксеоны закончились на {variant} -> блокируем все {variant} и исключаем ({bi+1}:{r+1},{c+1}): {e}",
                            )
                        else:
                            self._log(tab_id, f"[WARN] Ксеоны закончились -> исключаем ({bi+1}:{r+1},{c+1}): {e}")
                        sharpening.click_repeat(reset_window_top_left=True)
                        sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))
                        backpacks.ensure_backpack_window_available(int(bi))
                        continue
                    raise

                sharpening.click_ok()
                sleep_ms(int(timings_ms.get("after_click_ok_ms", 500)))
                sharpening.click_map()
                sleep_ms(int(timings_ms.get("after_click_map_ms", 800)))
                sharpening.click_repeat(reset_window_top_left=True)
                sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))

                # 4) keep for next iteration
                next_active.append((int(bi), int(r), int(c), int(target_level)))

            active = list(next_active)

    def _run_mode_group_max30(
        self,
        tab_id: str,
        *,
        groups: list,
        group_configs: list,
        stop: threading.Event,
        timings_ms: dict[str, int],
        backpacks: BackpackManager,
        sharpening: SharpeningManager,
        skip_xeon: bool,
        safe_first: bool,
        blocked_variants: set[str] | None = None,
    ) -> None:
        """
        Режим 3: "Групповая точка" (макс. до +30).

        Конечная цель: получить минимум K предметов на +30 в каждой группе (K задаётся в UI).

        Алгоритм строго по этапам:
        1) SAFE-проход: по каждому предмету в группе точим ДО безопасного уровня.
           Как только следующий шаг становится unsafe — НЕ точим дальше, переходим к следующему предмету.
        2) Выравнивание: доводим все предметы до одного уровня — уровня самого заточенного (max по группе).
           Предмет, который уже равен max перед попыткой, пропускаем.
        3) Подъём общего уровня: берём предметы по очереди.
           - если предмет ниже текущего max — догоняем до max (повторяя попытки)
           - если предмет равен max — делаем одну попытку +1
           - после каждой попытки заново вставляем и проверяем уровень/наличие предмета
           - если откатился — возвращаемся к этапу 2
           - если сломался/исчез — исключаем; если стало невозможно набрать K -> следующая группа
           - если закончились ксеоны (при включённом skip_xeon) — следующая группа

        Примечание: параметр safe_first здесь не используется (safe-проход обязателен).
        """

        def stopped() -> bool:
            return bool(stop.is_set())

        def sleep_ms(ms: int) -> None:
            ms_i = int(ms or 0)
            if ms_i <= 0:
                return
            poll = max(1, int(timings_ms.get("poll_ms", 10) or 10))
            remaining = ms_i
            while remaining > 0:
                if stopped():
                    return
                chunk = min(remaining, poll)
                time.sleep(float(chunk) / 1000.0)
                remaining -= chunk

        blocked = blocked_variants if blocked_variants is not None else set()

        # group_id теперь = номер строки в списке (1..N)
        cfg_map: dict[int, tuple[int, int]] = {}
        for idx, it in enumerate(list(group_configs or []), start=1):
            if not isinstance(it, dict):
                continue
            try:
                mx = int(it.get("max_level", 30) or 30)
            except Exception:
                mx = 30
            try:
                need = int(it.get("need_count", 2) or 2)
            except Exception:
                need = 2
            mx = max(1, min(30, int(mx)))
            need = max(1, min(25, int(need)))
            cfg_map[int(idx)] = (int(mx), int(need))

        # Build group_id -> list[(bi,r,c)] in stable order.
        group_map: dict[int, list[tuple[int, int, int]]] = {}
        for bi, bag in enumerate(groups or []):
            if not isinstance(bag, list):
                continue
            for r, row in enumerate(bag or []):
                if not isinstance(row, list):
                    continue
                for c, gv in enumerate(row or []):
                    try:
                        gid = int(gv or 0)
                    except Exception:
                        gid = 0
                    if gid <= 0:
                        continue
                    group_map.setdefault(int(gid), []).append((int(bi), int(r), int(c)))
        if not group_map:
            self._log(tab_id, "[WARN] Групповая точка: группы не назначены.")
            return

        def roman(n: int) -> str:
            m = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX"}
            return m.get(int(n), str(int(n)))

        def drag_and_read(bi: int, r: int, c: int) -> tuple[bool, str, int]:
            moved = sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                backpack_index=int(bi), row=int(r), col=int(c)
            )
            if not moved:
                return (False, "", -1)
            sleep_ms(int(timings_ms.get("after_drag_ms", 100)))
            if sharpening.check_reject_ok_popup_and_close():
                sleep_ms(int(timings_ms.get("after_reject_close_ms", 350)))
                backpacks.ensure_backpack_window_available(int(bi))
                return (False, "", -1)

            variant = ""
            current_level = -1
            for attempt in range(1, 6):
                try:
                    variant = sharpening.ensure_item_is_sharpenable()
                    current_level = int(sharpening.get_current_sharpening_value(variant=variant))
                    if current_level > 30:
                        raise RuntimeError(f"Некорректный уровень заточки: {current_level} (>30)")
                    break
                except Exception as e:
                    self._log(
                        tab_id,
                        f"[WARN] Группа: распознавание уровня не удалось (попытка {attempt}/5): {e} -> повторный drag",
                    )
                    _ = sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                        backpack_index=int(bi), row=int(r), col=int(c)
                    )
                    sleep_ms(int(timings_ms.get("after_drag_ms", 100)))
                    variant = ""
                    current_level = -1
                    continue
            return (True, str(variant or ""), int(current_level))

        def finalize_and_back(bi: int) -> None:
            sharpening.click_repeat(reset_window_top_left=True)
            sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))
            backpacks.ensure_backpack_window_available(int(bi))

        def do_one_attempt(variant: str) -> bool:
            sharpening.click_auto()
            sleep_ms(int(timings_ms.get("after_click_auto_ms", 100)))
            try:
                sharpening.ensure_auto_button_active()
            except Exception as e:
                if bool(skip_xeon):
                    if variant:
                        blocked.add(str(variant))
                        self._log(tab_id, f"[WARN] Ксеоны закончились на {variant} -> блокируем все {variant}: {e}")
                    else:
                        self._log(tab_id, f"[WARN] Ксеоны закончились -> переход к следующей группе: {e}")
                    return False
                raise
            sharpening.click_ok()
            sleep_ms(int(timings_ms.get("after_click_ok_ms", 500)))
            sharpening.click_map()
            sleep_ms(int(timings_ms.get("after_click_map_ms", 800)))
            sharpening.click_repeat(reset_window_top_left=True)
            sleep_ms(int(timings_ms.get("after_repeat_ready_ms", 250)))
            return True

        def _read_inserted_item() -> tuple[bool, str, int]:
            """
            Читает уровень/вариант по уже вставленному предмету (без drag).
            Возвращает (ok, variant, level).
            """
            try:
                v = sharpening.ensure_item_is_sharpenable()
                lv = int(sharpening.get_current_sharpening_value(variant=v))
                if lv > 30:
                    raise RuntimeError(f"Некорректный уровень заточки: {lv} (>30)")
                return (True, str(v or ""), int(lv))
            except Exception:
                return (False, "", -1)

        def _backpack_cell_is_filled(*, bi: int, r: int, c: int) -> bool:
            """
            Проверяем ячейку рюкзака ПОСЛЕ попытки:
            - если ячейка пустая -> предмет сломался
            """
            try:
                info = backpacks.get_backpack_cell_info(int(bi), int(r), int(c), threshold=0.99, timeout_s=0.25, poll_s=0.05)
                return str(info.get("state", "")) == "filled"
            except Exception:
                # если не смогли определить — не будем ошибочно считать сломанным, просто считаем заполненной
                return True

        def attempt_stage3_plus1(
            *,
            gid: int,
            bi: int,
            r: int,
            c: int,
            inserted: bool,
            before_level_hint: int | None,
        ) -> tuple[str, int, str, bool] | None:
            """
            ЭТАП 3: пробуем +1 и проверяем результат.

            Порядок (как просил пользователь):
            1) если предмет НЕ вставлен — вставляем (drag) и читаем before
               если вставлен — читаем before без drag
            2) делаем попытку +1 (предмет исчезает из окна заточки)
            3) проверяем, что ячейка рюкзака НЕ пустая (если пустая -> сломался)
            4) снова перетаскиваем предмет в заточку и читаем after (теперь предмет СНОВА вставлен)
            5) сравниваем before/after и возвращаем outcome {"up","same","down"}

            Возвращает (variant, level_after, outcome, inserted_after=True) или None (сломался/ксеоны/не вставился).
            """
            bi_i = int(bi)
            r_i = int(r)
            c_i = int(c)

            # 1) before
            variant = ""
            before = -1
            if bool(inserted):
                ok_b, v_b, lv_b = _read_inserted_item()
                if ok_b:
                    variant = str(v_b or "")
                    before = int(lv_b)
                else:
                    # fallback: если не можем прочитать вставленный — делаем drag
                    inserted = False

            if not bool(inserted):
                moved, variant, before = drag_and_read(int(bi_i), int(r_i), int(c_i))
                if not moved:
                    return None

            if before < 0 and before_level_hint is not None:
                before = int(before_level_hint)

            if bool(skip_xeon) and variant and (str(variant) in blocked):
                self._log(
                    tab_id,
                    f"[WARN] Группа {roman(gid)}: вариант {variant} заблокирован (ксеоны ранее) -> исключаем ({bi_i+1}:{r_i+1},{c_i+1})",
                )
                finalize_and_back(int(bi_i))
                return None

            try:
                is_safe_now = bool(sharpening.is_sharpening_safe())
            except Exception:
                is_safe_now = False

            self._log(
                tab_id,
                f"[RUN] Группа {roman(gid)}: попытка {before}>{before+1} "
                f"{'safe' if is_safe_now else 'unsafe'} {variant} ({bi_i+1}:{r_i+1},{c_i+1})",
            )

            # 2) attempt +1 (после этого предмет исчезает из окна заточки)
            ok_xeon = do_one_attempt(str(variant))
            if not ok_xeon:
                return None

            # 3) check backpack cell not empty
            if not _backpack_cell_is_filled(bi=bi_i, r=r_i, c=c_i):
                self._log(tab_id, f"[WARN] Группа {roman(gid)}: предмет сломался (ячейка пустая) ({bi_i+1}:{r_i+1},{c_i+1})")
                return None

            # 4) drag again to read after (и заодно вставить предмет обратно)
            moved2, variant2, after = drag_and_read(int(bi_i), int(r_i), int(c_i))
            if not moved2:
                self._log(tab_id, f"[WARN] Группа {roman(gid)}: предмет не удалось вставить после попытки ({bi_i+1}:{r_i+1},{c_i+1})")
                return None
            if variant2:
                variant = variant2

            # 5) compare
            if int(after) > int(before):
                return (str(variant), int(after), "up", True)
            if int(after) == int(before):
                return (str(variant), int(after), "same", True)
            return (str(variant), int(after), "down", True)

        # main: groups in numeric order
        for gid in sorted(group_map.keys()):
            if stopped():
                return

            order = list(group_map.get(int(gid)) or [])
            if not order:
                continue

            cfg = cfg_map.get(int(gid))
            if cfg is None:
                self._log(tab_id, f"[WARN] Группа {roman(gid)}: нет строки настроек для этой группы -> пропуск")
                continue
            max_level, need_k = cfg

            self._log(
                tab_id,
                f"[RUN] Групповая точка: группа {roman(gid)} (предметов={len(order)}, max=+{max_level}, нужно={need_k})",
            )
            if len(order) < int(need_k):
                self._log(tab_id, f"[WARN] Группа {roman(gid)}: предметов меньше чем нужно ({need_k}) -> пропуск")
                continue

            alive: dict[tuple[int, int, int], int] = {tuple(cell): -1 for cell in order}

            def alive_count() -> int:
                return int(len(alive))

            def maxed_count() -> int:
                return int(sum(1 for v in alive.values() if int(v) >= int(max_level)))

            def impossible() -> bool:
                return bool(alive_count() < int(need_k))

            def refresh_level(cell: tuple[int, int, int]) -> bool:
                bi, r, c = cell
                moved, variant, lvl = drag_and_read(int(bi), int(r), int(c))
                if not moved:
                    alive.pop(cell, None)
                    return False
                if bool(skip_xeon) and variant and (str(variant) in blocked):
                    self._log(tab_id, f"[WARN] Группа {roman(gid)}: {variant} заблокирован -> исключаем ({bi+1}:{r+1},{c+1})")
                    finalize_and_back(int(bi))
                    alive.pop(cell, None)
                    return False
                alive[cell] = int(lvl)
                finalize_and_back(int(bi))
                return True

            # --------
            # ЭТАП 1: SAFE-проход по всем предметам (обязателен)
            # --------
            abort_group = False
            for cell in list(order):
                if stopped():
                    return
                if cell not in alive:
                    continue

                bi, r, c = cell
                safe_guard = 0
                while not stopped() and safe_guard < 300:
                    safe_guard += 1
                    moved, variant, lvl = drag_and_read(int(bi), int(r), int(c))
                    if not moved:
                        alive.pop(cell, None)
                        break
                    if bool(skip_xeon) and variant and (str(variant) in blocked):
                        self._log(tab_id, f"[WARN] Группа {roman(gid)}: {variant} заблокирован -> исключаем ({bi+1}:{r+1},{c+1})")
                        finalize_and_back(int(bi))
                        alive.pop(cell, None)
                        break

                    alive[cell] = int(lvl)
                    if int(lvl) >= int(max_level):
                        finalize_and_back(int(bi))
                        break

                    try:
                        is_safe_now = bool(sharpening.is_sharpening_safe())
                    except Exception:
                        is_safe_now = False

                    if not is_safe_now:
                        # следующий шаг unsafe -> не точим, идём к следующему предмету
                        finalize_and_back(int(bi))
                        break

                    self._log(tab_id, f"[RUN] Группа {roman(gid)}: SAFE {lvl}>{lvl+1} {variant} ({bi+1}:{r+1},{c+1})")
                    ok_xeon = do_one_attempt(str(variant))
                    if not ok_xeon:
                        abort_group = True
                        break
                    # На этапе 1 не проверяем "заточился или нет" (без сравнения before/after).
                    # На следующем цикле мы снова сделаем drag_and_read и увидим текущий уровень.
                    finalize_and_back(int(bi))

                if abort_group:
                    break

            if abort_group:
                self._log(tab_id, f"[WARN] Группа {roman(gid)}: ксеоны закончились -> следующая группа")
                continue
            if impossible():
                self._log(
                    tab_id,
                    f"[WARN] Группа {roman(gid)}: недостаточно предметов для max={need_k} (max=+{max_level}) -> следующая группа",
                )
                continue
            if maxed_count() >= int(need_k):
                self._log(
                    tab_id,
                    f"[OK] Группа {roman(gid)}: уже есть max={maxed_count()}/{need_k} (max=+{max_level}) -> следующая группа",
                )
                continue

            # --------
            # ЭТАП 2: Выравнивание до max уровня в группе
            # --------
            def stage2_equalize() -> bool:
                stage_guard = 0
                while not stopped() and stage_guard < 2500:
                    stage_guard += 1
                    if impossible():
                        return False
                    # освежим уровни
                    for cell2 in list(order):
                        if stopped():
                            return False
                        if cell2 not in alive:
                            continue
                        _ = refresh_level(cell2)
                        if impossible():
                            return False
                    if not alive:
                        return False
                    try:
                        max_level = max(int(v) for v in alive.values())
                    except Exception:
                        return False
                    if all(int(v) == int(max_level) for v in alive.values()):
                        return True

                    progressed = False
                    for cell2 in list(order):
                        if stopped():
                            return False
                        if cell2 not in alive:
                            continue
                        if int(alive[cell2]) >= int(max_level):
                            continue
                        bi2, r2, c2 = cell2
                        # ЭТАП 2: дотачиваем до max_level без проверки результата после каждой попытки.
                        # Уровень обновится на следующем цикле через refresh_level().
                        guard2 = 0
                        while (
                            not stopped()
                            and guard2 < 500
                            and cell2 in alive
                            and int(alive[cell2]) < int(max_level)
                        ):
                            guard2 += 1
                            moved3, variant3, lvl3 = drag_and_read(int(bi2), int(r2), int(c2))
                            if not moved3:
                                alive.pop(cell2, None)
                                return False
                            if bool(skip_xeon) and variant3 and (str(variant3) in blocked):
                                self._log(
                                    tab_id,
                                    f"[WARN] Группа {roman(gid)}: {variant3} заблокирован -> исключаем ({bi2+1}:{r2+1},{c2+1})",
                                )
                                finalize_and_back(int(bi2))
                                alive.pop(cell2, None)
                                return False
                            alive[cell2] = int(lvl3)
                            if int(lvl3) >= int(max_level):
                                finalize_and_back(int(bi2))
                                break
                            ok_xeon2 = do_one_attempt(str(variant3))
                            if not ok_xeon2:
                                return False
                            progressed = True
                            finalize_and_back(int(bi2))
                    if not progressed:
                        return True
                return True

            ok2 = stage2_equalize()
            if not ok2:
                self._log(tab_id, f"[WARN] Группа {roman(gid)}: не удалось выровнять/ксеоны/сломалось -> следующая группа")
                continue

            # --------
            # ЭТАП 3: Подъём общего уровня (+1), rollback -> этап 2
            # --------
            stage3_guard = 0
            while not stopped() and stage3_guard < 6000:
                stage3_guard += 1
                if impossible():
                    self._log(
                        tab_id,
                        f"[WARN] Группа {roman(gid)}: невозможно набрать max={need_k} (max=+{max_level}) -> следующая группа",
                    )
                    break
                if maxed_count() >= int(need_k):
                    self._log(
                        tab_id,
                        f"[OK] Группа {roman(gid)}: достигнуто max={maxed_count()}/{need_k} (max=+{max_level}) -> следующая группа",
                    )
                    break

                try:
                    current_max = max(int(v) for v in alive.values())
                except Exception:
                    break

                rollback = False
                abort = False

                for cell3 in list(order):
                    if stopped():
                        return
                    if cell3 not in alive:
                        continue
                    if maxed_count() >= int(need_k):
                        break

                    bi3, r3, c3 = cell3

                    # 3.a) догоняем до current_max, если отстаёт
                    catch_guard = 0
                    while (
                        not stopped()
                        and catch_guard < 600
                        and cell3 in alive
                        and int(alive[cell3]) < int(current_max)
                    ):
                        catch_guard += 1
                        before = int(alive[cell3])
                        res = attempt_stage3_plus1(
                            gid=int(gid),
                            bi=int(bi3),
                            r=int(r3),
                            c=int(c3),
                            inserted=False,
                            before_level_hint=before,
                        )
                        if res is None:
                            alive.pop(cell3, None)
                            abort = True
                            break
                        _, after, outcome, _ins = res
                        alive[cell3] = int(after)
                        if outcome == "down":
                            rollback = True
                            break
                    if abort or rollback:
                        break
                    if cell3 not in alive:
                        continue
                    if int(alive[cell3]) >= int(max_level):
                        continue
                    # Важно: если мы ДОтягивали предмет до current_max в этом проходе (catch_guard>0),
                    # то не делаем тут же вторую попытку +1 (иначе получается "догнал -> сразу ещё +1").
                    if catch_guard > 0:
                        continue

                    # 3.b) если равен current_max — одна попытка +1
                    if int(alive[cell3]) == int(current_max) and int(current_max) < int(max_level):
                        before = int(alive[cell3])
                        res = attempt_stage3_plus1(
                            gid=int(gid),
                            bi=int(bi3),
                            r=int(r3),
                            c=int(c3),
                            inserted=False,
                            before_level_hint=before,
                        )
                        if res is None:
                            alive.pop(cell3, None)
                            abort = True
                            break
                        _, after, outcome, _ins = res
                        alive[cell3] = int(after)
                        if outcome == "down":
                            rollback = True
                            break
                        if int(after) > int(current_max):
                            current_max = int(after)

                if abort:
                    self._log(tab_id, f"[WARN] Группа {roman(gid)}: ксеоны/сломалось -> следующая группа")
                    break
                if rollback:
                    self._log(tab_id, f"[WARN] Группа {roman(gid)}: откат -> этап 2 (выравнивание)")
                    ok2 = stage2_equalize()
                    if not ok2:
                        self._log(tab_id, f"[WARN] Группа {roman(gid)}: не удалось выровнять после отката -> следующая группа")
                        break
                    continue

                # после прохода выравниваем (на случай, если max вырос и кто-то не догнал)
                ok2 = stage2_equalize()
                if not ok2:
                    self._log(tab_id, f"[WARN] Группа {roman(gid)}: выравнивание не удалось -> следующая группа")
                    break
