from __future__ import annotations

# pylint: disable=import-error,no-name-in-module,broad-exception-caught
import ctypes
import json
import threading
import time

from sa_ui_operations import IntegerSetting, PluginInterface

# pylint: disable=import-error,no-name-in-module
from PySide6.QtCore import QMetaObject, Qt, Q_ARG

from ..utils import login_state
from ..utils.launcher_rows import LauncherRow, load_launcher_rows_raw_anywhere, parse_launcher_rows_json
from ..utils.windows import find_hwnd_by_pid_and_exact_title, focus_hwnd, pid_exists
from .ui import ClientItem, DisassembleWidget

from ....modules.backpack_manager import BackpackManager
from ....modules.clicker import Clicker
from ....modules.disassemble_manager import DisassembleManager
from ....modules.image_finder import ImageFinder
from ....modules.windows_mouse_client import WindowsMouseClient


WINDOW_TITLE = "Requiem"


class DisassemblePlugin(PluginInterface):
    """
    Плагин: настройка разбора (матрица 5x5 по ячейкам рюкзака).

    ВАЖНО:
    - без настроек (ни tab-local, ни global)
    """

    def __init__(self) -> None:
        self._widgets: dict[str, DisassembleWidget] = {}
        self._tab_contexts: dict[str, object] = {}
        self._console_out: dict[str, object] = {}
        self._worker_stop: dict[str, threading.Event] = {}
        self._worker_thread: dict[str, threading.Thread] = {}

    def get_key(self) -> str:
        return "disassemble_plugin"

    def get_title(self) -> str:
        return "Requiem: Разбор"

    def get_settings(self):
        # Настройки вкладки (tab-local): задержки между действиями разбора (мс).
        return [
            IntegerSetting(
                key="disassemble_after_drag_ms",
                label="Разбор: пауза после перетаскивания (мс)",
                default_value=200,
                description="После drag предмета в окно разбора.",
            ),
            IntegerSetting(
                key="disassemble_after_ok_ms",
                label="Разбор: пауза после нажатия OK (мс)",
                default_value=1000,
                description="После клика подтверждения разбора.",
            ),
            IntegerSetting(
                key="disassemble_worker_poll_ms",
                label="Разбор: poll (мс)",
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
        return "disassemble/selected_nickname"

    @staticmethod
    def _profile_base(nickname: str) -> str:
        nick = str(nickname or "").strip()
        # Keep keys stable and safe for QSettings
        return f"disassemble/profiles/{nick}" if nick else "disassemble/profiles/__none__"

    @classmethod
    def _settings_key_profile_collapsed_mask(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/collapsed_mask"

    @classmethod
    def _settings_key_profile_targets_json(cls, nickname: str) -> str:
        return f"{cls._profile_base(nickname)}/targets_json"

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

        initial_targets, initial_mask = self._load_profile(tab_context, initial_nick)

        w = DisassembleWidget(
            window_title=WINDOW_TITLE,
            on_get_clients=lambda: self._get_clients_for_tab(tab_id),
            initial_selected_nickname=initial_nick,
            initial_collapsed_mask=initial_mask,
        )
        w.start_disassemble_clicked.connect(lambda tid=tab_id: self._start_worker(tid))
        w.stop_disassemble_clicked.connect(lambda tid=tab_id: self._stop_worker(tid))
        w.selected_nickname_changed.connect(
            lambda nick, ctx=tab_context, wid=w: self._on_selected_nickname(ctx, wid, str(nick))
        )
        w.collapsed_mask_changed.connect(lambda mask, ctx=tab_context, wid=w: self._persist_profile(ctx, wid, mask_only=True))
        w.config_changed.connect(lambda ctx=tab_context, wid=w: self._persist_profile(ctx, wid, mask_only=False))
        self._widgets[tab_id] = w

        # apply initial profile (targets + collapsed) right away
        try:
            w.apply_profile(
                targets=initial_targets,
                collapsed_mask=int(initial_mask),
            )
        except Exception:
            pass
        return w

    def _persist_selected_nickname(self, tab_context, nickname: str) -> None:
        try:
            tab_context.save_value(self._settings_key_selected_nickname(), str(nickname or "").strip())
        except Exception:
            pass

    def _load_profile(self, tab_context, nickname: str) -> tuple[list[list[list[int]]] | None, int]:
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
        if not raw:
            return (None, int(mask))
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                data = None
            return (data, int(mask))
        except Exception:
            return (None, int(mask))

    def _persist_profile(self, tab_context, widget: DisassembleWidget, *, mask_only: bool) -> None:
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

    def _on_selected_nickname(self, tab_context, widget: DisassembleWidget, nickname: str) -> None:
        nick = str(nickname or "").strip()
        self._persist_selected_nickname(tab_context, nick)
        targets, mask = self._load_profile(tab_context, nick)
        try:
            widget.apply_profile(
                targets=targets,
                collapsed_mask=int(mask),
            )
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
            console_output_fn("[RUN] Плагин 'Разбор' активен.")
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
        # Snapshot timings from UI thread (не читаем QSettings из воркера).
        timings_ms = {
            "after_drag_ms": int(self._get_tab_int_setting(tab_id, key="disassemble_after_drag_ms", default_v=200, min_v=0)),
            "after_ok_ms": int(self._get_tab_int_setting(tab_id, key="disassemble_after_ok_ms", default_v=1000, min_v=0)),
            "poll_ms": int(self._get_tab_int_setting(tab_id, key="disassemble_worker_poll_ms", default_v=10, min_v=1)),
        }

        def _worker() -> None:
            try:
                self._run_disassemble_worker(
                    tab_id,
                    nickname=nickname,
                    targets=targets,
                    stop=stop,
                    timings_ms=timings_ms,
                )
            except Exception as e:
                self._log(tab_id, f"[ERROR] Разбор: исключение: {e}")
            finally:
                # hide loader when stopped/finished
                w = self._widgets.get(tab_id)
                if w is not None:
                    QMetaObject.invokeMethod(w, "set_busy", Qt.QueuedConnection, Q_ARG(bool, False))

        t = threading.Thread(target=_worker, name=f"disassemble-worker-{tab_id}", daemon=True)
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

    def _run_disassemble_worker(
        self,
        tab_id: str,
        *,
        nickname: str,
        targets: list,
        stop: threading.Event,
        timings_ms: dict[str, int],
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
                    self._log(tab_id, "[STOP] Разбор: остановлено (Backspace).")
                    stop.set()
                    return
                last_state = bool(state)
                time.sleep(0.02)

        threading.Thread(target=_backspace_watcher, name=f"disassemble-backspace-{tab_id}", daemon=True).start()

        nickname = str(nickname or "").strip()
        if not nickname:
            self._log(tab_id, "[WARN] Разбор: клиент не выбран.")
            return

        pid = int(self._pid_for_nickname(tab_id, nickname))
        if pid <= 0 or (not pid_exists(pid)):
            self._log(tab_id, f"[WARN] Разбор: клиент не активен (ник={nickname!r}).")
            return

        hwnd = int(find_hwnd_by_pid_and_exact_title(pid=pid, title=WINDOW_TITLE))
        if hwnd <= 0:
            self._log(tab_id, f"[WARN] Разбор: окно '{WINDOW_TITLE}' не найдено (ник={nickname!r}, PID={pid}).")
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
        disassemble = DisassembleManager(clicker=clicker, image_finder=image_finder, backpacks=backpacks, align_on_init=True)

        # 3) подготовка: закрыть открытые рюкзаки и найти окно разбора
        self._log(tab_id, f"[RUN] Разбор: старт (ник={nickname!r}, PID={pid}, HWND={hwnd}).")
        backpacks.close_all_opened_backpacks(refresh=True)
        disassemble.ensure_window_cached(threshold=0.98, timeout_s=2.0, poll_s=0.1)

        # 4) основной цикл
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

        total = sum(int(v or 0) for bag in (targets or []) for row in (bag or []) for v in (row or []))
        if total <= 0:
            self._log(tab_id, "[WARN] Разбор: нет заданных ячеек.")
            return
        done = 0

        for backpack_index, bag in enumerate(targets or []):
            if stopped():
                self._log(tab_id, "[STOP] Разбор: остановлено.")
                return
            if not bag:
                continue
            for row_idx, row in enumerate(bag or []):
                if stopped():
                    self._log(tab_id, "[STOP] Разбор: остановлено.")
                    return
                if not row:
                    continue
                for col_idx, repeat_raw in enumerate(row or []):
                    if stopped():
                        self._log(tab_id, "[STOP] Разбор: остановлено.")
                        return
                    repeat = int(repeat_raw or 0)
                    if repeat <= 0:
                        continue
                    for attempt in range(repeat):
                        if stopped():
                            self._log(tab_id, "[STOP] Разбор: остановлено.")
                            return
                        moved = disassemble.drag_item_from_backpack_cell_to_disassemble_cell(
                            backpack_index=int(backpack_index),
                            row=int(row_idx),
                            col=int(col_idx),
                        )
                        if moved:
                            sleep_ms(int(timings_ms.get("after_drag_ms", 200)))
                            disassemble.click_ok()
                            sleep_ms(int(timings_ms.get("after_ok_ms", 1000)))

                        done += 1
                        if not moved:
                            self._log(
                                tab_id,
                                f"[OK] Пусто: рюкзак={backpack_index+1} ({row_idx+1},{col_idx+1}) "
                                f"({done}/{max(1,total)})",
                            )
                        else:
                            self._log(
                                tab_id,
                                f"[OK] Разбор: рюкзак={backpack_index+1} ({row_idx+1},{col_idx+1}) "
                                f"{attempt+1}/{repeat} ({done}/{max(1,total)})",
                            )

        self._log(tab_id, "[OK] Разбор: завершено.")
