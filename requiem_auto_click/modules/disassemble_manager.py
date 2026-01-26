"""Управление интерфейсом разборки (disassemble) в Requiem.

Минимальная логика:
- Поиск окна разборки по PNG-шаблону в client area окна игры.
- Нижняя полоса 360px исключается из поиска (часто перекрыта UI).
"""

from __future__ import annotations

from pathlib import Path

from .clicker import Clicker
from .image_finder import ImageFinder
from .backpack_manager import BackpackManager
from .template_cache import preload_templates


class DisassembleManager:
    """Минимальный менеджер интерфейса разборки (disassemble)."""

    _ASSETS_DIR: Path = Path(__file__).resolve().parent / "disassemble"
    TEMPLATE_WINDOW_OPENED: Path = _ASSETS_DIR / "window_opened_disassemble.png"
    EXCLUDE_BOTTOM_STRIP_PX: int = 360
    DEFAULT_TITLE_CENTER_TARGET_ON_SCREEN: tuple[int, int] = (313, 76)
    # После выравнивания (drag заголовка в DEFAULT_TITLE_CENTER_TARGET_ON_SCREEN) шаблон оказывается здесь в client coords.
    # Используется, чтобы не дёргать перетаскивание лишний раз.
    ALIGNED_TITLE_TOP_LEFT_IN_CLIENT: tuple[int, int] = (1, 35)

    # Смещения (px) от top-left "заголовка" окна (top_left_in_client), координаты центров
    DISASSEMBLE_CELL_CENTER_FROM_TOP_RIGHT: tuple[int, int] = (143, 93)
    OK_BUTTON_CENTER_FROM_TOP_RIGHT: tuple[int, int] = (129, 267)
    CANCEL_BUTTON_CENTER_FROM_TOP_RIGHT: tuple[int, int] = (217, 267)

    WINDOW_NOT_FOUND_ERROR_MESSAGE: str = (
        "Окно разборки не найдено. "
        "Необходимо открыть интерфейс разборки у NPC, который занимается разборкой."
    )

    def __init__(
        self,
        *,
        clicker: Clicker,
        image_finder: ImageFinder,
        backpacks: BackpackManager,
        align_on_init: bool = False,
        title_center_target_on_screen: tuple[int, int] = DEFAULT_TITLE_CENTER_TARGET_ON_SCREEN,
    ) -> None:
        self._clicker = clicker
        self._image_finder = image_finder
        self.backpacks = backpacks
        self._title_center_target_on_screen = title_center_target_on_screen

        # Прогреваем шаблон окна разборки заранее.
        preload_templates([self.TEMPLATE_WINDOW_OPENED])

        # Обновляется при find/move
        self.top_left_in_client: tuple[int, int] | None = None

        if align_on_init:
            self.ensure_window_cached(
                threshold=0.98,
                timeout_s=2.0,
                poll_s=0.1,
                move_title_center_to_screen=self._title_center_target_on_screen,
            )

    def _find_disassemble_window_hit(
        self,
        *,
        threshold: float,
        timeout_s: float,
        poll_s: float,
    ) -> dict:
        coords = self._clicker.find_coords()
        cw, ch = coords["client_size"]
        roi_h = max(1, int(ch) - int(self.EXCLUDE_BOTTOM_STRIP_PX))

        hit = self._image_finder.find_template_in_client_roi(
            template_png_path=self.TEMPLATE_WINDOW_OPENED,
            roi_top_left_client=(0, 0),
            roi_size=(int(cw), int(roi_h)),
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        if hit is None:
            raise RuntimeError(self.WINDOW_NOT_FOUND_ERROR_MESSAGE)
        return hit

    def _update_cached_window_geometry(self, hit: dict) -> None:
        self.top_left_in_client = tuple(hit["top_left_in_client"])

    def _require_cached_top_left_in_client(self) -> tuple[int, int]:
        if self.top_left_in_client is None:
            raise RuntimeError("Окно разборки ещё не найдено (нет сохранённых координат). Вызови ensure_window_cached().")
        return self.top_left_in_client

    def ensure_window_cached(
        self,
        *,
        threshold: float = 0.98,
        timeout_s: float = 2.0,
        poll_s: float = 0.1,
        move_title_center_to_screen: tuple[int, int] | None = DEFAULT_TITLE_CENTER_TARGET_ON_SCREEN,
    ) -> tuple[int, int]:
        """
        Дорогая операция: находит окно (и при необходимости перемещает), затем сохраняет координаты.

        После вызова этого метода:
        - `drag_item_from_backpack_cell_to_disassemble_cell()`
        - `click_ok()`
        - `click_cancel()`
        работают без повторного поиска окна (по кэшу).
        """
        return self.find_disassemble_window_top_left(
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
            move_title_center_to_screen=move_title_center_to_screen,
        )

    def find_disassemble_window_top_left(
        self,
        *,
        threshold: float = 0.98,
        timeout_s: float = 2.0,
        poll_s: float = 0.1,
        move_title_center_to_screen: tuple[int, int] | None = DEFAULT_TITLE_CENTER_TARGET_ON_SCREEN,
    ) -> tuple[int, int]:
        """
        Находит окно разборки по "заголовку" (PNG-шаблон) и возвращает его top-left (screen coords).

        Поиск идёт по всему client area окна игры, но без нижней полосы 360px.
        """
        hit = self._find_disassemble_window_hit(threshold=threshold, timeout_s=timeout_s, poll_s=poll_s)
        self._update_cached_window_geometry(hit)

        if move_title_center_to_screen is not None and tuple(hit["top_left_in_client"]) != self.ALIGNED_TITLE_TOP_LEFT_IN_CLIENT:
            target = move_title_center_to_screen
            w, h = hit["template_size"]
            x0, y0 = hit["top_left_in_client"]
            header_center = (int(x0 + int(w) // 2), int(y0 + int(h) // 2))
            if header_center != target:
                self._clicker.drag_client(header_center, target)
                # После перемещения обязательно перепроверяем позицию и обновляем координаты
                hit = self._find_disassemble_window_hit(threshold=threshold, timeout_s=1.0, poll_s=poll_s)
                self._update_cached_window_geometry(hit)

        x, y = hit["top_left_on_screen"]
        return (int(x), int(y))

    def drag_item_from_backpack_cell_to_disassemble_cell(
        self,
        backpack_index: int,
        row: int,
        col: int,
        *,
        cell_threshold: float = 0.98,
        cell_timeout_s: float = 0.25,
        cell_poll_s: float = 0.05,
    ) -> None:
        """
        Перетаскивает предмет из конкретной ячейки рюкзака в центр ячейки разборки.
        """
        # Важно: окно должно быть уже найдено/закэшировано (ensure_window_cached или align_on_init=True)
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.DISASSEMBLE_CELL_CENTER_FROM_TOP_RIGHT
        target_client = (int(tlx + dx), int(tly + dy))

        cell = self.backpacks.get_backpack_cell_info(
            backpack_index=backpack_index,
            row=row,
            col=col,
            threshold=cell_threshold,
            timeout_s=cell_timeout_s,
            poll_s=cell_poll_s,
        )
        if cell["state"] == "empty":
            return False
        start_client = cell["center_client"]
        self._clicker.drag_client(start_client, target_client, steps = 50, step_delay = 0.008)
        return True

    def click_ok(
        self,
    ) -> None:
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.OK_BUTTON_CENTER_FROM_TOP_RIGHT
        self._clicker.click_at_client(int(tlx + dx), int(tly + dy))

    def click_cancel(
        self,
    ) -> None:
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.CANCEL_BUTTON_CENTER_FROM_TOP_RIGHT
        self._clicker.click_at_client(int(tlx + dx), int(tly + dy))


