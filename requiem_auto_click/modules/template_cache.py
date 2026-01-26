# pylint: disable=no-member

"""Общий кэш для PNG/JPG шаблонов (template matching).

Идея:
- Шаблоны читаются с диска один раз и сохраняются в памяти (lru_cache).
- Менеджеры UI могут "прогреть" кэш при инициализации (preload),
  чтобы во время проверок/матчинга не было чтения файлов.

Кэш хранит:
- tpl_gray: np.ndarray (GRAY)
- mask: np.ndarray | None (если исходный PNG с alpha)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np


def _load_cv2() -> Any:
    # Локальный импорт, чтобы type checker не пытался типизировать модуль.
    import cv2 as _cv2

    return _cv2


_cv2: Any = _load_cv2()


@lru_cache(maxsize=1024)
def get_template_gray_and_mask_cached(template_png_path: str, alpha_threshold: int = 10):
    """Возвращает (tpl_gray, mask) из кэша; при промахе читает с диска и кладёт в кэш."""
    tpl = _cv2.imread(template_png_path, _cv2.IMREAD_UNCHANGED)  # type: ignore[attr-defined]
    if tpl is None:
        raise FileNotFoundError(f"Template unreadable: {template_png_path}")

    # PNG с альфой: используем маску
    if tpl.ndim == 3 and tpl.shape[2] == 4:
        bgr = tpl[:, :, :3]
        alpha = tpl[:, :, 3]
        tpl_gray = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2GRAY)  # type: ignore[attr-defined]
        mask = np.where(alpha >= alpha_threshold, 255, 0).astype(np.uint8)
        return tpl_gray, mask

    # Обычное изображение: без маски
    if tpl.ndim == 2:
        tpl_gray = tpl
    else:
        tpl_gray = _cv2.cvtColor(tpl, _cv2.COLOR_BGR2GRAY)  # type: ignore[attr-defined]
    return tpl_gray, None


def preload_template(template_png_path: str | Path, *, alpha_threshold: int = 10) -> None:
    """Прогревает кэш для одного шаблона."""
    _ = get_template_gray_and_mask_cached(str(template_png_path), int(alpha_threshold))


def preload_templates(
    template_png_paths: Iterable[str | Path],
    *,
    alpha_threshold: int = 10,
) -> None:
    """Прогревает кэш для нескольких шаблонов."""
    thr = int(alpha_threshold)
    for p in template_png_paths:
        _ = get_template_gray_and_mask_cached(str(p), thr)


def cache_info() -> Any:
    """Отладка: статистика LRU."""
    return get_template_gray_and_mask_cached.cache_info()


def cache_clear() -> None:
    """Сбрасывает кэш (обычно не нужно, но полезно для тестов/отладки)."""
    get_template_gray_and_mask_cached.cache_clear()

