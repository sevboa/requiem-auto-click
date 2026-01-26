"""Управление рюкзаком (мешками) в интерфейсе Requiem.

Минимальная логика:
- ROI задан от правого нижнего края client area.
- Проверяем состояние по PNG-шаблонам из папки img.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence
import time

from .clicker import Clicker
from .image_finder import ImageFinder
from .template_cache import preload_templates


class BackpackManager:
    """Менеджер рюкзака/мешков (UI)."""

    _ASSETS_DIR: Path = Path(__file__).resolve().parent / "backpack"
    ROI_SIZE: tuple[int, int] = (27, 20)
    ROI_TOP_LEFT_FROM_BOTTOM_RIGHT: tuple[int, int] = (145, 65)  # (dx_from_right, dy_from_bottom)

    GRID_ROWS: int = 2
    GRID_COLS: int = 4
    GRID_STEP_X_PX: int = 35  # между рюкзаками в ряду (по X, от левого края ROI к левому краю ROI)
    GRID_STEP_Y_PX: int = 23  # между рядами (по Y, от верхнего края ROI к верхнему краю ROI)

    TEMPLATE_DISABLED: str = "bag_disabled.png"
    TEMPLATE_OPENED: str = "bag_opened.png"
    TEMPLATE_CLOSED: str = "bag_closed.png"

    # Ячейки содержимого 1-го рюкзака (5x5)
    FIRST_BACKPACK_CELL_TOP_LEFT_FROM_BOTTOM_RIGHT: tuple[int, int] = (224, 495)
    FIRST_BACKPACK_CELL_GRID_ROWS: int = 5
    FIRST_BACKPACK_CELL_GRID_COLS: int = 5
    FIRST_BACKPACK_CELL_STEP_X_PX: int = 36
    FIRST_BACKPACK_CELL_STEP_Y_PX: int = 36
    FIRST_BACKPACK_CELL_ROI_SIZE: tuple[int, int] = (33, 34)
    TEMPLATE_CELL_EXISTED: str = "cell_existed.png"
    TEMPLATE_CELL_EMPTY: str = "cell_empty.png"

    # Заголовок окна рюкзака (проверка, что окно рюкзака действительно открыто и не перекрыто)
    BACKPACK_WINDOW_TITLE_TOP_LEFT_FROM_BOTTOM_RIGHT: tuple[int, int] = (232, 531)
    BACKPACK_WINDOW_TITLE_ROI_SIZE: tuple[int, int] = (189, 29)
    TEMPLATE_BACKPACK_WINDOW_OPENED: str = "window_opened_bag.png"
    BACKPACK_WINDOW_TITLE_THRESHOLD: float = 0.99
    CELL_NOT_DETECTED_ERROR_MESSAGE: str = (
        "Не получается получить состояние ячейки. "
        "Рюкзак перекрыт, или приложение не активно"
    )

    BACKPACK_WINDOW_NOT_AVAILABLE_ERROR_MESSAGE: str = (
        "Окно рюкзака недоступно (заголовок не найден). "
        "Попробовал переоткрыть рюкзак, но не помогло."
    )

    INIT_CHECK_THRESHOLD: float = 0.99
    INIT_CHECK_TIMEOUT_S: float = 0.15
    INIT_CHECK_POLL_S: float = 0.03
    INIT_ERROR_MESSAGE: str = (
        "Не получается получить состояние рюкзаков. "
        "Пожалуйста активируйте окно игры, или уберите окно перекрывающее рюкзаки"
    )

    def __init__(
        self,
        *,
        clicker: Clicker,
        image_finder: ImageFinder,
        slots_from_bottom_right: Optional[Sequence[tuple[int, int]]] = None,
        grid_rows: int = GRID_ROWS,
        grid_cols: int = GRID_COLS,
        grid_step_x_px: int = GRID_STEP_X_PX,
        grid_step_y_px: int = GRID_STEP_Y_PX,
        grid_anchor_top_left_from_bottom_right: tuple[int, int] = ROI_TOP_LEFT_FROM_BOTTOM_RIGHT,
        templates_dir: Path | str = _ASSETS_DIR,
        validate_on_init: bool = True,
    ) -> None:
        self._clicker = clicker
        self._image_finder = image_finder
        self._templates_dir = Path(templates_dir)

        # Прогреваем кэш шаблонов сразу, чтобы во время проверок не читать PNG с диска.
        preload_templates(
            [
                self._templates_dir / self.TEMPLATE_DISABLED,
                self._templates_dir / self.TEMPLATE_OPENED,
                self._templates_dir / self.TEMPLATE_CLOSED,
                self._templates_dir / self.TEMPLATE_CELL_EMPTY,
                self._templates_dir / self.TEMPLATE_CELL_EXISTED,
                self._templates_dir / self.TEMPLATE_BACKPACK_WINDOW_OPENED,
            ]
        )

        if slots_from_bottom_right is None:
            slots_from_bottom_right = self._build_grid_slots_from_bottom_right(
                rows=grid_rows,
                cols=grid_cols,
                step_x_px=grid_step_x_px,
                step_y_px=grid_step_y_px,
                anchor_top_left_from_bottom_right=grid_anchor_top_left_from_bottom_right,
            )
        self._slots_from_bottom_right = list(slots_from_bottom_right)

        # Состояние рюкзаков определяется сразу при инициализации
        self.states: list[dict] = []

        if validate_on_init:
            self.states = self.get_backpacks_state(
                threshold=self.INIT_CHECK_THRESHOLD,
                timeout_s=self.INIT_CHECK_TIMEOUT_S,
                poll_s=self.INIT_CHECK_POLL_S,
            )
            if all(s.get("state") == "unknown" for s in self.states):
                raise RuntimeError(self.INIT_ERROR_MESSAGE)
        else:
            self.states = self.get_backpacks_state()

    def refresh_states(self, *, threshold: float = 0.99, timeout_s: float = 0.25, poll_s: float = 0.05) -> list[dict]:
        """Обновляет и возвращает `self.states` (все слоты)."""
        self.states = self.get_backpacks_state(threshold=threshold, timeout_s=timeout_s, poll_s=poll_s)
        return self.states

    def refresh_states_partial(
        self,
        indices: Sequence[int],
        *,
        threshold: float = 0.995,
        timeout_s: float = 0.25,
        poll_s: float = 0.05,
    ) -> list[dict]:
        """Обновляет `self.states` только для указанных индексов и возвращает полный `self.states`."""
        partial = self.get_backpacks_state(
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
            indices=indices,
        )
        for s in partial:
            idx = int(s["index"])
            if 0 <= idx < len(self.states):
                self.states[idx] = s
        return self.states

    def _detect_slot_state(
        self,
        *,
        roi_top_left_client: tuple[int, int],
        roi_size: tuple[int, int],
        threshold: float,
        timeout_s: float,
        poll_s: float,
    ) -> dict:
        disabled_path = self._templates_dir / self.TEMPLATE_DISABLED
        opened_path = self._templates_dir / self.TEMPLATE_OPENED
        closed_path = self._templates_dir / self.TEMPLATE_CLOSED

        hit_disabled = self._image_finder.find_template_in_client_roi(
            template_png_path=disabled_path,
            roi_top_left_client=roi_top_left_client,
            roi_size=roi_size,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        if hit_disabled is not None and hit_disabled["score"] > threshold:
            return {"state": "disabled", "score": float(hit_disabled["score"])}

        hit_opened = self._image_finder.find_template_in_client_roi(
            template_png_path=opened_path,
            roi_top_left_client=roi_top_left_client,
            roi_size=roi_size,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        if hit_opened is not None and hit_opened["score"] > threshold:
            return {"state": "opened", "score": float(hit_opened["score"])}

        hit_closed = self._image_finder.find_template_in_client_roi(
            template_png_path=closed_path,
            roi_top_left_client=roi_top_left_client,
            roi_size=roi_size,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        if hit_closed is not None and hit_closed["score"] > threshold:
            return {"state": "closed", "score": float(hit_closed["score"])}

        return {"state": "unknown", "score": None}


    def get_backpack_cell_info(
        self,
        backpack_index: int,
        row: int,
        col: int,
        *,
        threshold: float = 0.99,
        timeout_s: float = 0.25,
        poll_s: float = 0.05,
    ) -> dict:
        """
        Проверяет состояние ячейки (5x5) выбранного рюкзака и возвращает центр ячейки.

        Перед проверкой ячейки:
        - берём state нужного рюкзака из `self.states`
        - если рюкзак не открыт — закрываем все открытые (по кэшу) и открываем нужный

        Логика:
        - Проверяем, что заголовок окна рюкзака найден (TEMPLATE_BACKPACK_WINDOW_OPENED)
          в ROI BACKPACK_WINDOW_TITLE_TOP_LEFT_FROM_BOTTOM_RIGHT (размер BACKPACK_WINDOW_TITLE_ROI_SIZE).
          Если не найден — ошибка (рюкзак перекрыт / приложение не активно).
        - Проверяем ячейку на TEMPLATE_CELL_EMPTY:
          - если найдено -> "empty"
          - если НЕ найдено и заголовок окна найден -> "filled"

        Returns:
            dict:
            - row, col, index
            - state: "empty" | "filled"
            - center_client: (x, y)
            - top_left_client: (x, y)
            - roi_size: (w, h)
        """
        bi = int(backpack_index)
        if not (0 <= bi < len(self._slots_from_bottom_right)):
            raise IndexError(f"Backpack index out of range: {backpack_index}. Total={len(self._slots_from_bottom_right)}")

        # Управление открытием рюкзака строго по кэшу self.states (как просили)
        if not self.states or len(self.states) != len(self._slots_from_bottom_right):
            self.states = self.get_backpacks_state()

        if self.states[bi].get("state") != "opened":
            self.close_all_opened_backpacks(refresh=False)
            self.open_backpack(bi, refresh=False) 

        r = int(row)
        c = int(col)
        if not (0 <= r < self.FIRST_BACKPACK_CELL_GRID_ROWS and 0 <= c < self.FIRST_BACKPACK_CELL_GRID_COLS):
            raise IndexError(f"Cell out of range: row={row}, col={col}")

        # Важно: после открытия из "закрытого" состояния заголовок иногда не находится с первого раза,
        # но если сделать close->open (как ты и заметил), он появляется. Поэтому здесь используем
        # ensure_backpack_window_available() как самовосстановление.
        if not self._is_backpack_window_title_visible(timeout_s=timeout_s, poll_s=poll_s):
            self.ensure_backpack_window_available(
                bi,
                timeout_s=max(0.5, float(timeout_s)),
                poll_s=poll_s,
            )
            if not self._is_backpack_window_title_visible(timeout_s=max(0.5, float(timeout_s)), poll_s=poll_s):
                raise RuntimeError(self.CELL_NOT_DETECTED_ERROR_MESSAGE)

        coords = self._clicker.find_coords()
        client_size = coords["client_size"]

        base_dx, base_dy = self.FIRST_BACKPACK_CELL_TOP_LEFT_FROM_BOTTOM_RIGHT
        dx = int(base_dx - c * int(self.FIRST_BACKPACK_CELL_STEP_X_PX))
        dy = int(base_dy - r * int(self.FIRST_BACKPACK_CELL_STEP_Y_PX))

        top_left_client = self._roi_top_left_client_from_bottom_right(client_size, (dx, dy))
        roi_size = self.FIRST_BACKPACK_CELL_ROI_SIZE
        cx = int(top_left_client[0] + roi_size[0] // 2)
        cy = int(top_left_client[1] + roi_size[1] // 2)

        tpl_cell_empty = self._templates_dir / self.TEMPLATE_CELL_EMPTY

        hit_empty = self._image_finder.find_template_in_client_roi(
            template_png_path=tpl_cell_empty,
            roi_top_left_client=top_left_client,
            roi_size=roi_size,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )

        state = "empty" if hit_empty is not None else "filled"
        idx = int(r * self.FIRST_BACKPACK_CELL_GRID_COLS + c)
        return {
            "row": r,
            "col": c,
            "index": idx,
            "state": state,
            "center_client": (cx, cy),
            "top_left_client": top_left_client,
            "roi_size": roi_size,
        }

    def _is_backpack_window_title_visible(
        self,
        *,
        timeout_s: float = 0.15,
        poll_s: float = 0.03,
    ) -> bool:
        """Проверяет, что заголовок окна рюкзака видим (окно не перекрыто)."""
        coords = self._clicker.find_coords()
        client_size = coords["client_size"]

        window_title_top_left_client = self._roi_top_left_client_from_bottom_right(
            client_size,
            self.BACKPACK_WINDOW_TITLE_TOP_LEFT_FROM_BOTTOM_RIGHT,
        )
        tpl_window_opened = self._templates_dir / self.TEMPLATE_BACKPACK_WINDOW_OPENED
        hit_window = self._image_finder.find_template_in_client_roi(
            template_png_path=tpl_window_opened,
            roi_top_left_client=window_title_top_left_client,
            roi_size=self.BACKPACK_WINDOW_TITLE_ROI_SIZE,
            threshold=self.BACKPACK_WINDOW_TITLE_THRESHOLD,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        return hit_window is not None

    def ensure_backpack_window_available(
        self,
        backpack_index: int,
        *,
        timeout_s: float = 0.15,
        poll_s: float = 0.03,
    ) -> None:
        """
        Проверяет доступность окна рюкзака (по заголовку). Если заголовок не найден —
        закрывает все открытые рюкзаки и открывает нужный заново.
        """
        bi = int(backpack_index)
        if not self.states or len(self.states) != len(self._slots_from_bottom_right):
            self.states = self.get_backpacks_state(timeout_s=timeout_s, poll_s=poll_s)

        # Попробуем привести рюкзак в состояние opened
        if self.states[bi].get("state") != "opened":
            self.close_all_opened_backpacks(refresh=True, timeout_s=timeout_s, poll_s=poll_s)
            self.open_backpack(bi, refresh=True, timeout_s=timeout_s, poll_s=poll_s)

        if self._is_backpack_window_title_visible(timeout_s=timeout_s, poll_s=poll_s):
            return

        # Если окно перекрыто/не активно — пробуем переоткрыть рюкзак
        self.close_all_opened_backpacks(refresh=True, timeout_s=timeout_s, poll_s=poll_s)
        self.open_backpack(bi, refresh=True, timeout_s=timeout_s, poll_s=poll_s)

        if not self._is_backpack_window_title_visible(timeout_s=timeout_s, poll_s=poll_s):
            raise RuntimeError(self.BACKPACK_WINDOW_NOT_AVAILABLE_ERROR_MESSAGE)

    @staticmethod
    def _build_grid_slots_from_bottom_right(
        *,
        rows: int,
        cols: int,
        step_x_px: int,
        step_y_px: int,
        anchor_top_left_from_bottom_right: tuple[int, int],
    ) -> list[tuple[int, int]]:
        """
        Строит сетку смещений (dx_from_right, dy_from_bottom) для ROI.

        Важно:
        - anchor_top_left_from_bottom_right — это слот [row=0, col=0] (верхний левый в сетке).
        - По мере движения вправо/вниз в client-координатах, отступы от правого/нижнего края уменьшаются.
        """
        base_dx, base_dy = anchor_top_left_from_bottom_right
        out: list[tuple[int, int]] = []
        for r in range(int(rows)):
            for c in range(int(cols)):
                dx = int(base_dx - c * int(step_x_px))
                dy = int(base_dy - r * int(step_y_px))
                out.append((dx, dy))
        return out

    @staticmethod
    def _roi_top_left_client_from_bottom_right(
        client_size: tuple[int, int],
        roi_top_left_from_bottom_right: tuple[int, int],
    ) -> tuple[int, int]:
        cw, ch = client_size
        dx_from_right, dy_from_bottom = roi_top_left_from_bottom_right
        return (int(cw - dx_from_right), int(ch - dy_from_bottom))

    def get_backpacks_state(
        self,
        *,
        threshold: float = 0.995,
        timeout_s: float = 0.25,
        poll_s: float = 0.05,
        indices: Optional[Sequence[int]] = None,
    ) -> list[dict]:
        """Состояние слотов рюкзака.

        Returns:
            list[dict] с ключами:
            - index: int
            - state: str ("disabled" | "opened" | "closed" | "unknown")
            - score: float | None
            - roi_top_left_client: (x, y)
            - roi_size: (w, h)
        """
        coords = self._clicker.find_coords()
        client_size = coords["client_size"]

        out: list[dict] = []
        if indices is None:
            selected_indices = list(range(len(self._slots_from_bottom_right)))
        else:
            selected_indices = [int(i) for i in indices]

        for idx in selected_indices:
            if not (0 <= int(idx) < len(self._slots_from_bottom_right)):
                raise IndexError(f"Backpack index out of range: {idx}. Total={len(self._slots_from_bottom_right)}")

            slot_from_br = self._slots_from_bottom_right[int(idx)]
            roi_top_left_client = self._roi_top_left_client_from_bottom_right(client_size, slot_from_br)
            roi_size = self.ROI_SIZE

            detected = self._detect_slot_state(
                roi_top_left_client=roi_top_left_client,
                roi_size=roi_size,
                threshold=threshold,
                timeout_s=timeout_s,
                poll_s=poll_s,
            )

            out.append(
                {
                    "index": int(idx),
                    "state": detected["state"],
                    "score": detected["score"],
                    "roi_top_left_client": roi_top_left_client,
                    "roi_size": roi_size,
                }
            )

        return out

    def close_all_opened_backpacks(
        self,
        *,
        refresh: bool = False,
        threshold: float = 0.995,
        timeout_s: float = 0.25,
        poll_s: float = 0.05,
    ) -> list[dict]:
        """
        Закрывает все открытые рюкзаки хоткеями Num1..Num8 (toggle).

        Возвращает актуальные состояния (после refresh если refresh=True).
        """
        states = self.refresh_states(threshold=threshold, timeout_s=timeout_s, poll_s=poll_s) if refresh else self.states

        for s in states:
            if s.get("state") != "opened":
                continue
            # Индексы слотов 0..7 соответствуют Num1..Num8
            num_key = f"Num{int(s['index']) + 1}"
            self._clicker.press_keys([num_key])
            time.sleep(0.2)

        # Обновим состояния после кликов (чтобы сразу видеть результат)
        clicked_indices = [int(s["index"]) for s in states if s.get("state") == "opened"]
        if clicked_indices:
            return self.refresh_states_partial(clicked_indices, threshold=threshold, timeout_s=timeout_s, poll_s=poll_s)
        return self.states

    def open_backpack(
        self,
        index: int,
        *,
        refresh: bool = False,
        threshold: float = 0.995,
        timeout_s: float = 0.25,
        poll_s: float = 0.05,
    ) -> dict:
        """
        Открывает конкретный рюкзак по индексу (0..7) хоткеем Num1..Num8.

        Логика:
        - если уже opened — ничего не делаем
        - если closed — нажимаем соответствующую Num-клавишу
        - если disabled/unknown — кидаем ошибку (чтобы не кликать "вслепую")
        """
        states = (
            self.refresh_states_partial([int(index)], threshold=threshold, timeout_s=timeout_s, poll_s=poll_s)
            if refresh
            else self.states
        )

        if not (0 <= int(index) < len(states)):
            raise IndexError(f"Backpack index out of range: {index}. Total={len(states)}")

        s = states[int(index)]
        state = s.get("state")
        if state == "opened":
            return s
        if state != "closed":
            raise RuntimeError(f"Нельзя открыть рюкзак {index}: state={state!r}")

        # Индексы слотов 0..7 соответствуют Num1..Num8
        num_key = f"Num{int(index) + 1}"
        self._clicker.press_keys([num_key])
        time.sleep(0.2)

        self.refresh_states_partial([int(index)], threshold=threshold, timeout_s=timeout_s, poll_s=poll_s)
        return self.states[int(index)]


