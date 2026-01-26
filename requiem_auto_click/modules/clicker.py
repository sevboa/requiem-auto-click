"""Класс кликера, использующий клиент мыши для выполнения действий."""
import ctypes
import time
from typing import Tuple, Optional, Callable
from ctypes import wintypes
from .mouse_client_base import MouseClient
from .keyboard_utils import press_key_combo, type_text
from .window_utils import find_hwnd_by_title_substring, client_to_screen, SW_RESTORE

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    """Структура для прямоугольника Windows."""
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG)
    ]


class POINT(ctypes.Structure):
    """Структура для точки Windows."""
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG)
    ]


class Clicker:
    """Класс для выполнения кликов и перетаскиваний через клиент мыши."""
    
    def __init__(
        self,
        mouse_client: MouseClient,
        window_title_substring: str,
        *,
        hwnd: Optional[int] = None,
        hwnd_provider: Optional[Callable[[], int]] = None,
    ):
        """
        Инициализирует кликер с указанным клиентом мыши и подстрокой поиска окна.
        
        Args:
            mouse_client: Экземпляр класса, реализующего интерфейс MouseClient
            window_title_substring: Подстрока для поиска окна по заголовку
            hwnd: Явно заданный hwnd (если есть) — предпочтительнее поиска по заголовку.
            hwnd_provider: Функция, возвращающая hwnd. Используется, если hwnd не задан напрямую.
        """
        self.mouse_client = mouse_client
        self.window_title_substring = window_title_substring
        self._hwnd: Optional[int] = int(hwnd) if hwnd else None
        self._hwnd_provider: Optional[Callable[[], int]] = hwnd_provider
        self._set_dpi_aware()
    
    def _get_hwnd(self) -> int:
        """Получает handle окна, кэширует результат."""
        if self._hwnd is None:
            if self._hwnd_provider is not None:
                self._hwnd = int(self._hwnd_provider())
            else:
                self._hwnd = find_hwnd_by_title_substring(self.window_title_substring)
            if not self._hwnd:
                raise SystemExit(f"Окно не найдено по подстроке заголовка: {self.window_title_substring!r}")
        return self._hwnd

    def get_hwnd(self) -> int:
        """Публичный доступ к hwnd окна (единый источник для Clicker/ImageFinder)."""
        return self._get_hwnd()
    
    def _ensure_window_active(self) -> None:
        """Убеждается, что окно активно (восстанавливает если свернуто)."""
        hwnd = self._get_hwnd()
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.05)
    
    def _set_dpi_aware(self) -> None:
        """Устанавливает DPI awareness для корректной работы с координатами."""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
            return
        except Exception:
            pass
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    
    def click_at(self, screen_x: int, screen_y: int) -> None:
        """
        Выполняет клик по указанным экранным координатам.
        
        Args:
            screen_x: X координата на экране
            screen_y: Y координата на экране
        """
        self.mouse_client.click_at(screen_x, screen_y)

    def press_keys(self, keys: list[str]) -> None:
        """Нажимает комбинацию клавиш через SendInput.

        `keys` — массив строк, например: ["ctrl", "c"] / ["Shift", "F12"] / ["alt", "space"] / ["alt", " "].
        Правила валидации и поддерживаемые имена — см. `requiem_auto_click.modules.keyboard_utils.parse_key_combo`.
        """
        self._ensure_window_active()
        press_key_combo(keys)

    def input_text(self, text: str) -> None:
        """Вводит строку через SendInput как Unicode-сигналы (KEYEVENTF_UNICODE)."""
        self._ensure_window_active()
        type_text(text)
    
    def click_at_client(self, client_x: int, client_y: int) -> None:
        """
        Выполняет клик по указанным клиентским координатам окна.
        
        Args:
            client_x: X координата в клиентской области окна
            client_y: Y координата в клиентской области окна
        """
        self._ensure_window_active()
        hwnd = self._get_hwnd()
        screen_x, screen_y = client_to_screen(hwnd, client_x, client_y)
        self.mouse_client.click_at(screen_x, screen_y)
    
    def drag_screen(self, start_xy: Tuple[int, int], end_xy: Tuple[int, int], 
                   steps: int = 40, step_delay: float = 0.005) -> None:
        """
        Выполняет перетаскивание от start_xy до end_xy (экранные координаты).
        
        Args:
            start_xy: Начальные координаты (x, y) на экране
            end_xy: Конечные координаты (x, y) на экране
            steps: Количество шагов для перетаскивания
            step_delay: Задержка между шагами в секундах
        """
        self.mouse_client.drag_screen(start_xy, end_xy, steps, step_delay)
    
    def drag_client(self, start_xy: Tuple[int, int], end_xy: Tuple[int, int], 
                   steps: int = 40, step_delay: float = 0.005) -> None:
        """
        Выполняет перетаскивание от start_xy до end_xy (клиентские координаты).
        
        Args:
            start_xy: Начальные координаты (x, y) в клиентской области окна
            end_xy: Конечные координаты (x, y) в клиентской области окна
            steps: Количество шагов для перетаскивания
            step_delay: Задержка между шагами в секундах
        """
        self._ensure_window_active()
        hwnd = self._get_hwnd()
        start_screen = client_to_screen(hwnd, *start_xy)
        end_screen = client_to_screen(hwnd, *end_xy)
        self.mouse_client.drag_screen(start_screen, end_screen, steps, step_delay)
    
    def get_foreground_hwnd(self) -> int:
        """
        Получает handle активного окна.
        
        Returns:
            Handle активного окна
        """
        return user32.GetForegroundWindow()
    
    def get_window_rect(self, hwnd: int) -> RECT:
        """
        Получает прямоугольник окна в экранных координатах.
        
        Args:
            hwnd: Handle окна
            
        Returns:
            Структура RECT с координатами окна
        """
        r = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            raise ctypes.WinError()
        return r
    
    def get_client_rect(self, hwnd: int) -> RECT:
        """
        Получает прямоугольник клиентской области окна.
        
        Args:
            hwnd: Handle окна
            
        Returns:
            Структура RECT с размерами клиентской области
        """
        r = RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(r)):
            raise ctypes.WinError()
        return r
    
    def client_origin_on_screen(self, hwnd: int) -> Tuple[int, int]:
        """
        Получает экранные координаты начала клиентской области окна.
        
        Args:
            hwnd: Handle окна
            
        Returns:
            Кортеж (x, y) с экранными координатами начала клиентской области
        """
        pt = POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            raise ctypes.WinError()
        return pt.x, pt.y
    
    def get_cursor_pos(self) -> Tuple[int, int]:
        """
        Получает текущую позицию курсора мыши на экране.
        
        Returns:
            Кортеж (x, y) с экранными координатами курсора
        """
        pt = POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            raise ctypes.WinError()
        return pt.x, pt.y
    
    def find_coords(self) -> dict:
        """
        Получает информацию о координатах окна и курсора мыши.
        
        Returns:
            Словарь с информацией о координатах:
            - window_pos: позиция окна (x, y)
            - window_size: размер окна (width, height)
            - client_origin: начало клиентской области на экране (x, y)
            - client_size: размер клиентской области (width, height)
            - mouse_pos: позиция мыши на экране (x, y)
            - mouse_client: позиция мыши в клиентских координатах (x, y)
            - mouse_inside: находится ли мышь внутри клиентской области
        """
        hwnd = self._get_hwnd()
        wr = self.get_window_rect(hwnd)
        cr = self.get_client_rect(hwnd)
        cx0, cy0 = self.client_origin_on_screen(hwnd)
        mx, my = self.get_cursor_pos()
        
        c_w = cr.right - cr.left
        c_h = cr.bottom - cr.top
        
        rel_x = mx - cx0
        rel_y = my - cy0
        inside = (0 <= rel_x < c_w) and (0 <= rel_y < c_h)
        
        wx, wy = wr.left, wr.top
        ww, wh = (wr.right - wr.left), (wr.bottom - wr.top)
        
        return {
            'window_pos': (wx, wy),
            'window_size': (ww, wh),
            'client_origin': (cx0, cy0),
            'client_size': (c_w, c_h),
            'mouse_pos': (mx, my),
            'mouse_client': (rel_x, rel_y),
            'mouse_inside': inside
        }
    
    def start_coord_monitor(self, stop_flag: Optional[object] = None) -> None:
        """
        Запускает мониторинг координат в реальном времени.
        Управление через контроллер (Backspace для старт/стоп).
        
        Args:
            stop_flag: Флаг для остановки выполнения скрипта
        """
        while True:
            # Проверка флага остановки в начале цикла
            if stop_flag and stop_flag.is_set():
                print("\nМониторинг остановлен.")
                return
            
            try:
                coords = self.find_coords()
                
                # Проверка флага остановки после получения координат
                if stop_flag and stop_flag.is_set():
                    print("\nМониторинг остановлен.")
                    return
                
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
            except SystemExit:
                print("\nОкно не найдено.")
                return
            except KeyboardInterrupt:
                print("\nВыход.")
                return
            
            # Используем wait с таймаутом вместо sleep для более быстрой реакции на stop_flag
            if stop_flag:
                if stop_flag.wait(timeout=0.03):
                    print("\nМониторинг остановлен.")
                    return
            else:
                time.sleep(0.03)

