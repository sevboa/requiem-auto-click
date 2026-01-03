import ctypes
from pathlib import Path

import cv2
import win32gui


def set_dpi_aware():
    # Чтобы координаты не "плыли" из-за Windows scaling
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def find_window_hwnd(title_substring: str) -> int:
    hwnds = []

    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title_substring.lower() in title.lower():
                hwnds.append(hwnd)

    win32gui.EnumWindows(enum_handler, None)
    if not hwnds:
        raise RuntimeError(f"Window not found by title substring: {title_substring!r}")
    return hwnds[0]


def get_client_abs_region(hwnd: int, x: int, y: int, w: int, h: int):
    """
    ROI задан в координатах КЛИЕНТСКОЙ области окна:
      (0,0) — левый верх клиентской области (без заголовка/рамок)
    Возвращает абсолютный region (left, top, right, bottom) в координатах экрана.
    """
    if w <= 0 or h <= 0:
        raise ValueError("w and h must be > 0")

    # размеры клиентской области
    cl, ct, cr, cb = win32gui.GetClientRect(hwnd)  # обычно (0,0,client_w,client_h)
    client_w = cr - cl
    client_h = cb - ct

    # простая защита от выхода за границы
    x2 = min(max(x + w, 0), client_w)
    y2 = min(max(y + h, 0), client_h)
    x1 = min(max(x, 0), client_w)
    y1 = min(max(y, 0), client_h)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("ROI is outside of client area or has zero size after clamping.")

    # перевод (0,0) клиентской области в координаты экрана
    origin_screen = win32gui.ClientToScreen(hwnd, (0, 0))
    ox, oy = origin_screen

    left = ox + x1
    top = oy + y1
    right = ox + x2
    bottom = oy + y2
    return (left, top, right, bottom)


def grab_client_roi(title_substring: str, x: int, y: int, w: int, h: int, out_path: str = "roi.png") -> str:
    hwnd = find_window_hwnd(title_substring)

    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass

    region = get_client_abs_region(hwnd, x, y, w, h)

    import dxcam
    cam = dxcam.create(output_color="BGR")  # без фиксированного region
    frame = cam.grab(region=region)         # region вычислен по текущей позиции окна

    if frame is None:
        raise RuntimeError("Failed to capture ROI (frame is None).")

    out_path = str(Path(out_path).resolve())
    cv2.imwrite(out_path, frame)
    return out_path


if __name__ == "__main__":
    set_dpi_aware()

    # --- ВАРИАНТ 1: абсолютные координаты экрана ---
    # path = grab_screen_region_xywh(x=200, y=150, w=400, h=220, out_path="abs_region.png")
    # print("Saved:", path)

    # --- ВАРИАНТ 2: координаты внутри окна по подстроке заголовка ---
    path = grab_client_roi(
        title_substring="Requiem",
        x=792, y=237, w=189, h=29,
        out_path="win_region.png"
    )
    print("Saved:", path)