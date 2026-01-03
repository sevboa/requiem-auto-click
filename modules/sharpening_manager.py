"""Управление интерфейсом заточки (sharpening) в Requiem.

Пока реализована только базовая часть:
- Поиск окна заточки по PNG-шаблону в client area окна игры.
- Нижняя полоса 347px исключается из поиска (часто перекрыта UI).

Важно: в отличие от разборки, окно заточки НЕ перемещаем.
"""

from __future__ import annotations

from pathlib import Path

from modules.clicker import Clicker
from modules.image_finder import ImageFinder
from modules.backpack_manager import BackpackManager


class SharpeningManager:
    """Минимальный менеджер интерфейса заточки (sharpening)."""

    TEMPLATE_WINDOW_OPENED: Path = Path("modules/sharpening/window_opened_sharpening.png")
    EXCLUDE_BOTTOM_STRIP_PX: int = 347
    EXCLUDE_RIGHT_STRIP_PX: int = 145

    # Смещение (px) от top-left окна заточки (top_left_in_client) до центра ячейки заточки
    SHARPENING_CELL_CENTER_FROM_TOP_LEFT: tuple[int, int] = (134, 80)

    # Кнопки внутри окна заточки (смещения от top_left_in_client)
    AUTO_BUTTON_CENTER_FROM_TOP_LEFT: tuple[int, int] = (160, 192)
    OK_BUTTON_CENTER_FROM_TOP_LEFT: tuple[int, int] = (254, 192)

    # Клики в client area (НЕ относительно окна заточки)
    MAP_CLICK_CLIENT: tuple[int, int] = (584, 332)
    REPEAT_CLICK_CLIENT: tuple[int, int] = (557, 532)

    # После нажатия "Повторить" окно заточки считается вернувшимся в дефолтную позицию
    DEFAULT_WINDOW_TOP_LEFT_IN_CLIENT: tuple[int, int] = (203, 102)

    # Проверка, что предмет подходит для заточки: ищем '+' (один из 5 цветов) в маленьком ROI
    # Файлы: modules/sharpening/digits/+_a{1..5}.png
    PLUS_TOP_LEFT_FROM_WINDOW_TOP_LEFT: tuple[int, int] = (373, 11)
    PLUS_ROI_SIZE: tuple[int, int] = (7, 9)
    ITEM_NOT_SHARPENABLE_ERROR_MESSAGE: str = "Предмет не преднозначен для заточки, или произошла иная проблема"

    # Текущее число заточки (2 цифры в заголовке)
    SHARPENING_DIGIT_ROI_SIZE: tuple[int, int] = (7, 9)
    SHARPENING_DIGIT1_TOP_LEFT_FROM_WINDOW_TOP_LEFT: tuple[int, int] = (382, 11)
    SHARPENING_DIGIT2_TOP_LEFT_FROM_WINDOW_TOP_LEFT: tuple[int, int] = (390, 11)
    SHARPENING_DIGITS_ROI_SIZE: tuple[int, int] = (15, 9)  # обе цифры одним grab
    SHARPENING_DIGIT1_IN_DIGITS_ROI_TOP_LEFT: tuple[int, int] = (0, 0)
    SHARPENING_DIGIT2_IN_DIGITS_ROI_TOP_LEFT: tuple[int, int] = (8, 0)

    # Проверка активности кнопки "Авто" (наличие активного состояния в нижней части окна)
    TEMPLATE_AUTO_ACTIVE: Path = Path("modules/sharpening/bottom_auto_active.png")
    AUTO_ACTIVE_TOP_LEFT_FROM_WINDOW_TOP_LEFT: tuple[int, int] = (218, 181)
    AUTO_ACTIVE_ROI_SIZE: tuple[int, int] = (69, 22)
    AUTO_NOT_ACTIVE_ERROR_MESSAGE: str = "Закончились ксеоны, либо иная проблема"

    # Проверка безопасной заточки (если найдено — заточка "безопасная")
    TEMPLATE_SAVE_SHARPENING: Path = Path("modules/sharpening/save_sharpening.png")
    SAVE_SHARPENING_TOP_LEFT_FROM_WINDOW_TOP_LEFT: tuple[int, int] = (103, 332)
    SAVE_SHARPENING_ROI_SIZE: tuple[int, int] = (36, 7)

    # Проверка окна ошибки с кнопкой OK (попап). ROI вычисляется от центра client area.
    TEMPLATE_REJECT_OK: Path = Path("modules/sharpening/bottom_reject_ok.png")
    REJECT_OK_ROI_TOP_LEFT_FROM_CLIENT_CENTER: tuple[int, int] = (-53, -95)
    REJECT_OK_ROI_SIZE: tuple[int, int] = (106, 26)

    WINDOW_NOT_FOUND_ERROR_MESSAGE: str = (
        "Окно заточки не найдено. "
        "Открой интерфейс заточки у NPC/станка, который занимается заточкой."
    )

    def __init__(
        self,
        *,
        clicker: Clicker,
        image_finder: ImageFinder,
        backpacks: BackpackManager,
        cache_on_init: bool = False,
    ) -> None:
        self._clicker = clicker
        self._image_finder = image_finder
        self.backpacks = backpacks

        # Обновляется при find; используется как якорь для последующих кликов/смещений.
        self.top_left_in_client: tuple[int, int] | None = None

        if cache_on_init:
            self.ensure_window_cached(threshold=0.98, timeout_s=2.0, poll_s=0.1)

    def _find_sharpening_window_hit(
        self,
        *,
        threshold: float,
        timeout_s: float,
        poll_s: float,
    ) -> dict:
        coords = self._clicker.find_coords()
        cw, ch = coords["client_size"]
        roi_h = max(1, int(ch) - int(self.EXCLUDE_BOTTOM_STRIP_PX))
        roi_w = max(1, int(cw) - int(self.EXCLUDE_RIGHT_STRIP_PX))

        hit = self._image_finder.find_template_in_client_roi(
            template_png_path=self.TEMPLATE_WINDOW_OPENED,
            roi_top_left_client=(0, 0),
            roi_size=(int(roi_w), int(roi_h)),
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
            raise RuntimeError("Окно заточки ещё не найдено (нет сохранённых координат). Вызови ensure_window_cached().")
        return self.top_left_in_client

    def ensure_window_cached(
        self,
        *,
        threshold: float = 0.98,
        timeout_s: float = 2.0,
        poll_s: float = 0.1,
    ) -> tuple[int, int]:
        """
        Дорогая операция: находит окно заточки и сохраняет координаты в `top_left_in_client`.

        Returns:
            top-left найденного шаблона в screen coords (для отладки/логирования).
        """
        return self.find_sharpening_window_top_left(threshold=threshold, timeout_s=timeout_s, poll_s=poll_s)

    def find_sharpening_window_top_left(
        self,
        *,
        threshold: float = 0.98,
        timeout_s: float = 2.0,
        poll_s: float = 0.1,
    ) -> tuple[int, int]:
        """
        Находит окно заточки по "заголовку" (PNG-шаблон) и возвращает top-left (screen coords).

        Поиск идёт по всему client area окна игры, но без нижней полосы 347px.
        Окно НЕ перемещаем.
        """
        hit = self._find_sharpening_window_hit(threshold=threshold, timeout_s=timeout_s, poll_s=poll_s)
        self._update_cached_window_geometry(hit)
        x, y = hit["top_left_on_screen"]
        return (int(x), int(y))

    def get_cached_top_left_in_client(self) -> tuple[int, int]:
        """Возвращает сохранённый top-left окна в client coords (требует ensure_window_cached)."""
        return self._require_cached_top_left_in_client()

    def drag_item_from_backpack_cell_to_sharpening_cell(
        self,
        backpack_index: int,
        row: int,
        col: int,
        *,
        cell_threshold: float = 0.98,
        cell_timeout_s: float = 0.25,
        cell_poll_s: float = 0.05,
    ) -> bool:
        """
        Перетаскивает предмет из конкретной ячейки рюкзака в центр ячейки заточки.

        Возвращает:
            True если был предмет и перетаскивание выполнено, иначе False (ячейка пустая).
        """
        # Важно: окно должно быть уже найдено/закэшировано (ensure_window_cached или cache_on_init=True)
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.SHARPENING_CELL_CENTER_FROM_TOP_LEFT
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
        self._clicker.drag_client(start_client, target_client)
        return True

    def _get_plus_variant_in_window(
        self,
        *,
        threshold: float,
        early_threshold: float,
        grab_timeout_s: float,
        grab_poll_s: float,
    ) -> str | None:
        """
        Возвращает вариант цвета плюса: 'a1'..'a5', либо None если ни один не совпал.

        Оптимизация: делаем один grab ROI 7x9 и матчим по кэшированным шаблонам.
        """
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.PLUS_TOP_LEFT_FROM_WINDOW_TOP_LEFT
        roi_top_left_client = (int(tlx + dx), int(tly + dy))

        plus_gray = self._image_finder.grab_client_roi_gray(
            roi_top_left_client=roi_top_left_client,
            roi_size=self.PLUS_ROI_SIZE,
            timeout_s=grab_timeout_s,
            poll_s=grab_poll_s,
        )
        if plus_gray is None:
            return None

        best_variant: str | None = None
        best_score: float = -1.0
        for i in range(1, 6):
            variant = f"a{i}"
            tpl = Path(f"modules/sharpening/digits/+_{variant}.png")
            score = self._image_finder.match_template_score_in_gray(
                plus_gray,
                template_png_path=tpl,
            )
            if score >= float(early_threshold):
                return variant
            if score > best_score:
                best_score = float(score)
                best_variant = variant

        return best_variant if best_score >= float(threshold) else None

    def ensure_item_is_sharpenable(
        self,
        *,
        threshold: float = 0.98,
        early_threshold: float = 0.99,
        grab_timeout_s: float = 0.25,
        grab_poll_s: float = 0.02,
    ) -> str:
        """
        Проверяет, что вставленный предмет подходит для заточки.

        Сразу после вставки ищем '+' (modules/sharpening/digits/+_a*.png) в ROI 7x9 по смещению (373,11)
        от левого верхнего края окна заточки (top_left_in_client).
        """
        variant = self._get_plus_variant_in_window(
            threshold=threshold,
            early_threshold=early_threshold,
            grab_timeout_s=grab_timeout_s,
            grab_poll_s=grab_poll_s,
        )
        if variant is None:
            raise RuntimeError(self.ITEM_NOT_SHARPENABLE_ERROR_MESSAGE)
        return variant

    def _try_detect_digit_in_gray_patch(
        self,
        patch_gray,
        *,
        variant: str,
        threshold: float,
        early_threshold: float,
        required: bool,
    ) -> int | None:
        """
        Быстро распознаёт цифру (0..9) на уже вырезанном gray-патче 7x9.

        - Шаблоны берутся из кэша `ImageFinder`.
        - При score >= early_threshold — выходим сразу.
        """
        best_digit: int | None = None
        best_score: float = -1.0
        for d in range(9, -1, -1):
            tpl = Path(f"modules/sharpening/digits/{d}_{variant}.png")
            score = self._image_finder.match_template_score_in_gray(
                patch_gray,
                template_png_path=tpl,
            )
            if score >= float(early_threshold):
                return int(d)
            if score > best_score:
                best_score = float(score)
                best_digit = int(d)

        if best_digit is None or best_score < float(threshold):
            if required:
                raise RuntimeError("Не удалось распознать цифру уровня заточки (шаблоны 0-9 не совпали в ROI).")
            return None
        return int(best_digit)

    def get_current_sharpening_value(
        self,
        *,
        variant: str,
        threshold: float = 0.98,
        early_threshold: float = 0.99,
        grab_timeout_s: float = 0.25,
        grab_poll_s: float = 0.02,
    ) -> int:
        """
        Возвращает текущее число заточки (две цифры) из заголовка окна заточки.

        Оптимизировано:
        - Делаем ОДИН grab ROI (15x9) в (382,11) от top-left окна заточки,
          затем режем на две цифры: (0,0) и (8,0) (каждая 7x9).
        - Шаблоны 0..9 кэшируются в ImageFinder (без чтения с диска).
        - Если score >= early_threshold — выходим сразу (обычно ~0.998 у тебя).
        """
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.SHARPENING_DIGIT1_TOP_LEFT_FROM_WINDOW_TOP_LEFT
        roi_top_left_client = (int(tlx + dx), int(tly + dy))

        digits_gray = self._image_finder.grab_client_roi_gray(
            roi_top_left_client=roi_top_left_client,
            roi_size=self.SHARPENING_DIGITS_ROI_SIZE,
            timeout_s=grab_timeout_s,
            poll_s=grab_poll_s,
        )
        if digits_gray is None:
            # Фолбек: иногда dxcam.grab на маленьких ROI возвращает None (транзиентно).
            # В этом случае откатываемся на более надёжный путь через find_template_in_client_roi().
            d1 = self._detect_digit_in_window_fallback(
                digit_top_left_from_window_top_left=self.SHARPENING_DIGIT1_TOP_LEFT_FROM_WINDOW_TOP_LEFT,
                threshold=threshold,
                variant=variant,
                required=True,
            )
            d2 = self._detect_digit_in_window_fallback(
                digit_top_left_from_window_top_left=self.SHARPENING_DIGIT2_TOP_LEFT_FROM_WINDOW_TOP_LEFT,
                threshold=threshold,
                variant=variant,
                required=False,
            )
            return int(d1) if d2 is None else int(d1) * 10 + int(d2)

        # digits_gray: (h=9, w=15). Вырезаем две цифры: 7x9 и 7x9 с шагом 8.
        d1_patch = digits_gray[0 : self.SHARPENING_DIGIT_ROI_SIZE[1], 0 : self.SHARPENING_DIGIT_ROI_SIZE[0]]
        d2_patch = digits_gray[0 : self.SHARPENING_DIGIT_ROI_SIZE[1], 8 : 8 + self.SHARPENING_DIGIT_ROI_SIZE[0]]

        d1 = self._try_detect_digit_in_gray_patch(
            d1_patch,
            variant=variant,
            threshold=threshold,
            early_threshold=early_threshold,
            required=True,
        )
        d2 = self._try_detect_digit_in_gray_patch(
            d2_patch,
            variant=variant,
            threshold=threshold,
            early_threshold=early_threshold,
            required=False,
        )
        assert d1 is not None  # required=True выше
        return int(d1) if d2 is None else int(d1) * 10 + int(d2)

    def _detect_digit_in_window_fallback(
        self,
        *,
        digit_top_left_from_window_top_left: tuple[int, int],
        variant: str,
        threshold: float,
        required: bool,
    ) -> int | None:
        """
        Надёжный фолбек: ищем цифру через find_template_in_client_roi (он сам делает retry-loop).
        При этом шаблоны уже кэшируются в ImageFinder, так что чтения с диска нет.
        """
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = digit_top_left_from_window_top_left
        roi_top_left_client = (int(tlx + dx), int(tly + dy))

        best_digit: int | None = None
        best_score: float = -1.0
        for d in range(9, -1, -1):
            tpl = Path(f"modules/sharpening/digits/{d}_{variant}.png")
            hit = self._image_finder.find_template_in_client_roi(
                template_png_path=tpl,
                roi_top_left_client=roi_top_left_client,
                roi_size=self.SHARPENING_DIGIT_ROI_SIZE,
                threshold=threshold,
                timeout_s=0.25,
                poll_s=0.02,
            )
            if hit is None:
                continue
            score = float(hit.get("score", 0.0))
            if score > best_score:
                best_score = score
                best_digit = int(d)

        if best_digit is None:
            if required:
                raise RuntimeError("Не удалось распознать цифру уровня заточки (fallback тоже не сработал).")
            return None
        return int(best_digit)

    def _is_auto_active_visible_in_window(
        self,
        *,
        threshold: float,
        timeout_s: float,
        poll_s: float,
    ) -> bool:
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.AUTO_ACTIVE_TOP_LEFT_FROM_WINDOW_TOP_LEFT
        roi_top_left_client = (int(tlx + dx), int(tly + dy))

        hit = self._image_finder.find_template_in_client_roi(
            template_png_path=self.TEMPLATE_AUTO_ACTIVE,
            roi_top_left_client=roi_top_left_client,
            roi_size=self.AUTO_ACTIVE_ROI_SIZE,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        return hit is not None

    def ensure_auto_button_active(
        self,
        *,
        threshold: float = 0.98,
        timeout_s: float = 0.25,
        poll_s: float = 0.05,
    ) -> None:
        """
        Проверяет, что кнопка "Авто" активна (есть ксеоны/ресурс и окно в корректном состоянии).

        Ищем modules/sharpening/bottom_auto_active.png в ROI 69x22 по смещению (125,181)
        от левого верхнего края окна заточки (top_left_in_client).
        """
        if not self._is_auto_active_visible_in_window(threshold=threshold, timeout_s=timeout_s, poll_s=poll_s):
            raise RuntimeError(self.AUTO_NOT_ACTIVE_ERROR_MESSAGE)

    def _is_save_sharpening_visible_in_window(
        self,
        *,
        threshold: float,
        timeout_s: float,
        poll_s: float,
    ) -> bool:
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.SAVE_SHARPENING_TOP_LEFT_FROM_WINDOW_TOP_LEFT
        roi_top_left_client = (int(tlx + dx), int(tly + dy))

        hit = self._image_finder.find_template_in_client_roi(
            template_png_path=self.TEMPLATE_SAVE_SHARPENING,
            roi_top_left_client=roi_top_left_client,
            roi_size=self.SAVE_SHARPENING_ROI_SIZE,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        return hit is not None

    def is_sharpening_safe(
        self,
        *,
        threshold: float = 0.98,
        timeout_s: float = 0.25,
        poll_s: float = 0.05,
    ) -> bool:
        """
        Проверяет, является ли заточка "безопасной".

        Ищем modules/sharpening/save_sharpening.png в ROI 36x7 по смещению (103,332)
        от левого верхнего края окна заточки (top_left_in_client).
        """
        return self._is_save_sharpening_visible_in_window(threshold=threshold, timeout_s=timeout_s, poll_s=poll_s)

    def check_reject_ok_popup_and_close(
        self,
        *,
        threshold: float = 0.98,
        timeout_s: float = 0.15,
        poll_s: float = 0.03,
    ) -> bool:
        """
        Проверяет наличие всплывающего окна ошибки с кнопкой OK и закрывает его кликом.

        Алгоритм:
        - Берём центр client area окна игры.
        - ROI: от центра смещение (-53, -95) -> top-left; размер 106x26.
        - Ищем шаблон modules/sharpening/bottom_reject_ok.png в этом ROI.
        - Если найдено — кликаем в центр ROI (закрываем попап) и возвращаем True.
          Если нет — возвращаем False.
        """
        coords = self._clicker.find_coords()
        cw, ch = coords["client_size"]
        cx = int(cw // 2)
        cy = int(ch // 2)

        dx, dy = self.REJECT_OK_ROI_TOP_LEFT_FROM_CLIENT_CENTER
        roi_top_left_client = (int(cx + dx), int(cy + dy))
        roi_w, roi_h = self.REJECT_OK_ROI_SIZE

        hit = self._image_finder.find_template_in_client_roi(
            template_png_path=self.TEMPLATE_REJECT_OK,
            roi_top_left_client=roi_top_left_client,
            roi_size=(int(roi_w), int(roi_h)),
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )
        if hit is None:
            return False

        click_x = int(roi_top_left_client[0] + int(roi_w) // 2)
        click_y = int(roi_top_left_client[1] + int(roi_h) // 2)
        self._clicker.click_at_client(click_x, click_y)
        return True

    def click_auto(self) -> None:
        """Нажимает кнопку 'Авто' внутри окна заточки (по кэшу top_left_in_client)."""
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.AUTO_BUTTON_CENTER_FROM_TOP_LEFT
        self._clicker.click_at_client(int(tlx + dx), int(tly + dy))

    def click_ok(self) -> None:
        """Нажимает кнопку 'ОК' внутри окна заточки (по кэшу top_left_in_client)."""
        tlx, tly = self._require_cached_top_left_in_client()
        dx, dy = self.OK_BUTTON_CENTER_FROM_TOP_LEFT
        self._clicker.click_at_client(int(tlx + dx), int(tly + dy))

    def click_map(self) -> None:
        """Нажимает на карту в фиксированной точке client area."""
        x, y = self.MAP_CLICK_CLIENT
        self._clicker.click_at_client(int(x), int(y))

    def click_repeat(self, *, reset_window_top_left: bool = True) -> None:
        """
        Нажимает кнопку 'Повторить' в фиксированной точке client area.

        После клика (по требованию) сбрасывает кэш окна заточки на координаты по умолчанию,
        чтобы следующие действия работали без повторного поиска.
        """
        x, y = self.REPEAT_CLICK_CLIENT
        self._clicker.click_at_client(int(x), int(y))
        if reset_window_top_left:
            self.top_left_in_client = tuple(self.DEFAULT_WINDOW_TOP_LEFT_IN_CLIENT)


