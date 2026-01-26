from __future__ import annotations

# pylint: disable=broad-exception-caught
import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..image_finder import ImageFinder
from ..keyboard_utils import press_key_combo, type_text
from ..clicker import Clicker
from ..windows_mouse_client import WindowsMouseClient
from ..mouse_utils import MOUSEEVENTF_MOVE, send_mouse


_HERE = Path(__file__).resolve().parent
LOGIN_CHECK_TEMPLATE_PATH = _HERE / "window_login_check.png"
SELECT_SERVER_CHECK_TEMPLATE_PATH = _HERE / "window_select_server_check.png"
ENTER_CHAR_CHECK_TEMPLATE_PATH = _HERE / "window_enter_char_check.png"
FIND_DIGITAL_BLOCK_TEMPLATE_PATH = _HERE / "find_digital_block.png"
DIGITS_DIR = _HERE / "digital_block"


user32 = ctypes.windll.user32
SW_RESTORE = 9

# server select stage (client-relative; anchor = right-center of client area)
# Смещения x/y заданы "от крайней правой центральной точки" клиентской области:
# x — влево (px), y — вниз (px), положительные.
SELECT_SERVER_ROI_FROM_RIGHT_CENTER: tuple[int, int, int, int] = (390, -197, 50, 42)  # x,y,w,h
SELECT_SERVER_DOUBLE_CLICK_FROM_RIGHT_CENTER: tuple[int, int] = (209, -175)  # x,y
SELECT_SERVER_THRESHOLD: float = 0.95
# 0 или меньше = ждать бесконечно (до отмены)
SELECT_SERVER_TIMEOUT_S_DEFAULT: float = 20.0
SELECT_SERVER_POLL_S_DEFAULT: float = 0.25
SELECT_SERVER_ALPHA_THRESHOLD_DEFAULT: int = 10

# character select stage (client-relative; anchor = right-center of client area)
ENTER_CHAR_ROI_FROM_RIGHT_CENTER: tuple[int, int, int, int] = (55, -256, 50, 50)  # x,y,w,h
ENTER_CHAR_THRESHOLD: float = 0.95
ENTER_CHAR_TIMEOUT_S_DEFAULT: float = 10.0
ENTER_CHAR_POLL_S_DEFAULT: float = 0.25
ENTER_CHAR_ALPHA_THRESHOLD_DEFAULT: int = 10

# character slot select (double click point relative to right-center)
CHAR_SLOT_X_FROM_RIGHT_CENTER: int = 250  # dx_left
CHAR_SLOT_Y_FIRST_FROM_RIGHT_CENTER: int = -220  # dy_down (can be negative)
CHAR_SLOT_Y_STEP: int = 55  # px down per next slot

# pin stage
PIN_BLOCK_SEARCH_TOP_LEFT_CLIENT: tuple[int, int] = (129, 305)
PIN_BLOCK_SEARCH_SIZE: tuple[int, int] = (312, 279)
PIN_BLOCK_THRESHOLD: float = 0.93
PIN_BLOCK_TIMEOUT_S_DEFAULT: float = 5.0
PIN_BLOCK_POLL_S_DEFAULT: float = 0.25

PIN_DIGITS_SEARCH_SIZE: tuple[int, int] = (222, 154)
PIN_DIGIT_THRESHOLD: float = 0.93
PIN_DIGIT_TIMEOUT_S_DEFAULT: float = 5.0
PIN_DIGIT_POLL_S_DEFAULT: float = 0.1

PIN_DIGIT_CLICK_OFFSET: tuple[int, int] = (34, 16)  # from top-left of found digit
PIN_AFTER_DIGIT_MOVE_DELAY_S: float = 0.5
PIN_CONFIRM_CLICK_OFFSET_FROM_BLOCK_TL: tuple[int, int] = (63, -46)


@dataclass(frozen=True)
class LoginRoiFromCenter:
    dx: int
    dy: int
    w: int
    h: int


# --- login stage defaults (client-relative; anchor = center of client area) ---
LOGIN_ROI_FROM_CENTER_DEFAULT = LoginRoiFromCenter(dx=-160, dy=-82, w=320, h=20)
LOGIN_THRESHOLD_DEFAULT: float = 0.95
LOGIN_TIMEOUT_S_DEFAULT: float = 90.0
LOGIN_POLL_S_DEFAULT: float = 0.25
LOGIN_ALPHA_THRESHOLD_DEFAULT: int = 10


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(r)):
        raise ctypes.WinError()
    return int(r.left), int(r.top), int(r.right), int(r.bottom)


