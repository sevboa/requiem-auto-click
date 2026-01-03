"""Класс для автоматизации действий в Requiem."""
import time
import ctypes
from datetime import datetime
from typing import Optional, Protocol
from pathlib import Path
from modules.clicker import Clicker
from modules.image_finder import ImageFinder
from modules.mouse_client_base import MouseClient
from modules.backpack_manager import BackpackManager
from modules.disassemble_manager import DisassembleManager
from modules.sharpening_manager import SharpeningManager
from modules.sound_utils import play_start_sound, play_finish_sound

user32 = ctypes.windll.user32
VK_OEM_6 = 0xDD  # ']' на основной клавиатуре
VK_BACKSPACE = 0x08


class StopFlag(Protocol):
    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


class BackspaceStopFlag:
    """
    StopFlag, который срабатывает по нажатию Backspace.

    Нужен для сценариев, когда методы вызываются напрямую без внешнего stop_flag.
    """

    def __init__(self) -> None:
        self._stopped = False
        self._last_state = False

    def _poll(self) -> None:
        if self._stopped:
            return
        state = (user32.GetAsyncKeyState(VK_BACKSPACE) & 0x8000) != 0
        # фронт нажатия
        if state and not self._last_state:
            self._stopped = True
        self._last_state = state

    def is_set(self) -> bool:
        self._poll()
        return bool(self._stopped)

    def wait(self, timeout: float) -> bool:
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) < float(timeout):
            if self.is_set():
                return True
            time.sleep(0.02)
        return self.is_set()


def wait_for_mark_key(key=VK_OEM_6, prompt: str = "Нажмите ] для продолжения...") -> None:
    """Ожидает одиночного нажатия выбранной клавиши"""
    print(prompt)
    last_state = False
    while True:
        state = (user32.GetAsyncKeyState(key) & 0x8000) != 0
        if state and not last_state:
            # дождаться отпускания, чтобы не было двойных срабатываний
            while (user32.GetAsyncKeyState(key) & 0x8000) != 0:
                time.sleep(0.02)
            return
        last_state = state
        time.sleep(0.02)


def wait_for_backspace_key(prompt: str = "Активируйте окно Requiem и нажмите Backspace для запуска... (для остановки нажмите Backspace повторно)") -> None:
    """Ожидает одиночного нажатия Backspace (нажатие + отпускание)."""
    print(prompt)
    last_state = False
    while True:
        state = (user32.GetAsyncKeyState(VK_BACKSPACE) & 0x8000) != 0
        if state and not last_state:
            while (user32.GetAsyncKeyState(VK_BACKSPACE) & 0x8000) != 0:
                time.sleep(0.02)
            return
        last_state = state
        time.sleep(0.02)


