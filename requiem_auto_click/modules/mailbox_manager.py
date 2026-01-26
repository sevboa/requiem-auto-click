"""Логика работы с почтовым ящиком (mailbox) в интерфейсе Requiem.

Цель: держать действия/автоматизацию в `modules`, чтобы их можно было вызывать:
- из GUI-плагина
- из консольных скриптов в будущем
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# pylint: disable=broad-exception-caught

from .clicker import Clicker
from .image_finder import ImageFinder
from .mouse_utils import MOUSEEVENTF_MOVE, send_mouse
from .template_cache import preload_templates
from .window_utils import SW_RESTORE
from .windows_mouse_client import WindowsMouseClient


class MailboxCancelledError(RuntimeError):
    """Сигнализирует об остановке плагина/скрипта (cancel)."""


@dataclass(frozen=True)
class MailboxConfirmSpec:
    """Параметры окна подтверждения для конкретного сценария."""

    roi_top_left: tuple[int, int]
    roi_size: tuple[int, int]
    confirm_click_client: tuple[int, int]
    label: str


@dataclass(frozen=True)
class MailboxTimings:
    """Тайминги (в секундах) для стабилизации UI."""

    tab_switch_delay_s: float = 0.2
    click_settle_s: float = 0.05
    double_click_gap_s: float = 0.05
    open_first_mail_wait_s: float = 1.0
    after_click_get_content_before_wait_s: float = 0.2
    after_click_delete_before_wait_s: float = 0.1
    wait_get_content_active_timeout_s: float = 1.0
    wait_get_content_active_poll_s: float = 0.1
    mail_empty_check_timeout_s: float = 0.1

    wait_deletion_confirm_timeout_s: float = 1.0
    wait_deletion_confirm_timeout_delete_s: float = 2.0
    wait_deletion_confirm_poll_get_content_s: float = 0.1
    wait_deletion_confirm_poll_delete_s: float = 0.1

    deletion_confirm_post_click_delay_s: float = 0.2
    deletion_confirm_disappear_timeout_s: float = 1.0

    deletion_confirm_disappear_poll_s: float = 0.1


@dataclass(frozen=True)
class MailboxTabState:
    active_tab: str  # "incoming" | "outgoing"
    hit: dict


class MailboxManager:
    """Менеджер UI-части почты."""

    _ASSETS_DIR: Path = Path(__file__).resolve().parent / "mailbox"

    TEMPLATE_WINDOW_MAILBOX: Path = _ASSETS_DIR / "find_window_mailbox.png"
    TEMPLATE_TAB_INCOMING_ACTIVE: Path = _ASSETS_DIR / "check_incoming_tab.png"
    TEMPLATE_TAB_OUTGOING_ACTIVE: Path = _ASSETS_DIR / "check_outgoing_tab.png"
    TEMPLATE_FIRST_MAIL: Path = _ASSETS_DIR / "check_first_mail.png"
    TEMPLATE_FIRST_MAIL_OPENED: Path = _ASSETS_DIR / "check_first_mail_opened.png"
    TEMPLATE_EMPTY_MAILBOX: Path = _ASSETS_DIR / "check_empty_mailbox.png"
    TEMPLATE_GET_CONTENT_IS_ACTIVE: Path = _ASSETS_DIR / "check_get_content_is_active.png"
    TEMPLATE_MAIL_IS_EMPTY: Path = _ASSETS_DIR / "check_mail_is_empty.png"
    TEMPLATE_WINDOW_DELETION_CONFIRMATION: Path = _ASSETS_DIR / "window_deletion_confirmation.png"

    # ROI поиска окна почты (client coords)
    MAILBOX_WINDOW_ROI_TOP_LEFT: tuple[int, int] = (107, 102)
    MAILBOX_WINDOW_ROI_SIZE: tuple[int, int] = (395, 27)

    # Проверка наличия письма/пустоты в первой строке
    FIRST_MAIL_ROI_TOP_LEFT: tuple[int, int] = (126, 178)
    FIRST_MAIL_ROI_SIZE: tuple[int, int] = (60, 60)

    # Проверка активной кнопки "Получить содержимое"
    GET_CONTENT_IS_ACTIVE_ROI_TOP_LEFT: tuple[int, int] = (659, 543)
    GET_CONTENT_IS_ACTIVE_ROI_SIZE: tuple[int, int] = (235, 24)

    # Проверка "письмо пустое" (preview/contents area)
    MAIL_IS_EMPTY_ROI_TOP_LEFT: tuple[int, int] = (722, 486)
    MAIL_IS_EMPTY_ROI_SIZE: tuple[int, int] = (170, 50)

    # Окно подтверждения удаления (по умолчанию).
    # Важно: в игре может быть два разных подтверждения:
    # - auto-delete: сразу после "получить содержимое"
    # - manual-delete: после клика кнопки удаления
    # Если у них разные ROI/кнопки подтверждения — переопределяй через MailboxConfirmSpec.
    DELETION_CONFIRM_ROI_TOP_LEFT: tuple[int, int] = (395, 292)
    DELETION_CONFIRM_ROI_SIZE: tuple[int, int] = (97, 20)
    # Проверка активной вкладки: ROI задан от левого верхнего края client area.
    TAB_CHECK_TOP_LEFT_CLIENT: tuple[int, int] = (122, 148)
    TAB_CHECK_ROI_SIZE: tuple[int, int] = (300, 25)

    # Клик по "нужной вкладке" (как договорились) + задержка
    TAB_SWITCH_CLICK_CLIENT: tuple[int, int] = (173, 160)
    TARGET_TAB: str = "incoming"

    # Клики для действий с письмами
    CLICK_GET_CONTENT_CLIENT: tuple[int, int] = (769, 554)
    CLICK_DELETE_EMPTY_MAIL_CLIENT: tuple[int, int] = (726, 595)
    CLICK_CONFIRM_DELETION_CLIENT: tuple[int, int] = (444, 300)
    CLICK_OPEN_FIRST_MAIL_CLIENT: tuple[int, int] = (309, 207)

    def __init__(
        self,
        *,
        clicker: Clicker,
        image_finder: ImageFinder,
        log: Optional[Callable[[str], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
        timings: MailboxTimings | None = None,
        confirm_auto_delete: MailboxConfirmSpec | None = None,
        confirm_manual_delete: MailboxConfirmSpec | None = None,
    ) -> None:
        self._clicker = clicker
        self._image_finder = image_finder
        self._log = log
        self._cancel = cancel
        self._timings = timings or MailboxTimings()
        self._confirm_auto = confirm_auto_delete or MailboxConfirmSpec(
            roi_top_left=self.DELETION_CONFIRM_ROI_TOP_LEFT,
            roi_size=self.DELETION_CONFIRM_ROI_SIZE,
            confirm_click_client=self.CLICK_CONFIRM_DELETION_CLIENT,
            label="auto-delete",
        )
        self._confirm_manual = confirm_manual_delete or MailboxConfirmSpec(
            roi_top_left=self.DELETION_CONFIRM_ROI_TOP_LEFT,
            roi_size=self.DELETION_CONFIRM_ROI_SIZE,
            confirm_click_client=self.CLICK_CONFIRM_DELETION_CLIENT,
            label="manual-delete",
        )

        # Прогреваем кэш шаблонов
        preload_templates(
            [
                self.TEMPLATE_WINDOW_MAILBOX,
                self.TEMPLATE_TAB_INCOMING_ACTIVE,
                self.TEMPLATE_TAB_OUTGOING_ACTIVE,
                self.TEMPLATE_FIRST_MAIL,
                self.TEMPLATE_FIRST_MAIL_OPENED,
                self.TEMPLATE_EMPTY_MAILBOX,
                self.TEMPLATE_GET_CONTENT_IS_ACTIVE,
                self.TEMPLATE_MAIL_IS_EMPTY,
                self.TEMPLATE_WINDOW_DELETION_CONFIRMATION,
            ]
        )

    @classmethod
    def for_hwnd(
        cls,
        *,
        hwnd: int,
        window_title_substring: str = "Requiem",
        log: Optional[Callable[[str], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
        timings: MailboxTimings | None = None,
        confirm_auto_delete: MailboxConfirmSpec | None = None,
        confirm_manual_delete: MailboxConfirmSpec | None = None,
    ) -> "MailboxManager":
        clicker = Clicker(WindowsMouseClient(), window_title_substring, hwnd=int(hwnd))
        image_finder = ImageFinder(window_title_substring, hwnd_provider=clicker.get_hwnd)
        return cls(
            clicker=clicker,
            image_finder=image_finder,
            log=log,
            cancel=cancel,
            timings=timings,
            confirm_auto_delete=confirm_auto_delete,
            confirm_manual_delete=confirm_manual_delete,
        )

    def _emit_log(self, text: str) -> None:
        fn = self._log
        if fn is None:
            return
        try:
            fn(str(text))
        except Exception:
            pass

    def _is_cancelled(self) -> bool:
        fn = self._cancel
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:
            return False

    def _check_cancel(self) -> None:
        if self._is_cancelled():
            raise MailboxCancelledError("Остановлено (плагин выключен)")

    def _sleep(self, seconds: float, *, step: float = 0.05) -> None:
        """Сон с проверкой cancel, чтобы корректно останавливаться при Stop."""
        total = float(seconds)
        if total <= 0:
            return
        end = time.time() + total
        while True:
            self._check_cancel()
            now = time.time()
            if now >= end:
                break
            time.sleep(min(float(step), end - now))

    def _ensure_window_active(self) -> None:
        self._check_cancel()
        hwnd = int(self._clicker.get_hwnd())
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, int(SW_RESTORE))
        try:
            user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self._sleep(0.01)

    @staticmethod
    def _virtual_screen_rect() -> tuple[int, int, int, int]:
        """
        (left, top, width, height) виртуального рабочего стола (multi-monitor).
        SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
        """
        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(76))
        top = int(user32.GetSystemMetrics(77))
        width = int(user32.GetSystemMetrics(78))
        height = int(user32.GetSystemMetrics(79))
        return (left, top, width, height)

    def move_cursor_to_screen_center(self) -> None:
        self._check_cancel()
        left, top, w, h = self._virtual_screen_rect()
        if w <= 0 or h <= 0:
            return
        cx = int(left + (w // 2))
        cy = int(top + (h // 2))
        send_mouse(MOUSEEVENTF_MOVE, cx, cy)

    def check_mailbox_window(
        self,
        *,
        threshold: float = 0.93,
        timeout_s: float = 0.8,
        poll_s: float = 0.1,
    ) -> dict | None:
        """Проверка наличия окна почты по шаблону."""
        self._ensure_window_active()
        return self._image_finder.find_template_in_client_roi(
            template_png_path=self.TEMPLATE_WINDOW_MAILBOX,
            roi_top_left_client=self.MAILBOX_WINDOW_ROI_TOP_LEFT,
            roi_size=self.MAILBOX_WINDOW_ROI_SIZE,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )

    def detect_active_tab(
        self,
        *,
        threshold: float = 0.93,
        timeout_s: float = 0.5,
        poll_s: float = 0.1,
    ) -> MailboxTabState | None:
        """Определяет активную вкладку (incoming/outgoing) по шаблонам в ROI от левого верхнего края."""
        self._ensure_window_active()
        roi_tl = (int(self.TAB_CHECK_TOP_LEFT_CLIENT[0]), int(self.TAB_CHECK_TOP_LEFT_CLIENT[1]))
        roi_sz = (int(self.TAB_CHECK_ROI_SIZE[0]), int(self.TAB_CHECK_ROI_SIZE[1]))

        incoming = self._image_finder.find_template_in_client_roi(
            template_png_path=self.TEMPLATE_TAB_INCOMING_ACTIVE,
            roi_top_left_client=roi_tl,
            roi_size=roi_sz,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        if incoming is not None:
            return MailboxTabState(active_tab="incoming", hit=incoming)

        outgoing = self._image_finder.find_template_in_client_roi(
            template_png_path=self.TEMPLATE_TAB_OUTGOING_ACTIVE,
            roi_top_left_client=roi_tl,
            roi_size=roi_sz,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        if outgoing is not None:
            return MailboxTabState(active_tab="outgoing", hit=outgoing)

        return None

    def switch_to_needed_tab(self) -> None:
        """
        Переключает на нужную вкладку кликом (с задержкой) и возвращает курсор в центр экрана.

        Важно: этот метод делает клик без проверки текущей вкладки.
        Для "умного" поведения используйте `ensure_needed_tab_selected()`.
        """
        self._ensure_window_active()
        x, y = self.TAB_SWITCH_CLICK_CLIENT
        self._clicker.click_at_client(int(x), int(y))
        self._sleep(float(self._timings.tab_switch_delay_s))
        self.move_cursor_to_screen_center()

    def ensure_needed_tab_selected(self, tab_state: MailboxTabState) -> None:
        """
        Если активна не та вкладка — переключаемся на нужную.

        Сейчас "нужная" вкладка считается `incoming` (входящая).
        """
        current = str(getattr(tab_state, "active_tab", "") or "").strip().lower()
        target = str(self.TARGET_TAB or "incoming").strip().lower()
        if current == target:
            self._emit_log(f"[MAILBOX] tab already selected: {current}")
            return
        self._emit_log(f"[MAILBOX] tab mismatch: current={current} -> switch to {target}")
        self.switch_to_needed_tab()

    def _find_in_first_mail_roi(
        self,
        template_path: Path,
        *,
        threshold: float = 0.93,
        timeout_s: float = 0.4,
        poll_s: float = 0.1,
    ) -> dict | None:
        self._ensure_window_active()
        return self._image_finder.find_template_in_client_roi(
            template_png_path=template_path,
            roi_top_left_client=self.FIRST_MAIL_ROI_TOP_LEFT,
            roi_size=self.FIRST_MAIL_ROI_SIZE,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )

    def detect_first_row_state(self) -> str:
        """
        Проверяет состояние первой строки списка писем.

        Алгоритм:
        - если найдено `check_first_mail.png` -> "has_mail"
        - иначе если найдено `check_empty_mailbox.png` -> "empty_mailbox"
        - иначе если найдено `check_first_mail_opened.png` -> "opened_mail"
        - иначе -> "unknown"
        """
        self._emit_log("[MAILBOX] detect first row state")
        hit_mail = self._find_in_first_mail_roi(self.TEMPLATE_FIRST_MAIL, timeout_s=0.4)
        if hit_mail is not None:
            self._emit_log(
                f"[MAILBOX] first mail detected (score={hit_mail.get('score')}, elapsed={hit_mail.get('elapsed_s')}s)"
            )
            return "has_mail"

        self._emit_log("[MAILBOX] first mail not detected -> check empty mailbox")
        hit_empty = self._find_in_first_mail_roi(self.TEMPLATE_EMPTY_MAILBOX, timeout_s=0.4)
        if hit_empty is not None:
            self._emit_log(
                f"[MAILBOX] mailbox is empty (score={hit_empty.get('score')}, elapsed={hit_empty.get('elapsed_s')}s)"
            )
            return "empty_mailbox"

        self._emit_log("[MAILBOX] empty mailbox not detected -> check first mail opened")
        hit_opened = self._find_in_first_mail_roi(self.TEMPLATE_FIRST_MAIL_OPENED, timeout_s=0.4)
        if hit_opened is not None:
            self._emit_log(
                f"[MAILBOX] first mail opened detected (score={hit_opened.get('score')}, elapsed={hit_opened.get('elapsed_s')}s)"
            )
            return "opened_mail"

        return "unknown"

    def _find_in_roi(
        self,
        template_path: Path,
        roi_top_left: tuple[int, int],
        roi_size: tuple[int, int],
        *,
        threshold: float = 0.93,
        timeout_s: float = 0.4,
        poll_s: float = 0.1,
    ) -> dict | None:
        self._check_cancel()
        self._ensure_window_active()
        hit = self._image_finder.find_template_in_client_roi(
            template_png_path=template_path,
            roi_top_left_client=roi_top_left,
            roi_size=roi_size,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        self._check_cancel()
        return hit

    def _is_get_content_active(self) -> bool:
        hit = self._find_in_roi(
            self.TEMPLATE_GET_CONTENT_IS_ACTIVE,
            self.GET_CONTENT_IS_ACTIVE_ROI_TOP_LEFT,
            self.GET_CONTENT_IS_ACTIVE_ROI_SIZE,
            timeout_s=0.35,
        )
        return hit is not None

    def _wait_get_content_active(self) -> bool:
        """Ждёт (до timeout) появления активной кнопки 'получить содержимое'."""
        hit = self._find_in_roi(
            self.TEMPLATE_GET_CONTENT_IS_ACTIVE,
            self.GET_CONTENT_IS_ACTIVE_ROI_TOP_LEFT,
            self.GET_CONTENT_IS_ACTIVE_ROI_SIZE,
            timeout_s=float(self._timings.wait_get_content_active_timeout_s),
            poll_s=float(self._timings.wait_get_content_active_poll_s),
        )
        return hit is not None

    def _is_mail_empty(self) -> bool:
        hit = self._find_in_roi(
            self.TEMPLATE_MAIL_IS_EMPTY,
            self.MAIL_IS_EMPTY_ROI_TOP_LEFT,
            self.MAIL_IS_EMPTY_ROI_SIZE,
            timeout_s=0.35,
        )
        return hit is not None

    def _is_mail_empty_fast(self) -> bool:
        """
        Быстрая проверка пустого содержимого.
        По требованию: если проверка "пустого содержимого" не сработала за ~0.1с —
        считаем, что содержимое ЕСТЬ.
        """
        hit = self._find_in_roi(
            self.TEMPLATE_MAIL_IS_EMPTY,
            self.MAIL_IS_EMPTY_ROI_TOP_LEFT,
            self.MAIL_IS_EMPTY_ROI_SIZE,
            timeout_s=float(self._timings.mail_empty_check_timeout_s),
            poll_s=min(0.05, float(self._timings.mail_empty_check_timeout_s) / 2.0) if float(self._timings.mail_empty_check_timeout_s) > 0 else 0.05,
        )
        return hit is not None

    def _wait_deletion_confirmation(
        self,
        spec: MailboxConfirmSpec,
        *,
        timeout_s: float,
        poll_s: float,
        initial_delay_s: float = 0.0,
    ) -> dict | None:
        # Для разных сценариев (get-content vs delete) тайминги могут отличаться.
        self._sleep(float(initial_delay_s))
        return self._find_in_roi(
            self.TEMPLATE_WINDOW_DELETION_CONFIRMATION,
            tuple(spec.roi_top_left),
            tuple(spec.roi_size),
            timeout_s=float(timeout_s),
            poll_s=float(poll_s),
        )

    def _click_client(self, xy: tuple[int, int]) -> None:
        self._check_cancel()
        x, y = xy
        self._emit_log(f"[MAILBOX] click client=({int(x)},{int(y)})")
        self._clicker.click_at_client(int(x), int(y))
        self._sleep(float(self._timings.click_settle_s))

    def _ensure_deletion_confirmation_closed_or_error(self, spec: MailboxConfirmSpec) -> None:
        """
        После нажатия подтверждения удаления проверяем, что окно подтверждения реально закрылось.

        Требование:
        - после клика подождать 0.2с
        - затем ждать кнопку/лейбл подтверждения в течение 1с
          - если НЕ найдено -> ok
          - если найдено -> ошибка (клик не отработал)
        """
        self._sleep(float(self._timings.deletion_confirm_post_click_delay_s))
        hit = self._wait_deletion_confirmation(
            spec,
            timeout_s=float(self._timings.deletion_confirm_disappear_timeout_s),
            poll_s=float(self._timings.deletion_confirm_disappear_poll_s),
            initial_delay_s=0.0,
        )
        if hit is None:
            self._emit_log(f"[MAILBOX] deletion confirmation closed ({spec.label}): OK")
            return
        raise RuntimeError("Подтверждение удаления не закрылось (кнопка/окно всё ещё на месте).")

    def _confirm_deletion_or_error(self, spec: MailboxConfirmSpec, *, timeout_s: float, poll_s: float) -> None:
        self._emit_log(f"[MAILBOX] wait deletion confirmation ({spec.label}) timeout={timeout_s}s poll={poll_s}s")
        hit = self._wait_deletion_confirmation(spec, timeout_s=float(timeout_s), poll_s=float(poll_s), initial_delay_s=0.0)
        if hit is None:
            raise RuntimeError("Окно подтверждения удаления не появилось (timeout).")
        self._emit_log(f"[MAILBOX] deletion confirmation appeared ({spec.label}) -> click confirm")
        self._click_client(tuple(spec.confirm_click_client))
        self._ensure_deletion_confirmation_closed_or_error(spec)

    def _delete_mail_manual(self) -> None:
        """Удалить письмо (manual-delete сценарий)."""
        self._emit_log("[MAILBOX] manual-delete: click delete")
        self._click_client(self.CLICK_DELETE_EMPTY_MAIL_CLIENT)
        self._sleep(float(self._timings.after_click_delete_before_wait_s))
        self._confirm_deletion_or_error(
            self._confirm_manual,
            timeout_s=float(self._timings.wait_deletion_confirm_timeout_delete_s),
            poll_s=float(self._timings.wait_deletion_confirm_poll_delete_s),
        )

    def _get_content_auto_delete(self) -> str:
        """Забрать содержимое (auto-delete сценарий). Возвращает 'processed' или 'retry'."""
        self._emit_log("[MAILBOX] auto-delete: click get-content")
        self._click_client(self.CLICK_GET_CONTENT_CLIENT)

        self._sleep(float(self._timings.after_click_get_content_before_wait_s))
        hit = self._wait_deletion_confirmation(
            self._confirm_auto,
            timeout_s=float(self._timings.wait_deletion_confirm_timeout_s),
            poll_s=float(self._timings.wait_deletion_confirm_poll_get_content_s),
            initial_delay_s=0.0,
        )
        if hit is None:
            self._emit_log("[MAILBOX] auto-delete confirmation not appeared -> retry from start")
            return "retry"

        self._emit_log("[MAILBOX] auto-delete confirmation appeared -> click confirm")
        self._click_client(tuple(self._confirm_auto.confirm_click_client))
        self._ensure_deletion_confirmation_closed_or_error(self._confirm_auto)
        return "processed"

    def _process_one_mail(self) -> str:
        """
        Обрабатывает текущую первую строку (одна попытка).
        Возвращает:
          - "processed": письмо обработано (получено содержимое / подтверждено удаление)
          - "retry": подтверждение не появилось, нужно начать цикл заново
          - "empty_mailbox": ящик пуст -> успех
        """
        state = self.detect_first_row_state()
        if state == "empty_mailbox":
            return "empty_mailbox"
        if state not in ("has_mail", "opened_mail"):
            raise RuntimeError(
                "Не удалось определить состояние первой строки (нет письма / не пустой ящик / письмо не открыто)."
            )

        # Упрощённая логика:
        # 1) письмо есть/открыто — неважно: всегда делаем двойной клик по письму, чтобы оно стало активным
        self._emit_log(f"[MAILBOX] open/select mail by double click (state={state})")
        self._click_client(self.CLICK_OPEN_FIRST_MAIL_CLIENT)
        self._sleep(float(self._timings.double_click_gap_s))
        self._click_client(self.CLICK_OPEN_FIRST_MAIL_CLIENT)

        # 2) ждём, что кнопка "получить содержимое" станет активной (до 1с)
        self._emit_log("[MAILBOX] wait get-content button active")
        active = self._wait_get_content_active()
        self._emit_log(f"[MAILBOX] get-content active={active}")

        # 3) проверяем наличие содержимого: быстрый чек "письмо пустое"
        self._emit_log("[MAILBOX] check mail empty (fast)")
        empty = self._is_mail_empty_fast()
        self._emit_log(f"[MAILBOX] mail empty={empty}")

        if active and (not empty):
            # есть содержимое -> забираем
            return self._get_content_auto_delete()

        # иначе считаем, что содержимого нет -> удаляем (manual-delete)
        # (если active=True и empty=True, то это тоже сюда — содержимого нет)
        self._delete_mail_manual()
        return "processed"

    def prepare_get_mails(self, *, mail_limit: int) -> None:
        """
        Подготовка к основному скрипту "Получить письма":
        - курсор в центр
        - окно почты должно быть открыто
        - должна определиться активная вкладка (incoming/outgoing)
        - клик по нужной вкладке + курсор в центр
        """
        limit = int(mail_limit)
        if limit < 1 or limit > 50:
            raise ValueError("mail_limit must be in 1..50")

        self._emit_log(f"[MAILBOX] prepare_get_mails: limit={limit}")

        self._emit_log("[MAILBOX] cursor -> center (before checks)")
        self.move_cursor_to_screen_center()

        self._emit_log("[MAILBOX] check mailbox window (find_window_mailbox.png)")
        hit = self.check_mailbox_window(timeout_s=0.6)
        if hit is None:
            raise RuntimeError("Окно почтового ящика не обнаружено (find_window_mailbox.png)")
        self._emit_log(f"[MAILBOX] mailbox window OK (score={hit.get('score')}, elapsed={hit.get('elapsed_s')}s)")

        self._emit_log("[MAILBOX] detect active tab (incoming/outgoing)")
        tab = self.detect_active_tab()
        if tab is None:
            raise RuntimeError("Не удалось определить активную вкладку (входящая/исходящая)")
        self._emit_log(f"[MAILBOX] active tab={tab.active_tab} (score={tab.hit.get('score')}, elapsed={tab.hit.get('elapsed_s')}s)")

        # Переключаем вкладку только если она не нужная.
        self.ensure_needed_tab_selected(tab)

        processed = 0
        loops = 0
        while processed < limit:
            self._check_cancel()
            loops += 1
            self._emit_log(f"[MAILBOX] loop={loops} processed={processed}/{limit}")
            res = self._process_one_mail()
            if res == "empty_mailbox":
                self._emit_log("[MAILBOX] no mails: finish with success")
                return
            if res == "retry":
                continue
            # processed
            processed += 1

        self._emit_log("[MAILBOX] limit reached: finish")
