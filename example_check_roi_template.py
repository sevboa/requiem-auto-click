import time
import ctypes
from pathlib import Path

import cv2
import numpy as np
import win32gui


def set_dpi_aware():
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


def load_template_with_alpha_mask(template_png_path: str, alpha_threshold: int = 10):
    tpl = cv2.imread(template_png_path, cv2.IMREAD_UNCHANGED)
    if tpl is None:
        raise FileNotFoundError(f"Template unreadable: {template_png_path}")
    if tpl.ndim != 3 or tpl.shape[2] != 4:
        raise ValueError("Template must be PNG with alpha channel (BGRA).")

    bgr = tpl[:, :, :3]
    alpha = tpl[:, :, 3]
    template_gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mask = np.where(alpha >= alpha_threshold, 255, 0).astype(np.uint8)
    return template_gray, mask


def get_client_origin_on_screen(hwnd: int):
    # (0,0) клиентской области в координатах экрана
    return win32gui.ClientToScreen(hwnd, (0, 0))


def find_template_top_left_in_client_roi_alpha(
    title_substring: str,
    roi_xywh_client: tuple[int, int, int, int],
    template_png_path: str,
    threshold: float = 0.93,
    timeout_s: float = 2.0,
    poll_s: float = 0.1,
):
    """
    Возвращает dict с top-left координатами или None.
    ROI задаётся в координатах клиентской области окна (окно можно двигать).
    """
    hwnd = find_window_hwnd(title_substring)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass

    tpl_gray, mask = load_template_with_alpha_mask(template_png_path, alpha_threshold=10)
    th, tw = tpl_gray.shape[:2]

    import dxcam
    cam = dxcam.create(output_color="BGR")  # region задаём на каждом grab

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        rx, ry, rw, rh = roi_xywh_client

        # вычисляем абсолютный регион экрана для ROI (по текущей позиции окна)
        ox, oy = get_client_origin_on_screen(hwnd)
        region = (ox + rx, oy + ry, ox + rx + rw, oy + ry + rh)

        frame = cam.grab(region=region)  # BGR
        if frame is None:
            time.sleep(poll_s)
            continue

        img_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if th > img_gray.shape[0] or tw > img_gray.shape[1]:
            return None

        res = cv2.matchTemplate(img_gray, tpl_gray, cv2.TM_CCORR_NORMED, mask=mask)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if max_val >= threshold:
            # max_loc — top-left внутри ROI-кадра
            tl_roi_x, tl_roi_y = int(max_loc[0]), int(max_loc[1])

            # top-left в координатах client area
            tl_client_x = rx + tl_roi_x
            tl_client_y = ry + tl_roi_y

            # top-left в координатах экрана
            tl_screen_x = ox + tl_client_x
            tl_screen_y = oy + tl_client_y

            return {
                "score": float(max_val),
                "top_left_in_roi": (tl_roi_x, tl_roi_y),
                "top_left_in_client": (tl_client_x, tl_client_y),
                "top_left_on_screen": (tl_screen_x, tl_screen_y),
                "template_size": (tw, th),
            }

        time.sleep(poll_s)

    return None


if __name__ == "__main__":
    set_dpi_aware()


    # --- НАСТРОЙКИ ---
    TITLE_SUBSTRING = "Requiem"         # подстрока заголовка окна
    ROI_XYWH = (0, 0, 1024, 276)                # (x,y,w,h) внутри окна (в пикселях)
    TEMPLATE = str(Path("img") / "sharpening_window.png")
    THRESHOLD = 0.93

    t_start = time.time()
    hit = find_template_top_left_in_client_roi_alpha(
        title_substring=TITLE_SUBSTRING,
        roi_xywh_client=ROI_XYWH,
        template_png_path=TEMPLATE,
        threshold=THRESHOLD,
        timeout_s=2.0,
        poll_s=0.1,
    )
    elapsed = time.time() - t_start
    print(f"Время исполнения функции: {elapsed:.3f} сек")

    print(hit)  # None или dict со всеми координатами


