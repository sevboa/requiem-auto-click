"""Низкоуровневые функции для работы с клавиатурой через SendInput (Windows).

Две ключевые операции:
1) Нажатие комбинации клавиш (модификаторы + 1 обычная клавиша) по человеко-понятным строкам.
2) Ввод строки через KEYEVENTF_UNICODE (быстрее и надёжнее, чем эмулировать нажатия клавиш).
"""

from __future__ import annotations

import ctypes
import re
import time
from ctypes import wintypes
from typing import Sequence

user32 = ctypes.WinDLL("user32", use_last_error=True)

# --- SendInput constants ---
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# Virtual-Key codes (минимально необходимые)
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21  # Page Up
VK_NEXT = 0x22  # Page Down
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_INSERT = 0x2D
VK_DELETE = 0x2E

# Numpad
VK_NUMPAD0 = 0x60
VK_NUMPAD1 = 0x61
VK_NUMPAD2 = 0x62
VK_NUMPAD3 = 0x63
VK_NUMPAD4 = 0x64
VK_NUMPAD5 = 0x65
VK_NUMPAD6 = 0x66
VK_NUMPAD7 = 0x67
VK_NUMPAD8 = 0x68
VK_NUMPAD9 = 0x69
VK_MULTIPLY = 0x6A
VK_ADD = 0x6B
VK_SEPARATOR = 0x6C
VK_SUBTRACT = 0x6D
VK_DECIMAL = 0x6E
VK_DIVIDE = 0x6F

VK_F1 = 0x70

ULONG_PTR = getattr(wintypes, "ULONG_PTR", wintypes.WPARAM)

# MapVirtualKey constants
MAPVK_VK_TO_VSC = 0


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    # Нужен для корректного размера INPUT (union должен быть размером как max из *INPUT структур).
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _I)]


# Прототипы, чтобы ctypes корректно маршалил параметры.
# Важно: на некоторых сборках/средах Windows + ctypes передача второго аргумента как `c_void_p`
# может приводить к ERROR_INVALID_PARAMETER (87). Явный POINTER(INPUT) более надёжен.
user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT


_FKEY_RE = re.compile(r"^f([1-9]|1\d|2[0-4])$", re.IGNORECASE)

_EXTENDED_VKS: set[int] = {
    VK_LEFT,
    VK_UP,
    VK_RIGHT,
    VK_DOWN,
    VK_HOME,
    VK_END,
    VK_PRIOR,  # PgUp
    VK_NEXT,  # PgDn
    VK_INSERT,
    VK_DELETE,
}


def _send_input_keyboard(ki: KEYBDINPUT) -> None:
    inp = INPUT(type=INPUT_KEYBOARD, ki=ki)
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def _send_vk(vk: int, keyup: bool) -> None:
    flags = KEYEVENTF_KEYUP if keyup else 0
    _send_input_keyboard(
        KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    )


def _vk_to_scancode(vk: int) -> tuple[int, bool]:
    """Возвращает (scan_code, extended). scan_code==0 если не удалось."""
    sc = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    extended = vk in _EXTENDED_VKS
    return sc, extended


def _send_scancode(sc: int, keyup: bool, *, extended: bool) -> None:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if keyup else 0)
    _send_input_keyboard(
        KEYBDINPUT(wVk=0, wScan=sc, dwFlags=flags, time=0, dwExtraInfo=0)
    )


def _send_key(vk: int, keyup: bool, *, prefer_scancode: bool) -> None:
    """Шлёт клавишу либо scancode (как физическую), либо VK (fallback)."""
    if prefer_scancode:
        sc, extended = _vk_to_scancode(vk)
        if sc != 0:
            _send_scancode(sc, keyup=keyup, extended=extended)
            return
    _send_vk(vk, keyup=keyup)


def _send_unicode_unit(unit: int, keyup: bool) -> None:
    """Отправляет один UTF-16 unit (0..0xFFFF) как Unicode key event."""
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
    _send_input_keyboard(
        KEYBDINPUT(wVk=0, wScan=unit, dwFlags=flags, time=0, dwExtraInfo=0)
    )


def _normalize_key_token(token: str) -> str:
    # Пробел поддерживаем в двух вариантах: " " и "Space"
    if token == " ":
        return "space"
    t = token.strip()
    if t == "":
        raise ValueError("Пустая строка в списке клавиш недопустима")
    return t.lower()


def _token_to_modifier_vk(token_norm: str) -> int | None:
    # Синонимы модификаторов
    if token_norm in {"shift"}:
        return VK_SHIFT
    if token_norm in {"ctrl", "control", "ctl"}:
        return VK_CONTROL
    if token_norm in {"alt", "menu"}:
        return VK_MENU
    return None


