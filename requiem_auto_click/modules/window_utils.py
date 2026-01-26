"""Утилиты для работы с окнами Windows."""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

SW_RESTORE = 9

def enum_windows():
    """Перечисляет все видимые окна с заголовками."""
    results = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        results.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return results

def find_hwnd_by_title_substring(substr: str) -> int:
    """Находит handle окна по подстроке в заголовке."""
    s = substr.lower()
    for hwnd, title in enum_windows():
        if s in title.lower():
            return hwnd
    return 0

class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

def client_to_screen(hwnd, cx, cy):
    """Преобразует координаты из клиентской области окна в экранные."""
    pt = POINT(cx, cy)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y

