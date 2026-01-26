"""Низкоуровневые функции для работы с мышью через SendInput."""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32

# DPI-aware (чтобы координаты не поехали при 125%/150%)
try:
    user32.SetProcessDPIAware()
except Exception:
    pass

# --- SendInput ---
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [("type", ctypes.c_ulong), ("i", _I)]

def _virtual_screen_rect() -> tuple[int, int, int, int]:
    """
    (left, top, width, height) виртуального рабочего стола (multi-monitor).
    SM_XVIRTUALSCREEN=76, SM_YVIRTUALSCREEN=77, SM_CXVIRTUALSCREEN=78, SM_CYVIRTUALSCREEN=79
    """
    left = int(user32.GetSystemMetrics(76))
    top = int(user32.GetSystemMetrics(77))
    width = int(user32.GetSystemMetrics(78))
    height = int(user32.GetSystemMetrics(79))
    return left, top, width, height

def _to_absolute_virtual(x: int, y: int) -> tuple[int, int]:
    """Преобразует экранные coords (x,y) в 0..65535 для VIRTUALDESK."""
    left, top, w, h = _virtual_screen_rect()
    if w <= 1 or h <= 1:
        return 0, 0
    nx = int((int(x) - left) * 65535 / (w - 1))
    ny = int((int(y) - top) * 65535 / (h - 1))
    nx = max(0, min(65535, nx))
    ny = max(0, min(65535, ny))
    return nx, ny

def send_mouse(flags, x=None, y=None):
    """Низкоуровневая отправка события мыши."""
    dx, dy = 0, 0
    if x is not None and y is not None:
        dx, dy = _to_absolute_virtual(int(x), int(y))
        # Важно: используем VIRTUALDESK, иначе координаты считаются по primary screen и "улетают".
        flags |= (MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=None))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