def _get_client_size(hwnd: int) -> tuple[int, int]:
    """(w, h) клиентской области окна."""
    r = RECT()
    if not user32.GetClientRect(int(hwnd), ctypes.byref(r)):
        raise ctypes.WinError()
    w = int(r.right - r.left)
    h = int(r.bottom - r.top)
    return w, h


def _screen_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = POINT(int(x), int(y))
    if not user32.ScreenToClient(int(hwnd), ctypes.byref(pt)):
        raise ctypes.WinError()
    return int(pt.x), int(pt.y)


def _client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    pt = POINT(int(x), int(y))
    if not user32.ClientToScreen(int(hwnd), ctypes.byref(pt)):
        raise ctypes.WinError()
    return int(pt.x), int(pt.y)


def _ensure_window_active(hwnd: int) -> None:
    user32.ShowWindow(int(hwnd), SW_RESTORE)
    try:
        user32.SetForegroundWindow(int(hwnd))
    except Exception:
        pass
    time.sleep(0.01)


def _roi_top_left_client_from_center(hwnd: int, roi: LoginRoiFromCenter) -> tuple[int, int]:
    """
    ROI задаётся от центра ОКНА, но для стабильности мы считаем центр
    именно по КЛИЕНТСКОЙ области (без рамки/титлбара).
    Возвращаем top-left ROI в client координатах.
    """
    cw, ch = _get_client_size(hwnd)
    cx = int(cw / 2)
    cy = int(ch / 2)
    return int(cx + int(roi.dx)), int(cy + int(roi.dy))