def _token_to_main_vk(token_norm: str) -> int:
    # Именованные клавиши
    named: dict[str, int] = {
        "space": VK_SPACE,
        "enter": VK_RETURN,
        "return": VK_RETURN,
        "tab": VK_TAB,
        "esc": VK_ESCAPE,
        "escape": VK_ESCAPE,
        "backspace": VK_BACK,
        "delete": VK_DELETE,
        "del": VK_DELETE,
        "insert": VK_INSERT,
        "ins": VK_INSERT,
        "home": VK_HOME,
        "end": VK_END,
        "pageup": VK_PRIOR,
        "pgup": VK_PRIOR,
        "pagedown": VK_NEXT,
        "pgdn": VK_NEXT,
        "left": VK_LEFT,
        "right": VK_RIGHT,
        "up": VK_UP,
        "down": VK_DOWN,

        # Numpad алиасы (NumLock влияет на интерпретацию в некоторых приложениях)
        "num0": VK_NUMPAD0,
        "numpad0": VK_NUMPAD0,
        "num1": VK_NUMPAD1,
        "numpad1": VK_NUMPAD1,
        "num2": VK_NUMPAD2,
        "numpad2": VK_NUMPAD2,
        "num3": VK_NUMPAD3,
        "numpad3": VK_NUMPAD3,
        "num4": VK_NUMPAD4,
        "numpad4": VK_NUMPAD4,
        "num5": VK_NUMPAD5,
        "numpad5": VK_NUMPAD5,
        "num6": VK_NUMPAD6,
        "numpad6": VK_NUMPAD6,
        "num7": VK_NUMPAD7,
        "numpad7": VK_NUMPAD7,
        "num8": VK_NUMPAD8,
        "numpad8": VK_NUMPAD8,
        "num9": VK_NUMPAD9,
        "numpad9": VK_NUMPAD9,
        "num*": VK_MULTIPLY,
        "numpad*": VK_MULTIPLY,
        "num+": VK_ADD,
        "numpad+": VK_ADD,
        "num-": VK_SUBTRACT,
        "numpad-": VK_SUBTRACT,
        "num.": VK_DECIMAL,
        "numpad.": VK_DECIMAL,
        "num/": VK_DIVIDE,
        "numpad/": VK_DIVIDE,
    }
    if token_norm in named:
        return named[token_norm]

    # Функциональные клавиши F1..F24
    m = _FKEY_RE.match(token_norm)
    if m:
        n = int(m.group(1))
        return VK_F1 + (n - 1)

    # Один символ: буква/цифра/пробел (пробел нормализован выше в "space")
    if len(token_norm) == 1:
        ch = token_norm
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            return ord(ch.upper())
        # Пунктуацию/прочее — лучше вводить через type_text(), а не как "нажатие клавиши".
        raise ValueError(
            f"Неизвестная 'обычная' клавиша {ch!r}. Для ввода символов используйте ввод строки."
        )

    raise ValueError(f"Неизвестная клавиша: {token_norm!r}")


def parse_key_combo(keys: Sequence[str]) -> tuple[list[int], int]:
    """Парсит список строковых клавиш в (modifier_vks, main_vk) с валидацией.

    Правила:
    - всё приводится к нижнему регистру (и ' ' == 'Space')
    - модификаторы: Shift/Alt/Ctrl (без дублей)
    - обычная клавиша: ровно одна (без дублей и без "двух обычных")
    """
    if not keys:
        raise ValueError("Список клавиш пуст")

    mods: list[int] = []
    mod_seen: set[int] = set()
    main_tokens: list[str] = []

    for raw in keys:
        token = _normalize_key_token(raw)
        vk_mod = _token_to_modifier_vk(token)
        if vk_mod is not None:
            if vk_mod in mod_seen:
                raise ValueError(f"Модификатор {raw!r} указан дважды")
            mod_seen.add(vk_mod)
            mods.append(vk_mod)
        else:
            main_tokens.append(token)

    if len(main_tokens) == 0:
        raise ValueError("Не указана обычная клавиша (нужно ровно одну, кроме модификаторов)")
    if len(main_tokens) > 1:
        # запрещаем два нажатия обычных клавиш, включая F1..F24
        raise ValueError(f"Указано несколько обычных клавиш: {main_tokens!r}. Разрешена ровно одна.")

    main_vk = _token_to_main_vk(main_tokens[0])
    return mods, main_vk


def press_key_combo(keys: Sequence[str], *, hold_sec: float = 0.08, event_delay_sec: float = 0.0) -> None:
    """Нажимает комбинацию клавиш через SendInput.

    По умолчанию шлём scan-code (KEYEVENTF_SCANCODE), чтобы это выглядело как "физическое" нажатие
    и лучше работало в приложениях/играх. Если для какой-то клавиши scan-code получить нельзя,
    делаем fallback на VK.

    Важно: во многих играх/хуках слишком быстрый down/up "теряется", поэтому по умолчанию
    удерживаем основную клавишу `hold_sec` (как в `example_window_find_and_input.py`).
    """
    mods, main_vk = parse_key_combo(keys)

    # Модификаторы вниз
    for vk in mods:
        _send_key(vk, keyup=False, prefer_scancode=True)
        if event_delay_sec > 0:
            time.sleep(event_delay_sec)

    # Основная клавиша
    _send_key(main_vk, keyup=False, prefer_scancode=True)
    if hold_sec > 0:
        time.sleep(hold_sec)
    _send_key(main_vk, keyup=True, prefer_scancode=True)

    # Модификаторы вверх (в обратном порядке)
    for vk in reversed(mods):
        if event_delay_sec > 0:
            time.sleep(event_delay_sec)
        _send_key(vk, keyup=True, prefer_scancode=True)


def type_text(text: str) -> None:
    """Вводит строку через KEYEVENTF_UNICODE.

    Важно: Windows ожидает UTF-16 units. Для символов > 0xFFFF (суррогатные пары)
    отправляем два units.
    """
    if text == "":
        return

    data = text.encode("utf-16-le", errors="strict")
    # Каждые 2 байта — один unit
    for i in range(0, len(data), 2):
        unit = int.from_bytes(data[i : i + 2], byteorder="little", signed=False)
        _send_unicode_unit(unit, keyup=False)
        _send_unicode_unit(unit, keyup=True)