class RequiemClicker:
    """Класс для выполнения автоматизации в игре Requiem."""
    
    def __init__(
        self,
        mouse_client: MouseClient,
        window_title_substring: str = "Requiem",
        *,
        wait_for_backspace_on_init: bool = True,
    ):
        """
        Инициализирует RequiemClicker.
        
        Args:
            mouse_client: Экземпляр класса, реализующего интерфейс MouseClient
            window_title_substring: Подстрока для поиска окна по заголовку
            wait_for_backspace_on_init: Если True — перед стартом ждать одиночного Backspace.
        """
        # Самое первое действие (по умолчанию): ждём Backspace. До этого никаких проверок/поисков UI не выполняем.
        if wait_for_backspace_on_init:
            wait_for_backspace_key()
        self.clicker = Clicker(mouse_client, window_title_substring)
        # Важно: ImageFinder должен использовать тот же hwnd, что и Clicker,
        # иначе возможно рассогласование кликов и ROI-захвата при наличии нескольких hwnd.
        self.image_finder = ImageFinder(window_title_substring, hwnd_provider=self.clicker.get_hwnd)
        self.backpacks = BackpackManager(clicker=self.clicker, image_finder=self.image_finder)
        self.disassemble: Optional[DisassembleManager] = None
        self.sharpening: Optional[SharpeningManager] = None
        self._progress_ema_seconds: Optional[float] = None
    
    def find_image_in_roi(
        self,
        template_png_path: str,
        roi_top_left_client: tuple[int, int],
        roi_size: tuple[int, int],
        *,
        threshold: float = 0.93,
        timeout_s: float = 2.0,
        poll_s: float = 0.1,
    ) -> Optional[dict]:
        """
        Ищет изображение (шаблон) в ROI внутри окна Requiem.

        ROI задаётся в координатах клиентской области окна (окно можно двигать).

        Returns:
            dict (top-left, template_size, elapsed_s, score) или None.
        """
        # Ищем шаблоны относительно установленного пакета (site-packages), а не cwd.
        img_dir = Path(__file__).resolve().parents[1] / "img"
        return self.image_finder.find_template_in_client_roi(
            template_png_path=str(img_dir / template_png_path),
            roi_top_left_client=roi_top_left_client,
            roi_size=roi_size,
            threshold=threshold,
            timeout_s=timeout_s,
            poll_s=poll_s,
        )

    def save_roi_image_interactive(
        self,
        *,
        output_filename: Optional[str] = None,
        stop_flag: Optional[StopFlag] = None,
    ) -> None:
        """
        Интерактивное сохранение области экрана (ROI) внутри окна Requiem.

        Управление:
        - ']': 1-е нажатие — сохранить первую точку, 2-е — вторую; затем область между ними сохраняется в файл.

        Точки берутся в client-координатах окна (как в find_coords).
        """
        p1: Optional[tuple[int, int]] = None
        p2: Optional[tuple[int, int]] = None

        last_mark_state = False
        print(
            "\nВыбор области запущен. Наведите мышь на 1-ю точку и нажмите ]. "
            "Затем наведите на 2-ю точку и нажмите ] ещё раз. "
            "Backspace — остановить.\n"
        )

        while True:
            if stop_flag and stop_flag.is_set():
                print("\nОстановлено.")
                return

            mark_pressed = (user32.GetAsyncKeyState(VK_OEM_6) & 0x8000) != 0
            mark_edge = mark_pressed and not last_mark_state
            last_mark_state = mark_pressed

            coords = self.clicker.find_coords()
            mx, my = coords["mouse_pos"]
            cx, cy = coords["mouse_client"]
            inside = coords["mouse_inside"]

            status = "inside" if inside else "outside"
            line = f"Mouse=({mx},{my}) client=({cx},{cy}) {status}"
            if p1 is not None:
                line += f" | P1={p1}"
            if p2 is not None:
                line += f" | P2={p2}"

            print("\r" + line + " " * 10, end="\r", flush=True)

            if mark_edge:
                if not inside:
                    print("\nМышь сейчас вне client area окна — точка не сохранена.")
                else:
                    if p1 is None:
                        p1 = (int(cx), int(cy))
                        print(f"\nP1 задана: {p1}")
                    elif p2 is None:
                        p2 = (int(cx), int(cy))
                        print(f"P2 задана: {p2}")
                        break

            if stop_flag:
                if stop_flag.wait(timeout=0.01):
                    print("\nОстановлено.")
                    return
            else:
                time.sleep(0.01)

        # Сохранение области между двумя точками
        assert p1 is not None and p2 is not None
        x1, y1 = p1
        x2, y2 = p2
        left, right = (x1, x2) if x1 <= x2 else (x2, x1)
        top, bottom = (y1, y2) if y1 <= y2 else (y2, y1)
        w = max(1, int(right - left))
        h = max(1, int(bottom - top))

        if output_filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"roi_capture_{ts}.png"

        out_path = Path("sources") / output_filename
        result = self.image_finder.save_client_roi_to_file(
            output_path=out_path,
            roi_top_left_client=(int(left), int(top)),
            roi_size=(int(w), int(h)),
        )

        print("\n")
        print(f"Сохранено: {result['output_path']}")
        print(f"ROI client: top_left={result['roi_top_left_client']} size={result['roi_size']}")
        print(f"Elapsed: {result['elapsed_s']:.4f} s")

    def _reset_progress(self) -> None:
        """Сбрасывает состояние расчёта прогресса/ETA."""
        self._progress_ema_seconds = None

    @staticmethod
    def _ensure_stop_flag(stop_flag: Optional[StopFlag]) -> StopFlag:
        """Если stop_flag не передали — используем BackspaceStopFlag по умолчанию."""
        return stop_flag if stop_flag is not None else BackspaceStopFlag()

    def _print_progress(self, current_iter: int, total_iters: int, iter_seconds: float, prefix: str = "", suffix: str = "") -> None:
        """
        Печатает прогресс и приблизительное оставшееся время.

        Args:
            current_iter: Номер текущей итерации (1-based)
            total_iters: Общее число итераций
            iter_seconds: Время текущей итерации в секундах
            prefix: Префикс строки (например, "Мешок 1: ")
        """
        if total_iters <= 0:
            return

        # EMA по времени итерации, чтобы ETA не "прыгало"
        alpha = 0.2
        if self._progress_ema_seconds is None:
            self._progress_ema_seconds = max(0.0, float(iter_seconds))
        else:
            self._progress_ema_seconds = (1.0 - alpha) * self._progress_ema_seconds + alpha * max(0.0, float(iter_seconds))

        remaining_iters = max(0, int(total_iters) - int(current_iter))
        remaining_seconds = max(0.0, self._progress_ema_seconds * remaining_iters)
        formatted_time = time.strftime('%H:%M:%S', time.gmtime(remaining_seconds))

        msg = f"{prefix}Выполнено {current_iter} из {total_iters} | ETA ~ {formatted_time} {suffix}"
        print(msg, end="\r", flush=True)
     
    def find_coords(self, stop_flag: Optional[StopFlag] = None, short_mode: bool = False) -> None:
        """
        Запускает мониторинг координат окна и курсора мыши.
        
        Args:
            stop_flag: Флаг для остановки выполнения скрипта
            short_mode: Режим короткого отображения координат
        """
        last_mark_state = False
        print("\nМониторинг координат окна и курсора мыши запущен. Нажмите ] чтобы сохранить текущую строку координат.")
        play_start_sound()
        while True:
            # Проверка флага остановки в начале каждой итерации
            if stop_flag and stop_flag.is_set():
                print("\nМониторинг остановлен.")
                return

            # ']' — сохранить текущую строку координат (перевод строки)
            # ']' — сохранить текущую строку координат (перевод строки)
            mark_pressed = (user32.GetAsyncKeyState(VK_OEM_6) & 0x8000) != 0
            if mark_pressed and not last_mark_state:
                # Ставим перенос строки, чтобы "зафиксировать" текущую строку
                print("\n", end="", flush=True)
            last_mark_state = mark_pressed
            
            try:
                coords = self.clicker.find_coords()
                
                # Проверка флага остановки сразу после получения координат
                if stop_flag and stop_flag.is_set():
                    print("\nМониторинг остановлен.")
                    return
                
                if short_mode:
                    line = (
                        f"Mouse=({coords['mouse_pos'][0]},{coords['mouse_pos'][1]}) "
                        f"client=({coords['mouse_client'][0]},{coords['mouse_client'][1]}) "
                        f"inside={coords['mouse_inside']}"
                    )
                else:
                    line = (
                        f"w_pos=({coords['window_pos'][0]},{coords['window_pos'][1]}) "
                        f"size=({coords['window_size'][0]}x{coords['window_size'][1]}) | "
                        f"Client=({coords['client_origin'][0]},{coords['client_origin'][1]}) "
                        f"size=({coords['client_size'][0]}x{coords['client_size'][1]}) | "
                        f"Mouse=({coords['mouse_pos'][0]},{coords['mouse_pos'][1]}) "
                        f"client=({coords['mouse_client'][0]},{coords['mouse_client'][1]}) "
                        f"inside={coords['mouse_inside']}"
                    )
                print("\r" + line + " " * 10, end="\r", flush=True)
                
                # Проверка флага остановки после вывода
                if stop_flag and stop_flag.is_set():
                    print("\nМониторинг остановлен.")
                    return
                    
            except SystemExit:
                print("\nОкно не найдено.")
                return
            except KeyboardInterrupt:
                print("\nВыход.")
                return
            
            # Используем wait с коротким таймаутом для быстрой реакции на stop_flag
            if stop_flag:
                if stop_flag.wait(timeout=0.01):
                    print("\nМониторинг остановлен.")
                    return
            else:
                time.sleep(0.01)
    
    def sharpening_items(
        self,
        retries: Optional[list] = None,
        cost: int = 8,
        stop_flag: Optional[StopFlag] = None,
        *,
        only_save: bool = True,
    ) -> None:
        """
        Выполняет заточку предметов.
        
        Args:
            retries: Список списков с количеством повторений для каждой ячейки
            cost: Стоимость одной попытки (для расчёта требуемых ксеонов)
            stop_flag: Флаг для остановки выполнения скрипта
        """

        stop = self._ensure_stop_flag(stop_flag)

        if retries is None:
            retries = [[1]]

        self._reset_progress()
        start_time = time.perf_counter()
        total_iters = sum(sum(row) for row in retries)
        done_iters = 0
        print(f"Необходимо иметь ксеонов: {cost * sum([sum(row) for row in retries])}")
        play_start_sound()
        wait_for_mark_key(
            key=VK_OEM_6,
            prompt="Нажмите ] для продолжения..."
        )

        # Первый шаг: найти окно заточки и закэшировать его координаты (окно НЕ перемещаем)
        self.sharpening = SharpeningManager(clicker=self.clicker, image_finder=self.image_finder, backpacks=self.backpacks)
        top_left_screen = self.sharpening.ensure_window_cached(threshold=0.98, timeout_s=2.0, poll_s=0.1)
        top_left_client = self.sharpening.get_cached_top_left_in_client()
        print(f"Окно заточки найдено: top_left_client={top_left_client}, top_left_screen={top_left_screen}")

        for row in range(len(retries)):
            for col in range(len(retries[row])):
                assert self.sharpening is not None
                
                for attempt in range(retries[row][col]):
                    # Проверка флага остановки
                    if stop.is_set():
                        print("\nПолучен сигнал остановки.")
                        return

                    iter_started = time.perf_counter()
                    
                    # 1) Перетащить предмет из ячейки рюкзака в ячейку заточки
                    if self.sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                        backpack_index=0,
                        row=row,
                        col=col,
                    ):
                        if stop.wait(0.2):
                            print("\nПолучен сигнал остановки.")
                            return
                        # 1.0) Проверка "ошибочного" окна с кнопкой OK (после вставки предмета).
                        # Если появилось — закрываем кликом (внутри метода), ждём и пропускаем этот предмет.
                        if self.sharpening.check_reject_ok_popup_and_close():
                            if stop.wait(0.1):
                                print("\nПолучен сигнал остановки.")
                                return
                            # На всякий случай проверим доступность окна рюкзака перед переходом дальше
                            self.backpacks.ensure_backpack_window_available(0)
                            done_iters = done_iters + (retries[row][col] - attempt)
                            iter_seconds = time.perf_counter() - iter_started
                            self._print_progress(done_iters, total_iters, iter_seconds, suffix=" Неточимый предмет! Пропуск")
                            break
                        # 1.1) Проверить, подходит ли предмет для заточки (ищем '+')
                        variant = self.sharpening.ensure_item_is_sharpenable()
                        current_sharpening_value = self.sharpening.get_current_sharpening_value(variant=variant)
                        # 1.2) Проверить, что "Авто" активна (если нет — закончились ксеоны или проблема)
                        self.sharpening.ensure_auto_button_active()
                        # 2) Авто
                        self.sharpening.click_auto()
                        if stop.wait(0.2):
                            print("\nПолучен сигнал остановки.")
                            return
                        # 2.1) Проверка "безопасной" заточки: если safe и only_save=True — пропускаем предмет
                        is_sharpening_safe = self.sharpening.is_sharpening_safe()
                        if not(is_sharpening_safe) and only_save:
                            if stop.wait(0.25):
                                print("\nПолучен сигнал остановки.")
                                return
                            # При пропуске — убедимся, что окно рюкзака доступно (заголовок видим),
                            # иначе переоткроем рюкзак.
                            self.backpacks.ensure_backpack_window_available(0)
                            done_iters = done_iters + (retries[row][col] - attempt)
                            iter_seconds = time.perf_counter() - iter_started
                            self._print_progress(done_iters, total_iters, iter_seconds, suffix=" Только безопасная! Пропуск")
                            break

                        # 3) ОК
                        self.sharpening.click_ok()
                        if stop.wait(0.5):
                            print("\nПолучен сигнал остановки.")
                            return

                        # 4) Клик по карте (client coords)
                        self.sharpening.click_map()
                        if stop.wait(1.0):
                            print("\nПолучен сигнал остановки.")
                            return

                        # 5) Повторить (client coords) + сброс кэша top_left на DEFAULT_WINDOW_TOP_LEFT_IN_CLIENT
                        self.sharpening.click_repeat(reset_window_top_left=True)
                        if stop.wait(0.3):
                            print("\nПолучен сигнал остановки.")
                            return
                    
                    done_iters += 1
                    iter_seconds = time.perf_counter() - iter_started
                    self._print_progress(done_iters, total_iters, iter_seconds, suffix=f" {current_sharpening_value} > {current_sharpening_value + 1} безопасная" if is_sharpening_safe else f" {current_sharpening_value} < {current_sharpening_value + 1} опасная")
        
        print("\nГотово")
        print(f"Затрачено времени: {time.perf_counter() - start_time:.2f} секунд")
        play_finish_sound()

    def sharpening_items_to(
        self,
        targets: list,
        *,
        stop_flag: Optional[StopFlag] = None,
        backpack_indices: Optional[list[int]] = None,
        confirm_with_bracket: bool = True,
    ) -> None:
        """
        Точит предметы до заданного уровня (по фактическому уровню заточки).

        Args:
            targets:
                Массив рюкзаков с целевыми уровнями заточки.
                Ожидаемый формат: targets[backpack][row][col] -> int.
                Значение 0 означает "пропустить ячейку".

                Пример:
                    [
                      [  # backpack 0
                        [0, 10, 0, 0, 12],
                        [0,  0, 0, 0,  0],
                      ],
                      [  # backpack 1
                        [0, 0, 0, 0, 0],
                      ],
                    ]
            stop_flag: флаг остановки.
            backpack_indices:
                Необязательный список индексов рюкзаков, соответствующий внешнему измерению targets.
                Если None, то используется [0..len(targets)-1].

        Правила:
        - Если ячейка оказалась пустой — считаем, что предмет сломался, переходим к следующей.
        - Если после вставки появился reject_ok — уровень максимальный, переходим к следующей.
        - Переходим к следующей, когда фактический уровень >= требуемого (уровень может откатываться).
        - Не выводим оценку времени выполнения.
        """
        stop = self._ensure_stop_flag(stop_flag)

        # Не считаем "сколько времени займёт" и не печатаем "сколько ксеонов нужно" — как просили.
        self._reset_progress()
        done_items = 0

        # Приведём индексы рюкзаков к явному списку
        if backpack_indices is None:
            backpack_indices = list(range(len(targets)))
        if len(backpack_indices) != len(targets):
            raise ValueError("backpack_indices должен быть той же длины, что и targets")

        total_items = 0
        for bi, bag in enumerate(targets):
            _ = bi
            for row in bag:
                for target_level in row:
                    if int(target_level) > 0:
                        total_items += 1

        play_start_sound()
        if confirm_with_bracket:
            wait_for_mark_key(key=VK_OEM_6, prompt="Нажмите ] для продолжения...")

        # Закрываем мешающие окна рюкзаков, затем находим окно заточки и кэшируем координаты.
        self.backpacks.close_all_opened_backpacks(refresh=True)
        self.sharpening = SharpeningManager(clicker=self.clicker, image_finder=self.image_finder, backpacks=self.backpacks)
        self.sharpening.ensure_window_cached(threshold=0.98, timeout_s=2.0, poll_s=0.1)

        for t_idx, bag in enumerate(targets):
            backpack_index = int(backpack_indices[t_idx])
            if not bag:
                continue
            for row_idx, row in enumerate(bag):
                if not row:
                    continue
                for col_idx, target_level_raw in enumerate(row):
                    target_level = int(target_level_raw)
                    if target_level <= 0:
                        continue

                    item_started = time.perf_counter()
                    assert self.sharpening is not None

                    # Точим конкретный предмет до target_level (по фактическому уровню).
                    while True:
                        if stop.wait(0.2):
                            print("\nПолучен сигнал остановки.")
                            return
                        # 1) Перетащить предмет из ячейки рюкзака в ячейку заточки.
                        moved = self.sharpening.drag_item_from_backpack_cell_to_sharpening_cell(
                            backpack_index=backpack_index,
                            row=int(row_idx),
                            col=int(col_idx),
                        )
                        if not moved:
                            # Ячейка пустая -> предмет сломался/исчез -> переходим к следующему.
                            done_items += 1
                            iter_seconds = time.perf_counter() - item_started
                            self._print_progress(
                                done_items,
                                max(1, total_items),
                                iter_seconds,
                                prefix=f"Мешок {backpack_index + 1}: ",
                                suffix=" сломалось/пусто\n",
                            )
                            break

                        if stop.wait(0.1):
                            print("\nПолучен сигнал остановки.")
                            return

                        # 1.0) Проверка "ошибочного" окна с кнопкой OK (макс. уровень / отказ).
                        if self.sharpening.check_reject_ok_popup_and_close():
                            if stop.wait(0.4):
                                print("\nПолучен сигнал остановки.")
                                return
                            # Уровень максимальный -> переходим к следующему предмету.
                            self.backpacks.ensure_backpack_window_available(backpack_index)
                            done_items += 1
                            iter_seconds = time.perf_counter() - item_started
                            self._print_progress(
                                done_items,
                                max(1, total_items),
                                iter_seconds,
                                prefix=f"Мешок {backpack_index + 1}: ",
                                suffix=" max (reject_ok)\n",
                            )
                            if stop.wait(0.4):
                                print("\nПолучен сигнал остановки.")
                                return
                            break


                        # 1.1) Проверить '+', определить цвет (a1..a5)
                        variant = self.sharpening.ensure_item_is_sharpenable()

                        # 1.3) Прочитать текущий фактический уровень заточки
                        current_level = self.sharpening.get_current_sharpening_value(variant=variant)


                        # Если уже достигли/перешли нужный уровень — переходим к следующему предмету.
                        if int(current_level) >= int(target_level):
                            self.sharpening.click_repeat(reset_window_top_left=True)
                            if stop.wait(0.25):
                                print("\nПолучен сигнал остановки.")
                                return
                            self.backpacks.ensure_backpack_window_available(backpack_index)

                            done_items += 1
                            iter_seconds = time.perf_counter() - item_started
                            self._print_progress(
                                done_items,
                                max(1, total_items),
                                iter_seconds,
                                prefix=f"Мешок {backpack_index + 1}: ",
                                suffix=f" готово {current_level}->{target_level}\n",
                            )
                            break

                        # 2) Авто (запуск заточки)
                        self.sharpening.click_auto()
                        if stop.wait(0.2):
                            print("\nПолучен сигнал остановки.")
                            return

                        # 1.2) Проверить, что "Авто" активна (если нет — закончились ксеоны или проблема)
                        self.sharpening.ensure_auto_button_active()

                        # Печатаем прогресс и текущий уровень -> цель (чтобы было видно "с какой на какую")
                        self._print_progress(
                            done_items,
                            max(1, total_items),
                            time.perf_counter() - item_started,
                            prefix=f"Мешок {backpack_index + 1}: ",
                            suffix=f" {current_level} -> {target_level}",
                        )
                        
                        # 3) ОК
                        self.sharpening.click_ok()
                        if stop.wait(0.4):
                            print("\nПолучен сигнал остановки.")
                            return

                        # 4) Клик по карте (client coords)
                        self.sharpening.click_map()
                        if stop.wait(1.0):
                            print("\nПолучен сигнал остановки.")
                            return

                        # 5) Повторить (client coords) + сброс кэша top_left
                        self.sharpening.click_repeat(reset_window_top_left=True)
                        if stop.wait(0.4):
                            print("\nПолучен сигнал остановки.")
                            return

                        # Цикл продолжается: уровень мог повыситься или откатиться — сверяемся заново.

        print("\nГотово")
        play_finish_sound()

    def disassemble_items(
        self,
        retries: Optional[list] = None,
        stop_flag: Optional[StopFlag] = None,
        *,
        confirm_with_bracket: bool = True,
    ) -> None:
        """
        Выполняет разбор предметов.
        
        Args:
            retries: Список списков с количеством повторений для каждой ячейки
            stop_flag: Флаг для остановки выполнения скрипта
        """

        stop = self._ensure_stop_flag(stop_flag)
        self.backpacks.close_all_opened_backpacks()

        self.disassemble = DisassembleManager(
            clicker=self.clicker,
            image_finder=self.image_finder,
            backpacks=self.backpacks,
            align_on_init=True,
        )
        
        
        if retries is None:
            retries = [[1]]

        self._reset_progress()
        total_iters = sum(sum(sum(row) for row in bag) for bag in retries)
        done_iters = 0

        print("Этот скрипт разберет предметы согласно настройкам")
        play_start_sound()
        if confirm_with_bracket:
            wait_for_mark_key(
                key=VK_OEM_6,
                prompt="Нажмите ] для продолжения..."
            )

        for bag in range(len(retries)):
            if len(retries[bag]) == 0:    
                continue
            for row in range(len(retries[bag])):
                if len(retries[bag][row]) == 0:    
                    continue
                for col in range(len(retries[bag][row])):
                    if retries[bag][row][col] == 0:    
                        continue
                    for _ in range(retries[bag][row][col]):
                        # Проверка флага остановки
                        if stop.is_set():
                            print("\nПолучен сигнал остановки.")
                            return

                        iter_started = time.perf_counter()

                        assert self.disassemble is not None
                        if self.disassemble.drag_item_from_backpack_cell_to_disassemble_cell(backpack_index=bag, row=row, col=col):   
                            if stop.wait(0.2):
                                print("\nПолучен сигнал остановки.")
                                return
                            self.disassemble.click_ok()
                            if stop.wait(1.0):
                                print("\nПолучен сигнал остановки.")
                                return

                        done_iters += 1
                        iter_seconds = time.perf_counter() - iter_started
                        self._print_progress(done_iters, total_iters, iter_seconds, prefix=f"Мешок {bag + 1}: ")
                        
        
        print("Готово")
        play_finish_sound()