def _anchor_client_right_center(hwnd: int) -> tuple[int, int]:
    cw, ch = _get_client_size(hwnd)
    # anchor "на правой границе" клиентской области.
    # Используем cw (а не cw-1), чтобы формулы вида (cw - dx) совпадали с ожиданиями.
    return int(max(0, cw)), int(ch // 2)


def _roi_top_left_client_from_anchor(anchor_xy: tuple[int, int], *, dx_left: int, dy_down: int) -> tuple[int, int]:
    ax, ay = anchor_xy
    return int(ax - int(dx_left)), int(ay + int(dy_down))


def _is_cancelled(cancel: object | None) -> bool:
    try:
        return bool(cancel and getattr(cancel, "is_set")())
    except Exception:
        return False


def wait_for_template_in_client_roi(
    *,
    hwnd: int,
    label: str,
    template_path: Path,
    roi_top_left_client: tuple[int, int],
    roi_size: tuple[int, int],
    threshold: float,
    timeout_s: float,
    poll_s: float,
    alpha_threshold: int = 10,
    cancel: object | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Универсальная проверка: ждём template в client-ROI окна через ImageFinder."""
    if not template_path.exists():
        if log:
            log(f"[ERROR] Не найден шаблон: {template_path}")
        return False

    _ensure_window_active(hwnd)

    x0, y0 = int(roi_top_left_client[0]), int(roi_top_left_client[1])
    w, h = int(roi_size[0]), int(roi_size[1])
    cw, ch = _get_client_size(hwnd)

    # базовая валидация ROI, чтобы явно ловить промахи по координатам
    if x0 < 0 or y0 < 0 or w <= 0 or h <= 0 or (x0 + w) > cw or (y0 + h) > ch:
        if log:
            log(
                f"[ERROR] ROI вне client area для '{label}': "
                f"client_size=({cw}x{ch}), roi_client=(x={x0},y={y0},w={w},h={h})"
            )
        return False

    if log:
        tr_pct = int(round(float(threshold) * 100.0))
        # В логах всегда пишем относительно окна (client coords), т.к. эти числа используются для отладки.
        log(f"[RUN] Ожидаю {label} (x={x0},y={y0},w={w},h={h},tr={tr_pct})")

    finder = ImageFinder("Requiem", hwnd_provider=lambda: int(hwnd))
    timeout_val = float(timeout_s)
    has_deadline = timeout_val > 0.0
    deadline = (time.time() + timeout_val) if has_deadline else None
    while True:
        if has_deadline and deadline is not None and time.time() >= deadline:
            break
        if _is_cancelled(cancel):
            return False

        remain = 0.7 if (not has_deadline or deadline is None) else max(0.2, min(0.7, deadline - time.time()))
        hit = finder.find_template_in_client_roi(
            template_png_path=str(template_path),
            roi_top_left_client=(x0, y0),
            roi_size=(w, h),
            threshold=float(threshold),
            timeout_s=float(remain),
            poll_s=float(poll_s),
            alpha_threshold=int(alpha_threshold),
        )
        if hit is not None:
            if log:
                try:
                    sc = float(hit.get("score") or 0.0)
                except Exception:
                    sc = 0.0
                sc_pct = int(round(sc * 100.0))
                log(f"[OK] {label} (sc={sc_pct})")
            return True

        time.sleep(float(poll_s))

    if log:
        log(f"[WARN] {label} (timeout)")
    return False


def find_template_hit_in_client_roi(
    *,
    hwnd: int,
    label: str,
    template_path: Path,
    roi_top_left_client: tuple[int, int],
    roi_size: tuple[int, int],
    threshold: float,
    timeout_s: float,
    poll_s: float,
    alpha_threshold: int = 10,
    cancel: object | None = None,
    log: Callable[[str], None] | None = None,
) -> dict | None:
    """Как wait_for_template_in_client_roi, но возвращает hit (top_left_in_client и score)."""
    if not template_path.exists():
        if log:
            log(f"[ERROR] Не найден шаблон: {template_path}")
        return None

    x0, y0 = int(roi_top_left_client[0]), int(roi_top_left_client[1])
    w, h = int(roi_size[0]), int(roi_size[1])
    cw, ch = _get_client_size(hwnd)
    if x0 < 0 or y0 < 0 or w <= 0 or h <= 0 or (x0 + w) > cw or (y0 + h) > ch:
        if log:
            log(
                f"[ERROR] ROI вне client area для '{label}': "
                f"client_size=({cw}x{ch}), roi_client=(x={x0},y={y0},w={w},h={h})"
            )
        return None

    if log:
        tr_pct = int(round(float(threshold) * 100.0))
        log(f"[RUN] Ожидаю {label} (x={x0},y={y0},w={w},h={h},tr={tr_pct})")

    finder = ImageFinder("Requiem", hwnd_provider=lambda: int(hwnd))
    timeout_val = float(timeout_s)
    has_deadline = timeout_val > 0.0
    deadline = (time.time() + timeout_val) if has_deadline else None
    while True:
        if has_deadline and deadline is not None and time.time() >= deadline:
            break
        if _is_cancelled(cancel):
            return None

        remain = 0.7 if (not has_deadline or deadline is None) else max(0.2, min(0.7, deadline - time.time()))
        hit = finder.find_template_in_client_roi(
            template_png_path=str(template_path),
            roi_top_left_client=(x0, y0),
            roi_size=(w, h),
            threshold=float(threshold),
            timeout_s=float(remain),
            poll_s=float(poll_s),
            alpha_threshold=int(alpha_threshold),
        )
        if hit is not None:
            if log:
                try:
                    sc = float(hit.get("score") or 0.0)
                except Exception:
                    sc = 0.0
                sc_pct = int(round(sc * 100.0))
                log(f"[OK] {label} (sc={sc_pct})")
            return dict(hit)

        time.sleep(float(poll_s))

    if log:
        log(f"[WARN] {label} (timeout)")
    return None

def wait_for_login_screen(
    *,
    hwnd: int,
    roi_from_center: LoginRoiFromCenter,
    threshold: float,
    timeout_s: float,
    poll_s: float,
    cancel: object | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    roi_xy = _roi_top_left_client_from_center(hwnd, roi_from_center)
    return wait_for_template_in_client_roi(
        hwnd=int(hwnd),
        label="экран логина",
        template_path=LOGIN_CHECK_TEMPLATE_PATH,
        roi_top_left_client=(int(roi_xy[0]), int(roi_xy[1])),
        roi_size=(int(roi_from_center.w), int(roi_from_center.h)),
        threshold=float(threshold),
        timeout_s=float(timeout_s),
        poll_s=float(poll_s),
        alpha_threshold=int(LOGIN_ALPHA_THRESHOLD_DEFAULT),
        cancel=cancel,
        log=log,
    )


def input_login_password(*, hwnd: int, login: str, password: str, delay_before_enter_s: float = 1.0) -> None:
    """Вводит логин/пароль в активное окно: login → Tab → password → (delay) → Enter."""
    _ensure_window_active(hwnd)
    time.sleep(1.0)
    type_text(str(login))
    time.sleep(0.08)
    press_key_combo(["tab"])
    time.sleep(0.08)
    type_text(str(password))
    time.sleep(0.08)
    delay = float(delay_before_enter_s)
    if delay < 0:
        delay = 0.0
    if delay > 0:
        time.sleep(delay)
    press_key_combo(["enter"])


def wait_for_select_server_screen(
    *,
    hwnd: int,
    threshold: float = SELECT_SERVER_THRESHOLD,
    timeout_s: float = SELECT_SERVER_TIMEOUT_S_DEFAULT,
    poll_s: float = 0.25,
    cancel: object | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    # ROI от правого центра client area
    anchor = _anchor_client_right_center(hwnd)
    rx, ry, rw, rh = SELECT_SERVER_ROI_FROM_RIGHT_CENTER
    roi_xy = _roi_top_left_client_from_anchor(anchor, dx_left=int(rx), dy_down=int(ry))
    return wait_for_template_in_client_roi(
        hwnd=int(hwnd),
        label="экран выбора сервера",
        template_path=SELECT_SERVER_CHECK_TEMPLATE_PATH,
        roi_top_left_client=(int(roi_xy[0]), int(roi_xy[1])),
        roi_size=(int(rw), int(rh)),
        threshold=float(threshold),
        timeout_s=float(timeout_s),
        poll_s=float(poll_s),
        alpha_threshold=int(SELECT_SERVER_ALPHA_THRESHOLD_DEFAULT),
        cancel=cancel,
        log=log,
    )


def double_click_select_server(*, hwnd: int, log: Callable[[str], None] | None = None) -> None:
    """Двойной клик по координатам выбора сервера (от правого центра client area)."""
    _ensure_window_active(hwnd)
    dx_left, dy_down = SELECT_SERVER_DOUBLE_CLICK_FROM_RIGHT_CENTER
    cw, ch = _get_client_size(hwnd)
    # Строго как договаривались: от правой центральной точки client area:
    # x = cw - dx_left; y = (ch/2) + dy_down
    cx = int(cw - int(dx_left))
    cy = int((ch // 2) + int(dy_down))
    # safety clamp в границы client area
    cx = max(0, min(int(cw - 1), cx))
    cy = max(0, min(int(ch - 1), cy))
    clicker = Clicker(WindowsMouseClient(), "Requiem", hwnd=int(hwnd))
    clicker.click_at_client(int(cx), int(cy))
    time.sleep(0.06)
    clicker.click_at_client(int(cx), int(cy))
    if log:
        log(f"[OK] Двойной клик по серверу выполнен (x={int(cx)},y={int(cy)})")


def wait_for_character_select_screen(
    *,
    hwnd: int,
    threshold: float = ENTER_CHAR_THRESHOLD,
    timeout_s: float = ENTER_CHAR_TIMEOUT_S_DEFAULT,
    poll_s: float = ENTER_CHAR_POLL_S_DEFAULT,
    cancel: object | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Ждёт экран выбора персонажа (шаблон в ROI от правого центра client area)."""
    anchor = _anchor_client_right_center(hwnd)
    rx, ry, rw, rh = ENTER_CHAR_ROI_FROM_RIGHT_CENTER
    roi_xy = _roi_top_left_client_from_anchor(anchor, dx_left=int(rx), dy_down=int(ry))
    return wait_for_template_in_client_roi(
        hwnd=int(hwnd),
        label="экран выбора персонажа",
        template_path=ENTER_CHAR_CHECK_TEMPLATE_PATH,
        roi_top_left_client=(int(roi_xy[0]), int(roi_xy[1])),
        roi_size=(int(rw), int(rh)),
        threshold=float(threshold),
        timeout_s=float(timeout_s),
        poll_s=float(poll_s),
        alpha_threshold=int(ENTER_CHAR_ALPHA_THRESHOLD_DEFAULT),
        cancel=cancel,
        log=log,
    )


def double_click_character_slot(
    *,
    hwnd: int,
    slot: int,
    nickname: str = "",
    log: Callable[[str], None] | None = None,
) -> None:
    """Двойной клик по слоту персонажа (1..8) от правого центра client area."""
    _ensure_window_active(hwnd)
    try:
        s = int(slot)
    except Exception:
        s = 1
    if s < 1:
        s = 1
    if s > 8:
        s = 8

    dx_left = int(CHAR_SLOT_X_FROM_RIGHT_CENTER)
    dy_down = int(CHAR_SLOT_Y_FIRST_FROM_RIGHT_CENTER + (s - 1) * int(CHAR_SLOT_Y_STEP))

    cw, ch = _get_client_size(hwnd)
    cx = int(cw - dx_left)
    cy = int((ch // 2) + dy_down)
    cx = max(0, min(int(cw - 1), cx))
    cy = max(0, min(int(ch - 1), cy))

    if log:
        nick = str(nickname or "").strip()
        if nick:
            log(f"[RUN] Выбор персонажа: slot={s}, nick={nick!r} (x={cx},y={cy})")
        else:
            log(f"[RUN] Выбор персонажа: slot={s} (x={cx},y={cy})")

    clicker = Clicker(WindowsMouseClient(), "Requiem", hwnd=int(hwnd))
    clicker.click_at_client(int(cx), int(cy))
    time.sleep(0.06)
    clicker.click_at_client(int(cx), int(cy))
    if log:
        log(f"[OK] Персонаж выбран (slot={s})")


def _move_cursor_to_client_center(hwnd: int) -> None:
    cw, ch = _get_client_size(hwnd)
    sx, sy = _client_to_screen(hwnd, int(cw // 2), int(ch // 2))
    send_mouse(MOUSEEVENTF_MOVE, int(sx), int(sy))


def enter_pin_code(
    *,
    hwnd: int,
    pin_code: str,
    pin_block_timeout_s: float | None = None,
    pin_digit_timeout_s: float | None = None,
    pin_delay_s: float | None = None,
    cancel: object | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Ввод 4-цифрового PIN через цифровую панель."""
    pin = "".join([c for c in str(pin_code or "") if c.isdigit()])[:4]
    if len(pin) != 4:
        if log:
            log("[ERROR] PIN должен быть из 4 цифр.")
        return False

    # 1) найти цифровой блок (возвращаем top-left)
    block_hit = find_template_hit_in_client_roi(
        hwnd=int(hwnd),
        label="цифровой блок",
        template_path=FIND_DIGITAL_BLOCK_TEMPLATE_PATH,
        roi_top_left_client=PIN_BLOCK_SEARCH_TOP_LEFT_CLIENT,
        roi_size=PIN_BLOCK_SEARCH_SIZE,
        threshold=float(PIN_BLOCK_THRESHOLD),
        timeout_s=float(pin_block_timeout_s if pin_block_timeout_s is not None else PIN_BLOCK_TIMEOUT_S_DEFAULT),
        poll_s=float(PIN_BLOCK_POLL_S_DEFAULT),
        alpha_threshold=10,
        cancel=cancel,
        log=log,
    )
    if block_hit is None:
        return False
    try:
        btl = block_hit.get("top_left_in_client") or (0, 0)
        bx, by = int(btl[0]), int(btl[1])
    except Exception:
        bx, by = 0, 0

    clicker = Clicker(WindowsMouseClient(), "Requiem", hwnd=int(hwnd))

    # 2) клик по цифрам (ищем внутри области кнопок у блока)
    buttons_roi_tl = (int(bx), int(by))
    for d in pin:
        digit_tpl = DIGITS_DIR / f"num_{d}.png"
        delay = float(pin_delay_s if pin_delay_s is not None else PIN_AFTER_DIGIT_MOVE_DELAY_S)
        if delay < 0:
            delay = 0.0
        time.sleep(delay)
        digit_hit = find_template_hit_in_client_roi(
            hwnd=int(hwnd),
            label=f"цифра {d}",
            template_path=digit_tpl,
            roi_top_left_client=buttons_roi_tl,
            roi_size=PIN_DIGITS_SEARCH_SIZE,
            threshold=float(PIN_DIGIT_THRESHOLD),
            timeout_s=float(pin_digit_timeout_s if pin_digit_timeout_s is not None else PIN_DIGIT_TIMEOUT_S_DEFAULT),
            poll_s=float(PIN_DIGIT_POLL_S_DEFAULT),
            alpha_threshold=10,
            cancel=cancel,
            log=log,
        )
        if digit_hit is None:
            return False
        try:
            dtl = digit_hit.get("top_left_in_client") or (0, 0)
            dx, dy = int(dtl[0]), int(dtl[1])
        except Exception:
            dx, dy = 0, 0

        offx, offy = PIN_DIGIT_CLICK_OFFSET
        cx = int(dx + int(offx))
        cy = int(dy + int(offy))
        clicker.click_at_client(int(cx), int(cy))
        _move_cursor_to_client_center(int(hwnd))

    # 3) финальный клик подтверждения
    ok_off_x, ok_off_y = PIN_CONFIRM_CLICK_OFFSET_FROM_BLOCK_TL
    okx = int(bx + int(ok_off_x))
    oky = int(by + int(ok_off_y))
    cw, ch = _get_client_size(hwnd)
    okx = max(0, min(int(cw - 1), okx))
    oky = max(0, min(int(ch - 1), oky))
    delay = float(pin_delay_s if pin_delay_s is not None else PIN_AFTER_DIGIT_MOVE_DELAY_S)
    if delay < 0:
        delay = 0.0
    time.sleep(delay * 2)
    clicker.click_at_client(int(okx), int(oky))
    if log:
        log("[OK] PIN введён")
    return True

def auto_login(
    *,
    hwnd: int,
    login: str,
    password: str,
    character_slot: int = 1,
    character_nickname: str = "",
    pin_code: str = "",
    roi_from_center: LoginRoiFromCenter | None = None,
    threshold: float | None = None,
    timeout_s: float | None = None,
    poll_s: float | None = None,
    delay_before_enter_s: float = 1.0,
    select_server_timeout_s: float | None = None,
    enter_char_timeout_s: float | None = None,
    pin_block_timeout_s: float | None = None,
    pin_digit_timeout_s: float | None = None,
    pin_delay_s: float | None = None,
    cancel: object | None = None,
    log: Callable[[str], None] | None = None,
) -> bool:
    """Полный сценарий: дождаться экрана логина и ввести логин/пароль."""
    if not str(login or "").strip() or not str(password or ""):
        if log:
            log("[WARN] Автологин: пустой логин/пароль — пропускаю.")
        return False

    if log:
        log(
            "[RUN] Автологин: ожидаю экран логина "
            f"(tpl={LOGIN_CHECK_TEMPLATE_PATH}, roi={roi_from_center or LOGIN_ROI_FROM_CENTER_DEFAULT}, "
            f"threshold={float(threshold if threshold is not None else LOGIN_THRESHOLD_DEFAULT):.3f})..."
        )

    ok = wait_for_login_screen(
        hwnd=int(hwnd),
        roi_from_center=roi_from_center or LOGIN_ROI_FROM_CENTER_DEFAULT,
        threshold=float(threshold if threshold is not None else LOGIN_THRESHOLD_DEFAULT),
        timeout_s=float(timeout_s if timeout_s is not None else LOGIN_TIMEOUT_S_DEFAULT),
        poll_s=float(poll_s if poll_s is not None else LOGIN_POLL_S_DEFAULT),
        cancel=cancel,
        log=log,
    )
    if not ok:
        return False

    if log:
        log("[RUN] Автологин: ввожу логин/пароль...")
    input_login_password(
        hwnd=int(hwnd),
        login=str(login),
        password=str(password),
        delay_before_enter_s=float(delay_before_enter_s),
    )

    # После логина ждём экран выбора сервера и выбираем сервер двойным кликом.
    ok2 = wait_for_select_server_screen(
        hwnd=int(hwnd),
        threshold=float(SELECT_SERVER_THRESHOLD),
        timeout_s=float(select_server_timeout_s if select_server_timeout_s is not None else SELECT_SERVER_TIMEOUT_S_DEFAULT),
        poll_s=float(SELECT_SERVER_POLL_S_DEFAULT),
        cancel=cancel,
        log=log,
    )
    if not ok2:
        return False
    #time.sleep(1.0)
    double_click_select_server(hwnd=int(hwnd), log=log)

    # Ждём экран выбора персонажа и выбираем слот двойным кликом.
    ok3 = wait_for_character_select_screen(
        hwnd=int(hwnd),
        threshold=float(ENTER_CHAR_THRESHOLD),
        timeout_s=float(enter_char_timeout_s if enter_char_timeout_s is not None else ENTER_CHAR_TIMEOUT_S_DEFAULT),
        poll_s=float(ENTER_CHAR_POLL_S_DEFAULT),
        cancel=cancel,
        log=log,
    )
    if not ok3:
        return False
    #time.sleep(2.0)
    double_click_character_slot(
        hwnd=int(hwnd),
        slot=int(character_slot),
        nickname=str(character_nickname or ""),
        log=log,
    )

    # Ввод PIN (цифровая панель)
    ok4 = enter_pin_code(
        hwnd=int(hwnd),
        pin_code=str(pin_code or ""),
        pin_block_timeout_s=pin_block_timeout_s,
        pin_digit_timeout_s=pin_digit_timeout_s,
        pin_delay_s=pin_delay_s,
        cancel=cancel,
        log=log,
    )
    if not ok4:
        return False
    return True

