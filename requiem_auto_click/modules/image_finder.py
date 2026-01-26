# pylint: disable=no-member

"""Поиск изображения (template matching) на экране в области окна.

Первый метод: поиск шаблона в ROI (области интереса), заданной в координатах client area окна.
Возвращает координаты найденного шаблона (top-left), размер шаблона, score и время поиска.
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Callable

import numpy as np
import dxcam

from .window_utils import find_hwnd_by_title_substring, client_to_screen, SW_RESTORE
from .template_cache import get_template_gray_and_mask_cached, preload_templates

user32 = ctypes.windll.user32

# У opencv-python часто нет корректных stubs (pyright/pylance ругаются на атрибуты),
# поэтому загружаем cv2 динамически и типизируем как Any.
def _load_cv2() -> Any:
    import cv2 as _cv2  # локальный импорт, чтобы type checker не пытался типизировать модуль

    return _cv2


_cv2: Any = _load_cv2()


@dataclass(frozen=True)
class TemplateHit:
    """Результат поиска шаблона."""

    score: float
    top_left_in_roi: Tuple[int, int]
    top_left_in_client: Tuple[int, int]
    top_left_on_screen: Tuple[int, int]
    template_size: Tuple[int, int]  # (w, h)
    elapsed_s: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": float(self.score),
            "top_left_in_roi": tuple(self.top_left_in_roi),
            "top_left_in_client": tuple(self.top_left_in_client),
            "top_left_on_screen": tuple(self.top_left_on_screen),
            "template_size": tuple(self.template_size),
            "elapsed_s": float(self.elapsed_s),
        }


class ImageFinder:
    """Класс для поиска изображений на экране в контексте окна (по подстроке заголовка)."""

    def __init__(self, window_title_substring: str, *, hwnd_provider: Optional[Callable[[], int]] = None):
        self.window_title_substring = window_title_substring
        self._hwnd: Optional[int] = None
        self._hwnd_provider = hwnd_provider
        self._set_dpi_aware()
        # Захват экрана: предпочитаем dxcam (быстро), но на некоторых системах
        # Desktop Duplication / D3D11 feature level может быть недоступен (COMError).
        self._cam = None
        self._mss = None
        self.capture_backend: str = "unknown"

        def _grab_with_dxcam(region: Tuple[int, int, int, int]) -> Optional[np.ndarray]:
            # dxcam возвращает BGR или None
            return self._cam.grab(region=region)  # type: ignore[union-attr]

        def _grab_with_mss(region: Tuple[int, int, int, int]) -> Optional[np.ndarray]:
            # mss возвращает BGRA; конвертируем в BGR numpy array
            left, top, right, bottom = region
            width = int(right - left)
            height = int(bottom - top)
            if width <= 0 or height <= 0:
                return None
            mon = {"left": int(left), "top": int(top), "width": width, "height": height}
            img = self._mss.grab(mon)  # type: ignore[union-attr]
            arr = np.asarray(img)
            # arr: (h, w, 4) BGRA -> BGR
            return arr[:, :, :3].copy()

        try:
            # dxcam лучше создавать один раз и переиспользовать
            self._cam = dxcam.create(output_color="BGR")
            self._grab_region_bgr: Callable[[Tuple[int, int, int, int]], Optional[np.ndarray]] = _grab_with_dxcam
            self.capture_backend = "dxcam"
        except Exception:  # pylint: disable=broad-exception-caught
            # Фолбэк на mss: медленнее, но работает даже там, где dxcam не поддержан
            try:
                import mss  # type: ignore  # pylint: disable=import-error

                self._mss = mss.mss()
                self._grab_region_bgr = _grab_with_mss
                self.capture_backend = "mss"
            except Exception as e:  # pylint: disable=broad-exception-caught
                raise RuntimeError(
                    "Не удалось инициализировать захват экрана (dxcam и mss). "
                    "На этой системе dxcam может быть не поддержан (драйвер/DirectX/RDP/VM), "
                    "а mss не установлен. Установите зависимости и/или обновите драйвер видеокарты."
                ) from e

    def _set_dpi_aware(self) -> None:
        """Устанавливает DPI awareness для корректной работы с координатами."""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
            return
        except (OSError, AttributeError):
            pass
        try:
            user32.SetProcessDPIAware()
        except (OSError, AttributeError):
            pass

    def _get_hwnd(self) -> int:
        if self._hwnd is None:
            if self._hwnd_provider is not None:
                self._hwnd = int(self._hwnd_provider())
            else:
                self._hwnd = find_hwnd_by_title_substring(self.window_title_substring)
            if not self._hwnd:
                raise SystemExit(
                    f"Окно не найдено по подстроке заголовка: {self.window_title_substring!r}"
                )
        return self._hwnd

    def _ensure_window_active(self) -> None:
        hwnd = self._get_hwnd()
        user32.ShowWindow(hwnd, SW_RESTORE)
        # Фокус может быть запрещён политиками, но ShowWindow обычно безопасен
        try:
            user32.SetForegroundWindow(hwnd)
        except (OSError, AttributeError):
            pass
        time.sleep(0.01)

    @staticmethod
    def preload_template_cache(
        template_png_paths: list[str | Path],
        *,
        alpha_threshold: int = 10,
    ) -> None:
        """Прогревает общий кэш шаблонов, чтобы в рантайме не читать их с диска."""
        preload_templates(template_png_paths, alpha_threshold=int(alpha_threshold))

    def find_template_in_client_roi(
        self,
        template_png_path: str | Path,
        roi_top_left_client: Tuple[int, int],
        roi_size: Tuple[int, int],
        *,
        threshold: float = 0.93,
        timeout_s: float = 2.0,
        poll_s: float = 0.1,
        alpha_threshold: int = 10,
    ) -> Optional[Dict[str, Any]]:
        """
        Ищет шаблон в ROI, заданном в координатах client area окна.

        Args:
            template_png_path: путь к шаблону (PNG/JPG/etc). Если PNG с alpha — будет использована mask.
            roi_top_left_client: (x, y) левого верхнего угла ROI в client координатах.
            roi_size: (w, h) ROI в client координатах.
            threshold: порог совпадения (score).
            timeout_s: таймаут поиска (повторные пробы).
            poll_s: задержка между пробами.
            alpha_threshold: порог альфа-канала для маски (для PNG с alpha).

        Returns:
            dict как в example_check_roi_template.py + elapsed_s, или None если не найдено.
        """
        self._ensure_window_active()
        hwnd = self._get_hwnd()

        template_path = str(template_png_path)
        tpl_gray, mask = get_template_gray_and_mask_cached(template_path, int(alpha_threshold))
        th, tw = tpl_gray.shape[:2]

        rx, ry = roi_top_left_client
        rw, rh = roi_size

        t0 = time.perf_counter()
        while (time.perf_counter() - t0) < float(timeout_s):
            # ROI в экранных координатах, учитывая текущую позицию окна
            ox, oy = client_to_screen(hwnd, 0, 0)
            region = (ox + rx, oy + ry, ox + rx + rw, oy + ry + rh)

            frame = self._grab_region_bgr(region)  # BGR
            if frame is None:
                time.sleep(poll_s)
                continue

            img_gray = _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY)  # type: ignore[attr-defined]
            if th > img_gray.shape[0] or tw > img_gray.shape[1]:
                return None

            # mask работает только с некоторыми методами, TM_CCORR_NORMED поддерживает mask
            res = _cv2.matchTemplate(  # type: ignore[attr-defined]
                img_gray, tpl_gray, _cv2.TM_CCORR_NORMED, mask=mask
            )
            _, max_val, _, max_loc = _cv2.minMaxLoc(res)  # type: ignore[attr-defined]

            if float(max_val) >= float(threshold):
                tl_roi_x, tl_roi_y = int(max_loc[0]), int(max_loc[1])
                tl_client_x = int(rx + tl_roi_x)
                tl_client_y = int(ry + tl_roi_y)
                tl_screen_x = int(ox + tl_client_x)
                tl_screen_y = int(oy + tl_client_y)
                elapsed = time.perf_counter() - t0

                hit = TemplateHit(
                    score=float(max_val),
                    top_left_in_roi=(tl_roi_x, tl_roi_y),
                    top_left_in_client=(tl_client_x, tl_client_y),
                    top_left_on_screen=(tl_screen_x, tl_screen_y),
                    template_size=(int(tw), int(th)),
                    elapsed_s=float(elapsed),
                )
                return hit.as_dict()

            time.sleep(poll_s)

        return None


    def grab_client_roi_bgr(
        self,
        roi_top_left_client: Tuple[int, int],
        roi_size: Tuple[int, int],
    ) -> Optional[np.ndarray]:
        """
        Захватывает ROI (в client координатах) одним кадром.

        Returns:
            np.ndarray (BGR) или None, если кадр не получен.
        """
        self._ensure_window_active()
        hwnd = self._get_hwnd()

        rx, ry = roi_top_left_client
        rw, rh = roi_size

        ox, oy = client_to_screen(hwnd, 0, 0)
        region = (ox + rx, oy + ry, ox + rx + rw, oy + ry + rh)
        return self._grab_region_bgr(region)  # BGR или None

    def grab_client_roi_gray(
        self,
        roi_top_left_client: Tuple[int, int],
        roi_size: Tuple[int, int],
        *,
        timeout_s: float = 0.25,
        poll_s: float = 0.02,
    ) -> Optional[np.ndarray]:
        """
        Захватывает ROI (в client координатах) и возвращает GRAY.

        Важно: dxcam.grab иногда возвращает None (транзиентно), поэтому здесь есть
        короткий цикл ожидания (timeout/poll), как в find_template_in_client_roi().

        Returns:
            np.ndarray (GRAY) или None, если кадр не получен.
        """
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) < float(timeout_s):
            frame = self.grab_client_roi_bgr(roi_top_left_client=roi_top_left_client, roi_size=roi_size)
            if frame is not None:
                return _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY)  # type: ignore[attr-defined]
            time.sleep(float(poll_s))
        return None

    @staticmethod
    def match_template_score_in_gray(
        gray_image: np.ndarray,
        *,
        template_png_path: str | Path,
        alpha_threshold: int = 10,
    ) -> float:
        """
        Быстро считает score (max_val) совпадения шаблона в уже готовом gray-изображении.

        Используется для сверхмаленьких ROI (например, цифры 7x9), чтобы:
        - не делать повторный grab,
        - не читать шаблон с диска (кэш).
        """
        template_path = str(template_png_path)
        tpl_gray, mask = get_template_gray_and_mask_cached(template_path, int(alpha_threshold))
        th, tw = tpl_gray.shape[:2]
        if th > gray_image.shape[0] or tw > gray_image.shape[1]:
            return 0.0

        res = _cv2.matchTemplate(  # type: ignore[attr-defined]
            gray_image, tpl_gray, _cv2.TM_CCORR_NORMED, mask=mask
        )
        _, max_val, _, _ = _cv2.minMaxLoc(res)  # type: ignore[attr-defined]
        return float(max_val)

    def save_client_roi_to_file(
        self,
        output_path: str | Path,
        roi_top_left_client: Tuple[int, int],
        roi_size: Tuple[int, int],
    ) -> Dict[str, Any]:
        """
        Сохраняет ROI (в client координатах) в файл изображением.

        Returns:
            dict с output_path, roi, elapsed_s и др. для отладки.
        """
        t0 = time.perf_counter()
        frame = self.grab_client_roi_bgr(roi_top_left_client=roi_top_left_client, roi_size=roi_size)
        elapsed = time.perf_counter() - t0

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        if frame is None:
            raise RuntimeError("Не удалось получить кадр (grab вернул None)")

        ok = _cv2.imwrite(str(out), frame)  # type: ignore[attr-defined]
        if not ok:
            raise RuntimeError(f"Не удалось сохранить изображение в файл: {str(out)!r}")

        hwnd = self._get_hwnd()
        ox, oy = client_to_screen(hwnd, 0, 0)
        rx, ry = roi_top_left_client

        return {
            "output_path": str(out),
            "roi_top_left_client": tuple(roi_top_left_client),
            "roi_size": tuple(roi_size),
            "roi_top_left_on_screen": (int(ox + rx), int(oy + ry)),
            "image_size": (int(frame.shape[1]), int(frame.shape[0])),  # (w, h)
            "elapsed_s": float(elapsed),
        }

