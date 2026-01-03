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

def _screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

def _to_absolute(x, y, w, h):
    ax = int(x * 65535 / (w - 1))
    ay = int(y * 65535 / (h - 1))
    return ax, ay

def send_mouse(flags, x=None, y=None):
    """Низкоуровневая отправка события мыши."""
    w, h = _screen_size()
    dx, dy = 0, 0
    if x is not None and y is not None:
        dx, dy = _to_absolute(x, y, w, h)
        flags |= MOUSEEVENTF_ABSOLUTE
    inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=None))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

